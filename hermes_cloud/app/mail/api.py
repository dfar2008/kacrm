"""Mail-account API — ``/api/mail`` GET/PUT/DELETE + ``/api/mail/test``.

Session-authenticated (dashboard cookie). Users can only read/write
their own row.

``/test`` exercises the IMAP/SMTP credentials against the real servers
with a tight timeout and returns a pair of ok/error flags. Used by the
SPA's "测试连接" button so users find bad configs before the poller
silently logs them in ``last_error``.
"""
from __future__ import annotations

import asyncio
import imaplib
import logging
import smtplib
import socket
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from hermes_cloud.app.auth import require_dashboard_auth
from hermes_cloud.app.config import AppConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mail", tags=["mail"])


def _auth_user_id(request: Request) -> int:
    cfg = getattr(request.app.state, "config", None) or AppConfig()
    user_id, _ = require_dashboard_auth(request, cfg)
    try:
        return int(user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="user id must be numeric")


def _store(request: Request):
    store = getattr(request.app.state, "mail_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="mail store unavailable")
    return store


def _cfg_secret(request: Request) -> str:
    cfg = getattr(request.app.state, "config", None) or AppConfig()
    return cfg.internal_key


def _account_to_dict(account) -> Dict[str, Any]:
    """Serialize for the SPA. Never leaks the password ciphertext."""
    return {
        "username": account.username,
        "imap_host": account.imap_host,
        "imap_port": account.imap_port,
        "imap_use_ssl": account.imap_use_ssl,
        "imap_enabled": account.imap_enabled,
        "smtp_host": account.smtp_host,
        "smtp_port": account.smtp_port,
        "smtp_use_tls": account.smtp_use_tls,
        "has_password": account.has_password,
        "last_poll_at": account.last_poll_at.isoformat() if account.last_poll_at else None,
        "last_error": account.last_error,
    }


@router.get("")
def get_mail(request: Request):
    user_id = _auth_user_id(request)
    account = _store(request).get_for_user(user_id)
    return {"data": _account_to_dict(account) if account else None}


def _port_or_default(raw: Any, default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except Exception:
        raise HTTPException(status_code=400, detail=f"port must be an integer, got {raw!r}")
    if not (1 <= v <= 65535):
        raise HTTPException(status_code=400, detail="port must be 1..65535")
    return v


@router.put("")
async def put_mail(request: Request):
    user_id = _auth_user_id(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be JSON object")

    username = str(body.get("username") or "").strip()
    imap_host = str(body.get("imap_host") or "").strip()
    if not username or not imap_host:
        raise HTTPException(
            status_code=400,
            detail="username and imap_host are required",
        )

    imap_port = _port_or_default(body.get("imap_port"), 993)
    imap_use_ssl = bool(body.get("imap_use_ssl", True))
    imap_enabled = bool(body.get("imap_enabled", True))

    smtp_host_raw = body.get("smtp_host")
    smtp_host = str(smtp_host_raw).strip() if smtp_host_raw else None
    smtp_port = _port_or_default(body.get("smtp_port"), 465) if smtp_host else None
    smtp_use_tls = bool(body.get("smtp_use_tls", True))

    # Empty string = keep existing. Explicit null also keeps.
    password = body.get("password")
    if password is not None:
        password = str(password)
        if password == "":
            password = None

    account = _store(request).upsert(
        user_id=user_id,
        username=username,
        password=password,
        imap_host=imap_host,
        imap_port=imap_port,
        imap_use_ssl=imap_use_ssl,
        imap_enabled=imap_enabled,
        smtp_host=smtp_host,
        smtp_port=smtp_port,
        smtp_use_tls=smtp_use_tls,
        secret=_cfg_secret(request),
    )
    return {"data": _account_to_dict(account)}


@router.delete("")
def delete_mail(request: Request):
    user_id = _auth_user_id(request)
    ok = _store(request).delete(user_id)
    return {"data": {"deleted": ok}}


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------

_TEST_TIMEOUT = 10  # seconds


def _test_imap(host: str, port: int, use_ssl: bool, username: str, password: str) -> str:
    """Return '' on success, error message otherwise."""
    try:
        IMAP = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
        conn = IMAP(host, port, timeout=_TEST_TIMEOUT)
        try:
            conn.login(username, password)
            conn.select("INBOX")
        finally:
            try:
                conn.logout()
            except Exception:
                pass
    except (socket.error, imaplib.IMAP4.error) as exc:
        return f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


def _test_smtp(host: str, port: int, use_tls: bool, username: str, password: str) -> str:
    try:
        if use_tls and port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=_TEST_TIMEOUT)
        else:
            srv = smtplib.SMTP(host, port, timeout=_TEST_TIMEOUT)
            srv.ehlo()
            if use_tls:
                srv.starttls()
                srv.ehlo()
        try:
            srv.login(username, password)
        finally:
            try:
                srv.quit()
            except Exception:
                pass
    except (socket.error, smtplib.SMTPException) as exc:
        return f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return ""


@router.post("/test")
async def test_mail(request: Request):
    """Exercise the stored credentials against IMAP and/or SMTP.

    Returns ``{data: {imap: {ok, error?}, smtp: {ok, error?} | null}}``.
    SMTP block is ``null`` if the user didn't configure a smtp_host.
    Skipped entirely (``{skipped: true}``) when no password is stored.
    """
    user_id = _auth_user_id(request)
    secret = _cfg_secret(request)
    account = _store(request).get_for_user(user_id, with_password=True, secret=secret)
    if account is None:
        raise HTTPException(status_code=404, detail="no mail account configured")
    if not account.password:
        return {"data": {"skipped": True, "reason": "no password stored"}}

    loop = asyncio.get_running_loop()
    imap_err = await loop.run_in_executor(
        None,
        _test_imap,
        account.imap_host, account.imap_port, account.imap_use_ssl,
        account.username, account.password,
    )
    imap_result = {"ok": not imap_err, **({"error": imap_err} if imap_err else {})}

    smtp_result = None
    if account.smtp_host and account.smtp_port:
        smtp_err = await loop.run_in_executor(
            None,
            _test_smtp,
            account.smtp_host, account.smtp_port, account.smtp_use_tls,
            account.username, account.password,
        )
        smtp_result = {"ok": not smtp_err, **({"error": smtp_err} if smtp_err else {})}

    return {"data": {"imap": imap_result, "smtp": smtp_result}}
