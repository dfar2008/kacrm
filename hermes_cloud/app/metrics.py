"""Prometheus metrics for hermes-cloud.

Exposes metrics at GET /metrics (plain text, Prometheus format).
"""
import re
import time

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

# Use a custom registry (not the default global one) to avoid conflicts in tests
REGISTRY = CollectorRegistry(auto_describe=True)

# Request metrics
http_requests_total = Counter(
    "hermes_cloud_http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
    registry=REGISTRY,
)

http_request_duration_seconds = Histogram(
    "hermes_cloud_http_request_duration_seconds",
    "HTTP request duration",
    ["method", "path"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0],
    registry=REGISTRY,
)

# Business metrics
chat_requests_total = Counter(
    "hermes_cloud_chat_requests_total",
    "Total chat completion requests",
    ["user_id", "model", "stream"],
    registry=REGISTRY,
)

tokens_used_total = Counter(
    "hermes_cloud_tokens_used_total",
    "Total tokens consumed",
    ["user_id", "token_type"],  # token_type: prompt, completion
    registry=REGISTRY,
)

active_sessions_gauge = Gauge(
    "hermes_cloud_active_sessions",
    "Number of active sessions (touched in last 24h)",
    registry=REGISTRY,
)

worker_healthy_gauge = Gauge(
    "hermes_cloud_workers_healthy",
    "Number of healthy workers in the pool",
    registry=REGISTRY,
)

worker_total_gauge = Gauge(
    "hermes_cloud_workers_total",
    "Total configured workers",
    registry=REGISTRY,
)

billing_orders_total = Counter(
    "hermes_cloud_billing_orders_total",
    "Total billing orders created",
    ["plan_code", "status"],  # status: created, paid
    registry=REGISTRY,
)

rate_limit_hits_total = Counter(
    "hermes_cloud_rate_limit_hits_total",
    "Total rate limit rejections",
    ["user_id", "limit_type"],  # limit_type: rpm, rpd, quota
    registry=REGISTRY,
)

auth_failures_total = Counter(
    "hermes_cloud_auth_failures_total",
    "Total authentication failures",
    ["endpoint_type"],  # endpoint_type: api, dashboard, admin
    registry=REGISTRY,
)


def _normalize_path(path: str) -> str:
    """Replace dynamic path segments (UUIDs, IDs) with placeholders."""
    # Replace UUIDs
    path = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "{uuid}",
        path,
    )
    # Replace conv_xxx session IDs
    path = re.sub(r"conv_[0-9a-f]+", "{conv_id}", path)
    # Replace user IDs in admin paths
    path = re.sub(r"/users/[^/]+/", "/users/{user_id}/", path)
    return path


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        # Normalize path to avoid high cardinality (replace UUIDs and IDs)
        path = _normalize_path(request.url.path)

        http_requests_total.labels(
            method=request.method,
            path=path,
            status_code=str(response.status_code),
        ).inc()

        http_request_duration_seconds.labels(
            method=request.method,
            path=path,
        ).observe(duration)

        return response
