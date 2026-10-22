# KaCRM 部署文档

[English](deployment.en.md) · **简体中文**

更新：2026-10-15。由 hermes-cloud 的部署说明整理而来，仅描述当前 KaCRM 代码的部署路径。

当前 Hermes Agent 为 **v0.21.6**，固定提交 `818c13be1dc4fd28987e1e881a9408224afd4535`；数据库迁移 head 为 **0118**。部署使用仓库固定版本与配套 7 份补丁，不自动追踪上游 main。变更与历史验收见 [v0.21.6 升级记录](2026-10-12-hermes-v0.21.6-upgrade.md)。该记录中的通过数属于来源项目当时的验证，不代表每次新部署自动通过。

本文介绍源码部署。另提供可本地构建的 **Preview 单容器镜像**（内置 PostgreSQL/Redis）与单服务 Compose 示例，参见[容器部署说明](../deploy/container/README.md)。镜像不包含模型 API key，需在运行后自行配置。该镜像的健康检查与 CRM 验证不等同于真实模型和飞书收发验收。

## 1. 架构和前置条件

- React SPA：构建至 `frontend/dist/`，FastAPI 提供 `/app/`。
- FastAPI：CRM API、多租户 profile 管理及认证。
- Hermes 子模块加本地补丁：默认由单个 multiplex 网关承载隔离的 profiles、消息渠道与 Agent cron。
- PostgreSQL + pgvector：身份、CRM、会话和消息共用业务数据库。
- PgBouncer：事务连接池；Alembic 直连 PostgreSQL。
- Redis、Celery worker 与 beat：异步任务、工作流调度和保留策略。

需要 Git、uv、Python 3.11、Node.js 20.19+ 或 22.12+、npm、PostgreSQL、pgvector、Redis。完整 supervisor 还需要 PgBouncer 和 Bash。生产示例采用 Linux/systemd，外部访问由 HTTPS 反向代理提供。模型和渠道凭据通过管理员界面配置。

## 2. 获取源码、安装依赖

```bash
git clone --recurse-submodules https://github.com/dfar2008/kacrm.git
cd kacrm
git submodule update --init --recursive
git -C hermes describe --tags --always
git -C hermes rev-parse HEAD

uv venv .venv --python 3.11
uv pip install --python .venv/bin/python -e '.[dev]'
uv pip install --python .venv/bin/python --no-deps -e ./hermes
```

版本检查应输出 `v0.21.6` 与上述 SHA。父仓库和 Hermes 共用外部虚拟环境；不要在此环境运行会清除额外安装依赖的 `uv sync`。

上游多数依赖声明受 Python 3.14 条件限制，Python 3.11 的 editable install 不会自动补齐。显式安装基础 Agent 运行依赖：

```bash
uv pip install --python .venv/bin/python \
  openai==2.24.0 fire==0.7.1 rich==14.3.3 tenacity==9.1.4 \
  'httpx[socks]==0.28.1' ruamel.yaml==0.18.16 jinja2==3.1.6 \
  prompt_toolkit==3.0.52 psutil==7.2.2 Markdown==3.10.2 \
  'PyJWT[crypto]==2.13.0' 'aiohttp>=3.13.3,<4' \
  truststore==0.10.4 snowballstemmer==3.1.1 tomli-w==1.2.0 nemo-relay==0.9.3

cd frontend
npm ci
npm run build
cd ..
```

这是基础环境；启用的模型、渠道、浏览器和文档工具可能需要相应可选依赖，对照 `hermes/pyproject.toml` 的 extras 安装并验证实际功能。

## 3. 数据库和认证配置

复制 `.env.example`，替换示例值。模板中的 `postgresql+psycopg2` 改为 `postgresql+psycopg`。以下假定已创建数据库 `kacrm` 与具有迁移权限的数据库用户 `kacrm`。

