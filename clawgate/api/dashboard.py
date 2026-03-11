"""Dashboard API - Observability endpoints for ClawGate

Provides:
  GET /dashboard/overview    - Overall health: request count, success rate, avg latency
  GET /dashboard/models      - Per-model: count, ttft(p50/p99), tokens, cost, success_rate
  GET /dashboard/backends    - Per-backend: circuit breaker state, availability, error rate
  GET /dashboard/context     - Context engine: cache hits, compression ratio, LTM recall
  GET /dashboard/scheduler   - Queue scheduler: lanes, per-model, per-agent stats
  GET /dashboard/timeline    - Time series: requests per minute (last 1 hour)
  GET /dashboard/costs       - Cost breakdown: per-model, per-backend, daily trend
  GET /dashboard/sessions    - Active sessions: count, segments, top sessions
  GET /dashboard/cache       - Cache system: Prompt Cache stats, Cache Tuning status
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger("clawgate.api.dashboard")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Module-level references (set by init_dashboard)
_db_store = None
_cloud_dispatcher = None
_context_manager = None
_queue_manager = None
_budget_checker = None
_prompt_cache_manager = None
_engine_manager = None


def init_dashboard(db_store, cloud_dispatcher=None, context_manager=None, queue_manager=None, budget_checker=None, prompt_cache_manager=None, engine_manager=None):
    """Initialize dashboard with dependencies (called from startup)"""
    global _db_store, _cloud_dispatcher, _context_manager, _queue_manager, _budget_checker, _prompt_cache_manager, _engine_manager
    _db_store = db_store
    _cloud_dispatcher = cloud_dispatcher
    _context_manager = context_manager
    _queue_manager = queue_manager
    _budget_checker = budget_checker
    _prompt_cache_manager = prompt_cache_manager
    _engine_manager = engine_manager


# ========== Response Models ==========


class OverviewResponse(BaseModel):
    total_requests_24h: int = Field(description="Total requests in last 24 hours")
    success_rate: float = Field(description="Success rate (0-1)")
    avg_latency_ms: float = Field(description="Average latency in milliseconds")
    active_models: int = Field(description="Number of models with recent traffic")
    backends_healthy: int = Field(description="Number of healthy backends (circuit closed)")
    backends_total: int = Field(description="Total number of backends")


class ModelStat(BaseModel):
    model: str
    count: int
    success_rate: float
    avg_latency_ms: float
    ttft_p50_ms: float = Field(description="Time to First Token P50 (ms)")
    ttft_p99_ms: float = Field(description="Time to First Token P99 (ms)")
    total_input_tokens: int
    total_output_tokens: int
    total_cost: float


class ModelsResponse(BaseModel):
    models: List[ModelStat]


class BackendsResponse(BaseModel):
    backends: Dict[str, Any] = Field(description="Per-backend circuit breaker state")


class ContextResponse(BaseModel):
    cache_entries: int = 0
    cache_total_hits: int = 0
    active_segments: int = 0
    work_segments: int = 0
    casual_segments: int = 0
    ltm_count: int = 0
    ltm_total_recalls: int = 0
    prompt_cache_entries: int = 0
    prompt_cache_hits: int = 0
    semantic_cache_entries: int = 0
    semantic_cache_hits: int = 0


class TimelinePoint(BaseModel):
    minute: str
    count: int
    success_count: int


class TimelineResponse(BaseModel):
    timeline: List[TimelinePoint]


class CostByModel(BaseModel):
    model: str
    total_cost: float
    request_count: int
    total_input_tokens: int
    total_output_tokens: int
    avg_cost_per_request: float


class DailyCost(BaseModel):
    date: str
    total_cost: float
    request_count: int


class CostsResponse(BaseModel):
    total_cost_24h: float = Field(description="Total cost in last 24 hours")
    total_cost_7d: float = Field(description="Total cost in last 7 days")
    by_model: List[CostByModel] = Field(description="Cost breakdown by model")
    daily_trend: List[DailyCost] = Field(description="Daily cost trend (last 7 days)")


class PromptCacheStats(BaseModel):
    enabled: bool
    hot_cache_size: int = Field(description="Current hot cache entries")
    hot_cache_max: int = Field(description="Hot cache capacity")
    hit_hot: int = Field(description="Hot cache hits")
    hit_warm: int = Field(description="Warm cache hits")
    miss: int = Field(description="Cache misses")
    total_requests: int = Field(description="Total cacheable requests")
    hit_rate: float = Field(description="Overall cache hit rate (0-1)")
    store: int = Field(description="Total cache stores")
    evict_hot: int = Field(description="Hot cache evictions")
    evict_warm: int = Field(description="Warm cache evictions")


class CacheTuningStats(BaseModel):
    enabled: bool
    current_cache_mb: int = Field(description="Current cache-ram size (MB)")
    candidates_mb: List[int] = Field(description="Candidate cache sizes")
    last_recommendation: Optional[int] = Field(description="Last recommended cache size (MB)")
    last_switch_time: Optional[float] = Field(description="Last cache size switch timestamp")
    switch_count: int = Field(description="Total number of cache size switches")


class CacheResponse(BaseModel):
    prompt_cache: PromptCacheStats
    cache_tuning: Optional[CacheTuningStats] = None


# ========== Endpoints ==========


@router.get("/overview", response_model=OverviewResponse)
async def dashboard_overview():
    """Overall gateway health snapshot (last 24 hours)"""
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


@router.get("/models", response_model=ModelsResponse)
async def dashboard_models():
    """Per-model statistics (last 24 hours)"""
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


@router.get("/backends", response_model=BackendsResponse)
async def dashboard_backends():
    """Per-backend health (circuit breaker status)"""
    if not _cloud_dispatcher:
        return {"backends": {}}

    return {"backends": _cloud_dispatcher.get_health()}


@router.get("/context", response_model=ContextResponse)
async def dashboard_context():
    """Context engine statistics (cache, segments, LTM, semantic cache)"""
    if not _db_store:
        return {"error": "Database not initialized"}

    return _db_store.get_context_stats()


@router.get("/scheduler")
async def dashboard_scheduler():
    """Queue scheduler status (lanes, per-model concurrency, per-agent fairness)"""
    if not _queue_manager:
        return {"scheduler": "not_initialized"}
    return _queue_manager.get_stats()


@router.get("/timeline", response_model=TimelineResponse)
async def dashboard_timeline(minutes: int = 60):
    """Requests per minute over time"""
    if not _db_store:
        return {"error": "Database not initialized"}

    rpm = _db_store.get_requests_per_minute(minutes=minutes)
    return {"timeline": rpm}


@router.get("/costs")
async def dashboard_costs():
    """Cost breakdown by model and daily trend, with optional budget info"""
    if not _db_store:
        return {"error": "Database not initialized"}

    result = _db_store.get_cost_breakdown()

    # Attach budget info if available
    if _budget_checker:
        result["budget"] = _budget_checker.get_budget_info()

    return result


@router.get("/sessions")
async def dashboard_sessions():
    """Active session statistics for the dashboard.

    Returns aggregate counts and top 10 sessions by recent activity.
    """
    if not _db_store:
        return {"error": "Database not initialized"}

    try:
        db_file = _db_store.db_path / "context.db"
        if not db_file.exists():
            return {
                "active_sessions": 0,
                "total_segments": 0,
                "total_messages": 0,
                "sessions": [],
            }

        conn = sqlite3.connect(str(db_file))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Aggregate stats (only non-expired)
        cursor.execute(
            """
            SELECT
                COUNT(DISTINCT conversation_id) AS active_sessions,
                COUNT(*) AS total_segments,
                COALESCE(SUM(message_count), 0) AS total_messages
            FROM conversation_segments
            WHERE expires_at > datetime('now')
            """
        )
        stats_row = cursor.fetchone()

        # Top 10 sessions by last activity
        cursor.execute(
            """
            SELECT
                conversation_id AS session_id,
                COUNT(*) AS segment_count,
                COALESCE(SUM(message_count), 0) AS message_count,
                MAX(created_at) AS last_activity
            FROM conversation_segments
            WHERE expires_at > datetime('now')
            GROUP BY conversation_id
            ORDER BY last_activity DESC
            LIMIT 10
            """
        )

        sessions = []
        for row in cursor.fetchall():
            sessions.append({
                "session_id": row["session_id"],
                "segment_count": row["segment_count"],
                "message_count": row["message_count"],
                "last_activity": row["last_activity"],
            })

        conn.close()

        return {
            "active_sessions": stats_row["active_sessions"] or 0,
            "total_segments": stats_row["total_segments"] or 0,
            "total_messages": stats_row["total_messages"] or 0,
            "sessions": sessions,
        }
    except Exception as e:
        logger.error(f"[Dashboard] sessions stats failed: {e}")
        return {"error": str(e)}


@router.get("/cache", response_model=CacheResponse)
async def dashboard_cache():
    """Cache system statistics (Prompt Cache + Cache Tuning)

    Returns:
        - Prompt Cache stats: hot/warm hits, miss, hit_rate, evictions
        - Cache Tuning stats: current cache_ram_mb, recommendations, switch history
    """
    # Default empty response
    response = {
        "prompt_cache": {
            "enabled": False,
            "hot_cache_size": 0,
            "hot_cache_max": 0,
            "hit_hot": 0,
            "hit_warm": 0,
            "miss": 0,
            "total_requests": 0,
            "hit_rate": 0.0,
            "store": 0,
            "evict_hot": 0,
            "evict_warm": 0,
        },
        "cache_tuning": None,
    }

    # Prompt Cache stats
    if _prompt_cache_manager:
        try:
            stats = _prompt_cache_manager.get_stats()
            response["prompt_cache"] = stats
        except Exception as e:
            logger.error(f"[Dashboard] Failed to get prompt cache stats: {e}")

    # Cache Tuning stats (from ThunderLLAMA Engine)
    if _engine_manager:
        try:
            # Get ThunderLLAMA engine if available
            thunderllama_engine = _engine_manager.get_engine("qwen-1.7b")  # or any local model
            if not thunderllama_engine:
                # Try to get any available local engine
                available_models = _engine_manager.get_available_models()
                if available_models:
                    thunderllama_engine = _engine_manager.get_engine(available_models[0])

            if thunderllama_engine:
                engine_stats = thunderllama_engine.get_stats()

                # Extract cache tuning info
                if engine_stats.get("cache_tuner_enabled"):
                    cache_tuner_stats = engine_stats.get("cache_tuner", {})
                    last_decision = cache_tuner_stats.get("last_decision", {})

                    response["cache_tuning"] = {
                        "enabled": True,
                        "current_cache_mb": engine_stats.get("cache_ram_mb", 0),
                        "candidates_mb": cache_tuner_stats.get("candidates_mb", []),
                        "last_recommendation": last_decision.get("target_cache_mb"),
                        "last_switch_time": cache_tuner_stats.get("last_switch_time"),
                        "switch_count": 0,  # TODO: 需要在 HeuristicCacheTuner 中添加 switch_count 字段
                    }
                else:
                    # Cache tuning disabled, but still show current cache size
                    response["cache_tuning"] = {
                        "enabled": False,
                        "current_cache_mb": engine_stats.get("cache_ram_mb", 0),
                        "candidates_mb": [],
                        "last_recommendation": None,
                        "last_switch_time": None,
                        "switch_count": 0,
                    }
        except Exception as e:
            logger.error(f"[Dashboard] Failed to get cache tuning stats: {e}")

    return response
