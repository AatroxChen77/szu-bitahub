# YAML 模板、Zero-Tar 发布与 GPU 任务运行规范

本文涵盖：**实测已跑通**的「智算任务 → 分布式训练 → 其他框架 → 单副本（`taskNum: 1`）→ 单机 1 GPU / 2 GPU → 单一持久化文件存储挂载」完整运行与监控流程。

> [!NOTE]
> 多机分布式（`taskNum > 1` / `VC_TASK*_HOSTS` / `DeepSpeed` / `Colossal-AI`）、`H100` 与超算 `Slurm` 尚未在本团队流水中实测，仅收录在 [official-manual.md](./official-manual.md) 供检索参考。

---

## 一、Web 首次建档导出模板 YAML（不可省略）

`bita apply -f <job.yaml>` 会直接把顶层 `spec` 转发给 BitaHub 后端。其中 `clusterId`、`teamId`、`imageId`、`resourcePoolId`、`resourcePoolConfId` 和 `storage.input[0]` **与每个人的账号、团队和 GPU 规格强绑定**。

### 操作方法（每种卡型导 1 份，仅首次需要）
1. 进入控制台「**智算任务 → 分布式训练 → 创建**」。
2. 必填项按以下约定选择：
   - **团队**：选择当前计费与存储所属团队。
   - **计算框架**：选 **`其他`**（不要选 DeepSpeed / Colossal-AI，除非实际需要）。
   - **镜像选择**：选你已准备好的团队镜像或自定义镜像。
   - **存储挂载**：**文件存储**挂载你的项目根路径至容器内统一路径（例如 `/mnt/<project>`）。建议**保持单一持久卷挂载**，最易排查权限和路径问题。
   - **任务配置（Role）**：保持单一 Role `worker`、副本数 `1`；选择目标 `1× GPU` 或 `2× GPU` 实例规格。
   - **启动命令**：可先填一行占位（如 `bash /mnt/<project>/code-v1/run.sh`）。
   - **挂起检测**：建议开启，默认 `30` 分钟。
   - **自动重试**：初学与调试阶段建议关闭（`0`），避免代码语法错误无限反复重启计费。
3. 在确认或详情页点击「**查看 / 导出 YAML**」，保存为 `web_1gpu_template.yaml`（如需双卡再导一份 `web_2gpu_template.yaml`，注意 **`1× GPU` 与 `2× GPU` 的 `resourcePoolConfId` 不同**）。

---

## 二、标准 `jobKind: training` YAML 结构剖析（脱敏占位版）

```yaml
apiVersion: v1
kind: Job
metadata:
  # 必填：<=128字符，支持字母数字._-，不能含空格及 \/:*?"<>|，同一用户名下不可与已有活跃/历史重名冲突
  name: exp-smoke-v1
spec:
  clusterId: "<FROM_YOUR_WEB_YAML>"
  jobKind: "training"
  teamId: "<FROM_YOUR_WEB_YAML>"
  deepSpeedFlag: 0
  imageId: "<FROM_YOUR_WEB_YAML>"
  tasks:
  - type: "master"
    taskNum: 1
    resourcePoolConfId: "<FROM_YOUR_WEB_YAML_FOR_TARGET_GPU_COUNT>"
    resourcePoolId: "<FROM_YOUR_WEB_YAML>"
    # 必须为单行，由容器内极简 /bin/sh 解析
    command: "bash /mnt/<project>/code-v1/run.sh --batch /mnt/<project>/runs/exp-v1"
    name: "worker"
  storage:
    input:
    - type: "bucket"
      businessId: "home"
      path: "/share/home/<your_team>/<your_user>/<project_dir>"
      mountPath: "/mnt/<project>"
  frames:
  - "other"
  faultTolerance:
    envDiagnosis: 0
    suspendDetection: 1
    suspendDetectionDuration: 30
    autoRetry: 0
    retryConditions: []
    retryInterval: 0
    retryMaxNum: 0
```

### `/bin/sh` 启动命令三条红线（官方 FAQ 一.15）
BitaHub 用极简 `/bin/sh`（不是 `bash`）解析 `tasks[0].command`：
1. **必须单行**：禁止换行符、禁止 `\` 续行。
2. **禁止 `source` 与 `.`**：改用 `bash /mnt/.../entry.sh ...`，把所有 `export`、`source`、条件判断全写进 `.sh` 脚本内部。
3. **禁止启动命令里原地 `tar` 解压代码**：使用 Zero-Tar 不可变版本目录，防止覆盖并发任务读取的文件或撞上非空目录异常。

使用 [render_bitahub_yaml.py](../scripts/render_bitahub_yaml.py) 自动生成与校验 YAML（`<SKILL_DIR>` 为本 Skill 所在目录）：
```bash
python3 <SKILL_DIR>/scripts/render_bitahub_yaml.py \
  --template web_1gpu_template.yaml \
  --name exp-smoke-v1 \
  --command "bash /mnt/<project>/code-v1/run.sh --batch /mnt/<project>/runs/exp-v1" \
  --heartbeat-minutes 30 \
  --auto-retry 0 \
  --out exp-smoke-v1.yaml
