"""Tests for Dashboard API endpoints."""

import pytest
import httpx
from fastapi import FastAPI

from clawgate.api.dashboard import router as dashboard_router, init_dashboard
from clawgate.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def db_store(tmp_path):
    """Create a real SQLiteStore in a temp directory."""
    return SQLiteStore(db_path=str(tmp_path / "test_db"))


@pytest.fixture()
def client(db_store):
    """Async httpx client wired to the dashboard router via ASGITransport."""
    app = FastAPI()
    app.include_router(dashboard_router)
    init_dashboard(db_store, cloud_dispatcher=None, context_manager=None)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def _seed_request(db_store, model="llama-3", status="success", ttft=0.05,
                  total_time=0.2, input_tokens=100, output_tokens=50, cost=0.001):
    """Helper: insert one request record into the store."""
    db_store.log_request({
        "model": model,
        "messages": [{"role": "user", "content": "hello"}],
        "status": status,
        "ttft": ttft,
        "total_time": total_time,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": cost,
    })


@pytest.mark.anyio
async def test_overview_after_five_requests(client, db_store):
    """GET /dashboard/overview returns correct totals for 5 successful requests."""
    for _ in range(5):
        _seed_request(db_store)

    resp = await client.get("/dashboard/overview")
    assert resp.status_code == 200

    data = resp.json()
    assert data["total_requests_24h"] == 5
    assert data["success_rate"] == 1.0
    assert data["active_models"] == 1
    assert data["backends_healthy"] == 0
    assert data["backends_total"] == 0


@pytest.mark.anyio
async def test_models_two_different_models(client, db_store):
    """GET /dashboard/models lists per-model stats with correct counts."""
    for _ in range(3):
        _seed_request(db_store, model="llama-3")
    for _ in range(2):
        _seed_request(db_store, model="qwen-2")

    resp = await client.get("/dashboard/models")
    assert resp.status_code == 200

    models = resp.json()["models"]
    by_name = {m["model"]: m for m in models}

    assert "llama-3" in by_name
    assert "qwen-2" in by_name
    assert by_name["llama-3"]["count"] == 3
    assert by_name["qwen-2"]["count"] == 2
    assert by_name["llama-3"]["success_rate"] == 1.0


@pytest.mark.anyio
async def test_backends_empty_without_dispatcher(client):
    """GET /dashboard/backends returns empty dict when no cloud_dispatcher."""
    resp = await client.get("/dashboard/backends")
    assert resp.status_code == 200
    assert resp.json() == {"backends": {}}


@pytest.mark.anyio
async def test_context_stats(client, db_store):
    """GET /dashboard/context returns context engine stats with expected keys."""
    resp = await client.get("/dashboard/context")
    assert resp.status_code == 200

    data = resp.json()
    assert "cache_entries" in data
    assert "active_segments" in data
    assert "ltm_count" in data
    assert data["cache_entries"] == 0
    assert data["active_segments"] == 0


@pytest.mark.anyio
async def test_timeline_has_entries(client, db_store):
    """GET /dashboard/timeline returns timeline entries after seeding requests."""
    for _ in range(4):
        _seed_request(db_store)

    resp = await client.get("/dashboard/timeline")
    assert resp.status_code == 200

    timeline = resp.json()["timeline"]
    assert len(timeline) >= 1
    total = sum(entry["count"] for entry in timeline)
    assert total == 4
