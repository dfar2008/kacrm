"""§2.5.C — every dangerous tool must register with human_only=True.

The list below is the source of truth for what's "dangerous" — if a
new tool joins one of these plugins, it goes here.
"""
from hermes.tools.crm.dispatch import HUMAN_ONLY_TOOLS, bootstrap_plugins


_EXPECTED_HUMAN_ONLY = {
    # CRM gateway-state mutators (§2.5.C step 2)
    "crm_approve_action", "crm_reject_action",
    "crm_approve_chain_step", "crm_reject_chain_step",
    "crm_set_approval_chain", "crm_remove_approval_chain",
    "crm_transfer_approval", "crm_back_approval",
    "crm_set_field_writeback_rule", "crm_remove_field_writeback_rule",
}


def test_human_only_set_covers_crm_gateway_mutators():
    bootstrap_plugins()
    missing = _EXPECTED_HUMAN_ONLY - HUMAN_ONLY_TOOLS
    assert not missing, f"these CRM tools must be human_only: {missing}"


def test_human_only_set_covers_cronjob_tools():
    # cronjob is shipped by hermes/tools/cronjob_tools.py (not the
    # hermes_cloud.plugins path) and is hardcoded into HUMAN_ONLY_TOOLS
    # by bootstrap_plugins(). §2.5.C: a specialist must not be able to
    # rewrite its own schedule.
    bootstrap_plugins()
    assert "cronjob" in HUMAN_ONLY_TOOLS, "cronjob must be human_only (§2.5.C)"
