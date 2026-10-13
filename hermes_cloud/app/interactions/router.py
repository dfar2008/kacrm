"""Event Router for new interactions.

Routes selected interaction kinds into async Intake processing.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

WHITELIST_KINDS = {
    "chat",
    "im_message",
    "email",
    "call_transcript",
    "voice_memo",
    "contact_log",
    "meeting_note",
    "webhook",
}


def should_route_kind(kind: str) -> bool:
    return str(kind or "").strip() in WHITELIST_KINDS


def route_new_interaction(
    interaction_id: str,
    kind: str,
    profile_id: str = "",
) -> None:
    if not should_route_kind(kind):
        return
    try:
        from hermes_cloud.app.celery_app import run_intake_for_interaction

        run_intake_for_interaction.delay(str(interaction_id), str(profile_id or ""))
    except Exception as exc:
        logger.warning(
            "route_new_interaction_failed interaction_id=%s kind=%s err=%r",
            interaction_id,
            kind,
            exc,
        )
