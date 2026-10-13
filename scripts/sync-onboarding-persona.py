#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync the repo-canonical onboarding persona to a hermes home.

Source of truth: deploy/onboarding-persona/{SOUL.md,system_prompt.md}.
Targets:
  * <hermes_home>/onboarding_template.json — only the ``soul_md`` /
    ``system_prompt`` keys; model / skills / cron are left untouched
    (they hold per-deployment secrets and belong to the SPA admin page).
  * with ``--profiles``: each profile's SOUL.md and config.yaml
    ``agent.system_prompt`` — again nothing else.

Gateways cache SOUL.md at startup — restart them after syncing
(prod: restart kacrm-cloud.service as a whole; do NOT SIGTERM the
admin gateway alone).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PERSONA_DIR = REPO_ROOT / "deploy" / "onboarding-persona"


def load_canonical() -> tuple[str, str]:
    soul = (PERSONA_DIR / "SOUL.md").read_text(encoding="utf-8")
    prompt = (PERSONA_DIR / "system_prompt.md").read_text(encoding="utf-8")
    if not soul.strip() or not prompt.strip():
        sys.exit("canonical persona files are empty — refusing to sync")
    return soul, prompt


def sync_template(home: Path, soul: str, prompt: str) -> bool:
    path = home / "onboarding_template.json"
    if not path.is_file():
        sys.exit(f"{path} not found — is --hermes-home right?")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("soul_md") == soul and data.get("system_prompt") == prompt:
        return False
    data["soul_md"] = soul
    data["system_prompt"] = prompt
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return True


def sync_profile(home: Path, pid: str, soul: str, prompt: str) -> list[str]:
    pdir = home / "profiles" / pid
    if not pdir.is_dir():
        sys.exit(f"profile dir {pdir} not found")
    touched: list[str] = []

    soul_path = pdir / "SOUL.md"
    if not soul_path.is_file() or soul_path.read_text(encoding="utf-8") != soul:
        soul_path.write_text(soul, encoding="utf-8")
        touched.append("SOUL.md")

    cfg_path = pdir / "config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else None
    data = data or {}
    agent = data.get("agent") or {}
    if agent.get("system_prompt") != prompt:
        agent["system_prompt"] = prompt
        data["agent"] = agent
        cfg_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        touched.append("config.yaml")
    return touched


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hermes-home", required=True, type=Path,
                    help="target hermes home (dev ~/.hermes, prod ~/.kacrm)")
    ap.add_argument("--profiles", nargs="*", default=[],
                    help="also overwrite these profiles' SOUL/system_prompt "
                         "(mind hand-tuned variants, e.g. dev admin)")
    args = ap.parse_args()

    home = args.hermes_home.expanduser()
    soul, prompt = load_canonical()

    print(f"template: {'updated' if sync_template(home, soul, prompt) else 'already in sync'}")
    for pid in args.profiles:
        touched = sync_profile(home, pid, soul, prompt)
        print(f"profile {pid}: {'updated ' + '+'.join(touched) if touched else 'already in sync'}")
    if args.profiles:
        print("remember: restart gateways for SOUL.md to take effect")


if __name__ == "__main__":
    main()
