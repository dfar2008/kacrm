"""Regression — coach scanner uses TERMINAL_MILESTONES constant after
the pipeline_milestones dictionary table was dropped.

Two flavours:

* ``scan_user`` must not surface deals whose milestone is ``won`` /
  ``lost`` (they're closed; coach has nothing to nudge on).
* Stage-regression detection must work when the only source of order
  is the live ``module_fields.options`` string (the old
  ``pipeline_milestones.sort_order`` is gone).
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _crm_reachable() -> bool:
    try:
        from hermes.crm.db import db_session

        with db_session() as db:
            db.fetchcolumn("SELECT 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _crm_reachable(), reason="hermes_crm_dev not reachable"
)


def _seed_potential(db, owner_by: int, name: str, milestone: str) -> int:
    row = db.fetchone(
        """
        INSERT INTO potentials (potentials_name, owner_by, milestone,
                                created_by, updated_by, amount)
        VALUES (%s, %s, %s, %s, %s, 0)
        RETURNING potentials_id
        """,
        (name, owner_by, milestone, owner_by, owner_by),
    )
    return int(row["potentials_id"])


def test_scan_user_excludes_won_lost_potentials():
    """The terminal filter sits in the WHERE clause and uses
    TERMINAL_MILESTONES — without it, every closed deal would surface
    as a 'silent' lead and drown out actual signal."""
    from hermes.crm.db import db_session
    from hermes.crm.insights.scanners.coach import scan_user

    with db_session() as db:
        admin_id = int(
            db.fetchone("SELECT id FROM users WHERE username='admin'")["id"]
        )

    suffix = secrets.token_hex(3)
    created: list[int] = []
    try:
        with db_session() as db:
            created.append(_seed_potential(
                db, admin_id, f"coach-active-{suffix}", "negotiation",
            ))
            created.append(_seed_potential(
                db, admin_id, f"coach-won-{suffix}", "won",
            ))
            created.append(_seed_potential(
                db, admin_id, f"coach-lost-{suffix}", "lost",
            ))

        with db_session() as db:
            hints = scan_user(db, admin_id)

        # Hints reference record_id; ensure the won/lost ones never show.
        seen_ids = {h.get("record_id") for h in hints}
        assert created[1] not in seen_ids, "won deal should be filtered out"
        assert created[2] not in seen_ids, "lost deal should be filtered out"
    finally:
        with db_session() as db:
            for pid in created:
                db.execute(
                    "DELETE FROM potentials WHERE potentials_id = %s", (pid,)
                )


def test_milestone_sort_index_reads_from_module_fields():
    """The new ``_milestone_sort_index`` helper is what stage-regression
    detection uses now that ``pipeline_milestones.sort_order`` is gone.
    Verify it reads the live picklist order and produces a 0-based map
    matching the seeded funnel."""
    from hermes.crm.db import db_session
    from hermes.crm.insights.scanners.coach import _milestone_sort_index

    with db_session() as db:
        idx = _milestone_sort_index(db)

    assert idx.get("initial_contact") == 0
    assert idx.get("negotiation") < idx.get("closing")
    assert idx.get("closing") < idx.get("won")  # terminals sit at the end


def test_stage_regression_detected_via_module_fields_order():
    """End-to-end: a deal that moves backward in the funnel triggers a
    'stage_regression' hint based on options-string order, not on the
    dead ``pipeline_milestones`` table."""
    from hermes.crm.db import db_session
    from hermes.crm.insights.scanners.coach import scan_user

    with db_session() as db:
        admin_id = int(
            db.fetchone("SELECT id FROM users WHERE username='admin'")["id"]
        )
        mod_id = int(
            db.fetchone("SELECT id FROM collections WHERE name='potentials'")["id"]
        )

    suffix = secrets.token_hex(3)
    pid = None
    try:
        with db_session() as db:
            pid = _seed_potential(
                db, admin_id, f"coach-regression-{suffix}", "needs_analysis",
            )
            # Simulate the regression as if it just happened: log a
            # changelog row showing 'negotiation' (later) → 'needs_analysis'
            # (earlier). We bypass Save here because we want a clean
            # synthetic audit row without all the surrounding metadata.
            db.execute(
                """
                INSERT INTO changelog
                    (module_id, data_id, field_name, field_label,
                     field_type, old_value, new_value, updated_by)
                VALUES (%s, %s, 'milestone', 'Milestone', 'select',
                        'negotiation', 'needs_analysis', %s)
                """,
                (mod_id, pid, admin_id),
            )

        with db_session() as db:
            hints = scan_user(db, admin_id)

        regression = next(
            (h for h in hints
             if h.get("signal") == "stage_regression"
                and h.get("record_id") == pid),
            None,
        )
        assert regression is not None, (
            "stage_regression hint not emitted for needs_analysis ← negotiation"
        )
        assert regression["payload"]["from_milestone"] == "negotiation"
        assert regression["payload"]["to_milestone"] == "needs_analysis"
    finally:
        with db_session() as db:
            if pid:
                db.execute(
                    "DELETE FROM changelog WHERE data_id = %s "
                    "AND module_id IN (SELECT id FROM collections WHERE name='potentials')",
                    (pid,),
                )
                db.execute(
                    "DELETE FROM potentials WHERE potentials_id = %s", (pid,)
                )


def test_stage_regression_to_terminal_is_not_a_regression():
    """Moving TO won/lost from any earlier stage is closing the deal,
    not regressing it — the scanner must explicitly exclude this."""
    from hermes.crm.db import db_session
    from hermes.crm.insights.scanners.coach import scan_user

    with db_session() as db:
        admin_id = int(
            db.fetchone("SELECT id FROM users WHERE username='admin'")["id"]
        )
        mod_id = int(
            db.fetchone("SELECT id FROM collections WHERE name='potentials'")["id"]
        )

    suffix = secrets.token_hex(3)
    pid = None
    try:
        with db_session() as db:
            pid = _seed_potential(
                db, admin_id, f"coach-no-regress-{suffix}", "won",
            )
            db.execute(
                """
                INSERT INTO changelog
                    (module_id, data_id, field_name, field_label,
                     field_type, old_value, new_value, updated_by)
                VALUES (%s, %s, 'milestone', 'Milestone', 'select',
                        'negotiation', 'won', %s)
                """,
                (mod_id, pid, admin_id),
            )

        with db_session() as db:
            hints = scan_user(db, admin_id)

        regression = next(
            (h for h in hints
             if h.get("signal") == "stage_regression"
                and h.get("record_id") == pid),
            None,
        )
        assert regression is None, (
            "moving to won is closing the deal, not a regression"
        )
    finally:
        with db_session() as db:
            if pid:
                db.execute(
                    "DELETE FROM changelog WHERE data_id = %s "
                    "AND module_id IN (SELECT id FROM collections WHERE name='potentials')",
                    (pid,),
                )
                db.execute(
                    "DELETE FROM potentials WHERE potentials_id = %s", (pid,)
                )
