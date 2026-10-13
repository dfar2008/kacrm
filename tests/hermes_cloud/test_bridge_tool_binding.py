"""Tests for :mod:`hermes.tools.crm.dispatch`.

Phase 1f. Unit-level tests for :func:`_field_to_json_schema` and
:func:`_parse_tool_name` run without a live DB; everything that hits
``collections`` / ``module_fields`` / ``Save`` is gated behind the same
``_crm_reachable`` skip the other CRM tests use.
"""
from __future__ import annotations

import secrets

import pytest


# ---------------------------------------------------------------------------
# Pure-function tests (no DB)
# ---------------------------------------------------------------------------


def test_field_to_json_schema_text_types():
    from hermes.tools.crm.dispatch import _field_to_json_schema

    for ftype in ("text", "textarea", "email", "url", "phone"):
        out = _field_to_json_schema(
            {"field_type": ftype, "field_name": "x", "field_label": "X"}
        )
        assert out["type"] == "string"


def test_field_to_json_schema_numeric_types():
    from hermes.tools.crm.dispatch import _field_to_json_schema

    for ftype in ("number", "currency", "percent"):
        out = _field_to_json_schema(
            {"field_type": ftype, "field_name": "n", "field_label": "N"}
        )
        assert out["type"] == "number"


def test_field_to_json_schema_integer_ref_types():
    from hermes.tools.crm.dispatch import _field_to_json_schema

    for ftype in ("user", "department", "createdBy", "relation"):
        out = _field_to_json_schema(
            {"field_type": ftype, "field_name": "r", "field_label": "Rel"}
        )
        assert out["type"] == "integer"


def test_field_to_json_schema_date_format():
    from hermes.tools.crm.dispatch import _field_to_json_schema

    assert _field_to_json_schema(
        {"field_type": "date", "field_name": "d", "field_label": "D"}
    ) == {"description": "D", "type": "string", "format": "date"}

    assert _field_to_json_schema(
        {"field_type": "datetime", "field_name": "dt", "field_label": "DT"}
    ) == {"description": "DT", "type": "string", "format": "date-time"}


def test_field_to_json_schema_boolean():
    from hermes.tools.crm.dispatch import _field_to_json_schema

    out = _field_to_json_schema(
        {"field_type": "boolean", "field_name": "b", "field_label": "Flag"}
    )
    assert out == {"description": "Flag", "type": "boolean"}


def test_field_to_json_schema_select_emits_enum():
    from hermes.tools.crm.dispatch import _field_to_json_schema

    out = _field_to_json_schema(
        {
            "field_type": "select",
            "field_name": "status",
            "field_label": "Status",
            "options": [
                {"value": "active", "label": "Active"},
                {"value": "inactive", "label": "Inactive"},
            ],
        }
    )
    assert out["type"] == "string"
    assert out["enum"] == ["active", "inactive"]


def test_field_to_json_schema_select_without_options_has_no_enum():
    """If options are missing the schema should still be usable — a
    bare string, no enum. Seed may omit options for free-text picklists."""
    from hermes.tools.crm.dispatch import _field_to_json_schema

    out = _field_to_json_schema(
        {"field_type": "select", "field_name": "s", "field_label": "S"}
    )
    assert out["type"] == "string"
    assert "enum" not in out


def test_field_to_json_schema_unknown_type_is_string():
    """Defensive: seed typos shouldn't crash the catalogue — a tool with
    a slightly off schema is better than no catalogue at all."""
    from hermes.tools.crm.dispatch import _field_to_json_schema

    out = _field_to_json_schema(
        {"field_type": "alien-widget", "field_name": "x", "field_label": "X"}
    )
    assert out["type"] == "string"


def test_field_to_json_schema_description_combines_label_and_desc():
    from hermes.tools.crm.dispatch import _field_to_json_schema

    out = _field_to_json_schema(
        {
            "field_type": "text",
            "field_name": "n",
            "field_label": "Name",
            "description": "The display name",
        }
    )
    assert out["description"] == "Name — The display name"


def test_parse_tool_name_roundtrips():
    from hermes.tools.crm.dispatch import _parse_tool_name

    assert _parse_tool_name("crm_search_accounts") == ("search", "accounts")
    assert _parse_tool_name("crm_get_contacts") == ("get", "contacts")
    assert _parse_tool_name("crm_create_leads") == ("create", "leads")
    assert _parse_tool_name("crm_update_accounts") == ("update", "accounts")


