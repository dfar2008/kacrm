"""Contract: the cloud ``PgSessionDB`` must stay call-compatible with the
upstream hermes ``_SqliteSessionDB`` it shadows.

WHY THIS EXISTS
---------------
``run_agent`` and the gateway dispatch call ``self._session_db.<method>(...)``
by name. ``self._session_db`` is EITHER ``PgSessionDB`` (``HERMES_USE_CLOUD_DB=1``,
cloud) OR ``_SqliteSessionDB`` (CLI / offline). The two are independent
implementations of the same interface.

When an upstream hermes upgrade adds a parameter to a ``_SqliteSessionDB``
method (e.g. ``cwd``, ``timestamp``, ``duration_ms``), the shared call sites
start passing it — and if the matching ``PgSessionDB`` method cannot accept it,
the call raises ``TypeError``. ``run_agent``'s flush swallows that as a mere
warning, so the message is silently NOT persisted and the data loss only
surfaces days later (a user notices a conversation vanished on refresh).

This has recurred on nearly EVERY hermes upgrade — see git history:
  018ea13 / a95f26e  cwd + timestamp
  3977f5f / 67696e9  platform_message_id / observed / timestamp
  ebb47ef            search_messages sort kwarg
  (this release)     duration_ms

These tests turn that silent, ship-then-lose-data drift into a CI failure at
upgrade time. For every method the two classes share, every parameter one
side accepts must be acceptable by the other — declared explicitly OR
absorbed via ``**kwargs``.
"""
from __future__ import annotations

import inspect


def _param_contract(cls) -> dict:
    """Return ``{method_name: (named_params:set, accepts_var_keyword:bool)}``
    for every public method of *cls*."""
    out: dict = {}
    for name, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
        if name.startswith("_"):
            continue
        sig = inspect.signature(fn)
        named = {
            p.name
            for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY) and p.name != "self"
        }
        has_var_kw = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
        out[name] = (named, has_var_kw)
    return out


# The PERSISTENCE WRITE path — the methods whose parameter drift makes a
# message/session silently fail to persist (the recurring upgrade bug this
# whole file exists for). These are held to a STRICT contract: no accepted
# drift, ever. A new param on either side must be absorbed (``**kwargs``) or
# declared on both.
_PERSISTENCE_WRITE_METHODS = frozenset({
    "append_message",
    "create_session",
})

