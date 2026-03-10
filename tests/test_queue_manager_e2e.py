"""QueueManager 端到端集成测试

不 mock QueueManager, 不 mock CloudDispatcher.
注入一个假 backend (FakeBackend), 真实走完:
  HTTP 请求 → main_v2.py → TaskClassifier → ModelSelector(load_info) → QueueManager.submit()
    → _handle_cloud_request → CloudDispatcher.dispatch → FakeBackend.generate()
    → 响应 + SQLite 日志 + dashboard/scheduler

使用 httpx.ASGITransport 直接测试 ASGI app.
"""

import asyncio
import os
import time
import pytest
import sqlite3

import httpx

from clawgate.engines.base import GenerationResponse


# ========== Fake Backend ==========

class FakeBackend:
    """可控延迟的假后端, 真实走 CloudDispatcher 链路"""

    def __init__(self, delay: float = 0.01):
        self.delay = delay
        self.call_count = 0
        self.last_model = None

    async def generate(self, request, model=None):
        self.call_count += 1
        self.last_model = model
        await asyncio.sleep(self.delay)
        return GenerationResponse(
            content=f"Response from {model} (call #{self.call_count})",
            model=model or "fake",
            input_tokens=10,
            output_tokens=20,
            total_time=self.delay,
            ttft=self.delay / 2,
        )

    async def generate_stream(self, request, model=None):
        self.call_count += 1
        await asyncio.sleep(self.delay)
        yield f"chunk from {model}"

    async def close(self):
        pass


# ========== Fixtures ==========


@pytest.fixture
def fake_glm():
    return FakeBackend(delay=0.01)


@pytest.fixture
def fake_deepseek():
    return FakeBackend(delay=0.02)


