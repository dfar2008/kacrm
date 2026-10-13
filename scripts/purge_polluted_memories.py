#!/usr/bin/env python3
"""Purge agent-execution-trace rows from the ``memories`` table.

The memory ingest pipeline historically swept *every* ``interactions``
kind — including agent execution traces (``agent.message``, ``action``,
``copilot_*``, ``auto_drive.*``, …) — into ``memories`` as ``kind='fact'``
rows whose ``content`` is raw JSON. Those rows surface in the CRM customer
detail page ("系统记得的事") as scary unreadable blobs.

A second source: the ``memory.write`` gateway executor stored whatever
``content`` a specialist handed it, so a research agent occasionally
persisted its whole agent-run object (``{'final_response': ...,
'messages': [...]}``) as a fact.

This script removes both. A ``memories`` row is treated as polluted when:

  (a) it links (via ``source_refs[].interaction_id``) to an
      ``interactions`` row whose ``kind`` is NOT in the ingest allowlist
      (:func:`hermes.memory.ingest._ingestible_kinds`); or
  (b) it has no resolvable source interaction and its ``content``
      deserializes to a dict/list (a serialized object, not a prose fact).

Deleting a ``memories`` row cascades to ``memory_entity_sources``
(FK ``ON DELETE CASCADE``), so the customer "facts" panel is cleaned in
one step.

Default mode is dry-run. Pass ``--apply`` to execute the deletes. The
script is idempotent — re-running after an apply finds nothing.

Usage:
    .venv/bin/python scripts/purge_polluted_memories.py            # dry-run
    .venv/bin/python scripts/purge_polluted_memories.py --apply    # delete
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Optional

# ``hermes`` is a namespace package resolved via the repo root — make the
# script runnable from any working directory.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hermes.crm.db import db_session
from hermes.memory.ingest import _ingestible_kinds


def _is_serialized_payload(content: Any) -> bool:
    """True if ``content`` is a serialized object rather than a prose fact.

    A distilled memory fact is always prose. Content that opens with a
    dict brace ``{`` is an agent-run object dump — flagged unconditionally
    because oversized reprs arrive truncated mid-literal (the content
    column width clips them) and so cannot be round-tripped through a
    parser. A ``[``-opener is only flagged when it actually deserializes
    to a list/dict, since prose can legitimately start with a bracket.
    """
    s = (content or "")
    if not isinstance(s, str):
        return True  # non-text content is never a clean fact
    s = s.strip()
    if not s:
        return False
    if s[0] == "{":
        return True
    if s[0] != "[":
        return False
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(s)
        except (ValueError, SyntaxError):
            continue
        if isinstance(parsed, (dict, list)):
            return True
    return False


_RENDER_TAG_RE = re.compile(r"^\[([a-z][a-z0-9_.]*)\]")


def _bracket_tag_kind(content: Any) -> Optional[str]:
    """Extract the ``[kind]`` prefix ``_render_content`` stamps on every
    ingested memory. Lets us recover the source kind even when the source
    interaction row has since been deleted (dangling ``source_refs``)."""
    if not isinstance(content, str):
        return None
    m = _RENDER_TAG_RE.match(content.strip())
    return m.group(1) if m else None


def _interaction_id(source_refs: Any) -> Optional[str]:
    """Pull the first ``interaction_id`` out of a ``memories.source_refs``."""
    if isinstance(source_refs, str):
        try:
            source_refs = json.loads(source_refs)
        except Exception:
            return None
    if not isinstance(source_refs, list):
        return None
    for ref in source_refs:
        if isinstance(ref, dict) and ref.get("interaction_id"):
            return str(ref["interaction_id"])
    return None


def classify(*, apply: bool) -> dict:
    allowed = _ingestible_kinds()
    polluted: list[int] = []
    reasons: Counter = Counter()
    by_kind: Counter = Counter()
    samples: list[dict] = []

    with db_session() as db:
        rows = db.fetchall(
            "SELECT id, kind, title, content, source_refs FROM memories"
        ) or []

        # Batch-resolve source interaction kinds in one query.
        ref_by_mem: dict[int, Optional[str]] = {}
        for r in rows:
            ref_by_mem[int(r["id"])] = _interaction_id(r.get("source_refs"))
        iids = sorted({v for v in ref_by_mem.values() if v})
        kind_by_iid: dict[str, str] = {}
        if iids:
            irows = db.fetchall(
                "SELECT id::text AS id, kind FROM interactions "
                "WHERE id::text = ANY(%s)",
                (iids,),
            ) or []
            kind_by_iid = {str(ir["id"]): ir.get("kind") for ir in irows}

        scanned = len(rows)
        for r in rows:
            mid = int(r["id"])
            iid = ref_by_mem.get(mid)
            ikind = kind_by_iid.get(iid) if iid else None
            content = r.get("content")
            reason: Optional[str] = None

            if ikind is not None:
                # Source interaction resolved — kind decides.
                if ikind not in allowed:
                    reason = f"interaction_kind:{ikind}"
            else:
                # No resolvable source interaction: it never had one (a
                # direct upsert_memory write — Cause B) or it was deleted.
                tag = _bracket_tag_kind(content)
                if tag is not None and tag not in allowed:
                    # A _render_content artifact whose source interaction
                    # is gone — the [kind] prefix still names the kind.
                    reason = f"render_tag:{tag}"
                elif _is_serialized_payload(content):
                    reason = "serialized_content"

            if reason is None:
                continue
            polluted.append(mid)
            head, _, tail = reason.partition(":")
            reasons[head] += 1
            if tail:
                by_kind[tail] += 1
            if len(samples) < 12:
                samples.append({
                    "id": mid,
                    "reason": reason,
                    "title": (r.get("title") or "")[:40],
                    "content_head": str(r.get("content") or "")[:80],
                })

        deleted = 0
        if apply and polluted:
            db.execute(
                "DELETE FROM memories WHERE id = ANY(%s)", (polluted,)
            )
            deleted = len(polluted)

    return {
        "mode": "apply" if apply else "dry_run",
        "scanned": scanned,
        "polluted": len(polluted),
        "deleted": deleted,
        "reasons": dict(reasons),
        "by_source_kind": dict(by_kind.most_common()),
        "samples": samples,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply", action="store_true",
        help="execute the deletes (default is dry-run)",
    )
    args = p.parse_args()
    summary = classify(apply=bool(args.apply))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
