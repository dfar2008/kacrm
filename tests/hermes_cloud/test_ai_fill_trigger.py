from hermes_cloud.app.bridge import events


def test_ai_source_does_not_retrigger():
    assert events._should_dispatch_ai_fill("agent:ai-attr:default") is False


def test_normal_sources_dispatch():
    assert events._should_dispatch_ai_fill("manual") is True
    assert events._should_dispatch_ai_fill("feishu:bot") is True
    assert events._should_dispatch_ai_fill("agent:other:x") is True


def test_workflow_source_does_not_retrigger():
    # 防 ai→workflow→ai 级联:workflow 写不再触发 AI 填值
    assert events._should_dispatch_ai_fill("workflow") is False