# Read/query methods where the two impls have KNOWN, ACCEPTED signature gaps:
# PgSessionDB simply doesn't implement these upstream filter/scope params yet.
# Unlike the write path, a mismatch here surfaces as a LOUD TypeError at the
# call site (wrong-results-or-crash, not silent data loss), so we track them
# explicitly rather than papering over them with ``**kwargs`` (which would
# silently change query semantics). Shrink this list by implementing the
# param in PgSessionDB; a NEW entry appearing here is a red that forces a
# conscious decision.
_ACCEPTED_READ_PATH_GAPS = {
    # sqlite-only default-valued scope param; verified NO caller ever passes
    # it (grep: 0 call sites), so PG omitting it can't be triggered.
    # v0.20: archive_dropped is TUI-only (tui_gateway/methods_prompt.py);
    # no cloud caller reaches the PG path with it.
    "replace_messages": {"active_only", "archive_dropped"},
    # v0.20: expected_delete_ids is a CLI-only integrity check
    # (hermes_cli/sessions_cmd.py); no cloud PG call path.
    "delete_session": {"sessions_dir", "expected_delete_ids",
        # Native CLI/TUI and upstream api_server deletion only; cloud REST
        # deletes through its own handler, never these write-guard filters.
        "expected_display_messages", "exclude_active_write_guards", "include_compression_chain"},
    # Guarded prune is hermes_cli/sessions_cmd + native web sessions only;
    # cloud PG retention does not invoke SessionDB.prune_sessions.
    "prune_sessions": {"sessions_dir", "exclude_active_write_guards"},
    # v0.19: sqlite get_messages gained pagination (limit/offset). Only the
    # hermes CLI web dashboard (hermes_cli/web_server.py) paginates; the cloud
    # SPA/API never calls get_messages() on the PG path with these — verified
    # grep: 0 hermes_cloud call sites. PG returns full history unpaginated.
    # v0.20: after_id/latest are incremental-poll params used only by the
    # hermes CLI web dashboard + TUI; grep: 0 cloud/gateway PG call sites.
    # v0.21.6 include_ancestors is native web/TUI history only.
    # v0.21: include_compacted is CLI-web-dashboard only
    # (hermes_cli/web_routers/sessions.py); no cloud PG call path.
    "get_messages": {
        "include_inactive", "limit", "offset", "after_id", "latest",
        "include_compacted", "include_ancestors",
    },
    # v0.20 CORRECTION (found live): repair_alternation is NOT TUI-only —
    # gateway/session.py transcript reads pass it on the cloud path, and the
    # gap caused every IM transcript read to TypeError into an empty
    # conversation. PgSessionDB now declares it (accepted-and-ignored).
    # include_compacted is native api_server/web/TUI history only.
    # include_row_ids also serves shared turn_facade_lease reloads; PG implements it.
    "get_messages_as_conversation": {
        "include_inactive", "include_compacted",
    },
    # v0.20 NOTE: ``fields`` is NOT a gap — session_search IS in the default
    # cloud composite (hermes-api-server includes it, and profiles without an
    # explicit toolsets config fall back to that composite), so its
    # discovery mode passes fields= against PG. PgSessionDB.search_messages
    # declares it (accepted-and-ignored, returns full rows).
    "search_messages": {"include_inactive"},
    # v0.20: workspace_key is CLI-only (hermes_cli/main.py workspace scoping).
    "search_sessions": {"workspace_key"},
    "list_sessions_rich": {
        "archived_only", "cwd_prefix", "id_query", "include_archived",
        "min_message_count", "order_by_last_active", "project_compression_tips",
        # v0.19: compact_rows (CLI/TUI dashboard row-shape) + search_query
        # (CLI session search); both CLI/TUI-only, no cloud PG call path.
        "compact_rows", "search_query",
        # v0.20: include_pinned (api_server /api/sessions — same native-API
        # endpoint as order_by_last_active above, no cloud PG call path);
        # session_key + sources are CLI/TUI listing filters.
        "include_pinned", "session_key", "sources",
        # v0.21: include_hidden rides the SAME api_server /api/sessions call
        # as include_pinned (verified: one call site, both params together)
        # plus tui_gateway — the cloud SPA serves its own /api/sessions from
        # PG directly, so hermes' handler is internal-only here.
        "include_hidden",
        # include_subagents is native web profiles/sessions + TUI listing;
        # cloud session listing queries PG directly.
        "include_subagents",
    },
    "session_count": {
        "archived_only", "cwd_prefix", "exclude_children", "exclude_sources",
        "include_archived", "min_message_count",
        # v0.20: sources filter — CLI/TUI dashboard only, like the rest.
        "sources",
        # Same native web/TUI listing callers as list_sessions_rich.
        "include_subagents",
    },
    # Export flags are used by hermes_cli/sessions_cmd.py export, native
    # web sessions export and TUI; shared agent/gateway/tools/cron never export.
    "export_session": {"include_compacted", "include_inactive"},
    "export_all": {"include_compacted", "include_inactive"},
}


def _drift(sqlite: dict, pg: dict) -> dict:
    """method -> set of params one impl declares that the other can't accept."""
    drift: dict = {}
    for name in sorted(set(sqlite) & set(pg)):
        sq_params, sq_kw = sqlite[name]
        pg_params, pg_kw = pg[name]
        missing: set = set()
        # Direction 1 — upstream added a param to sqlite; PG must accept it
        # (cloud path) or absorb via **kwargs.
        if not pg_kw:
            missing |= (sq_params - pg_params)
        # Direction 2 — a local patch added a param to PG; sqlite must accept
        # it (CLI/offline path) or absorb via **kwargs.
        if not sq_kw:
            missing |= (pg_params - sq_params)
        if missing:
            drift[name] = missing
    return drift


def test_persistence_write_path_call_compatible():
    """append_message / create_session / replace_messages MUST stay
    call-compatible across the sqlite (CLI) and PG (cloud) SessionDB impls.

    A red here is the exact recurring upgrade bug: run_agent passes a param
    one impl can't accept, the write raises TypeError, run_agent swallows it
    as a warning, and the message silently never persists. Fix by absorbing
    the param via ``**kwargs`` on the lagging impl (or declaring it on both).
    NEVER delete/relax this test to go green.
    """
    from hermes_state import _SqliteSessionDB
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    drift = _drift(_param_contract(_SqliteSessionDB), _param_contract(PgSessionDB))
    problems = {m: p for m, p in drift.items() if m in _PERSISTENCE_WRITE_METHODS}
    assert not problems, (
        "SessionDB WRITE-path interface drift — messages/sessions will "
        "silently stop persisting on one runtime:\n  "
        + "\n  ".join(f"{m}(): incompatible on {sorted(p)}" for m, p in problems.items())
    )