@pytest.fixture
async def setup_app(fake_glm, fake_deepseek, tmp_path):
    """启动带真实 QueueManager 的 app, 注入 fake backends"""
    # 清除环境变量, 避免真实 backend 初始化
    for key in ["GLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                "CHATGPT_ACCESS_TOKEN", "GEMINI_API_KEY", "GOOGLE_API_KEY"]:
        os.environ.pop(key, None)

    from clawgate.api import main_v2 as m

    # 保存原始状态
    originals = {
        "cloud_backends": m.cloud_backends.copy(),
        "engine_manager": m.engine_manager,
        "cloud_dispatcher": m.cloud_dispatcher,
        "queue_manager": m.queue_manager,
        "db_store": m.db_store,
        "task_classifier": m.task_classifier,
        "model_selector": m.model_selector,
        "context_manager": m.context_manager,
        "semantic_cache": m.semantic_cache,
        "cb_scheduler": m.cb_scheduler,
    }

    # 注入 fake backends
    m.cloud_backends.clear()
    m.cloud_backends["glm"] = fake_glm
    m.cloud_backends["deepseek"] = fake_deepseek

    from clawgate.backends.cloud.dispatcher import CloudDispatcher
    m.cloud_dispatcher = CloudDispatcher(backends=m.cloud_backends, max_retries=2)

    from clawgate.storage.sqlite_store import SQLiteStore
    m.db_store = SQLiteStore(db_path=str(tmp_path / "sqlite"))

    from clawgate.router.classifier import TaskClassifier
    from clawgate.router.selector import ModelSelector
    m.task_classifier = TaskClassifier()
    m.model_selector = ModelSelector()

    m.engine_manager = None
    m.context_manager = None
    m.semantic_cache = None
    m.cb_scheduler = None

    # 初始化真实 QueueManager
    from clawgate.scheduler.queue_manager import QueueManager
    qm = QueueManager(concurrency_config={
        "max_total_queue": 50,
        "agent_fair_share": 0.6,
        "workers": {"fast": 2, "normal": 2, "background": 1},
        "concurrency": {
            "local_default": 1, "cloud_default": 5,
            "per_backend": {"deepseek": 3, "glm": 3},
        },
    })
    await qm.start()
    m.queue_manager = qm

    from clawgate.api.dashboard import init_dashboard, router as dashboard_router
    init_dashboard(m.db_store, m.cloud_dispatcher, m.context_manager, m.queue_manager)

    # 注册 dashboard router (startup_event 不走, 需手动注册)
    existing_paths = {r.path for r in m.app.routes}
    if "/dashboard/scheduler" not in existing_paths:
        m.app.include_router(dashboard_router)

    yield m.app, qm, fake_glm, fake_deepseek, m.db_store, m

    # 清理
    await qm.stop()
    m.cloud_backends.clear()
    m.cloud_backends.update(originals["cloud_backends"])
    m.engine_manager = originals["engine_manager"]
    m.cloud_dispatcher = originals["cloud_dispatcher"]
    m.queue_manager = originals["queue_manager"]
    m.db_store = originals["db_store"]
    m.task_classifier = originals["task_classifier"]
    m.model_selector = originals["model_selector"]
    m.context_manager = originals["context_manager"]
    m.semantic_cache = originals["semantic_cache"]
    m.cb_scheduler = originals["cb_scheduler"]


async def _post(app, path, json_data):
    """发送 POST 请求到 ASGI app"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(path, json=json_data)


async def _get(app, path):
    """发送 GET 请求到 ASGI app"""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(path)


# ========== E2E Tests ==========


class TestE2EBasicFlow:

    @pytest.mark.asyncio
    async def test_specified_model_goes_through_queue(self, setup_app):
        """指定模型 → QueueManager → CloudDispatcher → FakeBackend → 响应"""
        app, qm, fake_glm, fake_deepseek, db, m = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "glm-5",
            "messages": [{"role": "user", "content": "你好"}],
        })

        assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
        data = resp.json()

        # 验证 1: FakeBackend 真被调用了
        assert fake_glm.call_count >= 1, "FakeBackend.generate() 未被调用"

        # 验证 2: 响应包含 backend 返回的内容
        content = data["choices"][0]["message"]["content"]
        assert "Response from" in content, f"内容不对: {content}"
        assert "glm-5" in content

        # 验证 3: QueueManager 统计更新
        stats = qm.get_stats()
        assert stats["totals"]["submitted"] >= 1
        assert stats["totals"]["completed"] >= 1

    @pytest.mark.asyncio
    async def test_auto_model_selection(self, setup_app):
        """model=auto → TaskClassifier → ModelSelector → QueueManager"""
        app, qm, fake_glm, fake_deepseek, db, m = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "auto",
            "messages": [{"role": "user", "content": "简单问题"}],
        })

        assert resp.status_code == 200, f"status={resp.status_code} body={resp.text}"
        data = resp.json()

        # auto 模式下, 至少一个 backend 被调用
        assert fake_glm.call_count + fake_deepseek.call_count >= 1
        assert "choices" in data
        assert data["choices"][0]["message"]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_agent_id_propagated_to_queue(self, setup_app):
        """agent_id 传递到 QueueManager tracker"""
        app, qm, fake_glm, fake_deepseek, db, m = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "glm-5",
            "messages": [{"role": "user", "content": "test"}],
            "agent_id": "judge-001",
            "agent_type": "judge",
        })

        assert resp.status_code == 200

        stats = qm.get_stats()
        assert "judge-001" in stats["agents"], f"agents={stats['agents']}"
        assert stats["agents"]["judge-001"]["total"] >= 1

    @pytest.mark.asyncio
    async def test_priority_0_goes_through(self, setup_app):
        """priority=0 (urgent) 请求正常走完"""
        app, qm, fake_glm, *_ = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "glm-5",
            "messages": [{"role": "user", "content": "紧急问题"}],
            "priority": 0,
        })

        assert resp.status_code == 200
        assert fake_glm.call_count >= 1

    @pytest.mark.asyncio
    async def test_deepseek_model_works(self, setup_app):
        """deepseek 模型走完整链路"""
        app, qm, fake_glm, fake_deepseek, *_ = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "deepseek-v3",
            "messages": [{"role": "user", "content": "测试 deepseek"}],
        })

        assert resp.status_code == 200
        assert fake_deepseek.call_count >= 1
        content = resp.json()["choices"][0]["message"]["content"]
        assert "deepseek" in content


class TestE2ESchedulerDashboard:

    @pytest.mark.asyncio
    async def test_dashboard_scheduler_returns_data(self, setup_app):
        """/dashboard/scheduler 返回结构化数据"""
        app, qm, *_ = setup_app

        resp = await _get(app, "/dashboard/scheduler")

        assert resp.status_code == 200
        data = resp.json()
        assert "lanes" in data
        assert "models" in data
        assert "agents" in data
        assert "admission" in data
        assert data["admission"]["capacity"] == 50

    @pytest.mark.asyncio
    async def test_dashboard_updates_after_requests(self, setup_app):
        """发请求后 /dashboard/scheduler 数据更新"""
        app, qm, fake_glm, fake_deepseek, *_ = setup_app

        await _post(app, "/v1/chat/completions", {
            "model": "deepseek-v3",
            "messages": [{"role": "user", "content": "test"}],
            "agent_id": "builder-002",
        })

        resp = await _get(app, "/dashboard/scheduler")
        data = resp.json()

        assert data["totals"]["submitted"] >= 1
        assert data["totals"]["completed"] >= 1
        assert "builder-002" in data["agents"]


class TestE2ESQLiteLogging:

    @pytest.mark.asyncio
    async def test_agent_id_written_to_sqlite(self, setup_app):
        """agent_id 写入 requests.db"""
        app, qm, fake_glm, fake_deepseek, db, m = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "glm-5",
            "messages": [{"role": "user", "content": "db test"}],
            "agent_id": "tester-999",
            "agent_type": "flash",
        })

        assert resp.status_code == 200

        # 直接查 SQLite
        import sqlite3 as sq
        conn = sq.connect(str(db.db_path / "requests.db"))
        conn.row_factory = sq.Row
        rows = conn.execute(
            "SELECT agent_id, agent_type, model FROM requests ORDER BY timestamp DESC LIMIT 1"
        ).fetchall()
        conn.close()

        assert len(rows) >= 1, "requests 表无记录"
        row = dict(rows[0])
        assert row["agent_id"] == "tester-999", f"agent_id={row['agent_id']}"
        assert row["agent_type"] == "flash"
        assert row["model"] == "glm-5"


class TestE2ELoadAwareRouting:

    @pytest.mark.asyncio
    async def test_model_selector_gets_load_info(self, setup_app):
        """model=auto 时 select() 收到 load_info 参数"""
        app, qm, fake_glm, fake_deepseek, db, m = setup_app

        original_select = m.model_selector.select
        captured = {}

        def spy_select(*args, **kwargs):
            captured.update(kwargs)
            return original_select(*args, **kwargs)

        m.model_selector.select = spy_select
        try:
            resp = await _post(app, "/v1/chat/completions", {
                "model": "auto",
                "messages": [{"role": "user", "content": "负载测试"}],
            })

            assert resp.status_code == 200
            assert "load_info" in captured, f"kwargs={captured.keys()}"
            assert captured["load_info"] is not None
        finally:
            m.model_selector.select = original_select


class TestE2EDispatcherInFlight:

    @pytest.mark.asyncio
    async def test_health_includes_in_flight(self, setup_app):
        """get_health() 包含 in_flight 字段"""
        app, qm, fake_glm, fake_deepseek, db, m = setup_app

        health = m.cloud_dispatcher.get_health()
        for name, info in health.items():
            assert "in_flight" in info, f"{name} 缺少 in_flight"

    @pytest.mark.asyncio
    async def test_in_flight_returns_to_zero(self, setup_app):
        """请求完成后 in_flight 归零"""
        app, qm, fake_glm, fake_deepseek, db, m = setup_app

        await _post(app, "/v1/chat/completions", {
            "model": "glm-5",
            "messages": [{"role": "user", "content": "test"}],
        })

        in_flight = m.cloud_dispatcher.get_in_flight()
        assert in_flight.get("glm", 0) == 0


class TestE2EBackwardCompat:

    @pytest.mark.asyncio
    async def test_no_agent_id_works(self, setup_app):
        """不传 agent_id 正常工作"""
        app, *_ = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "glm-5",
            "messages": [{"role": "user", "content": "hello"}],
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_fields_ignored(self, setup_app):
        """未知字段被忽略"""
        app, *_ = setup_app

        resp = await _post(app, "/v1/chat/completions", {
            "model": "glm-5",
            "messages": [{"role": "user", "content": "test"}],
            "some_future_field": True,
            "another_field": 42,
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health_shows_queue_manager(self, setup_app):
        """/health 返回 queue_manager=True"""
        app, *_ = setup_app

        resp = await _get(app, "/health")
        data = resp.json()
        assert data["features"]["queue_manager"] is True
