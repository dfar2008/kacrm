#!/usr/bin/env python3
"""LLM-as-judge for offline eval trajectories — Charter v2 §十.

Reads ``trajectories.jsonl`` from a run, scores each against its
``success_criteria`` using a cheap LLM, writes ``scores.jsonl`` and
``scores_summary.json``.

Usage:
    HERMES_JUDGE_TOKEN=<bearer for judge> \\
    python scripts/eval/judge.py \\
        --run-dir data/eval/baseline-2026-05-11 \\
        --judge-endpoint http://127.0.0.1:8765/v1/chat/completions \\
        --judge-model haiku \\
        --max-concurrency 4

Judge endpoint can be the same hermes_cloud (different user/token) or
any OpenAI-compatible endpoint. The judge prompt asks for strict JSON
output; non-JSON responses get ``score=null`` and surface in
``judge_failed`` count.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import httpx


JUDGE_PROMPT_TEMPLATE = """\
你是 CRM eval 评分官。对一条 prompt 的 agent 应答进行 0-1 评分。

# Prompt（用户原话）
{prompt}

# 期望命中 skill
{skill_hint}

# Success criteria（自然语言，agent 应满足的点）
{success_criteria}

# Agent 应答（OpenAI chat.completion 响应 JSON，含 final message + 可能的 tool_calls）
{response}

请按以下 JSON 格式输出（**严格 JSON，无多余文字、无 markdown 代码块**）：
{{
  "score": <0.0~1.0 浮点>,
  "reasoning": "<一两句话评分理由>",
  "criteria_met": ["<命中的具体点>", ...],
  "criteria_missed": ["<未命中的具体点>", ...]
}}

评分标尺：
- 1.0 = 完全满足 success criteria 所有要点
- 0.7-0.9 = 主要要点满足，细节有偏差
- 0.4-0.6 = 部分满足或方向对但执行差
- 0.0-0.3 = 完全没满足或方向错
"""


def load_trajectories(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _strip_json_fence(text: str) -> str:
    """Some judges wrap JSON in ```json ... ``` despite instructions."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
        # also handle 'json' label
        if t.startswith("json\n"):
            t = t[5:]
    return t.strip()


async def judge_one(
    client: httpx.AsyncClient,
    endpoint: str,
    token: str,
    model: str,
    traj: Dict[str, Any],
) -> Dict[str, Any]:
    base = {
        "id": traj["id"],
        "skill_hint": traj.get("skill_hint"),
    }
    if traj.get("status") != "ok":
        return {
            **base,
            "score": 0.0,
            "reasoning": f"trajectory status={traj.get('status')}, not evaluated",
            "criteria_met": [],
            "criteria_missed": ["trajectory failed to capture"],
        }

    response_blob = json.dumps(
        traj.get("response") or {}, ensure_ascii=False, default=str,
    )
    user_prompt = JUDGE_PROMPT_TEMPLATE.format(
        prompt=traj["prompt"],
        skill_hint=traj.get("skill_hint") or "(unspecified)",
        success_criteria=traj.get("success_criteria") or "",
        response=response_blob[:8000],
    )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": user_prompt}],
        "stream": False,
    }
    try:
        resp = await client.post(
            endpoint,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=90.0,
        )
        if resp.status_code >= 400:
            return {
                **base,
                "score": None,
                "reasoning": f"judge http {resp.status_code}: {resp.text[:400]}",
            }
        data = resp.json()
        text = (
            data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
        )
        try:
            parsed = json.loads(_strip_json_fence(text))
        except json.JSONDecodeError:
            return {
                **base,
                "score": None,
                "reasoning": f"judge returned non-JSON: {text[:400]}",
            }
        return {
            **base,
            "score": float(parsed.get("score", 0)),
            "reasoning": str(parsed.get("reasoning", "")),
            "criteria_met": parsed.get("criteria_met", []),
            "criteria_missed": parsed.get("criteria_missed", []),
        }
    except Exception as exc:
        return {
            **base,
            "score": None,
            "reasoning": f"judge exception: {exc!r}",
        }


async def main_async(args: argparse.Namespace) -> None:
    run_dir: Path = args.run_dir
    traj_path = run_dir / "trajectories.jsonl"
    if not traj_path.exists():
        raise SystemExit(f"missing {traj_path} — run runner.py first")
    trajs = load_trajectories(traj_path)
    print(f"Loaded {len(trajs)} trajectories from {traj_path}")

    scores_path = run_dir / "scores.jsonl"
    if scores_path.exists():
        scores_path.unlink()

    sem = asyncio.Semaphore(args.max_concurrency)
    scores: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        async def run_one(traj: Dict[str, Any]) -> None:
            async with sem:
                s = await judge_one(
                    client, args.judge_endpoint, args.judge_token,
                    args.judge_model, traj,
                )
                with scores_path.open("a", encoding="utf-8") as fh:
                    fh.write(
                        json.dumps(s, ensure_ascii=False, default=str) + "\n",
                    )
                score_str = (
                    f"{s['score']:.2f}" if s.get("score") is not None else "null"
                )
                print(
                    f"  [{s['id']:<14}] score={score_str:<5} "
                    f"{(s.get('reasoning') or '')[:90]}"
                )
                scores.append(s)

        await asyncio.gather(*(run_one(t) for t in trajs))

    per_skill: Dict[str, List[float]] = defaultdict(list)
    for s in scores:
        if s.get("score") is None:
            continue
        per_skill[s.get("skill_hint") or "unknown"].append(s["score"])

    valid_scores = [s["score"] for s in scores if s.get("score") is not None]
    summary = {
        "run_dir": str(run_dir),
        "judge_model": args.judge_model,
        "total": len(scores),
        "scored": len(valid_scores),
        "judge_failed": sum(1 for s in scores if s.get("score") is None),
        "overall_avg": (
            sum(valid_scores) / len(valid_scores) if valid_scores else 0.0
        ),
        "per_skill_avg": {
            skill: sum(vs) / len(vs) for skill, vs in per_skill.items()
        },
        "per_skill_count": {skill: len(vs) for skill, vs in per_skill.items()},
    }
    (run_dir / "scores_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nWrote {scores_path}")
    print(f"Wrote {run_dir / 'scores_summary.json'}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument(
        "--judge-endpoint",
        default="http://127.0.0.1:8765/v1/chat/completions",
    )
    p.add_argument(
        "--judge-token", default=os.environ.get("HERMES_JUDGE_TOKEN", ""),
    )
    p.add_argument("--judge-model", default="haiku")
    p.add_argument("--max-concurrency", type=int, default=4)
    args = p.parse_args()
    if not args.judge_token:
        raise SystemExit("--judge-token or HERMES_JUDGE_TOKEN required")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
