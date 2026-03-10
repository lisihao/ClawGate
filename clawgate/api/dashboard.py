"""Dashboard API - Observability endpoints for ClawGate

Provides:
  GET /dashboard/overview    - Overall health: request count, success rate, avg latency
  GET /dashboard/models      - Per-model: count, ttft(p50/p99), tokens, cost, success_rate
  GET /dashboard/backends    - Per-backend: circuit breaker state, availability, error rate
  GET /dashboard/context     - Context engine: cache hits, compression ratio, LTM recall
  GET /dashboard/timeline    - Time series: requests per minute (last 1 hour)
"""

import logging
from typing import Optional

from fastapi import APIRouter

logger = logging.getLogger("clawgate.api.dashboard")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Module-level references (set by init_dashboard)
_db_store = None
_cloud_dispatcher = None
_context_manager = None
_queue_manager = None


def init_dashboard(db_store, cloud_dispatcher=None, context_manager=None, queue_manager=None):
    """Initialize dashboard with dependencies (called from startup)"""
    global _db_store, _cloud_dispatcher, _context_manager, _queue_manager
    _db_store = db_store
    _cloud_dispatcher = cloud_dispatcher
    _context_manager = context_manager
    _queue_manager = queue_manager


@router.get("/overview")
async def dashboard_overview():
    """Overall gateway health snapshot"""
    if not _db_store:
        return {"error": "Database not initialized"}

    model_stats = _db_store.get_model_stats(hours=24)

    total_requests = sum(s["count"] for s in model_stats)
    total_successes = sum(s["success_count"] for s in model_stats)
    avg_latency = (
        sum(s["avg_latency"] * s["count"] for s in model_stats) / total_requests
        if total_requests > 0 else 0
    )

    return {
        "total_requests_24h": total_requests,
        "success_rate": total_successes / total_requests if total_requests > 0 else 1.0,
        "avg_latency_ms": round(avg_latency * 1000, 1),
        "active_models": len(model_stats),
        "backends_healthy": (
            sum(1 for h in _cloud_dispatcher.get_health().values() if h["state"] == "closed")
            if _cloud_dispatcher else 0
        ),
        "backends_total": len(_cloud_dispatcher.get_health()) if _cloud_dispatcher else 0,
    }


@router.get("/models")
async def dashboard_models():
    """Per-model statistics"""
    if not _db_store:
        return {"error": "Database not initialized"}

    model_stats = _db_store.get_model_stats(hours=24)
    result = []
    for stat in model_stats:
        ttft_pct = _db_store.get_percentile_ttft(stat["model"], hours=24)
        result.append({
            "model": stat["model"],
            "count": stat["count"],
            "success_rate": stat["success_rate"],
            "avg_latency_ms": round(stat["avg_latency"] * 1000, 1),
            "ttft_p50_ms": round(ttft_pct.get("p50", 0) * 1000, 1),
            "ttft_p99_ms": round(ttft_pct.get("p99", 0) * 1000, 1),
            "total_input_tokens": stat["total_input_tokens"],
            "total_output_tokens": stat["total_output_tokens"],
            "total_cost": stat.get("total_cost", 0),
        })
    return {"models": result}


@router.get("/backends")
async def dashboard_backends():
    """Per-backend health (circuit breaker status)"""
    if not _cloud_dispatcher:
        return {"backends": {}}

    return {"backends": _cloud_dispatcher.get_health()}


@router.get("/context")
async def dashboard_context():
    """Context engine statistics"""
    if not _db_store:
        return {"error": "Database not initialized"}

    return _db_store.get_context_stats()


@router.get("/scheduler")
async def dashboard_scheduler():
    """Queue scheduler status (lanes, models, agents, admission)"""
    if not _queue_manager:
        return {"scheduler": "not_initialized"}
    return _queue_manager.get_stats()


@router.get("/timeline")
async def dashboard_timeline(minutes: int = 60):
    """Requests per minute over time"""
    if not _db_store:
        return {"error": "Database not initialized"}

    rpm = _db_store.get_requests_per_minute(minutes=minutes)
    return {"timeline": rpm}