```dotenv
# 同一数据库；迁移绕过 PgBouncer。
HERMES_CLOUD_DB_URL=postgresql+psycopg://kacrm:REPLACE_DB_PASSWORD@127.0.0.1:6432/kacrm
HERMES_CRM_DATABASE_URL=postgresql+psycopg://kacrm:REPLACE_DB_PASSWORD@127.0.0.1:6432/kacrm
ALEMBIC_DATABASE_URL=postgresql+psycopg://kacrm:REPLACE_DB_PASSWORD@127.0.0.1:5432/kacrm
HERMES_CLOUD_REQUIRE_DB=1
CELERY_BROKER_URL=redis://127.0.0.1:6379/3
CELERY_RESULT_BACKEND=redis://127.0.0.1:6379/3

# seed 与 dev_server 使用相同管理员邮箱和密码。
HERMES_CRM_ADMIN_USERNAME=admin
HERMES_CRM_ADMIN_EMAIL=admin@example.com
HERMES_CRM_ADMIN_PASSWORD=REPLACE_ADMIN_PASSWORD
HERMES_CLOUD_ADMIN_EMAIL=admin@example.com
HERMES_CLOUD_ADMIN_PASSWORD=REPLACE_ADMIN_PASSWORD
HERMES_CLOUD_INTERNAL_KEY=REPLACE_INTERNAL_KEY
HERMES_SESSION_SECRET=REPLACE_SESSION_SECRET

# 替换为实际安装位置和独立可写目录。
HERMES_BIN=/opt/kacrm/.venv/bin/hermes
HERMES_PYTHON=/opt/kacrm/.venv/bin/python
HERMES_HOME_ROOT=/var/lib/kacrm
CLOUD_INTERNAL_URL=http://127.0.0.1:8765
PORT=8765
HERMES_CLOUD_PORT_RANGE_START=9310
HERMES_CLOUD_PORT_RANGE_END=9399
HERMES_CLOUD_SCALE_TO_ZERO=1
HERMES_CLOUD_IDLE_TIMEOUT_MINUTES=5
```

内部密钥和会话密钥分别生成，例如 `.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'`。实际会话签名变量是 `HERMES_SESSION_SECRET`。限制 `.env` 的读取权限且不入库。Redis 使用独立 DB/实例，避免其他项目的 Celery 消费本项目任务。

网关默认端口范围从 `9100` 开始；multiplex 使用所配范围的首个端口。同机运行多个实例时，为每个实例设置互不重叠的网关端口范围和独立 `HERMES_HOME_ROOT`。上例 `9310–9399` 也需先确认空闲，否则 Hermes 会因端口占用退出，网页状态显示红色。

配置 `pgbouncer/pgbouncer.ini` 的 `[databases]` 别名 `kacrm`，后端指向 PostgreSQL 的 `kacrm`。仓库中的 `auth_type=trust`、`openclaw` 用户是本机示例，生产需替换为自己的认证配置，建议 SCRAM。确保服务用户可访问认证文件、日志、PID 和 profile 目录。

本机仅验证 Web/API 时，两条应用 URL 可以直连 `5432`。完整 supervisor 固定启动 PgBouncer 并检查 `6432`，使用它需完成上述配置。

## 4. 应用补丁、迁移和 seed

在新获取的干净子模块上先应用补丁，使 `hermes.crm` 可导入：

```bash
export PYTHONPATH="$PWD:$PWD/hermes"
.venv/bin/python - <<'PY'
from pathlib import Path
from hermes_cloud.app.hermes_patcher import ensure_hermes_patches_applied
root = Path.cwd()
ensure_hermes_patches_applied(root / 'hermes', root / 'hermes-patches')
PY

set -a
. ./.env
set +a
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic current
.venv/bin/python -m alembic heads
.venv/bin/python -c 'from hermes.crm.seed import seed; seed()'
```

`current` 与 `heads` 应一致，当前仓库 head 为 `0118`。seed 必须在迁移之后执行，创建管理员、CRM 模块和元数据；再次 seed 不应作为已有管理员密码的重置方式。seed 可能输出新管理员 API key，日志按凭据管理。

补丁器对 dirty 或不完整的子模块会执行 reset/clean，运行前保存个人修改。应用后 dirty 状态属于 overlay。开发默认允许启动时重放；制作已打补丁的发布目录后设置 `HERMES_CLOUD_SKIP_PATCHES=1`，运行端使用已验证的制品，不再重置源码。

### 产品小数价格迁移（0116）

`0116` 将 `products.price` 从 BIGINT 改为 NUMERIC，并把价格字段元数据改为 currency。已有整数价格完整保留；支持保存 12.50 等小数。应用前按部署流程备份数据库，确认目标库当前版本为 0115，再运行 `alembic upgrade 0116`。迁移有 5 秒锁等待限制，超时应排查长事务后重试。降级遇到小数或超出 BIGINT 范围的价格会拒绝执行，避免数据损失。

本地 5173 是前端预览地址，其代理可连接远端 API；本地代码更新不会自动迁移远端数据库。必须使用真实服务的 SSH 账号、部署密钥和数据库环境。文档中的示例地址不能作为实际部署配置。

`0117` 继续将商机金额和产品参考价从 BIGINT 改为 NUMERIC，并保留商机统计视图及其权限。从 0116 升级前先备份数据库；遇到不能精确表示为 BIGINT 的金额时，降级会拒绝执行。

### 应收与回款联动迁移（0118）

`0118` 增加事务内 PostgreSQL 触发器，维护应收已收额、余额、回款状态及订单回款汇总。新增回款、修改金额或日期、更换应收关联、软删除和恢复时按回款净变动重算，保留已有手工录入的期初已收金额。新建应收自动初始化已收额、余额和状态；修改订单金额或恢复已删除订单也会重算汇总。迁移不批量回填历史业务记录。从 0117 升级前先备份数据库；降级移除自动联动，保留已记录金额。现有只读订单回款状态增加明确的中英文选项标签。

