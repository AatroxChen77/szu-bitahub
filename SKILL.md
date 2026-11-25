---
name: szu-bitahub
description: >-
  End-to-end guide and helper toolkit for running GPU training workloads on the
  Shenzhen University Computing Center (SZU BitaHub / 深大智算中心): bita CLI
  installation and proxy/auth troubleshooting, zero-Docker dev-container
  save-as-image and conda-pack environment setup, Zero-Tar immutable code
  releases, Web-exported YAML rendering, single-node 1-GPU or 2-GPU distributed
  job submission, real startup verification, and SHA-256 artifact retrieval.
  Use whenever helping users onboard to SZU BitaHub, install or debug bita CLI,
  migrate Python environments, build or inspect BitaHub job YAMLs, run or
  monitor GPU jobs, or retrieve outputs.
---

# SZU BitaHub (`深大智算中心`) Workflow Skill

Generic, project-agnostic runbook and toolkit for running reproducible GPU jobs on **SZU BitaHub** from a Linux workstation/server (primary) or Windows/macOS client (supported for `bita` transfer & submission).

## Scope & Verified Boundaries

- **Verified End-to-End Scope (Core Automation)**:
  `Web 控制台建档 + 文件存储持久化挂载 -> bita CLI 传输与提交 -> 网页 GPU 开发环境配置依赖（如 conda-pack）并「保存为镜像」（或直接挂载 conda-pack）-> 分布式训练（其他框架 / 单副本 taskNum=1 / 单机 1× 或 2× GPU）-> 容器内进程/日志与心跳管理 -> SHA-256 回收验证`。
- **Unverified Capabilities (Reference Only, Do Not Claim Tested)**:
  Docker client Harbor push (`docker login/tag/push`), Web `Dockerfile`/tar image builds, multi-node distributed training (`taskNum > 1`, `VC_TASK*_HOSTS`, `DeepSpeed`, `Colossal-AI`), `H100`, HPC Slurm queues (`超算任务`), Web inference services, and `Gitea`/`数据集` primary pipelines were not live-tested by us. Point users to [references/environment.md](./references/environment.md) / [references/official-manual.md](./references/official-manual.md) clearly marked as unverified official options.

## Hard Guardrails (Read Before Any Action)

1. **Zero Credentials / No Hardcoded Tenant IDs**: Never embed passwords, encrypted tokens, JWTs, personal `clusterId`, `teamId`, `imageId`, `resourcePoolId`, `resourcePoolConfId`, or bucket paths in shared docs, scripts, or committed YAMLs. Always parse them from each user's own Web-exported YAML or ask the user to paste their current Web-UI command.
2. **Require User Authorization Before Billing Actions**: Do **not** execute `bita apply` (which starts GPU billing) or bulk transfers without explicit user confirmation of the target YAML, GPU count, and remote path.
3. **Zero `sudo` / Per-User Isolation on Shared Servers**: Never modify system files (`/etc`, `/usr/bin`) or global proxy configs. Install `bita` per-user in `$HOME/.local/bin` if absent, and clear proxy variables **only per command** with `env -u ...`.
4. **Compute Nodes Have No Internet**: Pre-stage code, datasets, pip wheels, and model weights on persistent storage or inside the container image before submitting. Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
5. **Dual-Token Expiry Trap (`401 / code 1027`)**: `bita download`/`upload` can still succeed after the User Center REST JWT used by `bita apply` expires. Never infer submit capability from download success; on `401`/`1027`, stop and ask the user to run a fresh `bita login` copied from Web UI.

## Standard 6-Stage Execution Workflow

> Path convention: Replace `<SKILL_DIR>` below with the directory containing this `SKILL.md` (e.g., `.agents/skills/szu-bitahub` or `~/.claude/skills/szu-bitahub`).

Follow this order; read the linked reference file only when needed for the active step:

### Stage 1 · Verify `bita` CLI & Session
- Run [check_bita.sh](./scripts/check_bita.sh) to check architecture, `bita` path, proxy variables, and CLI help.
- If `bita` is missing (or user is on another host/OS), follow [references/bita-cli.md](./references/bita-cli.md) for rootless Linux (`~/.local/bin/bita`), Windows, or macOS installation, plus `bita login -u <user> -p <token> -t szdx -e <endpoint>`.

### Stage 2 · Choose & Prepare Python Environment
Follow the verified priority order in [references/environment.md](./references/environment.md):
1. **Primary (Verified End-to-End · No Docker Required)**: Pack locally with `conda-pack --ignore-missing-files` -> `bita upload` to persistent storage -> launch a Web **开发环境** (`智算任务 -> 开发环境`) mounting persistent storage -> unpack inside a **container-local** path (such as `/opt/<env>`, NOT only on `/mnt/...`, since persistent mounts are excluded when saving images), run `conda-unpack`, verify PyTorch/CUDA -> click **「保存为镜像 / 存为镜像」** in Web UI (then stop the dev environment to halt billing) -> select that custom image in subsequent distributed training jobs (e.g., unpacking into `/opt/my_env` before saving produces an image with `/opt/my_env/bin/python` ready on startup).
2. **Quick Option (Team Shared Image)**: If the user's team already has a saved compatible image in Web UI, reuse it directly (official team sharing supported; cross-team sharing unsupported).
3. **Fallback (Verified Migration/Init)**: Unpack `conda-pack` once into `<mount>/envs/<env>` + `conda-unpack` + `.unpack_complete` marker on persistent storage and run directly without saving a custom image.
4. **Optional Reference (Untested by Us)**: Docker client `docker login/tag/push` to Harbor (`harbor.aicloud.szu.edu.cn:5000`); user-executed only if they prefer Docker — agents must not attempt to install Docker or modify Docker daemon permissions on shared servers.

