"""新用户模板 — provisioning defaults for newly created agent profiles.

A single deployment-global JSON file holding the defaults every new
profile inherits:

  * ``soul_md``         — the SOUL.md identity overlay
  * ``system_prompt``   — the Personality (config.yaml ``agent.system_prompt``)
  * ``skills_disabled`` — the skills disabled-list (config.yaml ``skills.disabled``)
  * ``model``           — model / provider config (model, api_key, base_url,
                          providers_json)

Admin edits it via the SPA「新用户模板」page; ``admin_create_user`` applies
it to every new profile, and an apply endpoint back-fills existing ones.

File-based on purpose — consistent with ``channel_config.py``: config that
cloud reads straight from disk, no DB table, no migration, no sync layer.
The file lives at ``<hermes_home>/onboarding_template.json``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

TEMPLATE_FILENAME = "onboarding_template.json"

# Secrets are masked with this prefix before leaving the server. A real
# API key never starts with it, so the PUT handler can tell "unchanged
# masked value" apart from "a new key the admin typed".
MASK_PREFIX = "••••"  # ••••


def empty_template() -> dict[str, Any]:
    """A fully-populated template dict with blank values.

    Every key is always present so callers never need defensive
    ``.get()`` chains. ``cron_jobs`` defaults to ``[]`` — applying
    an empty template results in a profile with no scheduled tasks
    (the SPA's 「从 admin 复制定时任务」 button is the only way to
    populate it).
    """
    return {
        "soul_md": "",
        "system_prompt": "",
        "skills_disabled": [],
        "cron_jobs": [],
        "model": {
            "model": None,
            "api_key": None,
            "base_url": None,
            "providers_json": None,
        },
    }


def template_path(hermes_home: Path) -> Path:
    return Path(hermes_home) / TEMPLATE_FILENAME


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce an arbitrary dict into the canonical template shape."""
    merged = empty_template()
    for key in ("soul_md", "system_prompt"):
        if isinstance(data.get(key), str):
            merged[key] = data[key]
    sd = data.get("skills_disabled")
    if isinstance(sd, list):
        merged["skills_disabled"] = sorted({str(x) for x in sd})
    cj = data.get("cron_jobs")
    if isinstance(cj, list):
        # Keep only entries that look like job specs (have at least a
        # name). Drop anything else silently — the source of truth for
        # the schema is the snapshot endpoint, not this normalizer.
        merged["cron_jobs"] = [
            dict(j) for j in cj
            if isinstance(j, dict) and isinstance(j.get("name"), str) and j.get("name").strip()
        ]
    model = data.get("model")
    if isinstance(model, dict):
        for key in ("model", "api_key", "base_url", "providers_json"):
            if key in model:
                merged["model"][key] = model[key]
    return merged


def read_template(hermes_home: Path) -> dict[str, Any] | None:
    """Return the stored template, or ``None`` if it was never set."""
    path = template_path(hermes_home)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return _normalize(data)


def write_template(hermes_home: Path, data: dict[str, Any]) -> None:
    """Persist the template atomically (temp file + rename)."""
    path = template_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(_normalize(data), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def mask_secret(value: str | None) -> str | None:
    """Mask an API key for transport — keep only the last 4 chars."""
    if not value:
        return value
    s = str(value)
    if len(s) <= 4:
        return MASK_PREFIX
    return MASK_PREFIX + s[-4:]


def is_masked(value: Any) -> bool:
    """True when ``value`` is a masked secret echoed back unchanged."""
    return isinstance(value, str) and value.startswith(MASK_PREFIX)
