KaCRM 单容器发行版：Web、Hermes、PostgreSQL 15/pgvector、Redis 和 Celery 后台任务集成在一个容器内。首次启动自动初始化数据库并生成独立的管理员密码，用户登录后配置自己的模型 API key。

镜像支持 **linux/amd64**，不包含模型 API key、预配置账号或客户数据。数据持久化至 `/var/lib/kacrm`。本版同时修复手工跟进正文和进展字段被丢弃的问题。

安装：

```bash
docker pull ghcr.io/dfar2008/kacrm:v0.1.1
docker run -d --name kacrm --restart unless-stopped --stop-timeout 120 \
  -p 127.0.0.1:8765:8765 -v kacrm-data:/var/lib/kacrm \
  ghcr.io/dfar2008/kacrm:v0.1.1
docker exec kacrm python -m deploy.container.kacrm_all_in_one credentials
```

打开 `http://127.0.0.1:8765/app/`，详细说明见随附 `kacrm-container-guide.md`。也可下载 `kacrm-linux-amd64.tar.gz`，通过 `gunzip -c kacrm-linux-amd64.tar.gz | docker load` 离线导入。使用 `SHA256SUMS` 校验压缩包。

验证：首次初始化、管理员登录、客户创建与查询、手工跟进保存、重建容器数据保留；此前已在 Codex 浏览器复测创建、编辑和跟进流程。模型 API key 未提供，真实模型对话及飞书收发未测试。状态编辑后左侧客户列表标签需要刷新才同步，详情与保存值正常。

国内网络能否访问 GHCR 取决于网络环境，未作多运营商实测，不承诺国内直连稳定；国内镜像仓库尚未配置。本次发布已确认包为 Public，且已用无登录凭据的 Docker 配置完成拉取，核对镜像配置摘要与 CI 测试结果一致。

The single-container distribution includes PostgreSQL 15/pgvector, Redis, Web and background workers. No provider API keys or runtime data are embedded. Linux/amd64 only. Model calls and Feishu delivery need your own credentials and separate validation.
