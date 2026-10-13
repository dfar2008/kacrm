"""FastAPI router for authenticated channel callbacks.

Mounted at ``/api/channels``. Flow per request:

1. Look up :class:`ChannelReceiver` by ``{source}`` path segment.
2. Load per-channel config off :class:`AppConfig` via
   :meth:`ChannelReceiver.load_config`.
3. Run signature / encryption verification (raises
   :class:`ChannelVerificationError` → HTTP 401).
4. If it was a URL-verification challenge, return the receiver's
   challenge response (JSON or plaintext for WeCom echostr).
5. Otherwise hand the decrypted payload to the matching
   :class:`TouchpointAdapter` and write one row to ``interactions``.

These endpoints are **public** — they're authenticated by the
channel's own signature scheme, not by our session cookie.  That's
why the router doesn't call ``require_dashboard_auth``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request, Response

from hermes_cloud.app.channels.base import (
    ChannelVerificationError, get_receiver, list_receivers,
)
from hermes_cloud.app.config import AppConfig
from hermes_cloud.app.touchpoints.base import get_adapter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["channels"])


def _config(request: Request) -> AppConfig:
    return getattr(request.app.state, "config", None) or AppConfig()


def _headers(request: Request) -> Dict[str, str]:
    # Normalise to lowercase for case-insensitive lookups.
    return {k.lower(): v for k, v in request.headers.items()}


def _query(request: Request) -> Dict[str, str]:
    return dict(request.query_params)


@router.get("/")
def list_channels():
    """List registered receivers (no secrets). Handy for ops sanity check."""
    return {"channels": list_receivers()}


@router.get("/{source}/callback")
async def callback_get(request: Request, source: str) -> Any:
    """URL-verification GET path — WeCom uses this for echostr.

    Most other channels POST their challenge; they go through
    :func:`callback_post` instead.
    """
    receiver = get_receiver(source)
    if receiver is None:
        raise HTTPException(status_code=404, detail=f"unknown channel {source!r}")
    cfg = receiver.load_config(_config(request))
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail=f"{source} channel disabled")
    try:
        resp = receiver.challenge_response(
            body=b"", query=_query(request), cfg=cfg,
        )
    except ChannelVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    if resp is None:
        raise HTTPException(status_code=400, detail="no challenge in query")
    # WeCom wants raw plaintext back, not JSON.
    if isinstance(resp, dict) and "_plaintext_echo" in resp:
        return Response(content=resp["_plaintext_echo"], media_type="text/plain")
    return resp


@router.post("/{source}/callback")
async def callback_post(request: Request, source: str) -> Any:
    receiver = get_receiver(source)
    if receiver is None:
        raise HTTPException(status_code=404, detail=f"unknown channel {source!r}")

    cfg = receiver.load_config(_config(request))
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail=f"{source} channel disabled")

    body = await request.body()
    headers = _headers(request)
    query = _query(request)

    # URL-verification challenge short-circuit (Slack / Feishu / Discord).
    try:
        challenge = receiver.challenge_response(
            body=body, query=query, cfg=cfg,
        )
    except ChannelVerificationError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    if challenge is not None:
        if isinstance(challenge, dict) and "_plaintext_echo" in challenge:
            return Response(
                content=challenge["_plaintext_echo"], media_type="text/plain",
            )
        return challenge

    # Regular event callback — verify signature, decrypt, ingest.
    try:
        payload = receiver.verify_and_extract(
            headers=headers, body=body, query=query, cfg=cfg,
        )
    except ChannelVerificationError as exc:
        logger.warning("channel %s signature check failed: %s", source, exc)
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("channel %s verify crashed", source)
        raise HTTPException(status_code=500, detail=str(exc))

    if payload is None:
        # Was a challenge after all (some receivers detect it post-verify).
        return {"ok": True}

    adapter_key = receiver.touchpoint_source or receiver.source
    adapter = get_adapter(adapter_key)
    if adapter is None:
        logger.error(
            "channel %s verified but no touchpoint adapter for %s",
            source, adapter_key,
        )
        raise HTTPException(
            status_code=500,
            detail=f"no touchpoint adapter registered for {adapter_key}",
        )

    try:
        interaction_id = adapter.ingest(payload)
    except Exception as exc:
        logger.exception("channel %s ingest failed", source)
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "ok": True,
        "data": {"interaction_id": interaction_id, "source": adapter.source},
    }


__all__ = ["router"]
