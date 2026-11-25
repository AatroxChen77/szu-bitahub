# Python 与容器环境准备（实测主线 vs 官方可选方案）

智算中心计算节点（`4090` / `H100`）**不支持连接外网**（官方 FAQ 三.1），训练任务启动后无法在线 `pip install` 或拉取模型。环境必须在提交正式训练任务前准备完备。

> **核心原则**：**完全不需要使用 Docker，也能制作高性能、可直接复用的自定义训练镜像。** 本团队在无 `docker.sock` 权限的实验室服务器上，全程使用「`conda-pack` + Web 开发环境 + 保存为镜像」跑通正式批量训练。

---

## 一、方案总览与定位表

| 方案 | 验证状态 | Skill 中的定位 |
| :--- | :--- | :--- |
| **网页开发环境 → 配置依赖 → 保存为镜像** | **已实际跑通**（零 Docker） | **主教程，优先推荐** |
| **复用团队已有镜像** | 官方支持，未单独验证完整复用流程（注：同团队成员可共享既有已保存镜像） | 快捷选项 |
| **`conda-pack` 直接放持久卷运行** | **已验证环境迁移与初始化** | 备用方案 |
| **Docker 客户端上传 Harbor**（及 `Dockerfile`/镜像 tar） | **官方支持，本项目未实测** | 可选参考（由用户自行决定并操作） |

---

## 二、主教程（首选，已实测）：网页开发环境 → 配置依赖 → 保存为镜像

适用所有无 Docker 权限或希望跳过 Dockerfile 编写的同学。**「开发环境」用来装配与验证环境，「保存为镜像」固化成果，后续所有正式 GPU 分布式训练任务直接选择该镜像实现秒级启动。**

### 1. 本地打包并上传 Conda 环境包
在本地 Linux 工作站或共享服务器用 `conda-pack` 将已验证的 Conda 环境打成 `.tar.gz`，再通过 `bita upload` 传到持久化文件存储：
```bash
# 本地打包（建议加上 --ignore-missing-files 忽略个别无害缺失符号）
conda-pack -n <your_env_name> --ignore-missing-files -o <your_env_name>.tar.gz

# 清代理后上传到你的团队持久化存储目录
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u all_proxy -u ALL_PROXY \
  bita upload -e <平台地址> -b home -c <存储桶clusterID> \
    -o <team>/<user>/<project_dir>/ \
    -l ./<your_env_name>.tar.gz
```
> 参考实测规模：原约 9.6 GiB 的 PyTorch/CUDA Conda 环境，压缩后约 `4.97 GB`。

### 2. 网页创建「开发环境」并挂载持久化存储
1. 登录平台控制台 → 左侧「**智算任务 → 开发环境 → 创建**」。
2. 选择所属**团队**，挂载你的**文件存储**目录（如挂载到 `/mnt/<project>`，这样刚才上传的 `<your_env_name>.tar.gz` 就在容器 `/mnt/<project>/` 下）。
3. 选择匹配驱动的基础镜像（例如含 CUDA 的平台镜像），选择一张 GPU（如 `1× RTX 4090`），设好运行时长（注意开发环境最长 24 小时且到期不保留容器本地变更），启动实例。

### 3. 在开发环境中配置并验证 Python / PyTorch / CUDA
通过页面「Web Terminal」（或已安装 JupyterLab / SSH 的连接方式）进入开发环境后：
1. **解包到容器本地目录**（例如 `/opt/<your_env_name>`）：
   ```bash
   mkdir -p /opt/<your_env_name>
   tar -xzf /mnt/<project>/<your_env_name>.tar.gz -C /opt/<your_env_name>
   /opt/<your_env_name>/bin/conda-unpack
   ```
   > [!IMPORTANT]
   > **为什么解到容器本地 `/opt/...` 而不是只留在 `/mnt/...`？**
   > 平台点击「存为镜像」保存的是**容器自身文件系统层**，不会把外部挂载的持久化文件存储（`/mnt/...`）打包进镜像。解包到 `/opt/<env>` 后再存为镜像，后续任何新任务启动时 `/opt/<env>/bin/python` 就已经内置在镜像里了。
2. **现场验证依赖与 GPU 链路**（务必在保存镜像前跑通）：
   ```bash
   nvidia-smi
   /opt/<your_env_name>/bin/python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.device_count())"
   ```
   如有临时补充测试或小调整，也在这一步完成并确认无报错。

### 4. 网页点击「存为镜像 / 保存为镜像」（官方 FAQ 一.6）
1. 回到控制台当前运行中的**开发环境详情页**，点击**「存为镜像」**（或保存为镜像按钮），填写镜像名称、标签与场景后提交。
2. 等待「**自定义镜像**」列表中显示新镜像保存成功并生成可用镜像记录。
3. **保存完成后，立即在控制台关闭/停止这个「开发环境」**，避免继续按小时计费。

> 实测案例参考：通过这套零 Docker 流程保存的自定义镜像（地址形如 `harbor.aicloud.szu.edu.cn:5000/<命名空间>/<环境名>:pytorch241-cu124-v1`，Python 固定位于 `/opt/<环境名>/bin/python`），已稳定支撑单机双卡 RTX 4090 的百余次连续训练任务。

