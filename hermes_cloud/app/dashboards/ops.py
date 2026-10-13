"""Op application engine — applies LLM-emitted ops to dashboard state.

Op types (spec §6.d):
  add        {chart: {id?, title, dsl, position}}
  edit       {chart_id, dsl?, dsl_patch?, title?}
  remove     {chart_id}
  reorder    {order: [chart_id, ...]}
  rename_tab {title}

Each add/edit op validates the resulting DSL against the registry +
caller grants before persisting. Validation failure raises OpError so
the routes layer can emit an op_error SSE frame and continue.

Side effect: any successful op on a system-preset tab clears the
``is_system_preset`` flag (auto-fork-on-edit per spec §9).
"""
from __future__ import annotations

import copy
import pathlib
import uuid
from typing import Any, Dict, Iterable, List, Optional

from . import store
from .compiler import CompileError, compile_dsl
from .dsl import DSLQuery
from .semantic.registry import Registry


class OpError(Exception): ...


def _validate_dsl(dsl_dict: Dict[str, Any], *,
                  caller_id: int, grants: set, registry: Registry) -> None:
    # Text widgets carry no SQL — skip DSL/compile validation entirely.
    # The LLM sometimes forgets chart.title; the calling ``add`` op also
    # carries an outer title, so apply_ops fills the inner one if it's
    # missing. We just sanity-check that *something* is there after that
    # fallback runs, but it shouldn't actually trip in practice.
    chart_block = (dsl_dict or {}).get("chart") or {}
    chart_type = (chart_block.get("type") or "").lower()
    if chart_type == "text":
        return
    # 'composite' renders bar + line on the same x; it needs TWO
    # entries in ``measures``. The system prompt tells the LLM this,
    # but model output drifts — surface a clear error rather than
    # letting the chart render with a silently-empty line series.
    if chart_type == "composite":
        measures = (dsl_dict or {}).get("measures") or []
        if len(measures) < 2:
            raise OpError(
                "composite chart needs 2 measures (first→bar, second→line); "
                f"got {len(measures)} in ``measures``"
            )
    # 'report_table' is a pivot — it needs TWO entries in ``dimensions``
    # (row dim + column dim). Without two dims the pivot collapses to
    # a single column.
    if chart_type == "report_table":
        dimensions = (dsl_dict or {}).get("dimensions") or []
        if len(dimensions) < 2:
            raise OpError(
                "report_table needs 2 dimensions (row + column); "
                f"got {len(dimensions)} in ``dimensions``"
            )
    try:
        q = DSLQuery(**dsl_dict)
    except Exception as e:
        raise OpError(f"invalid DSL shape: {e}")
    try:
        compile_dsl(q, registry=registry, caller_id=caller_id, grants=grants)
    except CompileError as e:
        raise OpError(f"DSL does not compile: {e}")


def _apply_patch(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    """Apply dotted-path patches like {'chart.type': 'line'} to base dict."""
    out = copy.deepcopy(base)
    for path, val in patch.items():
        parts = path.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return out


def apply_ops(
    db, *,
    tab_id,
    user_id: int,
    ops: Iterable[Dict[str, Any]],
    registry: Registry,
    caller_grants: Optional[set] = None,
    source_message_id: Optional[uuid.UUID] = None,
) -> List[Dict[str, Any]]:
    grants = caller_grants or set()
    results: List[Dict[str, Any]] = []
    any_succeeded = False

    for op in ops:
        action = op.get("action")
        if action == "add":
            ch = op.get("chart") or {}
            dsl = ch.get("dsl") or {}
            # Fallback: when the LLM omits ``chart.title`` (especially
            # common on text widgets where every other field is empty)
            # copy the outer chart title in so downstream validators
            # and the renderer have something to show. Note the dsl-truthy
            # guard was previously here too; we now apply the fallback
            # unconditionally so a literally-empty ``dsl`` from the LLM
            # (e.g. type=text with no body provided) still gets a title.
            chart_block = (dsl or {}).get("chart") or {}
            if not chart_block.get("title"):
                chart_block["title"] = ch.get("title") or "untitled"
                dsl["chart"] = chart_block
            _validate_dsl(dsl, caller_id=user_id, grants=grants, registry=registry)
            r = store.create_chart(
                db, tab_id=tab_id,
                title=ch.get("title") or "untitled",
                dsl=dsl,
                position=int(ch.get("position", 0) or 0),
                source_prompt_message_id=source_message_id,
            )
            results.append({"op": "add", "chart_id": str(r.id)})
            any_succeeded = True
        elif action == "edit":
            ch_id = uuid.UUID(op["chart_id"])
            existing = store.get_chart(db, ch_id)
            if not existing or str(existing.tab_id) != str(tab_id):
                raise OpError(f"chart {ch_id} not in tab {tab_id}")
            new_dsl: Dict[str, Any] = op.get("dsl") or existing.dsl
            patch = op.get("dsl_patch") or {}
            if patch:
                new_dsl = _apply_patch(existing.dsl, patch)
            _validate_dsl(new_dsl, caller_id=user_id, grants=grants, registry=registry)
            updates: Dict[str, Any] = {"dsl": new_dsl}
            if "title" in op:
                updates["title"] = op["title"]
            r = store.update_chart(db, ch_id, **updates)
            results.append({"op": "edit", "chart_id": str(r.id)})
            any_succeeded = True
        elif action == "remove":
            ch_id = uuid.UUID(op["chart_id"])
            store.delete_chart(db, ch_id)
            results.append({"op": "remove", "chart_id": str(ch_id)})
            any_succeeded = True
        elif action == "reorder":
            for pos, cid in enumerate(op.get("order") or []):
                store.update_chart(db, uuid.UUID(cid), position=pos)
            results.append({"op": "reorder"})
            any_succeeded = True
        elif action == "rename_tab":
            store.update_tab(db, tab_id, title=op.get("title") or "")
            results.append({"op": "rename_tab"})
            any_succeeded = True
        else:
            raise OpError(f"unknown action {action!r}")

    if any_succeeded:
        # Auto-fork on edit (spec §9): any change to a system-preset tab
        # clears the flag. preset_key is preserved for traceability.
        tab = store.get_tab(db, tab_id)
        if tab and tab.is_system_preset:
            store.update_tab(db, tab_id, is_system_preset=False)

    return results