def test_parse_tool_name_rejects_non_crm_prefix():
    from hermes.tools.crm.dispatch import _parse_tool_name

    assert _parse_tool_name("search_accounts") is None
    assert _parse_tool_name("crm_delete_accounts") is None  # delete not exposed
    assert _parse_tool_name("crm_search_") is None  # empty module
    assert _parse_tool_name("") is None


def test_dispatch_via_gateway_pending_returns_success_false(monkeypatch):
    """v2.2-final freeze #4: pending_approval must return success=False
    and the pending_approval flag — the gateway has *not* auto-approved,
    and the LLM must not retry. Approval happens on the originating
    channel (Charter §1)."""
    from hermes.tools.crm import dispatch as tool_binding
    import hermes.action_gateway.dispatcher as real_dispatcher

    approve_calls: list = []

    def _submit(**kwargs):  # noqa: ARG001
        return {
            "id": "123",
            "state": "pending_approval",
            "target": {"module": "accounts", "id": None},
            "params": {"op": "create", "data": {"accounts_name": "A"}},
        }

    def _approve(*args, **kwargs):  # noqa: ARG001
        approve_calls.append((args, kwargs))
        raise AssertionError("dispatcher.approve must NOT be auto-called")

    # Patch the attributes on the real dispatcher module — robust to
    # ``from hermes.action_gateway import dispatcher`` resolving via the
    # already-imported package attribute rather than sys.modules.
    monkeypatch.setattr(real_dispatcher, "submit", _submit)
    monkeypatch.setattr(real_dispatcher, "approve", _approve, raising=False)

    result = tool_binding._dispatch_via_gateway(
        profile_id="p1",
        agent_name="a1",
        module_id=1,
        module_name="accounts",
        op="create",
        data={"accounts_name": "A"},
        user_id=1,
        db=object(),
    )
    assert result["success"] is False
    assert result["pending_approval"] is True
    assert result["action_id"] == "123"
    assert "summary" in result
    # No silent auto-approve.
    assert approve_calls == []


def test_pending_approval_prompt_contract_no_retry_hint():
    """The crm_write tool description MUST tell the model that
    ``pending_approval=true`` is not a failure and must not be retried.
    Without this textual companion the agent rephrases and retries.

    Charter v2 Phase 2 (2026-05-05): the intake / email_drafter
    specialist prompts that previously carried the same hint were
    folded into markdown skills (``crm-intake`` / ``email-drafting``).
    The pending_approval contract now lives in those SKILL.md files —
    the tool-description guard below is the runtime invariant; the
    skill text is the LLM-side guidance.
    """
    from hermes.tools.crm import dispatch as tool_binding

    # Build a minimal hint structure so build_crm_tools doesn't need DB.
    spec = tool_binding._build_crm_write_spec(
        module_hints=[("accounts", ["accounts_name*"])]
    ) if hasattr(tool_binding, "_build_crm_write_spec") else None

    # Fall back to grepping the source for the hint text — robust to the
    # internal helper changing shape.
    src = (
        __import__("pathlib").Path(tool_binding.__file__).read_text(
            encoding="utf-8"
        )
    )
    assert "pending_approval=true" in src
    assert "do not retry" in src.lower() or "不要重试" in src or "do not retry" in src


# ---------------------------------------------------------------------------
# DB-backed tests
# ---------------------------------------------------------------------------


def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


db_tests = pytest.mark.skipif(
    not _crm_reachable(), reason="hermes_crm_dev not reachable"
)


# ---------------------------------------------------------------------------
# build_crm_tools — unified tool catalogue
#
# The early port emitted 4 tools per collection (crm_search_accounts, etc.);
# the current architecture emits 3 unified tools (crm_search / crm_get /
# crm_write) that take the module as an argument, plus metadata + agent
# tools. Legacy per-module names are still accepted by ``dispatch`` for
# backwards compatibility but are NO LONGER surfaced in the tool catalogue.
# ---------------------------------------------------------------------------


@db_tests
def test_build_crm_tools_emits_unified_triplet():
    from hermes.tools.crm.dispatch import build_crm_tools

    tools = build_crm_tools()
    names = {t.name for t in tools}
    # The three unified CRUD tools are always present.
    for name in ("crm_search", "crm_get", "crm_write"):
        assert name in names, f"missing {name} in {sorted(names)}"
    # And metadata / agent helpers round out the catalogue.
    for name in ("crm_list_modules", "crm_get_schema", "crm_semantic_search"):
        assert name in names, f"missing {name} in {sorted(names)}"


