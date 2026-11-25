# 常见故障与实战避坑手册（Troubleshooting）

以下条目均来自深大智算中心真实迁移运行记录与官方手册 FAQ，遇到异常时按症状直接检索。

---

## 一、`bita` CLI 与认证/传输报错

### 1. `bita upload` / `download` 报 `EOF` 或连接超时
- **原因**：当前终端设置了 `http_proxy` / `https_proxy` / `ALL_PROXY` 等代理环境变量，与平台对象存储网关冲突（官方 FAQ 二.3 明确记录）。
- **解决**：共享服务器勿改全局配置；先运行 `env | grep -i proxy` 检查，再用无代理包装运行：
  ```bash
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
    bita upload ...
  ```

### 2. `bita download` 正常，但 `bita apply` 报 `status: 401` / `code: 1027`（用户认证信息过期）
- **原因**：`bita` 本地保存的对象存储传输 token 与用户中心 REST JWT 过期时间不一致。下载能通不代表能提交训练。
- **解决**：不要试图“绕过”或自动重试。到 Web 控制台重新登录账号，复制页面新生成的 `bita login -u ... -p ... -t szdx -e ...` 整行命令让用户重新执行一次，再重试 `bita apply`。

### 3. `bita upload` 传上去目录多套了一层（例如 `code-v1/code-v1/...`）
- **原因**：`bita upload` 会自动把 `-l <本地目录>` 的 `basename` 拼到 `-o` 后面。
- **解决**：`-o` 必须指向**远端父目录**且**以 `/` 结尾**（`-o team/user/proj/ -l ./staging/code-v1`）。

### 4. 需要中途停止异常任务或查看真实终端日志，找不到 `bita stop` / `bita logs`
- **原因**：已实测 `/usr/bin/bita` 仅含 `login / upload / download / apply / completion / help`，**不提供停止或状态查询子命令**。
- **解决**：立即前往 Web 控制台「分布式训练」查看日志并手动点停，防止卡死进程持续占用 GPU 计费。

---

## 二、代码发布与容器启动报错

