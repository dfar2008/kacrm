"""Resolve which LLM endpoint + credentials to use for a given user.

The single source of truth is the user's profile model config (stored
via :func:`/api/user-config/model` PUT and exposed as flat ``model``
/ ``api_key`` / ``base_url`` columns on the ``user_configs`` row, plus
the raw ``providers_json`` blob for per-provider lookups).

There is **no env-var fallback**: if a user hasn't configured a model,
we surface a typed exception. Callers — Coach scanners, agent skills,
chat-stream proxies — must handle ``NoProfileModelConfigured`` and
either skip the LLM call (for offline scanners) or return a clear UI
error (for interactive surfaces).

Why the strict no-fallback rule: silently routing through a free
public endpoint (``opencode-zen / big-pickle``) lies about which
model the user actually picked, costs cycles on a shared quota, and
risks shipping production traffic to a demo provider. Better to fail
loudly and let the user fix their profile.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ②b MoA「深度会诊」档在 ModelPicker 里的展示。preset 名 → 双语 label
# 由前端 i18n 负责渲染(provider_id=="moa" 走特判);后端只透传稳定
# id/provider_id + 一个兜底 label(前端无对应 key 时用)。
_MOA_LABEL_FALLBACK = "深度会诊(多模型会诊·慢/贵)"


class NoProfileModelConfigured(RuntimeError):
    """User has no LLM model configured on their profile."""


class ModelNotInActiveList(RuntimeError):
    """User explicitly requested ``model_override`` that's not on their
    profile's active model list (per ``providers_json.providers``)."""


@dataclass(frozen=True)
class LLMCreds:
    model: str       # provider's model id, e.g. "gpt-4o-mini" or "big-pickle"
    api_key: str
    base_url: str    # base URL ending without ``/chat/completions``