## 5. 启动和验收

只验证 Web/API：

```bash
set -a
. ./.env
set +a
export PYTHONPATH="$PWD:$PWD/hermes"
.venv/bin/python -m uvicorn hermes_cloud.app.dev_server:app \
  --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765/app/`，使用配置的管理员账号登录。此命令未启动 Celery，不能据此判断异步任务和定时工作流正常。

完整环境：

```bash
bash scripts/run_cloud_supervised.sh
```

supervisor 读取根目录 `.env`，管理 PgBouncer、Uvicorn、Celery worker 和 beat，日志在 `logs/`。它会清理同类旧进程，同机其他实例需要独立部署方式。该脚本对外监听 `0.0.0.0`，通过防火墙和 HTTPS 反向代理控制访问。

Linux 常驻服务以 `deploy/kacrm-cloud.service` 为模板，调整服务用户、目录和 ExecStart 后安装，执行 `systemctl daemon-reload`、`systemctl enable --now kacrm-cloud`。unit 停止超时为 60 秒；supervisor 自身目前等待 15 秒后强制结束子进程，应同时检查这两个预算，不能据此宣称完整 drain 或零停机。

已有部署改名时，先停止并禁用旧 service，再安装 `kacrm-cloud.service`，避免两套 supervisor 同时启动。将旧 profile 数据目录迁移到 `~/.kacrm/profiles`，或通过 `HERMES_HOME_ROOT`、部署脚本的 `PROFILES_ROOT` 显式保留原路径；核对配置、凭据与文件权限后再启动新服务。本仓库的改名不会自动迁移服务器上的已有数据。

验收至少包括：

1. `curl --fail http://127.0.0.1:8765/health` 返回 200。
2. 登录后 `/crm/modules`、`/crm/records/leads?limit=1` 返回 200，页面加载正常。
3. 配置自己的模型，实际对话并调用工具；刷新后历史仍存在。
4. 验证已启用的真实消息渠道、审批回调和跨租户隔离。
5. 验证 Celery worker/beat、工作流调度与 profile cron，检查重启恢复。

## 6. 更新现有服务器

先在测试环境完成升级验收。`scripts/deploy-to-server.sh` 面向已经配置的服务器；默认只生成计划并构建本地产物，远端写入需要 `--apply`。

```bash
export SSH_HOST=ubuntu@YOUR_SERVER
export SSH_KEY="$HOME/.ssh/YOUR_DEPLOY_KEY"
export REMOTE=/opt/kacrm
export MODE=source
bash scripts/deploy-to-server.sh
# 确认计划、备份和测试结果后：
bash scripts/deploy-to-server.sh --apply
```

脚本对 v0.21.6 SHA 硬校验，发送预先打补丁的 Hermes 和安装标记、重建前端，再备份、停止服务、同步、补装依赖、迁移、seed、重启，验证健康与认证 CRM 接口。

`203.0.113.10` 是占位地址。服务名 `kacrm-cloud`、数据库 `hermes_crm`、远端用户 `ubuntu` 和 profile 路径仍有固定约定；采用自己的数据库/账号时先修改脚本，不能只改 SSH_HOST。脚本仅补装四个升级依赖，不能当作新机器完整安装器。`SKIP_MIGRATE=1` 只适用于确认没有新迁移的代码更新。

## 7. 备份、回滚和升级门禁

迁移前保存直连 PostgreSQL 的完整 dump、已部署代码/补丁及 SHA、前端构建、安装标记、`.env` 和 profile 目录。备份含账号、模型和渠道凭据，应存放于受控位置。

代码回滚与数据库恢复分开处理。脚本自动回滚不等于恢复数据库；新 schema 与旧代码不兼容时，停服务，在独立库验证备份恢复后切换。部分迁移删表或修改数据，不把 `alembic downgrade` 作为通用恢复方式。恢复后重新验收实际业务。

Hermes 升级先在独立检出验证候选正式 tag 和 7 份补丁顺序重放，再运行 `scripts/upgrade-acceptance.sh`、云端回归、独立真实 PG 回归和受影响的上游测试；上游测试按 `hermes/tests/AGENTS.md` 使用 `scripts/run_tests.sh` 的逐文件隔离 runner。更换 pin 时同步补丁、迁移、部署脚本的版本校验和安装标记，不直接运行 `git submodule update --remote`。

升级记录明确：当时真实飞书审批点击和跨端同步尚未实测，应纳入上线验收。本文移除了旧版 SQLite/独立 worker 集群、微信支付、RL 和 auto_drive 部署步骤。
