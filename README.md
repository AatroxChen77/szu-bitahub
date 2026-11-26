# `szu-bitahub`

面向深圳大学智算中心（SZU BitaHub）的可复用 Agent Skill 与独立命令行辅助脚本集合，覆盖从首次安装 `bita` CLI、零 Docker 环境准备、数据集与 Zero-Tar 不可变代码发布、Web YAML 渲染提交、启动健康检查到结果 SHA-256 校验与回收的完整科研训练工作流。

优先面向 Linux 工作站与共享服务器（无需 `sudo` 权限），同时提供 Windows 与 macOS 的 `bita` 安装指引，兼容 Antigravity、Claude Code、Codex CLI 等主流编码 Agent。

> [!NOTE]
> **初版说明（v0.1）**：本 Skill 目前为基于真实科研训练迁移经验整理的**第一版（初版）**，优先覆盖我们已完整跑通的「单机 1/2 卡分布式训练 + 零 Docker 环境保存 + Zero-Tar 发布」链路，难免存在疏漏或尚未覆盖的平台场景。非常欢迎深大同学与各位使用者提交 **Issue** 反馈问题，或直接提 **PR（Pull Request）** 一起完善与扩充！

---

## 核心定位与实测范围

| 模块 / 能力 | 状态 | 说明 |
| :--- | :---: | :--- |
| **`bita` CLI 安装与代理/双令牌排障** | **已实测** | 含无 `sudo` `$HOME/.local/bin` 安装、单命令 `env -u` 清代理防 `EOF`、`401 / code 1027` JWT 刷新、`-o <父目录>/` 尾斜杠校验 |
| **零 Docker 环境配置（`conda-pack` → Web 开发环境 → 存为镜像）** | **已实测（主推）** | 本地 `conda-pack` → `bita upload` → 开发环境解包到容器本地 `/opt/<env>` 验证 CUDA → 网页点击「存为镜像」 |
| **团队共享镜像复用 / `conda-pack` 持久卷直接初始化** | **快捷选项 / 已验证初始化** | 同团队可共享已保存镜像；临时试验可一次性解包至持久卷配合 `.unpack_complete` 跳过重复解压 |
| **Zero-Tar 不可变代码发布 & 单机 1/2 GPU 分布式训练** | **已实测** | `分布式训练 → 其他框架 → 单副本(taskNum=1)` + `CODE-MANIFEST.sha256` 门禁 + 单行 `/bin/sh` YAML 渲染 |
| **容器防踩坑与 SHA-256 回收验证** | **已实测** | `AF_UNIX <= 107B`（短 `/tmp` `TMPDIR`）、C 扩展路径显式解析、`60s` 心跳防 30 分钟挂起误杀、首轮真实 `loss` 门槛 |
| **Docker 客户端推 Harbor / `Dockerfile` / `.tar` 建镜像** | **官方支持（未实测）** | 收录通用流程与注意事项作为可选参考，由具备 Docker 权限的用户自行操作 |
| **多机分布式、`H100`、超算 `Slurm`、在线推理、`Gitea`** | **官方支持（未实测）** | 仅保留官方帮助手册导航索引，不作为自动化提交流水线 |

---

## 安装到你的 AI Agent

将本仓库直接克隆到对应客户端的 `skills` 发现目录即可使用：

```bash
# 1. 项目级（推荐，兼容 Antigravity 等自动扫描 .agents/skills/ 的工作区工具）
git clone https://github.com/AatroxChen77/szu-bitahub.git .agents/skills/szu-bitahub

# 2. 全局安装（按你日常使用的客户端任选其一）
# Claude Code:
git clone https://github.com/AatroxChen77/szu-bitahub.git ~/.claude/skills/szu-bitahub

# Codex CLI:
git clone https://github.com/AatroxChen77/szu-bitahub.git ~/.codex/skills/szu-bitahub

# Antigravity 全局配置:
git clone https://github.com/AatroxChen77/szu-bitahub.git ~/.gemini/config/skills/szu-bitahub
```

---

## 目录结构

