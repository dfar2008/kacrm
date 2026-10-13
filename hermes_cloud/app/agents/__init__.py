"""Agents subsystem — REMOVED in Charter v2 Phase 3 (2026-05-06).

The 6 v2.3 specialists (intake / email_drafter / coach / compliance /
research / analyst) and the supporting infrastructure (registry,
resolver, toolset builder, ACL, dashboards, RL feedback) have all
been deleted. The main agent now does this work itself, guided by
markdown skills under ``~/.hermes/profiles/<user>/skills/`` (mirrored
from ``hermes_cloud/system_skills_seed/``).

This module is kept only to satisfy stale ``import hermes_cloud.app
.agents`` paths that may linger in third-party callers; it exports
nothing.
"""
from __future__ import annotations

__all__: list = []
