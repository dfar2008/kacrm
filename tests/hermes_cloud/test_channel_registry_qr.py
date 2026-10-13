from hermes_cloud.app.channel_registry import CHANNEL_REGISTRY


def test_dingtalk_qr_and_stream_fields():
    spec = CHANNEL_REGISTRY["dingtalk"]
    assert spec.supports_qr is True
    assert spec.required_fields == ["client_id", "client_secret"]
    assert "client_secret" in spec.secret_fields
    spec.validate({"client_id": "abc", "client_secret": "xyz"})


def test_wecom_qr_and_stream_fields():
    spec = CHANNEL_REGISTRY["wecom"]
    assert spec.supports_qr is True
    assert spec.required_fields == ["bot_id", "secret"]
    assert "secret" in spec.secret_fields
    spec.validate({"bot_id": "bot123", "secret": "s3cr3t"})
