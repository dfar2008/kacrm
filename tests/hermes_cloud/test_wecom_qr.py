import pytest
import httpx
from hermes_cloud.app.qr import wecom_qr


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def raise_for_status(self):
        pass
    def json(self):
        return self._payload


class _FakeErrResp:
    """Simulates a response where raise_for_status() raises HTTPStatusError."""
    def __init__(self, status_code: int = 403):
        self._status_code = status_code
    def raise_for_status(self):
        req = httpx.Request("GET", "https://x")
        resp = httpx.Response(self._status_code, request=req)
        raise httpx.HTTPStatusError(
            f"{self._status_code}", request=req, response=resp
        )
    def json(self):
        return {}


class _FakeClient:
    def __init__(self, payload):
        self._payload = payload
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def get(self, url, headers=None):
        return _FakeResp(self._payload)


class _FakeErrClient:
    """Client whose GET always raises HTTPStatusError via raise_for_status."""
    def __init__(self, status_code: int = 403):
        self._status_code = status_code
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def get(self, url, headers=None):
        return _FakeErrResp(self._status_code)


@pytest.mark.asyncio
async def test_qr_start_returns_native_auth_url(monkeypatch):
    # qr_url MUST be the generate response's auth_url (the for_native deep
    # link the WeCom app scans directly) — NOT the …/ai/qc/gen web page,
    # which would render a second QR ("码套码").
    native = "https://work.weixin.qq.com/ai/qc/c?s=SC1&hide_more_btn=true&for_native=true"
    monkeypatch.setattr(
        wecom_qr.httpx, "AsyncClient",
        lambda *a, **k: _FakeClient({"data": {"scode": "SC1", "auth_url": native}}),
    )
    out = await wecom_qr.qr_start()
    assert out["scode"] == "SC1"
    assert out["qr_url"] == native
    assert "/ai/qc/gen" not in out["qr_url"]


@pytest.mark.asyncio
async def test_qr_poll_completed(monkeypatch):
    monkeypatch.setattr(
        wecom_qr.httpx, "AsyncClient",
        lambda *a, **k: _FakeClient(
            {"data": {"status": "success", "bot_info": {"botid": "B1", "secret": "S1"}}}),
    )
    out = await wecom_qr.qr_poll_once("SC1")
    assert out == {"status": "completed", "creds": {"bot_id": "B1", "secret": "S1"}}


@pytest.mark.asyncio
async def test_qr_poll_pending(monkeypatch):
    monkeypatch.setattr(
        wecom_qr.httpx, "AsyncClient",
        lambda *a, **k: _FakeClient({"data": {"status": "waiting"}}),
    )
    out = await wecom_qr.qr_poll_once("SC1")
    assert out["status"] == "pending"


# Fix 1: HTTP 4xx must return error, not pending
@pytest.mark.asyncio
async def test_qr_poll_http_error_returns_error(monkeypatch):
    monkeypatch.setattr(
        wecom_qr.httpx, "AsyncClient",
        lambda *a, **k: _FakeErrClient(403),
    )
    out = await wecom_qr.qr_poll_once("SC1")
    assert out["status"] == "error"
    assert "403" in out.get("message", "")


# Fix 2: success status but missing creds → error
@pytest.mark.asyncio
async def test_qr_poll_missing_creds_returns_error(monkeypatch):
    monkeypatch.setattr(
        wecom_qr.httpx, "AsyncClient",
        lambda *a, **k: _FakeClient({"data": {"status": "success", "bot_info": {}}}),
    )
    out = await wecom_qr.qr_poll_once("SC1")
    assert out["status"] == "error"


# Fix 3: qr_start raises WeComQRError on malformed response (no scode/auth_url)
@pytest.mark.asyncio
async def test_qr_start_malformed_raises(monkeypatch):
    monkeypatch.setattr(
        wecom_qr.httpx, "AsyncClient",
        lambda *a, **k: _FakeClient({"data": {}}),
    )
    with pytest.raises(wecom_qr.WeComQRError):
        await wecom_qr.qr_start()
