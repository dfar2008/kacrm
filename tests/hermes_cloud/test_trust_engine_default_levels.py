from __future__ import annotations


class _NoRowDb:
    def fetchone(self, sql, args):  # noqa: ARG002
        return None


def test_default_level_for_crm_write_is_3():
    from hermes.trust.engine import get_level

    assert get_level("p", "a", "crm.write", db=_NoRowDb()) == 3


def test_default_level_for_memory_write_is_3():
    from hermes.trust.engine import get_level

    assert get_level("p", "a", "memory.write", db=_NoRowDb()) == 3


def test_default_level_for_task_create_is_3():
    from hermes.trust.engine import get_level

    assert get_level("p", "a", "task.create", db=_NoRowDb()) == 3


def test_default_level_for_workflow_submit_is_2():
    # agent-pool v2.2 plan §9: workflow.submit auto-executes only when
    # risk='low'.
    from hermes.trust.engine import get_level

    assert get_level("p", "a", "workflow.submit", db=_NoRowDb()) == 2


def test_default_level_for_email_send_is_1():
    from hermes.trust.engine import get_level

    assert get_level("p", "a", "email.send", db=_NoRowDb()) == 1


def test_default_level_for_crm_delete_is_0():
    from hermes.trust.engine import get_level

    assert get_level("p", "a", "crm.delete", db=_NoRowDb()) == 0


def test_unknown_action_kind_defaults_to_1():
    from hermes.trust.engine import get_level

    assert get_level("p", "a", "unknown.kind", db=_NoRowDb()) == 1
