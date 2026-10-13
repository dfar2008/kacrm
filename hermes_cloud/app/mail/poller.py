"""Background IMAP poller.

Fires on the existing asyncio scheduler. Every ``poll_interval_sec``
wakes up, snapshots the enabled-account list, and for each account:

1. Connect (IMAP4 / IMAP4_SSL) and ``LOGIN``.
2. ``SELECT INBOX`` → ``UID SEARCH UNSEEN``.
3. For each UID (up to ``max_per_poll``):
   a. ``UID FETCH`` RFC822 body.
   b. Parse via :mod:`email`.
   c. Build the payload shape :class:`EmailAdapter` expects and call
      :func:`touchpoints.ingest`.
   d. ``UID STORE +FLAGS \\Seen`` — commit the "we saw this" marker
      only after successful ingest.
4. ``LOGOUT``.

Failures are per-account; one broken mailbox doesn't stop the others.
Every cycle's outcome is persisted on the row (``last_poll_at`` +
``last_error``) so the SPA can show "last sync" state.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import socket
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Module-level for test/debug tuning.
DEFAULT_POLL_INTERVAL_SEC = 300  # 5 min
MAX_PER_POLL = 25  # cap per-account ingest to avoid a huge first sync


def _decode_subject(raw: str) -> str:
    from email.header import decode_header, make_header
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def _extract_text_body(msg: email.message.Message) -> str:
    """Prefer text/plain, fall back to text/html stripped of tags."""
    best: str = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain" and not part.get_filename():
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
                except Exception:
                    continue
            if not best and ctype == "text/html":
                try:
                    payload = part.get_payload(decode=True) or b""
                    charset = part.get_content_charset() or "utf-8"
                    best = payload.decode(charset, errors="replace")
                except Exception:
                    continue
    else:
        try:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except Exception:
            return ""
    # very simple HTML strip
    if best:
        import re as _re
        return _re.sub(r"<[^>]+>", "", best)
    return ""


def _build_email_payload(msg: email.message.Message, *, user_id: int) -> Dict[str, Any]:
    subject = _decode_subject(msg.get("Subject", ""))
    from_raw = msg.get("From", "") or ""
    _, from_addr = parseaddr(from_raw)
    to_raw = msg.get("To", "") or ""
    cc_raw = msg.get("Cc", "") or ""
    message_id = (msg.get("Message-ID") or "").strip()
    date_hdr = msg.get("Date", "")
    try:
        received = parsedate_to_datetime(date_hdr) if date_hdr else None
    except Exception:
        received = None

    body_text = _extract_text_body(msg)

    return {
        "user_id": user_id,
        "subject": subject,
        "body": body_text,
        "from": from_addr or from_raw,
        "from_display": from_raw,
        "to": to_raw,
        "cc": cc_raw,
        "message_id": message_id,
        "date": received.isoformat() if received else date_hdr,
        # Let the email adapter resolve subjects via from/to email lookup.
    }


async def _poll_one_account(
    account,
    *,
    ingest_fn,
    store,
    max_per_poll: int = MAX_PER_POLL,
) -> None:
    """Run one IMAP sweep. Any exception is caught and persisted."""
    loop = asyncio.get_running_loop()

    def _sync_fetch() -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        IMAP = imaplib.IMAP4_SSL if account.imap_use_ssl else imaplib.IMAP4
        conn = IMAP(account.imap_host, account.imap_port, timeout=20)
        try:
            conn.login(account.username, account.password)
            conn.select("INBOX")
            typ, data = conn.uid("SEARCH", None, "UNSEEN")
            if typ != "OK" or not data or not data[0]:
                return []
            uids = data[0].split()[:max_per_poll]
            for uid in uids:
                typ, raw = conn.uid("FETCH", uid, "(RFC822)")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                body = raw[0][1]
                msg = email.message_from_bytes(body)
                payloads.append((uid, _build_email_payload(
                    msg, user_id=account.user_id,
                )))
            return payloads  # type: ignore[return-value]
        finally:
            try:
                conn.logout()
            except Exception:
                pass

    try:
        payloads = await loop.run_in_executor(None, _sync_fetch)
    except (socket.error, imaplib.IMAP4.error) as exc:
        store.mark_poll_result(account.user_id, f"{type(exc).__name__}: {exc}")
        logger.warning("imap_poll_network_error user=%s host=%s: %s",
                       account.user_id, account.imap_host, exc)
        return
    except Exception as exc:
        store.mark_poll_result(account.user_id, f"{type(exc).__name__}: {exc}")
        logger.exception("imap_poll_unexpected user=%s", account.user_id)
        return

    ingested = 0
    for uid, payload in payloads:
        try:
            ingest_fn("email", payload)
            ingested += 1
            # mark as seen on success
            def _mark(uid_=uid) -> None:
                IMAP = imaplib.IMAP4_SSL if account.imap_use_ssl else imaplib.IMAP4
                conn = IMAP(account.imap_host, account.imap_port, timeout=20)
                try:
                    conn.login(account.username, account.password)
                    conn.select("INBOX")
                    conn.uid("STORE", uid_, "+FLAGS", "\\Seen")
                finally:
                    try: conn.logout()
                    except Exception: pass
            # Fire mark as Seen in executor; don't block on it.
            asyncio.create_task(loop.run_in_executor(None, _mark))
        except Exception:
            logger.exception(
                "imap_ingest_failed user=%s subject=%s",
                account.user_id, str(payload.get("subject"))[:80],
            )
    store.mark_poll_result(
        account.user_id,
        None if ingested >= 0 and not payloads else None,
    )
    logger.info("imap_poll_ok user=%s ingested=%d", account.user_id, ingested)


async def run_poller(
    *,
    store,
    ingest_fn,
    secret: str,
    interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
    stop_event: Optional[asyncio.Event] = None,
) -> None:
    """Main loop. Intended to be spawned as an ``asyncio.create_task``.

    Takes ``ingest_fn`` as a parameter so tests can inject a stub
    instead of importing ``touchpoints.ingest`` directly.
    """
    logger.info("imap_poller_started interval=%ds", interval_sec)
    while True:
        if stop_event and stop_event.is_set():
            break
        try:
            accounts = store.list_enabled(secret=secret)
        except Exception:
            logger.exception("imap_poller: list_enabled failed")
            accounts = []
        if accounts:
            await asyncio.gather(
                *(_poll_one_account(a, ingest_fn=ingest_fn, store=store)
                  for a in accounts),
                return_exceptions=True,
            )
        try:
            if stop_event:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
                break
            else:
                await asyncio.sleep(interval_sec)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break
    logger.info("imap_poller_stopped")
