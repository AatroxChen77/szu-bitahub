# `bita` CLI 安装、登录、传输与提交指南

本文基于对 Linux 环境 `bita` 客户端的实测（`bita --help`）与官方手册整理，严禁将任何个人账号令牌或团队存储 ID 写入版本库。

---

## 一、下载与多平台安装

### 1. 获取官方客户端
在控制台打开「**资源管理 → 文件存储**」，任选团队进入详情后点击上方「**命令行快传工具 → 上传文件/下载文件**」，按页面提供的当前有效链接下载对应平台客户端（官方手册「如何创建分布式训练任务 → 通过命令行创建」记录了四端安装包：Windows `.msi`、Linux 可执行文件、macOS Intel `.pkg`、macOS Apple Silicon `.pkg`，实际链接请以平台页面最新提供为准）。

### 2. Linux 安装（区分已有全局安装 vs 无 `sudo` 用户级安装）

先检测是否已安装（`<SKILL_DIR>` 为本 Skill 所在目录）：
```bash
bash <SKILL_DIR>/scripts/check_bita.sh
```
- **共享 Linux 服务器（已全局安装时）**：若已存在系统级 `/usr/bin/bita`（静态编译 Go 单文件，约 `11M`），直接使用即可，**不要尝试用 `sudo` 覆盖**。
- **其他无 `sudo` 权限的 Linux 工作站/个人账户**（注：官方手册建议放 `/usr/bin`，无 root 权限时采用标准用户级 `$HOME/.local/bin` 部署，避免修改系统目录）：
  ```bash
  mkdir -p "$HOME/.local/bin"
  install -m 755 ./bita "$HOME/.local/bin/bita"
  export PATH="$HOME/.local/bin:$PATH"
  command -v bita
  bita --help
  ```
  如需永久生效，将以下行追加到自己的 `~/.bashrc`：
  ```bash
  export PATH="$HOME/.local/bin:$PATH"
  ```

### 3. Windows 与 macOS 安装
- **Windows**：运行官方提供的 `.msi` 安装包；如终端未自动刷新环境变量，重开 PowerShell 后执行 `bita --help` 验证。
- **macOS（Intel / Apple Silicon）**：按官方指引运行 `.pkg` 安装包，或将二进制放在终端可访问的 `PATH` 目录中，在终端运行 `bita --help` 验证。

---

## 二、实测可用的子命令与硬边界

在已实测的 `bita` 客户端中，`bita --help` 仅包含以下 6 个子命令：

| 子命令 | 用途 | 关键参数 |
| :--- | :--- | :--- |
| `login` | 登录平台写入本地会话凭据 | `-u <user> -p <token> -t <tenant> -e <endpoint>` |
| `upload` | 上传本地文件或文件夹到文件存储 | `-e <endpoint> -b <bucket> -c <clusterID> -o <remote_dir/> -l <local_path>` |
| `download` | 从文件存储/数据集/模型下载文件或文件夹 | `-e <endpoint> -b <bucket> -c <clusterID> -o <remote_path> -l <local_dir>` |
| `apply` | 提交分布式训练任务 YAML | `-f <job.yaml>` |
| `completion` | 生成 shell 自动补全脚本 | — |
| `help` | 查看帮助 | — |

> [!IMPORTANT]
> **没有管理/停止子命令**：`bita` **不提供** `status`、`logs`、`stop`、`cancel`、`delete` 等命令。查看排队、看实时标准输出、强制停止异常作业以停止计费，一律回到 Web 控制台操作。

---

## 三、代理冲突清理与登录凭据生命周期

### 1. 执行前检查代理环境变量
实验室服务器常默认设置了全局代理，官方 FAQ 二.3 已确认代理会导致 `bita upload` 报 **`EOF`**。因此每次使用前建议先自检：
```bash
command -v bita
env | grep -i proxy
```
**在共享服务器上不要修改系统或全局配置**，只对单条 `bita` 命令用 `env -u` 清除代理即可：
```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  bita --help
```

### 2. 登录与“半衰期不一致”坑（`401 / code 1027`）
平台在「命令行快传工具」给出的登录命令形式如下（其中深大常见 `-t szdx`，具体 `-p` 加密令牌与 `-e` 地址必须从**当前控制台**复制）：
```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  bita login -u <用户名> -p <平台页面给的令牌> -t szdx -e <平台地址>
```
> [!WARNING]
> **实测高频坑**：`bita` 本地会同时维护“对象存储传输凭据”和“用户中心 REST JWT”，两者过期时间不同步。常见症状是 **`bita download`/`upload` 仍能成功，但 `bita apply` 返回 `status: 401, code: 1027`（用户认证信息过期）**。
> - **规则**：永远不能用 `bita download` 成功来等价判断可以提交作业；遇到 `401` 或 `code 1027`，立即停止重试，回到 Web 登录账号并重新复制一行 `bita login` 刷新。

---

## 四、上传、提交、下载实战规则

### 1. `bita upload`（记住 `-o` 末尾 `/`）
```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  bita upload \
    -e <平台地址> \
    -b home \
    -c <存储桶clusterID> \
    -o <团队名>/<用户名>/<项目目录>/ \
    -l <本地文件或文件夹路径>
```
- **末尾 `/` 铁律**：`bita upload` 会把 `-l` 的**目录名/文件名（basename）**直接拼接到 `-o` 后面。
  - 正确：`-o <team>/<user>/proj/ -l ./staging/code-v1` → 远端生成 `<team>/<user>/proj/code-v1/...`。
  - 错误：把 `-o` 写成 `<team>/<user>/proj/code-v1/` → 会套成 `proj/code-v1/code-v1/...`。
- **严禁借用他人参数**：`-e`、`-b`、`-c` 和 `-o` 的团队/用户前缀必须来自你自己的文件存储页面。
- **体积建议**（官方手册「文件存储 → 文件传输」）：`< 2 GiB` 可网页直接上传，`>= 2 GiB` 或含大量文件的目录推荐用 `bita upload`。
- **上传往返校验**：发布代码目录（如含 `CODE-MANIFEST.sha256`）后，立刻把远端 `CODE-MANIFEST.sha256` `bita download` 回来与本地 `diff -u`，确认无隐式截断或套层。

### 2. `bita apply` 提交 YAML
仅在用户明确授权启动计费任务后执行：
```bash
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  bita apply -f job.yaml
```
- 若返回配额不足（并发作业数上限）或认证过期（`401` / `code 1027`），立即停下向用户报告，不要盲目连环重试产生重复排队作业。

### 3. `bita download` 精确下载与避坑
官方推荐方式是在 Web「文件存储」选中目标文件或文件夹后，点击「**命令行快传工具 → 下载文件**」直接复制带精确 `-o` 与 `-c` 的命令：
- **下载单个文件**：`-o <remote_dir/file>`，`-l <本地已存在或目标父目录>`。**实操建议先删除本地同名旧文件再下载**，避免覆盖歧义或误读缓存旧文件。
- **下载整个文件夹（如 `logs/`）**：`-o <remote_dir/logs/>`（保留末尾 `/`），`-l <本地父目录>`，同样建议先清理本地旧目录 `rm -rf <out>/logs` 后再下载。
- **大权重延迟下载**：日常验收结果时，先拉取指标、日志、预测值和 manifest，大体积 `.pt` 权重单独估算磁盘预算后再按需拉取。
