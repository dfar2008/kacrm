#!/usr/bin/env python3
"""Offline eval runner — Charter v2 §十 Phase A.

Fires every prompt in a JSONL dataset against the hermes_cloud
``/v1/chat/completions`` endpoint, captures the response, and writes
per-run output to ``data/eval/<run_name>/``.

The hermes_cloud server process MUST be started with
``HERMES_EVAL_MODE=1`` set so the Action Gateway executors (crm_write,
task_create, email_send, channel_reply) dry-run their side effects.
Setting the env var only here has no effect — it has to flow into the
in-process executors.

Usage:
    HERMES_EVAL_TOKEN=<bearer for eval user> \\
    python scripts/eval/runner.py \\
        --dataset data/eval/crm-prompts.jsonl \\
        --run-name baseline-2026-05-11 \\
        --endpoint http://127.0.0.1:8765/v1/chat/completions \\
        --max-concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx


def load_prompts(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


async def fire_one(
    client: httpx.AsyncClient,
    endpoint: str,
    token: str,
    model: str,
    prompt: Dict[str, Any],
    timeout: float = 180.0,
) -> Dict[str, Any]:
    base = {
        "id": prompt["id"],
        "prompt": prompt["prompt"],
        "skill_hint": prompt.get("skill_hint"),
        "success_criteria": prompt.get("success_criteria"),
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt["prompt"]}],
        "stream": False,
    }
    started_at = time.time()
    try:
        resp = await client.post(
            endpoint,
            json=body,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        elapsed = time.time() - started_at
        if resp.status_code >= 400:
            return {
                **base,
                "status": "http_error",
                "http_status": resp.status_code,
                "error_body": resp.text[:4000],
                "elapsed_s": elapsed,
            }
        return {
            **base,
            "status": "ok",
            "response": resp.json(),
            "elapsed_s": elapsed,
        }
    except Exception as exc:
        return {
            **base,
            "status": "exception",
            "error": repr(exc),
            "elapsed_s": time.time() - started_at,
        }


async def main_async(args: argparse.Namespace) -> None:
    prompts = load_prompts(args.dataset)
    print(f"Loaded {len(prompts)} prompts from {args.dataset}")

    out_dir = Path("data/eval") / args.run_name
    out_dir.mkdir(parents=True, exist_ok=True)
    traj_path = out_dir / "trajectories.jsonl"
    if traj_path.exists():
        if not args.append:
            traj_path.unlink()
        else:
            print(f"  (appending to existing {traj_path})")

    sem = asyncio.Semaphore(args.max_concurrency)
    results: List[Dict[str, Any]] = []

    async with httpx.AsyncClient() as client:
        async def run_one(prompt: Dict[str, Any]) -> None:
            async with sem:
                r = await fire_one(
                    client, args.endpoint, args.token, args.model, prompt,
                )
                with traj_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
                marker = {"ok": "✓", "http_error": "✗", "exception": "!"}.get(
                    r["status"], "?",
                )
                print(
                    f"  {marker} [{r['id']:<14}] {r['status']:<11} "
                    f"({r['elapsed_s']:.1f}s)"
                )
                results.append(r)

        await asyncio.gather(*(run_one(p) for p in prompts))

    summary = {
        "run_name": args.run_name,
        "dataset": str(args.dataset),
        "endpoint": args.endpoint,
        "model": args.model,
        "total": len(prompts),
        "ok": sum(1 for r in results if r["status"] == "ok"),
        "http_error": sum(1 for r in results if r["status"] == "http_error"),
        "exception": sum(1 for r in results if r["status"] == "exception"),
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\nWrote {traj_path}")
    print(f"Wrote {out_dir / 'summary.json'}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--run-name", required=True)
    p.add_argument(
        "--endpoint", default="http://127.0.0.1:8765/v1/chat/completions",
    )
    p.add_argument("--token", default=os.environ.get("HERMES_EVAL_TOKEN", ""))
    p.add_argument("--model", default="hermes")
    p.add_argument("--max-concurrency", type=int, default=4)
    p.add_argument(
        "--append", action="store_true",
        help="Append to existing trajectories.jsonl instead of overwriting",
    )
    args = p.parse_args()
    if not args.token:
        raise SystemExit("--token or HERMES_EVAL_TOKEN required")
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
