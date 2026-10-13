from __future__ import annotations

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest
from hermes_cloud.app.profile_manager import _apply_user_char_limit


def test_empty_dict():
    result = _apply_user_char_limit({})
    assert result == {"memory": {"user_char_limit": 2750}}


def test_existing_memory_keys_preserved():
    existing = {"memory": {"memory_enabled": True}}
    result = _apply_user_char_limit(existing)
    assert result["memory"]["memory_enabled"] is True
    assert result["memory"]["user_char_limit"] == 2750


def test_existing_user_char_limit_overwritten():
    existing = {"memory": {"user_char_limit": 1375}}
    result = _apply_user_char_limit(existing)
    assert result["memory"]["user_char_limit"] == 2750


def test_non_dict_memory_replaced():
    """memory 值不是 dict(如旧版写成字符串)时应被替换为 dict。"""
    existing = {"memory": "some_string"}
    result = _apply_user_char_limit(existing)
    assert result == {"memory": {"user_char_limit": 2750}}


def test_other_top_level_keys_untouched():
    existing = {"API_SERVER_ENABLED": True, "model": {"default": "claude-3-5-sonnet"}}
    result = _apply_user_char_limit(existing)
    assert result["API_SERVER_ENABLED"] is True
    assert result["model"]["default"] == "claude-3-5-sonnet"
    assert result["memory"]["user_char_limit"] == 2750


# ---------------------------------------------------------------------------
# Task 2 — crm_draft_feedback_collect 工具形状 + 配对纯逻辑
# ---------------------------------------------------------------------------

def _dt(offset_hours: float = 0) -> datetime:
    """返回 UTC aware datetime，offset_hours 相对于 epoch 0。"""
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(hours=offset_hours)


def _make_card(card_id: str, created_at: datetime) -> dict:
    return {"card_id": card_id, "created_at": created_at}


def _make_log(log_id: str, occurred_at: datetime, subjects: list) -> dict:
    return {"log_id": log_id, "occurred_at": occurred_at,
            "subjects": subjects, "content": {}}


# ── ToolSpec 形状 ─────────────────────────────────────────────────────────

def test_draft_feedback_collect_toolspec_shape():
    from hermes.tools.crm.dispatch import _draft_feedback_collect_tool
    spec = _draft_feedback_collect_tool()
    assert spec.name == "crm_draft_feedback_collect"
    assert spec.verb == "view"
    props = spec.input_schema["properties"]
    assert "since_days" in props
    assert spec.input_schema["additionalProperties"] is False


# ── 配对纯逻辑 ────────────────────────────────────────────────────────────

def test_pair_log_to_card():
    """一张卡 + 一条 72h 内、簇内 log → 配上。"""
    from hermes.tools.crm.dispatch import _pair_cards_and_logs
    card = _make_card("c1", _dt(0))
    log = _make_log("l1", _dt(10), [{"module": "accounts", "id": 1}])
    cluster_of = {"c1": {("accounts", 1)}}
    matched = _pair_cards_and_logs([card], [log], cluster_of)
    assert matched["c1"] is not None
    assert matched["c1"]["log_id"] == "l1"
    assert matched["c1"]["_matched_subject_module"] == "accounts"


def test_pair_log_outside_window():
    """log 在卡 created_at + 80h → 超出 72h 窗口,不配。"""
    from hermes.tools.crm.dispatch import _pair_cards_and_logs
    card = _make_card("c1", _dt(0))
    log = _make_log("l1", _dt(80), [{"module": "accounts", "id": 1}])
    cluster_of = {"c1": {("accounts", 1)}}
    matched = _pair_cards_and_logs([card], [log], cluster_of)
    assert matched["c1"] is None


def test_pair_log_outside_cluster():
    """log 的 subject 不在卡簇 → 不配。"""
    from hermes.tools.crm.dispatch import _pair_cards_and_logs
    card = _make_card("c1", _dt(0))
    log = _make_log("l1", _dt(10), [{"module": "accounts", "id": 99}])
    cluster_of = {"c1": {("accounts", 1)}}
    matched = _pair_cards_and_logs([card], [log], cluster_of)
    assert matched["c1"] is None