### 5. 后续正式 GPU 任务直接复用该镜像
打开「智算任务 → 分布式训练 → 创建」，在「镜像选择 → 自定义镜像」中选中刚保存好的镜像（如再导出一次基准 YAML 即可获得对应的 `spec.imageId`），无需每次启动再去解包环境。

---

## 三、快捷选项：复用团队已有镜像

官方 FAQ 二.1 说明：自定义镜像、文件存储、数据集等均归属于所属团队，**同团队成员之间可共享自定义镜像，不支持跨团队共享**。
- 如果同门与前人处在同一个团队且依赖一致，可直接向同事确认已保存的共享镜像名称与容器内 Python 路径（例如 `/opt/<env>/bin/python`），在控制台「共享镜像 / 自定义镜像」选用，再导出一份 YAML 获取自己的 `imageId` 即可。

---

## 四、备用方案：`conda-pack` 直接放持久卷运行（已验证迁移与初始化）

如果暂不想新建自定义镜像，或者只是先在通用官方镜像上做快速验证，可以把 `conda-pack` 一次性解包并保留在持久化文件存储上跨任务复用：

```bash
ENV_DIR="${MOUNT_ROOT}/envs/my_env"
ENV_TAR="${MOUNT_ROOT}/my_env.tar.gz"
MARKER="${ENV_DIR}/.unpack_complete"

if [[ -x "/opt/my_env/bin/python" ]]; then
  PY="/opt/my_env/bin/python"
elif [[ -f "${MARKER}" && -x "${ENV_DIR}/bin/python" ]]; then
  PY="${ENV_DIR}/bin/python"
else
  mkdir -p "${ENV_DIR}"
  tar -xzf "${ENV_TAR}" -C "${ENV_DIR}"
  "${ENV_DIR}/bin/conda-unpack"
  date -u +"unpacked_utc=%Y-%m-%dT%H:%M:%SZ" > "${MARKER}"
  PY="${ENV_DIR}/bin/python"
fi

PY_PREFIX="$(cd "$(dirname "${PY}")/.." && pwd)"
export LD_LIBRARY_PATH="${PY_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
```
> 注意：**千万不要**在每次分布式训练启动时无条件重复往同一目录解压环境 tar 包；`MARKER` 文件用于确保只解压一次。

---

## 五、可选参考：使用 Docker 客户端上传自定义镜像（官方支持，本项目未实测）

> [!WARNING]
> **官方支持，但本项目尚未实测；具体操作由有 Docker 权限的用户在自己的 Docker 环境下自行完成。**
> 在共享实验室服务器上，Agent **不主动安装 Docker、不申请提权、也不修改系统 Docker 守护进程配置**。

官方「自定义镜像 → 客户端上传镜像」说明了标准 Harbor 推送流程（另见手册 `Dockerfile` / `镜像 tar 包` / `镜像迁移` 模块）：

1. **开通 Harbor 个人空间**：在控制台「自定义镜像」页面按指引开通个人镜像空间、记录个人命名空间并设定 Harbor 访问密码（登录时交互输入，**不要写入代码或 Skill**）。
2. **使用 Docker 客户端打标与推送（官方模板流程）**：
   ```bash
   # 1. 登录 Harbor（密码请在终端交互输入）
   docker login harbor.aicloud.szu.edu.cn:5000 -u <用户名>

   # 2. 为本地镜像添加 Harbor 标签
   docker tag <本地镜像:版本> \
     harbor.aicloud.szu.edu.cn:5000/<个人命名空间>/<镜像:版本>

   # 3. 推送到 Harbor
   docker push \
     harbor.aicloud.szu.edu.cn:5000/<个人命名空间>/<镜像:版本>
   ```
   官方仓库地址结构为：`harbor.aicloud.szu.edu.cn:5000/<用户命名空间>/<镜像名称:版本>`（用户名、命名空间与密码均从你自己的智算中心网页「自定义镜像」页面获取）。
3. **页面同步入库**：推送后回到控制台「自定义镜像 → 创建镜像 → 仓库地址 / 来源为个人空间镜像」填入上述地址同步入库，才能在训练任务列表中选择使用。
4. **平台其他官方构建方式说明（未实测）**：
   - 在线上传 `Dockerfile`（`<= 20 MB`，**官方手册明确注明不支持自动拉取境外镜像源**）；
   - 在可联网机器 `docker save` 导出 `.tar` 上传文件存储后再选择「镜像 tar 包」导入；
   - 无论哪种方式，若训练期需要编译 CUDA 算子，镜像须同时含 **CUDA Runtime 与 CUDA Toolkit**（官方 FAQ 一.10）。

---

## 六、无外网计算节点通用离线环境变量与自检

无论采用以上哪种方式，正式训练入口脚本建议统一注入：
```bash
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export PYTHONNOUSERSITE=1
export PYTHONUNBUFFERED=1
```
提交前或容器启动时使用通用检查脚本验证环境与挂载（`<SKILL_DIR>` 为本 Skill 所在目录）：
```bash
bash <SKILL_DIR>/scripts/check_environment.sh --mode local --python "$(which python3)"
```
