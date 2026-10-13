import hashlib
import hmac
import logging
import time
import uuid

from fastapi import HTTPException, Request, Response, status

from hermes_cloud.app.config import AppConfig

logger = logging.getLogger(__name__)

_COOKIE_NAME = "hc_session"
_COOKIE_TTL = 86400  # 24 hours


# ---------------------------------------------------------------------------
# Session cookie helpers
# ---------------------------------------------------------------------------

def _sign(secret: str, payload: str) -> str:
    """Return HMAC-SHA256 hex digest of payload."""
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _make_session_token(secret: str, user_id: str, raw_api_key: str) -> str:
    """Return a signed session token: '{sid}.{user_id}.{ts}.{sig}'."""
    sid = uuid.uuid4().hex
    ts = str(int(time.time()))
    body = f"{sid}.{user_id}.{ts}"
    sig = _sign(secret, body)
    return f"{body}.{sig}"


def _verify_session_token(secret: str, token: str) -> tuple[str, str] | None:
    """Return (user_id, token) if valid and not expired, else None."""
    try:
        parts = token.split(".")
        if len(parts) != 4:
            return None
        sid, user_id, ts_str, sig = parts
        body = f"{sid}.{user_id}.{ts_str}"
        expected = _sign(secret, body)
        if not hmac.compare_digest(expected, sig):
            return None
        if int(time.time()) - int(ts_str) > _COOKIE_TTL:
            return None
        return user_id, token
    except Exception:
        return None


def set_session_cookie(response: Response, secret: str, user_id: str, raw_api_key: str) -> None:
    token = _make_session_token(secret, user_id, raw_api_key)
    response.set_cookie(
        _COOKIE_NAME,
        token,
        max_age=_COOKIE_TTL,
        httponly=True,
        # "lax" rather than "strict" so normal top-level navigations
        # (e.g. clicking a bookmarked /app/ link) keep the session.
        # Our auth endpoints are POST so CSRF against them is blocked
        # by same-site regardless.
        samesite="lax",
        secure=False,  # Set to True behind HTTPS in production (nginx terminates TLS)
        path="/",
    )


def _resolve_session_cookie(request: Request, secret: str | None) -> str | None:
    """Return user_id if the `hc_session` cookie is present and valid."""
    if not secret:
        return None
    cookie_val = request.cookies.get(_COOKIE_NAME, "")
    if not cookie_val:
        return None
    result = _verify_session_token(secret, cookie_val)
    if result is None:
        return None
    return result[0]


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(_COOKIE_NAME)


# ---------------------------------------------------------------------------
# API key resolution helpers
# ---------------------------------------------------------------------------

def _resolve_api_key_token(token: str, config: AppConfig) -> str | None:
    user_id = config.api_keys.get(token)
    if user_id:
        return user_id
    return None


def require_bearer_auth(request: Request, config: AppConfig) -> str | None:
    """Return the authenticated user id when auth is configured.

    When no API keys are configured, the orchestrator stays open for local
    development. Once keys exist, /v1/* callers must provide a Bearer token.
    """
    auth_store = getattr(request.app.state, "auth_store", None)
    auth_required = bool(config.api_keys) or bool(auth_store and auth_store.has_user_keys())
    if not auth_required:
        return None

    # 1. Prefer the signed HttpOnly session cookie — this is how the SPA
    #    authenticates after login. No token ever touches JS memory.
    cookie_uid = _resolve_session_cookie(request, config.session_cookie_secret)
    if cookie_uid:
        return cookie_uid

    # 2. Fall back to `Authorization: Bearer <api_key>` — for CLIs, the
    #    hermes binary talking to the gateway, and any non-browser client.
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Missing or invalid bearer token.",
                    "type": "authentication_error",
                    "code": "unauthorized",
                }
            },
        )

    token = auth_header[len("Bearer "):].strip()
    # Internal key must never authenticate as a regular user
    internal_key = getattr(getattr(request.app, "state", None), "_internal_key", None)
    if internal_key and token == internal_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Invalid API key.", "type": "authentication_error", "code": "unauthorized"}},
        )
    user_id = auth_store.authenticate_user(token) if auth_store else None
    if not user_id:
        user_id = _resolve_api_key_token(token, config)
    if not user_id:
        logger.warning("auth_failed ip=%s", request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "message": "Invalid API key.",
                    "type": "authentication_error",
                    "code": "unauthorized",
                }
            },
        )
    return user_id