@db_tests
def test_build_crm_tools_module_enum_covers_seeded_collections():
    from hermes.tools.crm.dispatch import build_crm_tools

    by_name = {t.name: t for t in build_crm_tools()}
    # Every unified tool carries the same module enum derived from
    # collections × editable-fields. Seed guarantees the core three.
    for name in ("crm_search", "crm_get", "crm_write"):
        enum = set(by_name[name].input_schema["properties"]["module"]["enum"])
        for collection in ("accounts", "contacts", "leads"):
            assert collection in enum, (
                f"{name} module enum missing {collection}: {sorted(enum)}"
            )


@db_tests
def test_build_crm_tools_verbs_map_to_rbac_actions():
    from hermes.tools.crm.dispatch import build_crm_tools

    by_name = {t.name: t for t in build_crm_tools()}
    # Verb is the static RBAC action the dispatcher pre-checks. crm_write
    # is dynamic (create vs update is resolved from arguments["action"])
    # so its declared verb is the permissive one — "create".
    assert by_name["crm_search"].verb == "index"
    assert by_name["crm_get"].verb == "view"
    assert by_name["crm_write"].verb == "create"


@db_tests
def test_build_crm_tools_write_schema_shape():
    from hermes.tools.crm.dispatch import build_crm_tools

    by_name = {t.name: t for t in build_crm_tools()}
    write = by_name["crm_write"]
    props = write.input_schema["properties"]
    # Required: module, action, data. id is optional (update-only).
    assert set(write.input_schema["required"]) == {"module", "action", "data"}
    assert props["action"]["enum"] == ["create", "update"]
    assert props["data"]["type"] == "object"
    assert "id" in props
    # Seeded required fields surface via the description text so the LLM
    # sees them without calling crm_get_schema first.
    assert "accounts_name" in write.description
    assert "contacts_name" in write.description


@db_tests
def test_build_crm_tools_search_schema_has_pagination():
    from hermes.tools.crm.dispatch import build_crm_tools

    by_name = {t.name: t for t in build_crm_tools()}
    search = by_name["crm_search"]
    props = search.input_schema["properties"]
    assert props["page"]["type"] == "integer"
    assert props["limit"]["type"] == "integer"
    assert "search" in props
    # module is required — the enum scopes the search to a collection.
    assert "module" in search.input_schema["required"]


# ---------------------------------------------------------------------------
# Dispatcher happy path — admin user against the real schema
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def admin_id() -> int:
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1"
        )
    assert row, "admin user missing — run seed"
    return int(row["id"])


@db_tests
def test_dispatch_create_get_update_search_roundtrip(admin_id):
    from hermes.tools.crm.dispatch import dispatch

    suffix = secrets.token_hex(4)
    name = f"ToolQA-{suffix}"

    # CREATE
    created = dispatch(
        tool_name="crm_create_accounts",
        arguments={"accounts_name": name, "owner_by": admin_id},
        user_id=admin_id,
    )
    assert created["success"] is True
    rid = int(created["id"])
    assert rid > 0

    try:
        # GET
        got = dispatch(
            tool_name="crm_get_accounts",
            arguments={"id": rid},
            user_id=admin_id,
        )
        assert got["data"]["accounts_name"] == name

        # UPDATE
        updated = dispatch(
            tool_name="crm_update_accounts",
            arguments={"id": rid, "accounts_name": f"{name}-v2"},
            user_id=admin_id,
        )
        assert updated == {"success": True, "id": rid}

        # SEARCH — should find the renamed record
        hits = dispatch(
            tool_name="crm_search_accounts",
            arguments={"search": f"{name}-v2", "limit": 5},
            user_id=admin_id,
        )
        assert hits["total"] >= 1
        matched = [
            r for r in hits["data"] if r.get("accounts_name") == f"{name}-v2"
        ]
        assert len(matched) == 1
    finally:
        # Cleanup via soft-delete; no delete verb exposed so go direct.
        from hermes.crm.db import db_session

        with db_session() as db:
            db.execute(
                "UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s",
                (rid,),
            )