def test_pair_one_log_one_card():
    """同窗两卡争一条 log → 判给 created_at 最晚那张;另一张 matched 为 None。"""
    from hermes.tools.crm.dispatch import _pair_cards_and_logs
    early_card = _make_card("c_early", _dt(0))
    late_card = _make_card("c_late", _dt(5))  # created_at 更晚
    log = _make_log("l1", _dt(20), [{"module": "accounts", "id": 1}])
    cluster_of = {
        "c_early": {("accounts", 1)},
        "c_late":  {("accounts", 1)},
    }
    matched = _pair_cards_and_logs([early_card, late_card], [log], cluster_of)
    # 应配给 created_at 最晚的卡
    assert matched["c_late"] is not None
    assert matched["c_late"]["log_id"] == "l1"
    assert matched["c_early"] is None


# ── 集成测试(真实 PG;无 PG 自动 skip) ────────────────────────────────────

def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session
        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


_pg = pytest.mark.skipif(not _crm_reachable(), reason="hermes_crm_dev not reachable")


@pytest.fixture(scope="module")
def _profile_id() -> str:
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone("SELECT id FROM profiles LIMIT 1")
    assert row, "profiles 表为空 — 先 seed"
    return str(row["id"])


@pytest.fixture(scope="module")
def _admin_id() -> int:
    from hermes.crm.db import db_session
    with db_session() as db:
        row = db.fetchone(
            "SELECT id FROM users WHERE username = 'admin' LIMIT 1")
    assert row, "admin 用户缺失 — 先 seed"
    return int(row["id"])


@_pg
def test_e2e_collect(_profile_id, _admin_id):
    """真实造 coach 卡 + contact_log，验证配对后清理。"""
    import secrets
    from hermes.crm.db import db_session
    from hermes.tools.crm.dispatch import dispatch

    tag = secrets.token_hex(4)
    action_id = None
    interaction_id = None
    account_id = None

    with db_session() as db:
        # 造一个真实 account
        acc_row = db.fetchone(
            "INSERT INTO accounts (accounts_name) VALUES (%s) RETURNING accounts_id",
            (f"DFC-test-{tag}",),
        )
        account_id = int(acc_row["accounts_id"])

        # 造 coach.proposal 卡(params 含 action.type='draft_message')
        ga_row = db.fetchone(
            """
            INSERT INTO gateway_actions
                (kind, profile_id, state, target, params, created_at)
            VALUES (
                'coach.proposal', %s, 'executed',
                %s::jsonb,
                %s::jsonb,
                now() - interval '1 hour'
            )
            RETURNING id
            """,
            (
                _profile_id,
                f'{{"module":"accounts","id":{account_id}}}',
                '{"severity":"high","action":{"type":"draft_message",'
                '"draft":"你好，请问方便聊聊吗？","intended_channel":"wecom"}}',
            ),
        )
        action_id = str(ga_row["id"])

        # 造 contact_log(occurred_at 在卡之后 1 小时,subjects 指同一 account)
        inter_row = db.fetchone(
            """
            INSERT INTO interactions
                (kind, source, subjects, content, actors, occurred_at)
            VALUES (
                'contact_log', 'manual',
                %s::jsonb,
                %s::jsonb,
                %s::jsonb,
                now()
            )
            RETURNING id
            """,
            (
                f'[{{"module":"accounts","id":{account_id}}}]',
                '{"channel":"wecom","summary":"已沟通","body":"通话5分钟"}',
                f'[{{"type":"user","id":{_admin_id}}}]',
            ),
        )
        interaction_id = str(inter_row["id"])

    try:
        out = dispatch(
            tool_name="crm_draft_feedback_collect",
            arguments={"since_days": 14},
            user_id=_admin_id,
            source="agent:test",
            profile_id=_profile_id,
            agent_name="t",
        )
        assert out.get("success") is True, out
        cards = out["cards"]
        # 找我们造的那张卡
        our_card = next((c for c in cards if c["card_id"] == action_id), None)
        assert our_card is not None, f"造的卡 {action_id} 不在返回列表中"
        assert our_card["matched_contact_log"] is not None, \
            f"expected matched_contact_log, got None. card={our_card}"
        ml = our_card["matched_contact_log"]
        assert ml["channel"] == "wecom"
        assert ml["summary"] == "已沟通"
    finally:
        # 清理
        with db_session() as db:
            if action_id:
                db.execute(
                    "DELETE FROM gateway_actions WHERE id = %s",
                    (action_id,),
                )
            if interaction_id:
                db.execute(
                    "DELETE FROM interactions WHERE id = %s",
                    (interaction_id,),
                )
            if account_id:
                db.execute(
                    "DELETE FROM accounts WHERE accounts_id = %s",
                    (account_id,),
                )


