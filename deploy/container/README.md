# KaCRM 单容器镜像

一个容器运行 KaCRM Web、Hermes、Celery worker/beat、PostgreSQL 15（含 pgvector）和 Redis。数据库和 Redis 只监听容器内部的 `127.0.0.1`，对外只提供网页端口 8765。

首次启动自动创建数据库、执行迁移、初始化基础数据和管理员，并生成每套安装独立的密码与内部密钥。**镜像不包含模型 API key，也不包含任何已配置账号或客户数据。** 用户登录后在管理界面配置自己的模型 API key；飞书等渠道按需配置。

## 下载与安装

公开镜像：`ghcr.io/dfar2008/kacrm:v0.1.1`（linux/amd64），可匿名拉取：

```bash
docker pull ghcr.io/dfar2008/kacrm:v0.1.1
```

国内直连 GHCR 的可达性和速度取决于网络环境，未做国内多运营商实测；国内镜像仓库尚未配置。也可从 [v0.1.1 Release](https://github.com/dfar2008/kacrm/releases/tag/v0.1.1) 下载 `kacrm-linux-amd64.tar.gz` 和 `SHA256SUMS`，将包传到目标机器后离线导入：

```bash
# 同目录含 SHA256SUMS 与镜像包时，仅校验该镜像包：
sha256sum --ignore-missing -c SHA256SUMS
gunzip -c kacrm-linux-amd64.tar.gz | docker load
```

Linux/amd64 为已验证架构；ARM64 需要另外构建和验证。镜像不包括运行后的模型配置或客户数据。

## 启动：无需手工配置数据库和 Redis

```bash
docker run -d --name kacrm --restart unless-stopped --stop-timeout 120 \
  -p 127.0.0.1:8765:8765 \
  -v kacrm-data:/var/lib/kacrm \
  ghcr.io/dfar2008/kacrm:v0.1.1

# 首次初始化完成后，查看本机生成的管理员邮箱和初始密码：
docker exec kacrm python -m deploy.container.kacrm_all_in_one credentials
```

访问 `http://127.0.0.1:8765/app/`，用上述账号登录，再配置模型。默认管理员邮箱 `admin@kacrm.local`，密码为随机生成值。可在**首次安装**用 `-e KACRM_ADMIN_EMAIL=you@example.com` 和 `-e KACRM_ADMIN_PASSWORD=...` 指定账号；后续修改这些环境变量不会重置已有管理员。

默认仅允许本机访问；远程服务器请通过 SSH 转发或配置 HTTPS 反向代理。服务启动状态和日志：

```bash
docker inspect --format '{{.State.Health.Status}}' kacrm
docker logs --tail 100 kacrm
```

也可用单服务 Compose，效果与上面相同，不会创建独立的数据库或 Redis 容器：

```bash
docker compose -f deploy/container/kacrm-all-in-one-compose.yaml -p kacrm up -d
# 下载 Release 内单独的 Compose 文件时：
# docker compose -f kacrm-all-in-one-compose.yaml -p kacrm up -d
```

## 数据持久化、升级和恢复

`/var/lib/kacrm` 保存 PostgreSQL 数据、Redis AOF、用户配置、Hermes profiles 和 `kacrm-installation.json`。后者存储安装时生成的密码与内部密钥，权限为 0600；不写入普通启动日志。备份时必须一并保留，不能只复制数据库目录。数据卷可能包含用户后来配置的 API key，请作为私有数据保管。

停止容器后备份整个数据卷。升级时停止并删除旧容器，用新镜像挂载**同一个卷**重新启动；数据库迁移自动执行，已有用户不重复 seed，避免覆盖权限。不要用 `docker compose down -v` 或删除数据卷来升级。不要让两个容器同时使用同一个卷；启动器会加锁拒绝冲突。

PostgreSQL 主版本为 15；不能直接挂载其他主版本（例如 16）的数据库目录，需要先进行数据库迁移。使用宿主机目录代替命名卷时，必须让 UID/GID 10001 可写。

启动器等待 PostgreSQL、Redis、数据库初始化完成后再启动 Web 和后台任务。任一关键子进程退出时，容器停止并交由 Docker 重启策略恢复；正常停止会先关闭后台任务和 Web，再关闭 Redis 与 PostgreSQL。建议保留 120 秒停止宽限时间。

## 从源码构建

需要 Git（已初始化 Hermes 子模块）、Python 3.9+ 和 Docker Engine/BuildKit：

```bash
git submodule update --init --recursive
python3 scripts/build-kacrm-image.py --platform linux/amd64 --tag kacrm:all-in-one-preview
# 使用本地构建镜像运行 Compose：
KACRM_IMAGE=kacrm:all-in-one-preview docker compose -f deploy/container/kacrm-all-in-one-compose.yaml -p kacrm up -d
```

## 构建边界

请使用构建脚本：它收集允许的已跟踪应用源码及明确列出的容器文件，Hermes 从父仓库固定 gitlink 导出，构建时应用运行时补丁；不复制开发者的 dirty Hermes 工作树。`50-upstream-tests.patch` 仅涉及测试，不进入运行时；不修改原子模块。

`.dockerignore` 另行排除 `.env*`、Git 元数据、私钥、认证文件、数据库、缓存和日志；构建上下文不包含订阅号推广目录，没有 `COPY . .`。可单独检查干净上下文：

```bash
python3 scripts/build-kacrm-image.py --context-only /tmp/kacrm-clean-context
```

## 高级选项：只构建应用

保留 `application` 构建目标供已有数据库的部署使用，不作为上面默认安装方式：

```bash
python3 scripts/build-kacrm-image.py --target application --tag kacrm:application-preview
```

该目标仍使用 `kacrm_runtime`，角色为 `web`、`worker`、`beat`、`init`，需自行提供数据库、Redis、管理员和内部密钥。旧 `kacrm-compose.yaml` 仅供这种多服务高级部署，需私有 `.env`（字段见 `kacrm-runtime.env.example`）。它与单容器 Compose 二选一。

## 导出和验证边界

```bash
docker save ghcr.io/dfar2008/kacrm:v0.1.1 | gzip > kacrm-linux-amd64.tar.gz
```

导出镜像不包括运行容器或数据卷。健康检查、登录和 CRM 数据操作不能替代真实模型调用和飞书收发验收；这些需要用户配置自己的凭据后另外验证。当前验证结果随本地镜像产物保存。
