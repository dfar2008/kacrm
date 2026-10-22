KaCRM 单容器发行版：Web、Hermes、PostgreSQL 15/pgvector、Redis 和 Celery 后台任务集成在一个容器内。首次启动自动初始化数据库并生成独立的管理员密码，用户登录后配置自己的模型 API key。

镜像支持 **linux/amd64**，不包含模型 API key、预配置账号或客户数据。数据持久化至 `/var/lib/kacrm`。本版同时修复手工跟进正文和进展字段被丢弃的问题。

安装：使用本版本的 `ghcr.io/dfar2008/kacrm` 镜像标签；详细命令见随附 `kacrm-container-guide.md`。也可下载 `kacrm-linux-amd64.tar.gz`，通过 `gunzip -c kacrm-linux-amd64.tar.gz | docker load` 离线导入。使用 `SHA256SUMS` 校验压缩包。

验证：首次初始化、管理员登录、客户创建与查询、手工跟进保存、重建容器数据保留；此前已在 Codex 浏览器复测创建、编辑和跟进流程。模型 API key 未提供，真实模型对话及飞书收发未测试。状态编辑后左侧客户列表标签需要刷新才同步，详情与保存值正常。

国内网络能否访问 GHCR 取决于网络环境，未作多运营商实测，不承诺国内直连稳定；国内镜像仓库尚未配置。首次新建 GHCR 包需将包可见性设为 Public 后，外部用户才能匿名拉取。

The single-container distribution includes PostgreSQL 15/pgvector, Redis, Web and background workers. No provider API keys or runtime data are embedded. Linux/amd64 only. Model calls and Feishu delivery need your own credentials and separate validation.