```text
szu-bitahub/
├── SKILL.md                          # Agent 主指令入口（6 步标准流与安全红线）
├── README.md                         # 本说明文档
├── LICENSE                           # MIT 许可证
├── .gitignore
├── references/
│   ├── quickstart.md                 # 新人从零跑通一次 GPU 训练的 6 步清单
│   ├── bita-cli.md                   # bita 安装、代理清理、401/1027 与上传下载细则
│   ├── environment.md                # 零 Docker 存镜像主教程、conda-pack 与 Harbor 可选参考
│   ├── yaml-and-gpu-jobs.md          # Web 导出模板、Zero-Tar、/bin/sh 规范与进阶双卡调度案例
│   ├── troubleshooting.md            # 高频实战故障排查手册
│   └── official-manual.md            # 官方文档模块与未实测能力导航索引
└── scripts/
    ├── check_bita.sh                 # 检查 bita 安装位置、代理冲突与清代理封装执行
    ├── check_environment.sh          # 检查 Python/PyTorch/CUDA、持久卷读写、TMPDIR 长度与 Manifest
    ├── prepare_code_release.py       # 纯标准库 Zero-Tar 不可变代码目录打包与验证
    ├── render_bitahub_yaml.py        # 基于个人 Web 导出 YAML 渲染并校验单行启动配置（需 PyYAML）
    └── verify_artifacts.py           # 纯标准库产物 SHA-256 清单生成与完整性校验
```

---

## 独立脚本速查

以下脚本均不依赖特定课题代码或写死的个人/团队 ID，可单独运行（将 `<SKILL_DIR>` 替换为本目录路径）：

```bash
# 1. 检查 bita CLI 与代理环境变量
bash <SKILL_DIR>/scripts/check_bita.sh

# 2. 上传前或容器内环境自检（检查 CUDA、挂载可写性、TMPDIR <= 48 字符）
bash <SKILL_DIR>/scripts/check_environment.sh --mode local --python "$(which python3)"

# 3. 构建不可变版本代码目录与 CODE-MANIFEST.sha256
python3 <SKILL_DIR>/scripts/prepare_code_release.py \
  --src /path/to/project --out-dir ./staging --name code-v1

# 4. 基于个人从 Web 导出的模板 YAML 渲染任务配置
python3 <SKILL_DIR>/scripts/render_bitahub_yaml.py \
  --template web_1gpu_template.yaml \
  --name smoke-exp-v1 \
  --command "bash /mnt/<project>/code-v1/run.sh" \
  --out smoke-exp-v1.yaml

# 5. 生成 / 校验结果文件 SHA-256 清单
python3 <SKILL_DIR>/scripts/verify_artifacts.py generate --root ./outputs --manifest ./ARTIFACTS.sha256
python3 <SKILL_DIR>/scripts/verify_artifacts.py verify --root ./outputs --manifest ./ARTIFACTS.sha256
```

---

## 欢迎参与贡献（Contributing）

本项目目前仍是**初版（v0.1）**，很多平台高阶能力我们尚未逐一实测。非常欢迎大家通过 Issue 或 PR 一起共建，尤其欢迎以下方向的补充与改进：

- **已实测的新场景扩充**：多机多卡分布式训练（`taskNum > 1` / `DeepSpeed` / `Colossal-AI`）、`4×/8× GPU` 或 `H100` 实例、超算 `Slurm`（`sbatch`）作业模板、在线推理部署等经真实跑通的流程；
- **镜像与跨平台体验**：经深大平台实测可用的国内源 `Dockerfile` 模板、Harbor 客户端推送排障经验、Windows / macOS 本地端与 SSH 开发环境实操技巧；
- **实战避坑与脚本增强**：新的报错案例（补充至 [references/troubleshooting.md](./references/troubleshooting.md)）或辅助脚本改进。

> **PR 提交提醒**：提交前请确保**不包含任何个人账号密码、登录令牌、Harbor 凭据、个人/团队专属 ID（`teamId` / `clusterId` / `imageId`）或私有服务器路径**；若新增功能已经过你本人实测，请在 PR 中简要说明验证方式以便更新实测状态表。

---

## 安全与计费免责声明

1. **零凭据与零租户绑定**：本仓库不包含任何个人账号密码、JWT、`clusterId`、`teamId`、`imageId` 或存储桶路径。所有凭据与基础 YAML 必须由用户本人从 SZU BitaHub 控制台获取。
2. **计费动作需人工确认**：`bita apply -f` 会立即在平台发起算力资源申请并产生团队计费；切勿在未经本人确认的情况下由自动化工具提交训练作业。
3. **Web 控制台管理职责**：`bita` 客户端不包含任务状态查看、实时日志或停止计费子命令；监控排队与终止异常作业请在 Web 控制台操作。