def _parse_providers_json(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_for_user(
    config_store,
    user_id: str,
    *,
    profile_id: Optional[str] = None,
    model_override: Optional[str] = None,
) -> LLMCreds:
    """Look up the user's profile LLM credentials.

    Args:
        config_store: a ``ConfigStore`` instance (typically
            ``app.state.config_store`` for FastAPI routes, or a
            celery-built ``SqlAlchemyConfigStore`` for background tasks).
        user_id: bearer-resolved user id.
        profile_id: optional explicit profile id. If omitted, the
            default-profile path on ``ConfigStore`` is used. (One user
            currently maps to one profile per the product rule, so the
            override is rarely needed.)
        model_override: a ``"provider_id::model_id"`` string from the
            chat-box dropdown. When provided, the resolver picks the
            matching provider's credentials from ``providers_json`` and
            uses the supplied model id. ``ModelNotInActiveList`` is
            raised if the model isn't in the profile's active list —
            no silent fallback.

    Returns:
        :class:`LLMCreds` populated from the user's profile.

    Raises:
        NoProfileModelConfigured: profile has no model row, or its
            ``model`` / ``api_key`` / ``base_url`` triple is incomplete.
        ModelNotInActiveList: ``model_override`` doesn't match any
            active provider/model.
    """
    pcfg = config_store.get_user_model(user_id, profile_id=profile_id)

    if not pcfg or not pcfg.providers_json:
        raise NoProfileModelConfigured(
            f"user {user_id!r} has no model configured — "
            "open profile settings → 模型 to pick one"
        )

    providers_root = _parse_providers_json(pcfg.providers_json)
    providers = providers_root.get("providers") or {}

    # User picked a specific model in the dropdown.
    if model_override:
        if "::" not in str(model_override):
            raise ModelNotInActiveList(
                f"malformed model id {model_override!r} "
                "(expected ``provider_id::model_id``)"
            )
        prov_id, model_id = str(model_override).split("::", 1)
        prov = providers.get(prov_id) or {}
        if not prov or not prov.get("enabled", True):
            raise ModelNotInActiveList(
                f"provider {prov_id!r} not active in profile"
            )
        active_model_ids = {
            (m.get("id") if isinstance(m, dict) else m)
            for m in (prov.get("models") or [])
        }
        if model_id not in active_model_ids:
            raise ModelNotInActiveList(
                f"model {model_id!r} not active under provider {prov_id!r}"
            )
        api_key = prov.get("api_key") or pcfg.api_key
        base_url = prov.get("base_url") or pcfg.base_url
        if not api_key or not base_url:
            raise NoProfileModelConfigured(
                f"provider {prov_id!r} is missing api_key / base_url; "
                "open profile settings to complete it"
            )
        return LLMCreds(model=model_id, api_key=api_key, base_url=base_url)

    # Default-model path: use the derived flat fields the PUT handler
    # already wrote when the user picked their default.
    if not pcfg.model or not pcfg.api_key or not pcfg.base_url:
        raise NoProfileModelConfigured(
            f"user {user_id!r} profile model is incomplete "
            "(missing one of model / api_key / base_url) — "
            "open profile settings → 模型 to fix"
        )
    return LLMCreds(
        model=pcfg.model, api_key=pcfg.api_key, base_url=pcfg.base_url,
    )


def _moa_presets_from_profile(profile_dir: Optional[Path]) -> list[dict]:
    """读该 profile 的 config.yaml 顶层 ``moa.presets``,把每个 preset 变成
    ModelPicker 的一个可选「模型」。

    id 用 ``moa/<preset>``(和 hermes 侧执行 MoA 时认的模型串一致,注意是
    ``/`` 不是 ``::``——它不属于任何普通 provider,provider 隐式 = moa)。
    provider_id 固定 "moa",前端据此渲染醒目的「深度会诊」档。

    ②b 决策:MoA presets seed 在 profile 的 config.yaml(_sync_config_to_profile
    里,那儿才有该 profile「能到达」的 custom_providers),不进 DB 的
    providers_json。所以这里额外读一次 config.yaml——保持「config.yaml 是
    gateway 执行事实、DB 是 SPA 表单事实」的既有分工,不把 MoA 硬塞进
    providers_json 造成两处数据漂移。读文件失败一律静默返回空:MoA 是
    opt-in 加料,绝不能因它拖垮整个模型列表。
    """
    if profile_dir is None:
        return []
    cfg_path = profile_dir / "config.yaml"
    if not cfg_path.exists():
        return []
    try:
        import yaml  # 延迟 import:普通路径(无 profile_dir)不付这个代价
        raw = yaml.safe_load(cfg_path.read_text()) or {}
    except Exception:
        return []
    moa = raw.get("moa")
    if not isinstance(moa, dict):
        return []
    presets = moa.get("presets")
    if not isinstance(presets, dict):
        return []
    out: list[dict] = []
    for name, preset in presets.items():
        if not name or not isinstance(preset, dict):
            continue
        out.append({
            "id": f"moa/{name}",
            "label": _MOA_LABEL_FALLBACK,
            "provider_id": "moa",
        })
    return out


def active_models_for_user(
    config_store,
    user_id: str,
    *,
    profile_id: Optional[str] = None,
    profile_dir: Optional[Path] = None,
) -> dict:
    """Return ``{"models": [{id, label, provider_id}...], "default_id": "...", "configured": bool}``
    for the profile model picker UI.

    No exceptions on missing config — we encode the "not configured"
    state explicitly so the front-end can render an empty picker with
    a "go configure" hint instead of crashing.

    ``profile_dir``: 该 profile 的 ``~/.hermes/profiles/<pid>/`` 目录。传入时
    额外读 config.yaml 的 ``moa.presets``,把「深度会诊」档追加到列表末尾
    (②b)。不传则行为与旧版完全一致(纯 providers_json)。
    """
    pcfg = config_store.get_user_model(user_id, profile_id=profile_id)
    if not pcfg or not pcfg.providers_json:
        return {"models": [], "default_id": None, "configured": False}
    root = _parse_providers_json(pcfg.providers_json)
    providers = root.get("providers") or {}
    default_id = root.get("default_model") or None
    out_models: list[dict] = []
    for prov_id, prov in providers.items():
        if not prov.get("enabled", True):
            continue
        for m in prov.get("models") or []:
            if isinstance(m, dict):
                mid = m.get("id")
                if not mid:
                    continue
                label = m.get("name") or m.get("label") or mid
            else:
                mid = str(m)
                label = mid
            out_models.append({
                "id": f"{prov_id}::{mid}",
                "label": str(label),
                "provider_id": prov_id,
            })
    # ②b:普通模型至少要有 1 个(configured=True)才追加 MoA——「深度会诊」
    # 是加料档,不能是一个「未配置任何普通模型却只剩会诊」的孤岛。放在
    # 列表末尾,且绝不进 default_id(opt-in,永不设默认)。
    if out_models:
        out_models.extend(_moa_presets_from_profile(profile_dir))
    return {
        "models": out_models,
        "default_id": default_id if default_id else None,
        "configured": bool(out_models),
    }