def test_no_unexpected_sessiondb_interface_drift():
    """Comprehensive guard: any shared-method param drift NOT already logged
    in ``_ACCEPTED_READ_PATH_GAPS`` (nor on the strict write path) is a new
    divergence introduced by an upgrade/patch and must be reconciled.

    To go green: either add the param to PgSessionDB, give the method
    ``**kwargs``, or — if it's a read-path filter PG genuinely doesn't
    implement yet — add it to ``_ACCEPTED_READ_PATH_GAPS`` with intent.
    """
    from hermes_state import _SqliteSessionDB
    from hermes_cloud.app.agent_session_pg import PgSessionDB

    drift = _drift(_param_contract(_SqliteSessionDB), _param_contract(PgSessionDB))
    unexpected: dict = {}
    for name, params in drift.items():
        residual = params - _ACCEPTED_READ_PATH_GAPS.get(name, set())
        if residual:
            unexpected[name] = residual
    assert not unexpected, (
        "NEW SessionDB interface drift (not in _ACCEPTED_READ_PATH_GAPS) — a "
        "hermes upgrade or patch diverged the impls; reconcile before shipping:\n  "
        + "\n  ".join(f"{m}(): {sorted(p)}" for m, p in unexpected.items())
    )


# ── Missing-method blind spot (found live, v0.20.1) ─────────────────────────
# The two tests above compare PARAMS of methods BOTH classes define — a method
# that exists only on sqlite is invisible to them. v0.20 added
# flush_token_counts()/queue_token_counts()/publish_compression_child() to the
# sqlite store and called them from SHARED code (run_agent finalize,
# conversation_loop token accounting, compression rotation); PG lacked all
# three, and the failure surfaced only in the live gateway log as
# "'PgSessionDB' object has no attribute 'flush_token_counts'" every turn.
# This scan closes that hole: every ``session_db.<method>()`` call in shared
# hermes code must resolve on PgSessionDB, or be registered below with intent.

# Pre-existing gaps (present in v0.19 too, verified against 3ef6bbd20):
# call sites are guard-wrapped (getattr/try) or live on paths the cloud PG
# runtime doesn't traverse. Implement in PgSessionDB to shrink this set; a
# NEW entry appearing here must be a conscious decision, not a default.
_ACCEPTED_MISSING_ON_PG = frozenset({
    # sqlite-side compaction/lineage utilities; context_compressor checks
    # callable(getattr(...)) before using archive_and_compact.
    "archive_and_compact",
    "get_compression_tip",
    "get_conversation_root",
    "resolve_resume_session_id",
    # delegate-tool handoff ledger (sqlite tables); cloud delegation runs
    # in-process without the handoff queue.
    "claim_handoff", "complete_handoff", "fail_handoff", "list_pending_handoffs",
    # telegram topic-mode bindings — the cloud has no telegram channel.
    "bind_telegram_topic", "disable_telegram_topic_mode",
    "enable_telegram_topic_mode", "get_telegram_topic_binding",
    "is_telegram_session_linked_to_topic", "is_telegram_topic_mode_enabled",
    "list_telegram_topic_bindings_for_chat",
    "list_unlinked_telegram_sessions_for_user",
    # auxiliary-model usage ledger (sqlite table aux_usage).
    "record_auxiliary_usage",
    # billing-route refresh on /model switch; call site is try-guarded.
    "update_session_billing_route",
})


def test_shared_code_calls_resolve_on_pg():
    """Every session_db method invoked by shared hermes code must exist on
    PgSessionDB or be consciously registered in _ACCEPTED_MISSING_ON_PG."""
    import re
    from pathlib import Path

    from hermes_cloud.app.agent_session_pg import PgSessionDB

    hermes_root = Path(__file__).resolve().parents[2] / "hermes"
    called: set = set()
    scan_roots = ["run_agent.py", "agent", "gateway", "tools", "cron"]
    pat = re.compile(r"session_db\.([a-z_]+)\(")
    for root in scan_roots:
        p = hermes_root / root
        files = [p] if p.is_file() else list(p.rglob("*.py"))
        for fp in files:
            try:
                called |= set(pat.findall(fp.read_text(encoding="utf-8")))
            except (OSError, UnicodeDecodeError):
                continue

    missing = {
        name for name in called
        if not hasattr(PgSessionDB, name) and name not in _ACCEPTED_MISSING_ON_PG
    }
    assert not missing, (
        "Shared hermes code calls SessionDB methods PgSessionDB does not "
        "define — on the cloud path these raise AttributeError at runtime "
        "(usually swallowed into a warning and silent data loss). Implement "
        "them in PgSessionDB or register in _ACCEPTED_MISSING_ON_PG with "
        f"intent:\n  {sorted(missing)}"
    )

    stale = _ACCEPTED_MISSING_ON_PG & {
        m for m in _ACCEPTED_MISSING_ON_PG if hasattr(PgSessionDB, m)
    }
    assert not stale, (
        f"Entries now implemented on PgSessionDB — remove from registry: {sorted(stale)}"
    )
