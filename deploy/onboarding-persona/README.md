# 新用户模板人格 — 仓库内 source of truth

`onboarding_template.json`（`<hermes_home>/`，dev=`~/.hermes`、prod=`~/.kacrm`）是纯磁盘数据，
仓库里原本没有副本——工具面演进时人格文案跟不上、会静默漂移
（2026-07-05 实例：【会进化】段只提"加字段/选项"，且引用的
`crm_propose_new_field` 独立工具名早已合并进 `crm_schema`）。

本目录固化模板的两个**人格字段**（其余字段含 API key 等密钥，绝不进仓库）：

| 文件 | 对应模板键 | 作用 |
|---|---|---|
| `SOUL.md` | `soul_md` | 身份 overlay，网关启动时缓存 |
| `system_prompt.md` | `system_prompt` | Personality（config.yaml `agent.system_prompt`） |

## 改人格的流程

1. 改本目录的 md 文件，提交；
2. 跑 `scripts/sync-onboarding-persona.py` 同步到目标机器的模板（可选连带已有 profile）；
3. 重启网关（dev：SIGTERM 各 profile 网关；prod：`sudo systemctl restart kacrm-cloud.service`，
   单杀 admin 网关会触发飞书离线死锁——不要）。

```
# 只更新模板（影响之后新建的用户）
python3 scripts/sync-onboarding-persona.py --hermes-home ~/.hermes

# 连带回填指定已有 profile（只覆盖 SOUL/system_prompt，不碰 model/skills/cron）
python3 scripts/sync-onboarding-persona.py --hermes-home ~/.hermes --profiles sales wangz-914b
```

注意：
- **别用** SPA 的模板 apply 端点整体回填——它同时回填 model 配置，会冲掉
  `supports_vision` 这类 per-profile 修正；本脚本只动两个人格字段。
- dev 的 `admin` profile 有一份手工调优的 SOUL 变体（更丰富的自我介绍场景），
  不要用模板 SOUL 覆盖它。
- 审批链配置（`crm_set_approval_chain`）是 Charter v2 特意不暴露给 agent 的
  （SPA 管理面）；人格里不得声称 agent 能"加审批流"。
