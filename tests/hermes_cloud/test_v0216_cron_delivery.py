"""Exercise the extracted cron delivery path without network or profile writes."""
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "rendered,expected,error",
    [("coach cards", ["coach cards"], False), (None, [], False),
     (RuntimeError("renderer failed"), [], True)],
)
def test_rendered_delivery_replaces_agent_text(monkeypatch, rendered, expected, error):
    from cron import scheduler

    def render(job):
        if isinstance(rendered, Exception):
            raise rendered
        return rendered

    monkeypatch.setitem(scheduler._DELIVER_RENDERERS, "test", render)
    monkeypatch.setattr(scheduler, "save_job_output", lambda *args: None)
    monkeypatch.setattr(scheduler, "_is_interrupted", lambda *args: False)
    monkeypatch.setattr(scheduler, "_compose_run_delivery",
                        lambda *args, **kwargs: ("agent free text", False, False, False, None))
    send = Mock(return_value=None)
    monkeypatch.setattr(scheduler, "_deliver_result", send)
    job = {"id": "test", "deliver_render": "test", "deliver": "none"}
    outcome = scheduler._RunDelivery(job=job, success=True, error=None)

    scheduler._save_compose_deliver(
        outcome, scheduler._FireOwnership(job), "agent free text", "audit",
        adapters=None, loop=None, verbose=False, execution_token=None,
    )

    assert [call.args[1] for call in send.call_args_list] == expected
    assert bool(outcome.delivery_error) is error
    assert outcome.success is True