def require_dashboard_auth(request: Request, config: AppConfig) -> tuple[str | None, str | None]:
    """Authenticate dashboard requests.

    Priority order:
    1. Signed session cookie (issued after first ?api_key= login) — preferred,
       keeps the raw API key out of URLs and server logs.
    2. Authorization: Bearer header.
    3. ?api_key= query param — only when session_cookie_secret is not set
       (dev / single-user deployments).  When a secret IS configured we
       validate the key and redirect to the cookie-based flow instead.

    Returns (user_id, raw_api_key_for_redirect).  raw_api_key_for_redirect is
    None when the request is authenticated via cookie (no key in URLs needed).
    """
    auth_store = getattr(request.app.state, "auth_store", None)
    auth_required = bool(getattr(config, "api_keys", {})) or bool(auth_store and auth_store.has_user_keys())
    if not auth_required:
        return None, None

    # 1. Signed session cookie
    if config.session_cookie_secret:
        cookie_val = request.cookies.get(_COOKIE_NAME, "")
        if cookie_val:
            result = _verify_session_token(config.session_cookie_secret, cookie_val)
            if result is not None:
                user_id, _ = result
                return user_id, None  # authenticated via cookie; no key in redirect URLs

    # 2. Authorization: Bearer header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[len("Bearer "):].strip()
        user_id = auth_store.authenticate_user(token) if auth_store else None
        if not user_id:
            user_id = _resolve_api_key_token(token, config)
        if user_id:
            return user_id, token

    # 3. ?api_key= query param
    token = request.query_params.get("api_key", "").strip()
    if token:
        user_id = auth_store.authenticate_user(token) if auth_store else None
        if not user_id:
            user_id = _resolve_api_key_token(token, config)
        if user_id:
            return user_id, token

    logger.warning("dashboard_auth_failed ip=%s", request.client.host if request.client else "unknown")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "error": {
                "message": "Missing or invalid dashboard API key.",
                "type": "authentication_error",
                "code": "unauthorized",
            }
        },
    )


def require_internal_key_header(request: Request, config: AppConfig) -> None:
    """Validate the ``X-Internal-Key`` header for inbound /internal/* routes.

    All cloud-side ``/internal/*`` and ``/api/internal/*`` endpoints accept
    the ``X-Internal-Key`` header — see main.py's ``_require_internal_key``,
    ``hermes_cloud/app/touchpoints/api.py``, and the subprocess→cloud callers
    (hermes_state.py, cloud-approval plugin, weixin/feishu bridges,
    insights/notify.py). Outbound cloud→subprocess calls use ``Authorization:
    Bearer`` and are handled separately.

    Resolves the expected key from ``config.internal_key``. Raises 401 on
    mismatch / missing header, 503 when no internal key is configured.
    """
    expected = getattr(config, "internal_key", None)
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": {"message": "Internal key not configured.",
                              "type": "auth_error",
                              "code": "internal_key_unconfigured"}},
        )
    provided = request.headers.get("X-Internal-Key", "")
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Invalid or missing X-Internal-Key.",
                              "type": "auth_error",
                              "code": "invalid_internal_key"}},
        )


def require_admin_auth(request: Request, config: AppConfig) -> str:
    """Require an authenticated user with is_admin=True.

    Accepts the same Bearer token as regular user auth — no separate admin
    key table needed.  Returns the admin's user_id.
    """
    auth_store = getattr(request.app.state, "auth_store", None)

    # 1. Session cookie (SPA path).
    user_id = _resolve_session_cookie(request, config.session_cookie_secret)

    # 2. Bearer header / admin_key query param (CLI, legacy).
    if not user_id:
        token: str | None = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):].strip()
        if not token:
            token = request.query_params.get("admin_key", "").strip() or None

        if not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"message": "Missing bearer token.", "type": "authentication_error", "code": "unauthorized"}},
            )

        user_id = auth_store.authenticate_user(token) if auth_store else None
        if not user_id:
            # Fallback: static api_keys map
            user_id = config.api_keys.get(token)

    if not user_id:
        logger.warning("admin_auth_failed ip=%s", request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"message": "Invalid API key.", "type": "authentication_error", "code": "unauthorized"}},
        )

    # Check is_admin flag
    user = auth_store.get_user(user_id) if (auth_store and hasattr(auth_store, "get_user")) else None
    if user is None or not getattr(user, "is_admin", False):
        logger.warning("admin_access_denied uid=%s", user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"message": "Admin access required.", "type": "permission_error", "code": "forbidden"}},
        )

    return user_id
