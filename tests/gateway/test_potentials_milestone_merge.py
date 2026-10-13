"""Regression — potentials.status merged into milestone.

Fixes the live bug surfaced 2026-05-25 where agent `crm_write` to
``potentials.status`` raised ``UndefinedColumn`` because the column had
been silently dropped while ``seed_metadata.py`` kept declaring the
field. After this PR:

* ``module_fields`` no longer has a ``status`` row for potentials.
* ``milestone`` carries the full 7-stage funnel including ``won`` /
  ``lost`` terminal values.
* ``field_config.system_options`` lists ``[won, lost]`` so frontend can
  render the lock affordance and backend rejects deletion.
* ``pipeline_milestones`` dictionary table is gone.
"""
from __future__ import annotations

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


def test_potentials_status_field_removed():
    """``module_fields`` must not advertise a ``status`` field for
    potentials — agents read this list to know what they can write,
    and the underlying column is gone."""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            """
            SELECT mf.id FROM module_fields mf
              JOIN collections m ON m.id = mf.module_id
             WHERE m.name = 'potentials' AND mf.field_name = 'status'
            """
        )
    assert row is None, "potentials.status field should be removed by 0071"


def test_potentials_milestone_carries_terminal_options():
    """`won` and `lost` must appear in milestone options so the SPA
    dropdown lists them and agents can transition deals to terminal
    states."""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            """
            SELECT mf.options FROM module_fields mf
              JOIN collections m ON m.id = mf.module_id
             WHERE m.name = 'potentials' AND mf.field_name = 'milestone'
            """
        )
    assert row, "milestone field row missing"
    opts = [s.strip() for s in str(row["options"]).split(",") if s.strip()]
    for terminal in ("won", "lost"):
        assert terminal in opts, f"terminal '{terminal}' missing from milestone options: {opts}"


def test_potentials_milestone_field_config_marks_system_options():
    """``field_config.system_options`` is what the frontend and backend
    consult to enforce the lock; without it the lock UI / DELETE guard
    silently allow won/lost to be removed."""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            """
            SELECT mf.field_config FROM module_fields mf
              JOIN collections m ON m.id = mf.module_id
             WHERE m.name = 'potentials' AND mf.field_name = 'milestone'
            """
        )
    assert row and row.get("field_config")
    cfg = row["field_config"]
    sysopts = cfg.get("system_options") or []
    assert "won" in sysopts and "lost" in sysopts, (
        f"system_options must include won and lost; got {sysopts}"
    )


def test_pipeline_milestones_table_dropped():
    from hermes.crm.db import db_session

    with db_session() as db:
        rows = db.fetchall(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = 'pipeline_milestones'"
        )
    assert not rows, "pipeline_milestones should be dropped by 0071"


def test_terminal_milestones_constant_matches_picklist():
    """``hermes.crm.constants.TERMINAL_MILESTONES`` is the single source
    of truth code references — must stay in lock-step with the picklist
    system options."""
    from hermes.crm.constants import TERMINAL_MILESTONES
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            """
            SELECT mf.field_config FROM module_fields mf
              JOIN collections m ON m.id = mf.module_id
             WHERE m.name = 'potentials' AND mf.field_name = 'milestone'
            """
        )
    cfg = row["field_config"]
    sysopts = set(cfg.get("system_options") or [])
    assert set(TERMINAL_MILESTONES) == sysopts, (
        f"TERMINAL_MILESTONES {TERMINAL_MILESTONES} must equal "
        f"system_options {sysopts}"
    )


def test_milestone_options_seed_order_matches_pipeline_flow():
    """The order in module_fields.options drives the SPA dropdown order
    AND drives coach.py's stage-regression detection. Verify it reads
    initial → terminal so the funnel reads naturally."""
    from hermes.crm.db import db_session

    with db_session() as db:
        row = db.fetchone(
            """
            SELECT mf.options FROM module_fields mf
              JOIN collections m ON m.id = mf.module_id
             WHERE m.name = 'potentials' AND mf.field_name = 'milestone'
            """
        )
    opts = [s.strip() for s in str(row["options"]).split(",") if s.strip()]
    expected_order = [
        "initial_contact", "needs_analysis", "proposal_sent",
        "negotiation", "closing", "won", "lost",
    ]
    assert opts == expected_order, f"order drift: got {opts}"
