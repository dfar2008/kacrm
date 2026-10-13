# Offline Evaluation Substrate

Charter v2 §十 — 度量 skill / retrieval / prompt 改动是否让 agent 变好。

## 工作流总览

```
data/eval/crm-prompts.jsonl                     ← JSONL 测试集（需自行提供，本仓库未包含）
              │
              ▼
scripts/eval/runner.py  ──► data/eval/<run>/trajectories.jsonl
                                                   │
                                                   ▼
                            scripts/eval/judge.py  ──►  scores.jsonl
                                                        scores_summary.json
```

## 前置条件

**用 admin 用户跑**（2026-05-11 决策，理由见本文末"Tradeoffs"段）。runner 和 judge 都
用 admin 的 bearer token：

```bash
export HERMES_EVAL_TOKEN=<admin bearer token>
export HERMES_JUDGE_TOKEN=<admin bearer token>   # 同一个 token 即可，judge 也走 admin
```

如果想用外部 API 跑 judge（直连 OpenRouter / Anthropic 的 OpenAI 兼容端点），
`--judge-endpoint` 和 `--judge-token` 单独配。

## 跑一次完整 eval

### Step 1: 启动 hermes_cloud（必须开 eval mode）

```bash
HERMES_EVAL_MODE=1 \
HERMES_EVAL_LOG=/tmp/hermes-eval-intents.jsonl \
  uvicorn hermes_cloud.app.main:app --port 8765
```

**关键**：`HERMES_EVAL_MODE=1` 必须设在**服务端进程**，不是 runner 里。这个 env
flag 进入 hermes 子进程后才能让 Action Gateway 的 4 个 executor
（`crm_write` / `task_create` / `email_send` / `channel_reply`）走 dry-run 分支：
所有真实副作用被拦截，仅在 `HERMES_EVAL_LOG` 路径追加 `would_*` JSONL。

### Step 2: 跑 30 条 prompt

```bash
python scripts/eval/runner.py \
    --dataset data/eval/crm-prompts.jsonl \
    --run-name baseline-2026-05-11 \
    --endpoint http://127.0.0.1:8765/v1/chat/completions \
    --max-concurrency 4
```

完成后 `data/eval/baseline-2026-05-11/` 下出 `trajectories.jsonl` + `summary.json`。

### Step 3: judge 打分

```bash
python scripts/eval/judge.py \
    --run-dir data/eval/baseline-2026-05-11 \
    --judge-model haiku \
    --max-concurrency 4
```

完成后同目录下出 `scores.jsonl` + `scores_summary.json`。

### Step 4: 看结果

```bash
cat data/eval/baseline-2026-05-11/scores_summary.json
```

输出形态：

```json
{
  "total": 30,
  "scored": 28,
  "judge_failed": 2,
  "overall_avg": 0.78,
  "per_skill_avg": {
    "crm-intake": 0.85,
    "crm-email-drafting": 0.72,
    "crm-compliance-check": 0.91,
    "crm-coach": 0.74,
    "crm-research": 0.80,
    "crm-analyst": 0.66
  },
  "per_skill_count": {"crm-intake": 5, ...}
}
```

## 改 skill 后怎么回归

```bash
# 1. 改 hermes/skills/crm-coach/SKILL.md
# 2. 重启 hermes_cloud（仍开 HERMES_EVAL_MODE=1）
# 3. 跑新 run
python scripts/eval/runner.py \
    --dataset data/eval/crm-prompts.jsonl \
    --run-name post-coach-tweak-2026-05-12 \
    ...
python scripts/eval/judge.py \
    --run-dir data/eval/post-coach-tweak-2026-05-12 \
    ...

# 4. 对比两个 scores_summary.json 的 per_skill_avg.crm-coach 数字
#    delta > 0 说明改对了；delta < 0 说明改坏了，回滚或修
```

## 文件清单

```
data/eval/
  crm-prompts.jsonl            ← 用户提供的测试集（每行含 id / prompt / success_criteria）
  <run_name>/
    trajectories.jsonl         ← runner 输出（per prompt 一行）
    summary.json               ← runner 统计（total/ok/error 数）
    scores.jsonl               ← judge 输出（per prompt 一行，含 score / reasoning）
    scores_summary.json        ← judge 聚合（overall_avg + per_skill_avg）

scripts/eval/
  runner.py                    ← prompt → /v1/chat/completions → trajectory
  judge.py                     ← trajectory + success_criteria → score
  README.md                    ← 本文件

hermes/action_gateway/
  eval_mode.py                 ← dry_run_capture + is_eval_mode + is_eval_noop_token
  executors/crm_write.py       ← 头部加 eval_mode 短路
  executors/task_create.py     ← 同上
  executors/email_send.py      ← 同上
  channel_reply.py             ← reply_via_origin() 头部加 eval_mode 短路
```

## 什么不在范围内

- **memory.write / workflow_submit**：本期不 dry-run。memory 写到独立
  namespace `eval:<run>` 由 runner 控制（TODO，Phase B+1）；workflow_submit 没
  outbound 副作用，先不处理。
- **真 trust gate**：dry-run 在 executor 层短路，没走到 trust 审批；trust
  decision 路径需要单独评估（TODO，Phase C+1）。
- **CI 集成**：charter §十 Phase D，目前手动跑，CI hook 是下一步。

## Tradeoffs（用 admin 用户的代价，2026-05-11 决策）

我们用 admin 用户跑 eval 而不是建 eval-bot，简单优先。已知代价：

1. **eval 跑那 5-10 分钟系统对 admin 不可用**——`HERMES_EVAL_MODE=1` 是进程级
   env，不是 per-user。整个 hermes_cloud 进程内所有 4 个 executor 走 dry-run，
   admin 这段时间在 SPA / 飞书做的任何 CRM 写入、邮件、消息都被拦截不真执行。
   解法：eval 跑 5-10 分钟就完事，跑完 unset env 重启即可。

2. **admin 的 memory / sessions / agent_messages 会被 eval 污染**——dry-run 只
   拦 4 个 executor 的真实副作用。没拦的：`memory.write` 会真写进 admin
   namespace；`agent_messages` / `sessions` 会留下 30 条 eval 对话；interaction
   log 同上。看 admin 会话历史 / memory 全文搜索时 eval 数据会混进来。

3. **retrieval 对比测试有自染色风险**——未来做 "retrieval on vs off" 评估时，
   memory 里掺了上次 eval 自己写进去的条目，retrieval 命中的可能是"上次 eval
   的产物"而不是真实业务积累。慢性问题，跑 5-10 轮才感受得到。

如果后续这些代价开始干扰真实工作（污染明显 / retrieval 信号失真 / 想开 CI
自动跑），再建 eval-bot 替换 admin token。届时只需要换 HERMES_EVAL_TOKEN，
runner / judge / patch 35 都不用动。