@_pg
def test_e2e_collect_lead_card_via_leads_id(_profile_id, _admin_id):
    """卡 target 是 lead,contact_log 登记在该 lead 转化出的 account 上 →
    采集工具经 accounts.leads_id 桥接把 log 配给这张卡。"""
    import secrets
    from hermes.crm.db import db_session
    from hermes.tools.crm.dispatch import dispatch

    tag = secrets.token_hex(4)
    lead_id = None
    account_id = None
    action_id = None
    interaction_id = None

    with db_session() as db:
        # 1. 造 lead(标记为 converted)
        lead_row = db.fetchone(
            "INSERT INTO leads (leads_name, owner_by, convertedstatus) "
            "VALUES (%s, %s, 'converted') RETURNING leads_id",
            (f"DFC-lead-{tag}", _admin_id),
        )
        lead_id = int(lead_row["leads_id"])

        # 2. 造 account,带 leads_id 回指该 lead(模拟 crm_convert_lead 结果)
        acc_row = db.fetchone(
            "INSERT INTO accounts (accounts_name, owner_by, leads_id) "
            "VALUES (%s, %s, %s) RETURNING accounts_id",
            (f"DFC-acc-{tag}", _admin_id, lead_id),
        )
        account_id = int(acc_row["accounts_id"])

        # 3. 造 coach.proposal 卡,target 指向 lead(而非 account)
        ga_row = db.fetchone(
            """
            INSERT INTO gateway_actions
                (kind, profile_id, state, target, params, created_at)
            VALUES (
                'coach.proposal', %s, 'executed',
                %s::jsonb,
                %s::jsonb,
                now() - interval '2 hours'
            )
            RETURNING id
            """,
            (
                _profile_id,
                f'{{"module":"leads","id":{lead_id}}}',
                '{"severity":"medium","action":{"type":"draft_message",'
                '"draft":"测试草稿","intended_channel":"wechat"}}',
            ),
        )
        action_id = str(ga_row["id"])

        # 4. 造 contact_log,登记在 account 上(occurred_at 在卡后 1 小时)
        inter_row = db.fetchone(
            """
            INSERT INTO interactions
                (kind, source, subjects, content, actors, occurred_at)
            VALUES (
                'contact_log', 'manual',
                %s::jsonb,
                %s::jsonb,
                %s::jsonb,
                now() - interval '1 hour'
            )
            RETURNING id
            """,
            (
                f'[{{"module":"accounts","id":{account_id}}}]',
                '{"summary":"打了电话","channel":"phone"}',
                f'[{{"type":"user","id":{_admin_id}}}]',
            ),
        )
        interaction_id = str(inter_row["id"])

    try:
        out = dispatch(
            tool_name="crm_draft_feedback_collect",
            arguments={"since_days": 14},
            user_id=_admin_id,
            source="agent:test",
            profile_id=_profile_id,
            agent_name="t",
        )
        assert out.get("success") is True, out
        cards = out["cards"]

        our_card = next((c for c in cards if c["card_id"] == action_id), None)
        assert our_card is not None, \
            f"造的卡 {action_id} 不在返回列表中"

        ml = our_card["matched_contact_log"]
        assert ml is not None, \
            (f"expected matched_contact_log via accounts.leads_id bridge, "
             f"got None. card={our_card}")
        # 配对命中的 subject 是 account(经 leads_id 桥接)
        assert ml["subject_module"] == "accounts", \
            f"expected subject_module='accounts', got {ml['subject_module']!r}"
    finally:
        with db_session() as db:
            if action_id:
                db.execute(
                    "DELETE FROM gateway_actions WHERE id = %s",
                    (action_id,),
                )
            if interaction_id:
                db.execute(
                    "DELETE FROM interactions WHERE id = %s",
                    (interaction_id,),
                )
            if account_id:
                db.execute(
                    "DELETE FROM accounts WHERE accounts_id = %s",
                    (account_id,),
                )
            if lead_id:
                db.execute(
                    "DELETE FROM leads WHERE leads_id = %s",
                    (lead_id,),
                )


# ── methodology_skill 透传(Task 9) ────────────────────────────────────────