@db_tests
def test_dispatch_raises_on_unknown_tool(admin_id):
    from hermes.tools.crm.dispatch import (
        ToolNotFoundError,
        dispatch,
    )

    with pytest.raises(ToolNotFoundError):
        dispatch(
            tool_name="crm_search_nonexistent_module",
            arguments={},
            user_id=admin_id,
        )
    with pytest.raises(ToolNotFoundError):
        dispatch(
            tool_name="garbage_name",
            arguments={},
            user_id=admin_id,
        )


@db_tests
def test_dispatch_blocks_missing_required_field(admin_id):
    """The underlying Save layer's validation surfaces through dispatch —
    the MCP caller gets the same error the REST layer would produce."""
    from hermes.tools.crm.dispatch import dispatch
    from hermes.crm.save import FieldValidationException

    with pytest.raises(FieldValidationException) as exc:
        dispatch(
            tool_name="crm_create_contacts",
            arguments={"email": "nobody@example.com"},
            user_id=admin_id,
        )
    assert exc.value.field_name == "contacts_name"


# ---------------------------------------------------------------------------
# crm_get error-message richness — fail-fast signal for cheap LLM agents
#
# Regression cover for the 2026-05-31 daily-coach gateway.log loop:
# deepseek-v4-flash got back the bare string "ValueError: Record does not
# exist" and retried the same id 6 times before the same_tool_failure_warning
# could squelch it. The error string MUST now name the module + id and tell
# the agent NOT to retry, so even a dim model recognises the dead-end.
# ---------------------------------------------------------------------------


@db_tests
def test_dispatch_crm_get_not_found_carries_module_and_id(admin_id):
    """A missing record yields an error that names module+id and tells the
    agent to stop retrying — the signal the loop-prone flash models need."""
    from hermes.tools.crm.dispatch import dispatch

    with pytest.raises(ValueError) as exc:
        dispatch(
            tool_name="crm_get",
            arguments={"module": "accounts", "id": 99_999_999},
            user_id=admin_id,
        )
    msg = str(exc.value)
    assert "accounts" in msg, f"missing module in error: {msg!r}"
    assert "99999999" in msg, f"missing id in error: {msg!r}"
    assert "not found" in msg.lower() or "does not exist" in msg.lower()
    # The do-not-retry directive is the critical agent-steering signal.
    assert "not retry" in msg.lower() or "do not retry" in msg.lower(), (
        f"crm_get not-found error must tell agent to stop retrying: {msg!r}"
    )


@db_tests
def test_dispatch_crm_get_soft_deleted_says_deleted(admin_id):
    """Soft-deleted records must be distinguishable from never-existed —
    'deleted' is permanent, retry will never help, the message says so."""
    import secrets
    from hermes.tools.crm.dispatch import dispatch
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    created = dispatch(
        tool_name="crm_create_accounts",
        arguments={"accounts_name": f"DeletedQA-{suffix}", "owner_by": admin_id},
        user_id=admin_id,
    )
    rid = int(created["id"])
    # Soft-delete via direct DB write (no delete verb in the unified path).
    with db_session() as db:
        db.execute(
            "UPDATE accounts SET deleted = TRUE WHERE accounts_id = %s",
            (rid,),
        )

    with pytest.raises(ValueError) as exc:
        dispatch(
            tool_name="crm_get",
            arguments={"module": "accounts", "id": rid},
            user_id=admin_id,
        )
    msg = str(exc.value)
    assert "accounts" in msg
    assert str(rid) in msg
    assert "deleted" in msg.lower(), (
        f"soft-deleted record error must say 'deleted': {msg!r}"
    )
    assert "not retry" in msg.lower() or "do not retry" in msg.lower()


def test_dispatch_crm_get_rejects_non_integer_id():
    """String / UUID ids fail fast before touching the DB, with a message
    that explains ids are integers — pre-empts a class of agent fabrication
    where the LLM passes a UUID copied from a different tool's output."""
    from hermes.tools.crm.dispatch import dispatch

    with pytest.raises(ValueError) as exc:
        dispatch(
            tool_name="crm_get",
            arguments={"module": "accounts", "id": "abc-not-a-number"},
            user_id=1,
        )
    msg = str(exc.value)
    assert "integer" in msg.lower(), f"type-mismatch error: {msg!r}"
    assert "abc-not-a-number" in msg, f"actual bad value must appear: {msg!r}"
    assert "not retry" in msg.lower() or "do not retry" in msg.lower()


