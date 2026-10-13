# KaCRM

KaCRM 是基于 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的对话式 AI CRM，提供客户与商机管理、AI Agent、多租户配置、渠道接入、审批和定时任务。

## 源码结构

- `frontend/`：React + TypeScript 界面。
- `hermes_cloud/`：FastAPI 云端与 CRM 服务。
- `alembic/`：PostgreSQL 数据库迁移。
- `hermes/`：官方 Hermes Agent 子模块，固定到 v0.21.6。
- `hermes-patches/`：云端功能、自有 CRM 技能和兼容性补丁。启动时应用；不要直接更新子模块到最新版本。
- `tests/`：云端与升级回归测试。
- `scripts/`、`deploy/`：开发、运维和部署工具。

## 获取与运行

完整环境准备、部署和回滚步骤见 [部署文档](docs/deployment.md)。当前 Hermes Agent 固定为 **v0.21.6**，变更与验收边界见 [v0.21.6 升级记录](docs/2026-10-12-hermes-v0.21.6-upgrade.md)。

```bash
git clone --recurse-submodules https://github.com/dfar2008/kacrm.git
cd kacrm
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e ".[dev]"
cp .env.example .env
```

按部署文档第 2–4 节安装 Hermes editable 包和 Python 3.11 运行依赖、应用补丁并执行 seed。准备 PostgreSQL、pgvector 和 Redis，替换 `.env.example` 中的账号与默认值，将数据库驱动改为 `psycopg`。数据库迁移使用直连 PostgreSQL 的 `ALEMBIC_DATABASE_URL`，启动前加载自己的 `.env`。

```bash
PYTHONPATH="$PWD:$PWD/hermes" .venv/bin/python -m alembic upgrade head
cd frontend
npm ci
npm run build
cd ..
.venv/bin/python -m uvicorn hermes_cloud.app.dev_server:app --host 127.0.0.1 --port 8765
```

打开 http://127.0.0.1:8765/app/ 。开发服务器含开发用默认配置，生产部署前应设置独立凭据、数据库权限和认证配置。部署脚本中的服务器地址为示例，必须替换或通过环境变量配置。

## 测试

```bash
PYTHONPATH="$PWD/hermes:$PWD" .venv/bin/python -m pytest tests/hermes_cloud/ \
  --import-mode=importlib --ignore=tests/hermes_cloud/test_dev_server.py \
  -m 'not llm_eval' -q
cd frontend
bun test
npm run build
```

真实数据库测试须使用独立测试库和 `HERMES_TESTS_REAL_PG=1`；上述命令不代表现有测试已全部通过。`test_dev_server.py` 按 Makefile 的现有约定排除。

## 发布范围

此仓库从当前代码快照重新初始化，只包含上述两份公开部署与升级文档，不包含内部 Git 历史、其他内部文档、`ppt-build/`、内部备份、评测对话、邮件草稿、运行数据库或本地密钥。现有 CRM 导览录屏与封面未包含，因此导览视频需要自行录制后配置；录制脚本保留。

Hermes Agent 的许可证见 `hermes/LICENSE`。该上游许可证不自动授权 KaCRM 自有代码；当前公开副本尚未为自有代码指定开源许可证。