```

---

## 三、Zero-Tar 不可变代码发布流程

早期“把 `code.tar.gz` 传到挂载盘，每次容器启动 `tar -xzf` 解到同一目录”的做法极易在 Gate 失败或并发跑批时损坏代码或命中陈旧 `.pyc`。**正式推荐 Zero-Tar 流程**：

1. **打包静态版本目录 + SHA-256 清单**：
   ```bash
   python3 <SKILL_DIR>/scripts/prepare_code_release.py \
     --src /path/to/repo \
     --out-dir ./staging \
     --name code-v1
   ```
   （自动剥离 `__pycache__`、`.pyc`、`.git`，拒绝隐藏软链接并生成 `CODE-MANIFEST.sha256`。）
2. **上传为独立远端目录**（不覆盖正在跑旧任务的目录；若修 bug，必须升版本到 `code-v2` 配合新实验目录）：
   ```bash
   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
     bita upload -e <endpoint> -b <bucket> -c <clusterId> \
       -o <team>/<user>/<project>/ -l ./staging/code-v1
   ```
3. **往返验真与容器开机 fail-closed 校验**：
   - 上传后把远端 `code-v1/CODE-MANIFEST.sha256` 下载回来 `diff -u` 比对一次；
   - 在容器入口脚本里运行 `(cd /mnt/<project>/code-v1 && sha256sum -c CODE-MANIFEST.sha256)` 完成零依赖哈希校验；如需同时拦截多余文件或软链接，可将本 Skill 的 `scripts/prepare_code_release.py` 一并放入发布目录，并在入口脚本执行 `python3 /mnt/<project>/code-v1/prepare_code_release.py --verify /mnt/<project>/code-v1`，发现缺文件、多文件或摘要不一致立即 `exit 1`。

---

## 四、通用容器入口脚本推荐骨架（`run.sh`）

将下面 6 件事放进每个项目的 `.sh` 启动脚本（对应脚本参考 [check_environment.sh](../scripts/check_environment.sh)）：
1. **短本地 `/tmp` 规避 `AF_UNIX` 长度限制**：
   ```bash
   SHORT_TMP="/tmp/job_$$"
   mkdir -p "${SHORT_TMP}" && chmod 700 "${SHORT_TMP}"
   export TMPDIR="${SHORT_TMP}" TEMP="${SHORT_TMP}" TMP="${SHORT_TMP}"
   ```
2. **离线与日志即时刷新**：
   ```bash
   export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONNOUSERSITE=1 PYTHONUNBUFFERED=1
   ```
3. **日志落持久卷**：用 `exec > >(tee -a "${BATCH_DIR}/logs/driver-$(date -u +%Y%m%dT%H%M%SZ).log") 2>&1` 同步把输出保存到挂载盘。
4. **信号转发与正常退出停计费**：`trap` 捕获 `TERM`/`INT` 并传给 Python 子进程；全部任务正常结束必须明确 `exit 0`（容器自动销毁并立刻停止计费；异常提前返回非零退出码才会触发可选重试）。
5. **周期心跳防 30 分钟挂起误杀**：若训练阶段有长时间无 stdout 输出的数据加载/评估，确保每 `<= 60s`（或远小于 `suspendDetectionDuration`）向 `stdout` `flush=True` 打印一行心跳。
6. **原子落盘**：关键结果（`.pt` / `.npz` / 指标摘要）一律先写 `.partial` 再 `os.replace()`。

---

## 五、提交后验收标准与结果回收

1. **先本地干跑，再单卡 Smoke，最后大批量**：
   - 本地先确认输入数据读取、路径解析无开发机硬编码、输出目录可写。
   - **提交后不能只看 `bita apply` 成功！** 到 Web 控制台或拉取持久卷 `logs/` 确认：
     - `nvidia-smi` 显示卡数、卡型与申请一致；
     - 无 `Traceback` / `AF_UNIX path too long` / `FileNotFoundError` / OOM；
     - 看到**第 1 轮真实训练输出**（例如 `epoch 1 ... loss=...`）。
2. **结束后增量拉取 + 校验**：
   - 先用 Web 生成的 `bita download` 拉取小体积汇总结果与 `logs/`；
   - 确认全部必需文件无损坏（可用 [verify_artifacts.py](../scripts/verify_artifacts.py)）；
   - 大体积权重文件评估本地磁盘余量后再分批下载。

---

## 六、进阶实战案例：大规模单容器双卡自调度（92 作业经验摘要）

> 新手首次上云只需用一个训练脚本占 `1× GPU` 跑通即可。下面经验供未来同时跑几十上百个小中型实验时参考：

- **背景与打法**：实验室此前在单副本 `RTX 4090 × 2` 容器中，用自研任务队列调度器管理多种方法 × 数据集的 `92` 个正式训练作业（单进程/双进程按实测显存预算分流）。
- **核心工程要点**：
  1. **本地先降维再传大文件**：原始 `41.58 GiB` HDF5 中提取训练真实用到的区域切片缩至 `1.77 GiB` 后上传，显著降低跨节点 I/O 与存储压力。
  2. **先做轻量 Gate 再上大盘**：上线前用 ~3 分钟 `gpu-gate` 校验冷重启等价与双卡真实显存并发安全（重显存模型限制每卡 `1` 进程，小显存模型才在验证后允许每卡 `2` 进程），绝不盲塞同卡并发导致 OOM。
  3. **断点幂等 + 自动汇总**：每个子任务完成后写不可变完成标记（`.seal` / manifest）与结果摘要，被平台抢占或重提时自动跳过已完成子任务、从 `latest.pt` 续跑。
  4. **实测结果**：最终实现 **`92 / 92` 作业全部完成**，墙钟约 `6.10` 小时；回收 `504` 个预测、日志与审计产物，随后按需补下载 `32` 个 `final.pt`（共 `2.7183 GiB`）并逐文件 SHA-256 验证通过。
