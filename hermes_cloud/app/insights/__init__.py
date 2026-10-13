"""Cloud HTTP route layer for CRM insights.

The thin route layer that exposes the CRM insights capability over
``/api/insights/*``. The judgement logic — scanners, AI-noticed
pattern detection, brief/question generation, daily-feed assembly,
priority ranking, team-audit panels — lives in ``hermes/crm/insights/``
(a native hermes capability); the modules here (``api.py``,
``team_audit_api.py``) are FastAPI routers + auth glue, plus the
transport/telemetry infra ``notify.py`` and ``llm_usage.py``.
"""