### 5. `tasks[0].command` 启动即报 `source: not found` 或命令截断
- **原因**：平台使用极简 `/bin/sh`（非 `bash`）解析启动命令（官方 FAQ 一.15），多行脚本、`\` 续行、`source` 和嵌套复杂引号都会出错。
- **解决**：YAML 的 `command` 字段始终只写一行 `bash /mnt/<project>/code-v1/run.sh ...`（用 [render_bitahub_yaml.py](../scripts/render_bitahub_yaml.py) 强制检查）。

### 6. 原地 `tar -xzf code.tar.gz` 覆盖非空目录导致 Gate 失败或读到旧文件
- **原因**：重复运行或并发作业向同一个正在使用的挂载目录解压，引发文件写竞争、残留陈旧脚本或触发打包工具对非空目录异常。
- **解决**：采用 **Zero-Tar 不可变版本目录**（`code-v1`, `code-v2`...）配合 `CODE-MANIFEST.sha256`；任何代码修改发新版本号，不修改正在运行中的旧目录。

---

## 三、容器内 Python / PyTorch / C 扩展运行时陷阱

### 7. `DataLoader(num_workers > 0)` 卡死，日志抛 `OSError: AF_UNIX path too long`
- **现象（真实阻断案例）**：容器不退出但 GPU 利用率归零持续计费。
- **根因**：Linux 内核规定 Unix Domain Socket（`AF_UNIX sun_path`）最长只有 **107 字节**。如果环境变量 `TMPDIR` 落在深层持久卷目录（例如 `/mnt/<project>/.../experiments/batch/.runtime/tmp`），PyTorch `multiprocessing.resource_sharer` 再拼接 `/pymp-XXXXXXXX/listener-XXXXXXXX` 后极易突破 107 字节直接卡死。
- **解决**：
  1. 在入口脚本把 `TMPDIR`、`TEMP`、`TMP`、`MPLCONFIGDIR` 等全部指向容器本地极短路径（`<= 48` 字符、权限 `0700`）：
     ```bash
     export TMPDIR=/tmp/bt_tmp
     mkdir -p "${TMPDIR}" && chmod 700 "${TMPDIR}"
     ```
  2. 为所有多进程 `DataLoader(..., num_workers>0)` 显式设置 `timeout`（如 `180` 秒），宁可报错退出也不死锁烧卡。

### 8. 明明加了 Python `sitecustomize` 路径重映射，为什么 `h5py` / `np.memmap` 还是找不到 `/data1/...`？
- **根因**：Python 层对 `builtins.open` / `os.stat` / `pathlib` 的 hook 拦截不到 `h5py.File(...)`、`np.memmap(...)`、`libhdf5.so` 等通过 C/C++ 底层直接 `os.fspath()` 打开原始路径的调用，导致直到真正读取该大文件时才 `FileNotFoundError` 崩溃。
- **解决**：
  1. 彻底**去掉代码和配置清单里的本地绝对路径硬编码**，统一由命令行参数或环境变量（如 `MOUNT_ROOT` / `DATA_ROOT`）拼接相对路径；
  2. 对任何必须兼容历史清单的路径，在传入 `h5py.File`、`np.load`、`np.memmap`、`torch.load` 前显式过一次 `str(resolve_path(p))`；
  3. 用 `subprocess` / 多进程构造 `worker_env()` 时必须继承并传递 `PYTHONPATH`、`LD_LIBRARY_PATH` 和挂载根环境变量，不要重写为开发机路径；
  4. 上传前在本地建一个 `/tmp/mock_mnt` 模拟非原路径环境试跑一次。

### 9. 持久卷上 `chmod 0700` 失效或任务被强停后产物文件损坏
- **原因**：部分集群共享网络存储忽略 `chmod 0700` 权限设置；且 `fcntl.flock` 通常仅在同一容器节点内有效，跨容器不能当分布式锁。
- **解决**：需要严格 `0700` 的运行时临时目录放在容器本地 `/tmp/...`；写往持久卷的重要产物（`.pt` / `.npz` / `.json` / `.csv`）一律先写 `.partial` 再 `os.replace()` 原子替换。

---

## 四、无网环境、挂起误杀与显存 OOM

### 10. 长时间预处理或评估期间被平台判定“挂起”自动杀死
- **原因**：BitaHub 分布式训练开启挂起检测（`suspendDetection: 1`）时，默认超过 `suspendDetectionDuration`（常用 `30` 分钟）控制台无任何日志输出就会判定任务 Hang 并终止。
- **解决**：Python 端设置 `PYTHONUNBUFFERED=1`，在长耗时阶段每 `60s`–`300s` `print(..., flush=True)` 一行进度/心跳日志。

### 11. 训练初始化阶段卡死或报错连接 `huggingface.co` / `download.pytorch.org`
- **原因**：4090 / H100 计算节点无外网（官方 FAQ 一.13、三.1）。
- **解决**：提前下载预训练权重放到持久卷，入口脚本设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，并将代码中的权重路径指向挂载目录。

### 12. 单卡跑多进程并发突然 `CUDA Out of Memory`
- **原因**：未按模型实际峰值显存做隔离，盲目将高显存模型与其它任务共卡。
- **解决**：先跑单任务短测读取真实峰值显存（预留安全缓冲），高显存模型固定每卡 `1` 进程；仅当两任务叠加峰值远低于 `24 GiB`（RTX 4090）并实测共卡无 OOM 后，才允许单卡并跑。

### 13. 任务在集群一直排队（Pending）或双任务并发报配额受限
- **处理**：检查 Web 控制台队列与团队余额/并发配额；若同时提交多套任务被并发限额拦截，优先保持单一作业、在容器内顺序/按可用 GPU 调度，避免拆太多并行 Pod。