def test_dispatch_crm_get_rejects_missing_id():
    """Missing id is reported with module context, not a bare 'id is required'."""
    from hermes.tools.crm.dispatch import dispatch

    with pytest.raises(ValueError) as exc:
        dispatch(
            tool_name="crm_get",
            arguments={"module": "accounts"},
            user_id=1,
        )
    msg = str(exc.value)
    assert "id is required" in msg.lower()
    assert "accounts" in msg


def test_crm_get_tool_description_warns_against_retry():
    """The tool description seen by the LLM must include the do-not-retry
    rule so the steering hits even on the first failure."""
    from hermes.tools.crm.dispatch import _unified_get_tool

    spec = _unified_get_tool(["accounts", "contacts", "leads"])
    desc = spec.description.lower()
    assert "not retry" in desc or "do not retry" in desc
    assert "integer" in desc, "must remind the LLM that ids are integers"


@db_tests
def test_dispatch_enforces_rbac_for_non_admin():
    """A Sales-role user cannot use crm_create_users (users is admin-only)."""
    from hermes.tools.crm.dispatch import (
        ToolPermissionError,
        dispatch,
    )
    from hermes.crm.db import db_session
    from hermes.crm.models.users_db import UsersDb

    username = f"sales_tb_{secrets.token_hex(4)}"
    api_key = secrets.token_hex(24)

    with db_session() as db:
        role_id = db.fetchcolumn(
            "SELECT id FROM roles WHERE name = 'Sales' LIMIT 1"
        )
        assert role_id, "Sales role missing — run seed"

        users = UsersDb(db)
        uid = users.insert(
            {
                "username": username,
                "password": UsersDb.hash_password("pw"),
                "realname": "Sales TB",
                "email": f"{username}@example.test",
                "status": 1,
                "is_admin": False,
                "api_key": api_key,
                "plan": "free",
                "role_id": int(role_id),
            }
        )
        db.commit()

    # Clear the role entirely — a user with NULL role_id falls through the
    # permission check unconditionally, which is the easiest way to exercise
    # the non-admin denial path without relying on seed-specific verb matrices.
    with db_session() as db:
        db.execute("UPDATE users SET role_id = NULL WHERE id = %s", (int(uid),))
        db.commit()

    with pytest.raises(ToolPermissionError):
        dispatch(
            tool_name="crm_create_accounts",
            arguments={"accounts_name": "denied"},
            user_id=int(uid),
        )

    # Cleanup
    with db_session() as db:
        db.execute(
            "UPDATE users SET deleted = TRUE WHERE id = %s", (int(uid),)
        )


# ---------------------------------------------------------------------------
# Agent-sourced dispatch — gateway integration
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profile_id() -> str:
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone("SELECT id FROM profiles LIMIT 1")
    assert row, "profiles table empty — run seed"
    return str(row["id"])


@db_tests
def test_dispatch_agent_source_routes_through_gateway(admin_id, profile_id):
    """When source='agent:...', a create/update dispatches through the
    Action Gateway. ``crm.write`` lives in dispatcher
    ``_FORCE_AUTO_EXECUTE_KINDS``, so even at the low default trust
    level the dispatcher runs it immediately — the gateway is now an
    audit/revert layer, not a human gate. Only egress kinds still queue.
    """
    from hermes.tools.crm.dispatch import dispatch
    from hermes.trust.engine import set_level

    agent = f"probe_{secrets.token_hex(4)}"
    set_level(profile_id, agent, "crm.write", 1)

    suffix = secrets.token_hex(4)
    result = dispatch(
        tool_name="crm_create_accounts",
        arguments={"accounts_name": f"AgentQA-{suffix}", "owner_by": admin_id},
        user_id=admin_id,
        source=f"agent:default/{agent}",
        profile_id=profile_id,
        agent_name=agent,
    )
    # Internal kind → auto-executed; the gateway row is for revert/audit.
    assert result["success"] is True
    assert result["id"] > 0
    assert "action_id" in result

    # Cleanup the created account + gateway row + trust level
    from hermes.crm.db import db_session

    with db_session() as db:
        db.execute(
            "DELETE FROM accounts WHERE accounts_id = %s", (int(result["id"]),),
        )
        db.execute(
            "DELETE FROM trust_decisions WHERE action_id = %s",
            (result["action_id"],),
        )
        db.execute(
            "DELETE FROM gateway_actions WHERE id = %s",
            (result["action_id"],),
        )
        db.execute(
            "DELETE FROM trust_levels WHERE profile_id = %s AND agent_name = %s",
            (profile_id, agent),
        )