Before job submission (and inside container startup), validate Python, CUDA, mount writability, short `/tmp` (`TMPDIR`), and offline flags via [check_environment.sh](./scripts/check_environment.sh).

### Stage 3 · Prepare Data & Zero-Tar Code Release
- Keep large/read-only datasets under `<mount>/datasets/...` and avoid modifying shared datasets mid-run.
- **Deploy job code using Zero-Tar immutable versioned directories** (`code-v1`, `code-v2`, ...), **not** by re-extracting a tarball over a non-empty working directory on container startup.
- Stage and hash the release locally:
  ```bash
  python3 <SKILL_DIR>/scripts/prepare_code_release.py \
    --src /path/to/project --out-dir ./staging --name code-v1
  ```
- Upload via clean-proxy `bita upload` (mandatory trailing `/` on `-o` pointing to the remote parent directory), then download `CODE-MANIFEST.sha256` back and `diff` to verify integrity. See [references/bita-cli.md](./references/bita-cli.md).

### Stage 4 · Export Web Template Once & Render Job YAML
- On first setup (or when switching GPU count / image / mount), ask the user to create one template job in Web UI (`智算任务 -> 分布式训练 -> 其他框架 -> 单副本 -> 导出/查看 YAML`). Notice `1×` vs `2× GPU` usually use different `resourcePoolConfId` values.
- Render submittable YAMLs from that exported template with [render_bitahub_yaml.py](./scripts/render_bitahub_yaml.py), enforcing single-line `/bin/sh`-safe startup commands (`bash /mnt/.../entry.sh ...`), `suspendDetectionDuration: 30`, and zero `source` / `\` / startup `.tar` commands:
  ```bash
  python3 <SKILL_DIR>/scripts/render_bitahub_yaml.py \
    --template web_export.yaml \
    --name my-exp-v1 \
    --command "bash /mnt/<project>/code-v1/run.sh --batch /mnt/<project>/runs/exp-v1" \
    --out job.yaml
  ```
- Detailed rules and multi-job scheduling case study: [references/yaml-and-gpu-jobs.md](./references/yaml-and-gpu-jobs.md).

### Stage 5 · Submit & Verify Real Training Startup (Post-Authorization)
- Submit with clean proxy env:
  ```bash
  env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
    bita apply -f job.yaml
  ```
- Remember `/usr/bin/bita` has **no** `status`, `logs`, or `stop` subcommands: monitor queue state and terminate abnormal/idle billing jobs in Web UI.
- **Health Gate**: Do not treat job acceptance as a healthy run. Inspect Web console logs or pulled batch logs for:
  1. Expected `nvidia-smi` GPU count/model and writable persistent output directory;
  2. At least one real training step / epoch / loss line and steady console heartbeat (`<= 60s`–`300s` recommended so 30-min hang detection never fires);
  3. Absence of `AF_UNIX path too long` (`TMPDIR` must live in short `/tmp`), `FileNotFoundError` from C extensions (`h5py`/`memmap`), or OOM. See [references/troubleshooting.md](./references/troubleshooting.md).

### Stage 6 · Retrieve & Verify Outputs
- Download completion logs and small/medium outputs using the exact Web-UI-generated `bita download` commands (`-o <remote_file>` or `-o <remote_dir/>` with stale local targets cleared beforehand).
- Inspect oversized model checkpoints separately before pulling bulk weights onto workstation disks.
- Verify downloaded files against a SHA-256 manifest using [verify_artifacts.py](./scripts/verify_artifacts.py).

## Reference Map

| Resource | When to Read |
| :--- | :--- |
| [references/quickstart.md](./references/quickstart.md) | Onboarding a new labmate from zero to first GPU job |
| [references/bita-cli.md](./references/bita-cli.md) | Installing `bita` without `sudo`, login/proxy errors, `upload`/`download`/`apply` exact flags |
| [references/environment.md](./references/environment.md) | Web dev-container save-as-image (primary), team reuse, `conda-pack`, optional Harbor Docker push |
| [references/yaml-and-gpu-jobs.md](./references/yaml-and-gpu-jobs.md) | Exporting Web YAMLs, `jobKind: training` schema, `/bin/sh` rules, 92-job scheduling notes |
| [references/troubleshooting.md](./references/troubleshooting.md) | Diagnosing `EOF`, `401/1027`, `AF_UNIX` deadlock, `h5py` path bugs, OOM, Zero-Tar collisions |
| [references/official-manual.md](./references/official-manual.md) | Curated navigation to untested BitaHub modules (multi-node, H100, Slurm, Dev SSH, Serving) |