def _fake_db_with_card(params_action: dict) -> MagicMock:
    """造一个最小可用的 fake db,模拟 _dispatch_draft_feedback_collect 内部
    需要的 fetchall/fetchone 调用序列:
      1. 第 1 次 fetchall → coach.proposal 卡列表
      2. 第 2 次 fetchall → contact_log 列表(空)
      3. 后续 fetchone/fetchall → 簇构建相关(target=accounts,无 potentials/contacts)
    """
    db = MagicMock()
    card_row = {
        "card_id": "card-abc",
        "target": {"module": "accounts", "id": 1},
        "params": {"severity": "high", "action": params_action},
        "state": "executed",
        "reject_reason_type": None,
        "created_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
    }
    # fetchall 调用序列:cards, logs, then potentials/contacts (空)
    db.fetchall.side_effect = [
        [card_row],   # coach.proposal
        [],           # contact_log
        [],           # potentials (cluster build)
        [],           # contacts (cluster build)
    ]
    # fetchone 用于 leads_id 查询 → 无 lead
    db.fetchone.return_value = {"leads_id": None}
    return db


def test_draft_feedback_collect_includes_methodology_skill():
    """A 信号采集必须把 gateway_actions.params.action.methodology_skill 透传给蒸馏。

    Task 8 起 daily-coach emit 的 coach.proposal 卡 params.action 含
    methodology_skill 字段。采集工具必须把这个值原样附在每条 batch 记录的
    methodology_skill 字段上,蒸馏 prompt 才能据此分层学习。
    """
    from hermes.tools.crm.dispatch import _dispatch_draft_feedback_collect

    db = _fake_db_with_card({
        "type": "draft_message",
        "draft": "你好",
        "intended_channel": "wecom",
        "methodology_skill": "crm-sales-meddpicc",
    })

    out = _dispatch_draft_feedback_collect(
        {"since_days": 14},
        user_id=1,
        profile_id="p-1",
        db=db,
    )

    assert out.get("success") is True, out
    cards = out["cards"]
    assert len(cards) == 1
    assert cards[0]["methodology_skill"] == "crm-sales-meddpicc"


def test_draft_feedback_collect_methodology_skill_null_when_legacy():
    """老 card 没该字段时,采集返回 None(不报错)。

    Task 8 上线前的 coach.proposal 卡 params.action 不含 methodology_skill。
    采集工具必须返回 methodology_skill=None,而非抛 KeyError 或漏字段。
    """
    from hermes.tools.crm.dispatch import _dispatch_draft_feedback_collect

    db = _fake_db_with_card({
        "type": "draft_message",
        "draft": "老卡",
        "intended_channel": "wecom",
        # 注意: 故意不含 methodology_skill
    })

    out = _dispatch_draft_feedback_collect(
        {"since_days": 14},
        user_id=1,
        profile_id="p-1",
        db=db,
    )

    assert out.get("success") is True, out
    cards = out["cards"]
    assert len(cards) == 1
    assert "methodology_skill" in cards[0], \
        "legacy 卡也必须有 methodology_skill key,值为 None"
    assert cards[0]["methodology_skill"] is None


def test_distill_job_in_default_jobs():
    from hermes_cloud.app.cron_provisioner import DEFAULT_JOBS
    specs = [j for j in DEFAULT_JOBS if j["name"] == "crm-draft-style-distill"]
    assert len(specs) == 1
    spec = specs[0]
    assert spec["schedule"] == "0 23 * * sun"
    assert spec["skill"] == "crm-draft-style-distill"
    assert spec["requires_user_id"] is True
    assert spec["deliver"] == ""


def test_build_job_distill():
    from hermes_cloud.app.cron_provisioner import DEFAULT_JOBS, _build_job
    spec = next(j for j in DEFAULT_JOBS if j["name"] == "crm-draft-style-distill")
    job = _build_job(spec, user_id=123)
    assert job["name"] == "crm-draft-style-distill"
    assert job["skills"] == ["crm-draft-style-distill"]
    assert job["user_id"] == 123


@_pg
def test_e2e_distill_tool_in_catalogue():
    """crm_draft_feedback_collect 出现在 build_crm_tools 工具目录里。"""
    from hermes.tools.crm.dispatch import build_crm_tools
    names = {t.name for t in build_crm_tools()}
    assert "crm_draft_feedback_collect" in names
