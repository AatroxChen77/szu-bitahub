# 新手快速上手指南（Quickstart：从零跑通一次 GPU 训练）

本指南面向首次使用深圳大学智算中心（SZU BitaHub）的实验室成员。目标是在最短时间内跑通“本地准备 → 上传存储 → Web 导一次 YAML → 提交单卡训练 → 检查首轮 `loss` → 拉回并校验结果”的完整闭环。

---

## 一、核心分工模型

智算中心把执行拆为三段：
1. **本地机器 / 实验室服务器**：装好 `bita`，本地验证代码在 CPU/小样本能跑通，打包不可变代码目录与离线数据，生成/提交 YAML，回收结果。
2. **平台「文件存储」（持久卷）**：在任务之间长期保存你的数据集、代码发布目录和训练输出（挂载到容器内的例如 `/mnt/<project>`）。
3. **GPU 计算节点容器（临时环境）**：**无外网**，任务退出后容器本地文件系统（如 `/tmp`、`/root`）会被销毁；**所有要保留的模型、日志、预测值必须写进持久卷挂载目录**。

---

## 二、第一次使用 6 步清单

> 路径说明：下文中的 `<SKILL_DIR>` 指本 Skill 所在目录（如 `.agents/skills/szu-bitahub`、`~/.claude/skills/szu-bitahub` 或 `~/.codex/skills/szu-bitahub`）。

### 第 1 步：平台建档（Web 控制台，一次性）
1. 通过深大统一身份认证登录智算平台，**加入或创建一个团队**（平台按团队账户计费，未加团队无法完整使用文件存储和提交任务）。
2. 打开左侧「**资源管理 → 文件存储**」，点击你的团队进入专属目录。记下右上/操作栏「**命令行快传工具**」里给出的 `-e`（平台地址）、`-b`（存储桶，常见为 `home`）、`-c`（集群 ID）和你的 `-o <team>/<user>/...` 前缀。

### 第 2 步：本机或共享服务器配好 `bita` CLI
1. 先检查当前机器是否已系统安装 `bita`（部分实验室共享服务器已全局安装 `/usr/bin/bita`）：
   ```bash
   bash <SKILL_DIR>/scripts/check_bita.sh
   ```
2. 若未安装，按 [bita-cli.md](./bita-cli.md) 下载客户端；Linux 无 `sudo` 用户可直接装入 `$HOME/.local/bin/bita`。
3. 从 Web「文件存储 → 命令行快传工具」复制含令牌的登录命令，清理代理后登录：
   ```bash
   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
     bita login -u <你的用户名> -p <页面给的令牌> -t szdx -e <平台地址>
   ```

### 第 3 步：准备 Python 环境（首选零 Docker「开发环境存镜像」）
详细方案对比见 [environment.md](./environment.md)：
- **首选主路线（已实测，免 Docker）**：
  1. 本地执行 `conda-pack -n <env> --ignore-missing-files -o <env>.tar.gz` 并 `bita upload` 到持久卷；
  2. 在 Web「**智算任务 → 开发环境**」选基础镜像与 GPU，挂载持久卷进入终端；
  3. 将环境包解到容器本地目录（如 `/opt/<env>`），运行 `conda-unpack` 并验证 `torch.cuda.is_available()`；
  4. 在开发环境详情页点击「**存为镜像 / 保存为镜像**」，保存后立即关闭开发环境停计费；后续所有正式分布式训练任务直接选择该镜像。
- **快捷选项**：若同团队同事已保存适配环境，直接在控制台复用团队共享镜像。
- **备用路线**：不保存新镜像，直接把 `conda-pack` 解包在持久卷 `<mount>/envs/<env>` 并写 `.unpack_complete` 标记复用（[environment.md](./environment.md)）。
- **可选参考（官方支持，未实测）**：若你自己熟悉 Docker，也可在具备 Docker 权限的机器自行 `docker login/tag/push` 到 `harbor.aicloud.szu.edu.cn:5000`。