@db_tests
def test_dispatch_agent_source_l5_auto_executes(admin_id, profile_id):
    """Trust L5 on the agent → gateway auto-executes, returns success."""
    from hermes.tools.crm.dispatch import dispatch
    from hermes.trust.engine import set_level

    agent = f"probe_{secrets.token_hex(4)}"
    set_level(profile_id, agent, "crm.write", 5)

    suffix = secrets.token_hex(4)
    result = dispatch(
        tool_name="crm_create_accounts",
        arguments={"accounts_name": f"AgentL5-{suffix}", "owner_by": admin_id},
        user_id=admin_id,
        source=f"agent:default/{agent}",
        profile_id=profile_id,
        agent_name=agent,
    )
    assert result["success"] is True
    assert result["id"] > 0
    assert "action_id" in result

    # Cleanup
    from hermes.crm.db import db_session

    with db_session() as db:
        rid = result["id"]
        db.execute(
            "DELETE FROM trust_decisions WHERE action_id = %s",
            (result["action_id"],),
        )
        db.execute(
            "DELETE FROM gateway_actions WHERE id = %s",
            (result["action_id"],),
        )
        db.execute(
            "DELETE FROM accounts WHERE accounts_id = %s", (rid,)
        )
        db.execute(
            "DELETE FROM trust_levels WHERE profile_id = %s AND agent_name = %s",
            (profile_id, agent),
        )


# ---------------------------------------------------------------------------
# Schema-evolution propose tools — the proposal row must REALLY land, and the
# result must carry a ready-to-paste ``user_message`` so the agent quotes a
# real proposal id instead of fabricating one (2026-05-21 false-narration fix).
# ---------------------------------------------------------------------------


@db_tests
def test_propose_picklist_option_lands_row_and_returns_user_message(admin_id):
    from hermes.tools.crm.dispatch import _dispatch_propose_picklist_option
    from hermes.crm.db import db_session

    suffix = secrets.token_hex(4)
    value = f"qa_{suffix}"
    result = _dispatch_propose_picklist_option(
        {
            "module_name": "leads",
            "field_name": "source",
            "value": value,
            "label": f"QA渠道{suffix}",
            "reason": "integration test — verifies the proposal row actually lands",
        },
        user_id=admin_id,
    )
    assert result["success"] is True
    pid = result.get("proposal_id")
    assert isinstance(pid, int) and pid > 0
    # The narration the agent shows the user is sourced from the tool, and
    # must reference the real proposal id — no fabrication possible.
    assert "user_message" in result
    assert str(pid) in result["user_message"]
    try:
        # The row must really exist in field_proposals — the whole point.
        with db_session() as db:
            row = db.fetchone(
                "SELECT status, payload FROM field_proposals WHERE id = %s", (pid,)
            )
        assert row is not None, "proposal id returned but no field_proposals row"
        assert row["status"] == "pending"
    finally:
        with db_session() as db:
            db.execute("DELETE FROM field_proposals WHERE id = %s", (pid,))


@db_tests
def test_propose_picklist_option_failure_has_no_user_message(admin_id):
    """A rejected proposal carries NO user_message — so the skill's
    'quote user_message verbatim' rule leaves the agent with nothing to
    quote, and it cannot claim a proposal was filed."""
    from hermes.tools.crm.dispatch import _dispatch_propose_picklist_option

    result = _dispatch_propose_picklist_option(
        {
            "module_name": "leads",
            "field_name": "source",
            "value": "Bad Value With Spaces",  # fails the snake_case regex
            "label": "x",
            "reason": "integration test — invalid value path",
        },
        user_id=admin_id,
    )
    assert result["success"] is False
    assert "user_message" not in result
    assert "proposal_id" not in result


@db_tests
def test_count_by_status_pending_matches_list(admin_id):
    from hermes.crm.models.field_proposals_db import FieldProposalsDb
    from hermes.crm.db import db_session

    with db_session() as db:
        fp = FieldProposalsDb(db)
        listed = len(fp.list_by_status("pending", limit=500))
        counted = fp.count_by_status("pending")
    assert counted == listed
