"""Cloud → hermes dispatch primitives (Phase 4 Week 2 Day 1-2).

Single canonical helper to forward a chat-completion request to the
user's per-profile hermes subprocess. Replaces the ad-hoc cloud-side
``chat_with_tools`` calls in ``app/copilot/routes.py`` (retired in
Day 3-4) and is the function ``/v1/chat/completions`` handler's
non-streaming path uses internally.

The streaming path stays in ``app/main.py:_proxy_stream`` because SSE
fan-out is request-bound and doesn't fit the envelope/result shape.
"""
from hermes_cloud.app.proxy.dispatch import (
    DispatchResult,
    dispatch_to_hermes,
)

__all__ = ["DispatchResult", "dispatch_to_hermes"]
