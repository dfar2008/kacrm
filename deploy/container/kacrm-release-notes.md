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

国内镜像：`ccr.ccs.tencentyun.com/kacrm/kacrm:v0.1.1`（腾讯云广州公开仓库）。将上面 pull/run 命令中的 GHCR 地址替换为该地址即可，无需登录。已完整匿名下载并校验清单、配置与全部 22 个镜像层摘要一致，通过首次安装、管理员登录、客户和跟进保存、重建容器数据保留测试。尚未做国内多运营商测速；个人版镜像仓库受免费配额限制，不提供 SLA。

The single-container distribution includes PostgreSQL 15/pgvector, Redis, Web and background workers. No provider API keys or runtime data are embedded. Linux/amd64 only. Model calls and Feishu delivery need your own credentials and separate validation.