### 第 4 步：准备离线数据与 Zero-Tar 代码目录
1. **离线化检查**：确认训练时不会下载 HuggingFace/PyTorchHub/pip 模型权重；若需要预训练权重，先在本机下载放进准备上传的目录并在代码中指向该路径。
2. **写好容器入口脚本 `run.sh`**（放入你的代码目录）：
   - 先设定容器本地短 `/tmp`（如 `export TMPDIR=/tmp/run_tmp && mkdir -p "$TMPDIR" && chmod 700 "$TMPDIR"`），防止 `DataLoader` 报 `AF_UNIX path too long`；
   - 设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`、`PYTHONUNBUFFERED=1`；
   - 所有输出路径指向容器持久卷挂载点（如 `/mnt/<project>/runs/test-01`）；
   - 启动时先运行 `nvidia-smi` 和 Python 导入自检。
3. **生成不可变代码版本并上传**（不要让容器启动后再原地覆盖解压代码 `tar` 包）：
   ```bash
   python3 <SKILL_DIR>/scripts/prepare_code_release.py \
     --src /path/to/your_repo --out-dir ./staging --name code-v1

   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
     bita upload -e <平台地址> -b <bucket> -c <clusterId> \
       -o <team>/<user>/<project_dir>/ \
       -l ./staging/code-v1
   ```
   > 关键细节：`-o` 指向**远端父目录**且必须保留末尾 `/`，上传后 `code-v1` 会落在 `<project_dir>/code-v1/`。

### 第 5 步：Web 导一次基准 YAML → 渲染并提交试跑任务
1. 打开控制台「**智算任务 → 分布式训练 → 创建**」：
   - 计算框架选 `其他`；
   - Role 选 `worker` / 副本数 `taskNum = 1`；
   - 选资源池与 `1× GPU` 实例规格、选定镜像、配置存储挂载（例如挂到 `/mnt/<project>`）；
   - 启动命令先填一行占位（例如 `echo ready`）或点击「**查看 / 导出 YAML**」保存为 `web_1gpu_template.yaml`。
   - 如需 `2× GPU`，建议用同样方法再导一份 `web_2gpu_template.yaml`（不同卡数对应的 `resourcePoolConfId` 不同）。
2. 用脚本把你的实际单行入口注入模板并校验语法安全：
   ```bash
   python3 <SKILL_DIR>/scripts/render_bitahub_yaml.py \
     --template web_1gpu_template.yaml \
     --name first-smoke-1gpu \
     --command "bash /mnt/<project>/code-v1/run.sh --smoke" \
     --out first-smoke-1gpu.yaml
   ```
3. 确认配置无误后（与用户确认计费操作后）提交：
   ```bash
   env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
     bita apply -f first-smoke-1gpu.yaml
   ```

### 第 6 步：验收真实启动、等完成后回收校验
1. **排队与运行检查**：`bita` 没有 `status`/`logs`/`stop` 命令，请在 Web「分布式训练」查看任务状态与实时日志；若异常卡死，立即在网页点击停止以免持续计费。
2. **启动验收红线**：不仅看“提交成功”，必须看到日志里：
   - `nvidia-smi` 卡型和数量符合预期；
   - 持久化挂载目录可写；
   - 真正跑出第 1 轮迭代 / `loss` 日志（且无 `AF_UNIX`、`FileNotFoundError`、OOM）。
3. **回收结果与校验**：任务显示完成后（脚本 `exit 0` 自动停止计费），在 Web 文件存储勾选输出文件或日志目录复制 `bita download` 命令拉回本地；需要批量文件校验时可用 [verify_artifacts.py](../scripts/verify_artifacts.py)。

---

## 三、下一步看什么

- 遇到 `EOF`、`401 / code 1027` 或无 `sudo` 安装 → [bita-cli.md](./bita-cli.md)
- 网页开发环境存镜像、团队复用、`conda-pack` 与可选 Docker Harbor 指南 → [environment.md](./environment.md)
- YAML 字段解读、心跳防挂起与多任务 92 作业调度案例 → [yaml-and-gpu-jobs.md](./yaml-and-gpu-jobs.md)
- 容器死锁、C 扩展路径坑、`DataLoader` 卡死排查 → [troubleshooting.md](./troubleshooting.md)
