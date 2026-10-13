# KaCRM 脚本

[English](README.en.md) · **简体中文**

当前部署和本地启动步骤见 `docs/deployment.md`，数据库初始化使用
`hermes.crm.seed.seed()`。脚本不会因为放在此目录中就自动执行。

| 脚本 | 用途 |
| --- | --- |
| `run_cloud_supervised.sh` | 常驻服务：管理 PgBouncer、Web、Celery worker/beat |
| `deploy-to-server.sh` | 部署、备份、迁移、seed、验证与回滚；默认 dry-run |
| `build-pyc-artifact.sh` | 构建 Python 3.11 字节码部署包，由部署脚本调用 |
| `check-hermes-patches-against.sh` | 在临时上游检出中检查补丁兼容性 |
| `regen-hermes-patches.sh` | 将当前 Hermes 改动重新生成补丁 |
| `verify-patch-content.sh` | 检查补丁中必需的技能与工具 |
| `upgrade-acceptance.sh` | Hermes 升级后的契约验收 |
| `sync-profile-skills.sh` | 部署时更新 profile 中已有的内置技能副本 |
| `push_skill_to_active_profiles.sh` | 按指定技能同步 profile；默认 dry-run，支持备份 |
| `sync-kacrm-guide.sh` | 将使用指南技能写入补丁并同步到 profile |
| `sync-onboarding-persona.py` | 同步 onboarding persona 与 profile 的 SOUL/系统提示词 |
| `dump-style-portrait.sh` | 导出 profile 的话术风格与方法论画像，便于比较 |
| `purge_polluted_memories.py` | 清理旧执行轨迹污染的记忆；默认 dry-run |
| `reconcile_memory_entities.py` | 合并旧 CRM 实体引用；默认 dry-run |
| `seed_crm_commerce.py` | 开发演示用产品、订单、报价与回款数据 |
| `seed_crm_interactions.py` | 开发演示用 CRM 互动数据 |
| `seed_dashboard_demo.py` | 开发数据库的统计面板演示数据 |
| `verify-sales-coach-rollout.sh` | 运行 Coach 卡片验证 SQL |
| `verify-coach-cards.sql` | 检查 Coach 卡片及方法论技能引用 |
| `measure-coach-feedback.sql` | 按时间窗口分析 Coach 卡片；反馈 JOIN 为人工填写的模板 |
| `eval/runner.py` | 使用用户提供的 JSONL 测试集执行模型评测 |
| `eval/judge.py` | 对评测轨迹评分；用法见 `eval/README.md` |

Python 脚本使用仓库虚拟环境，并设置 `PYTHONPATH="$PWD:$PWD/hermes"`。
维护与演示脚本需要明确指定目标数据库；演示数据脚本仅用于开发数据库。
补丁生成会操作 Hermes 子模块的暂存区；部署和同步的远端模式需配置自己的 SSH 参数。
