"""QueueManager 单元测试"""

import asyncio
import pytest
import time
from unittest.mock import AsyncMock

from clawgate.scheduler.queue_manager import (
    QueueManager,
    ScheduledRequest,
    DurationEstimate,
    AdmissionError,
    AgentTracker,
)


@pytest.fixture
def qm():
    """创建一个测试用 QueueManager"""
    return QueueManager(concurrency_config={
        "max_total_queue": 10,
        "agent_fair_share": 0.6,
        "workers": {"fast": 1, "normal": 1, "background": 1},
        "concurrency": {
            "local_default": 1,
            "cloud_default": 3,
            "per_backend": {"deepseek": 2, "glm": 2, "gemini": 2},
        },
    })


# ========== Duration Estimation ==========


class TestDurationEstimation:

    def test_qa_short_is_fast(self, qm):
        result = qm.estimate_duration(
            {"task_type": "qa", "complexity": "low"}, msg_length=100, is_stream=False
        )
        assert result == DurationEstimate.FAST

    def test_qa_medium_length_is_fast(self, qm):
        result = qm.estimate_duration(
            {"task_type": "qa", "complexity": "medium"}, msg_length=300, is_stream=False
        )
        assert result == DurationEstimate.FAST

    def test_coding_high_is_long(self, qm):
        result = qm.estimate_duration(
            {"task_type": "coding", "complexity": "high"}, msg_length=1000, is_stream=False
        )
        assert result == DurationEstimate.LONG

    def test_reasoning_high_is_long(self, qm):
        result = qm.estimate_duration(
            {"task_type": "reasoning", "complexity": "high"}, msg_length=500, is_stream=False
        )
        assert result == DurationEstimate.LONG

    def test_high_complexity_long_msg_is_long(self, qm):
        result = qm.estimate_duration(
            {"task_type": "chat", "complexity": "high"}, msg_length=3000, is_stream=False
        )
        assert result == DurationEstimate.LONG

    def test_medium_complexity_is_medium(self, qm):
        result = qm.estimate_duration(
            {"task_type": "coding", "complexity": "medium"}, msg_length=800, is_stream=False
        )
        assert result == DurationEstimate.MEDIUM

    def test_none_task_info_is_medium(self, qm):
        result = qm.estimate_duration(None, msg_length=500, is_stream=False)
        assert result == DurationEstimate.MEDIUM


# ========== Submit + Lane Assignment ==========


class TestSubmitAndLanes:

    @pytest.mark.asyncio
    async def test_fast_priority_goes_to_fast_lane(self, qm):
        await qm.start()
        try:
            handler = AsyncMock(return_value={"ok": True})
            req = ScheduledRequest(
                request_id="r1", model="glm-5", priority=0,
                duration_estimate=DurationEstimate.FAST,
            )
            result = await qm.submit(req, handler)
            assert result == {"ok": True}
            handler.assert_called_once()
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_background_priority_goes_to_background(self, qm):
        await qm.start()
        try:
            handler = AsyncMock(return_value="bg_result")
            req = ScheduledRequest(
                request_id="r2", model="glm-5", priority=2,
                duration_estimate=DurationEstimate.MEDIUM,
            )
            result = await qm.submit(req, handler)
            assert result == "bg_result"
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_long_duration_goes_to_background(self, qm):
        await qm.start()
        try:
            handler = AsyncMock(return_value="long_result")
            req = ScheduledRequest(
                request_id="r3", model="deepseek-r1", priority=1,
                duration_estimate=DurationEstimate.LONG,
            )
            result = await qm.submit(req, handler)
            assert result == "long_result"
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_normal_request(self, qm):
        await qm.start()
        try:
            handler = AsyncMock(return_value="normal_result")
            req = ScheduledRequest(
                request_id="r4", model="glm-5", priority=1,
                duration_estimate=DurationEstimate.MEDIUM,
            )
            result = await qm.submit(req, handler)
            assert result == "normal_result"
        finally:
            await qm.stop()


# ========== Fast Lane Priority ==========


class TestFastLanePriority:

    @pytest.mark.asyncio
    async def test_fast_completes_before_background(self, qm):
        """快车道请求应该在后台请求之前完成"""
        await qm.start()
        try:
            order = []

            async def slow_handler():
                await asyncio.sleep(0.05)
                order.append("background")
                return "bg"

            async def fast_handler():
                order.append("fast")
                return "fast"

            # 先提交 background, 再提交 fast
            bg_req = ScheduledRequest(
                request_id="bg", model="glm-5", priority=2,
                duration_estimate=DurationEstimate.LONG,
            )
            fast_req = ScheduledRequest(
                request_id="fast", model="glm-5", priority=0,
                duration_estimate=DurationEstimate.FAST,
            )

            bg_task = asyncio.create_task(qm.submit(bg_req, slow_handler))
            fast_task = asyncio.create_task(qm.submit(fast_req, fast_handler))

            await asyncio.gather(bg_task, fast_task)
            # fast 应该先完成 (先出现在 order 中)
            assert order[0] == "fast"
        finally:
            await qm.stop()


# ========== Admission Control ==========


class TestAdmissionControl:

    @pytest.mark.asyncio
    async def test_queue_full_raises_admission_error(self, qm):
        """队列满时返回 AdmissionError"""
        await qm.start()
        try:
            # 让 handler 阻塞, 填满队列
            blocker = asyncio.Event()

            async def blocking_handler():
                await blocker.wait()
                return "done"

            # 提交 max_total_queue 个请求 (10)
            tasks = []
            for i in range(10):
                req = ScheduledRequest(
                    request_id=f"fill-{i}", model="glm-5", priority=1,
                    duration_estimate=DurationEstimate.MEDIUM,
                )
                tasks.append(asyncio.create_task(qm.submit(req, blocking_handler)))

            await asyncio.sleep(0.1)  # 等待入队

            # 第 11 个应该被拒绝
            req_overflow = ScheduledRequest(
                request_id="overflow", model="glm-5", priority=1,
                duration_estimate=DurationEstimate.MEDIUM,
            )
            with pytest.raises(AdmissionError):
                await qm.submit(req_overflow, blocking_handler)

            # 清理
            blocker.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await qm.stop()


# ========== Agent Fairness ==========


class TestAgentFairness:

    @pytest.mark.asyncio
    async def test_agent_demotion(self):
        """超额 agent 被降级到 background"""
        # 使用更小的并发和 fair_share 来确保触发降级
        # deepseek max_concurrent=2, fair_share=0.5, fair_limit=ceil(2*0.5)=1
        qm = QueueManager(concurrency_config={
            "max_total_queue": 20,
            "agent_fair_share": 0.5,
            "workers": {"fast": 1, "normal": 2, "background": 1},
            "concurrency": {
                "local_default": 1,
                "cloud_default": 3,
                "per_backend": {"deepseek": 2, "glm": 2, "gemini": 2},
            },
        })
        await qm.start()
        try:
            blocker = asyncio.Event()

            async def blocking_handler():
                await blocker.wait()
                return "done"

            async def quick_handler():
                return "quick"

            # fair_limit = ceil(2*0.5) = 1
            # 提交 1 个阻塞请求, 让 in_flight 达到 1
            req1 = ScheduledRequest(
                request_id="block-0", model="deepseek-v3", priority=1,
                agent_id="greedy-agent", agent_type="builder",
                duration_estimate=DurationEstimate.MEDIUM,
            )
            asyncio.create_task(qm.submit(req1, blocking_handler))
            await asyncio.sleep(0.1)  # 等待 worker 接手

            # 第 2 个请求: in_flight=1 >= fair_limit=1 → 降级
            req2 = ScheduledRequest(
                request_id="demote-me", model="deepseek-v3", priority=1,
                agent_id="greedy-agent", agent_type="builder",
                duration_estimate=DurationEstimate.MEDIUM,
            )
            task2 = asyncio.create_task(qm.submit(req2, quick_handler))
            await asyncio.sleep(0.05)

            # 验证 tracker 中 demoted_count > 0
            tracker = qm._agent_trackers.get("greedy-agent")
            assert tracker is not None
            assert tracker.demoted_count >= 1

            # 清理
            blocker.set()
            await asyncio.gather(task2, return_exceptions=True)
        finally:
            await qm.stop()


# ========== Per-Model Semaphore ==========


class TestModelSemaphore:

    @pytest.mark.asyncio
    async def test_local_model_concurrency_1(self, qm):
        """本地模型信号量=1, 请求串行化"""
        await qm.start()
        try:
            order = []

            async def handler_1():
                order.append("start_1")
                await asyncio.sleep(0.05)
                order.append("end_1")
                return "r1"

            async def handler_2():
                order.append("start_2")
                await asyncio.sleep(0.05)
                order.append("end_2")
                return "r2"

            req1 = ScheduledRequest(
                request_id="local-1", model="qwen2.5-7b-mlx", priority=1,
                duration_estimate=DurationEstimate.MEDIUM,
            )
            req2 = ScheduledRequest(
                request_id="local-2", model="qwen2.5-7b-mlx", priority=1,
                duration_estimate=DurationEstimate.MEDIUM,
            )

            t1 = asyncio.create_task(qm.submit(req1, handler_1))
            t2 = asyncio.create_task(qm.submit(req2, handler_2))

            await asyncio.gather(t1, t2)

            # 由于信号量=1, 第一个必须完成才开始第二个
            assert order[0] == "start_1"
            assert order[1] == "end_1"
            assert order[2] == "start_2"
            assert order[3] == "end_2"
        finally:
            await qm.stop()


# ========== Error Handling ==========


class TestErrorHandling:

    @pytest.mark.asyncio
    async def test_handler_exception_propagates(self, qm):
        """handler 异常应该传播到调用方"""
        await qm.start()
        try:
            async def failing_handler():
                raise ValueError("test error")

            req = ScheduledRequest(
                request_id="err-1", model="glm-5", priority=1,
                duration_estimate=DurationEstimate.MEDIUM,
            )
            with pytest.raises(ValueError, match="test error"):
                await qm.submit(req, failing_handler)
        finally:
            await qm.stop()


# ========== Stats ==========


class TestStats:

    @pytest.mark.asyncio
    async def test_get_stats_structure(self, qm):
        """get_stats 返回正确的结构"""
        await qm.start()
        try:
            stats = qm.get_stats()
            assert "lanes" in stats
            assert "models" in stats
            assert "agents" in stats
            assert "admission" in stats
            assert "totals" in stats

            assert stats["lanes"]["fast"]["workers"] == 1
            assert stats["lanes"]["normal"]["workers"] == 1
            assert stats["lanes"]["background"]["workers"] == 1
            assert stats["admission"]["capacity"] == 10
            assert stats["admission"]["used"] == 0
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_stats_after_requests(self, qm):
        """请求完成后 stats 更新"""
        await qm.start()
        try:
            handler = AsyncMock(return_value="ok")
            req = ScheduledRequest(
                request_id="stat-1", model="glm-5", priority=1,
                agent_id="test-agent",
                duration_estimate=DurationEstimate.MEDIUM,
            )
            await qm.submit(req, handler)

            stats = qm.get_stats()
            assert stats["totals"]["submitted"] == 1
            assert stats["totals"]["completed"] == 1
            assert "test-agent" in stats["agents"]
            assert stats["agents"]["test-agent"]["total"] == 1
        finally:
            await qm.stop()


# ========== Load Info ==========


class TestLoadInfo:

    @pytest.mark.asyncio
    async def test_get_model_load(self, qm):
        """get_model_load 返回正确结构"""
        await qm.start()
        try:
            load = qm.get_model_load("glm-5")
            assert "in_flight" in load
            assert "max_concurrent" in load
            assert "queue_depth" in load
            assert load["max_concurrent"] == 2  # per_backend glm=2
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_get_all_loads_empty(self, qm):
        """无请求时 all_loads 为空"""
        loads = qm.get_all_loads()
        assert isinstance(loads, dict)


# ========== Backward Compatibility ==========


class TestBackwardCompat:

    @pytest.mark.asyncio
    async def test_no_agent_id_works(self, qm):
        """无 agent_id 的请求正常工作"""
        await qm.start()
        try:
            handler = AsyncMock(return_value="compat")
            req = ScheduledRequest(
                request_id="compat-1", model="deepseek-v3", priority=1,
                duration_estimate=DurationEstimate.MEDIUM,
            )
            result = await qm.submit(req, handler)
            assert result == "compat"
        finally:
            await qm.stop()

    def test_default_config(self):
        """默认配置创建 QueueManager"""
        qm = QueueManager()
        assert qm._max_total_queue == 200
        assert qm._worker_counts["fast"] == 2


# ========== Semaphore / Model Detection ==========


class TestSemaphoreResolution:

    def test_known_model_uses_backend_hint(self, qm):
        """已知模型使用 MODEL_BACKEND_HINT"""
        sem = qm._get_semaphore("glm-5")
        assert "glm-5" in qm._model_semaphores
        # glm backend limit = 2 in test config
        assert qm._get_model_max_concurrent("glm-5") == 2

    def test_local_model_prefix(self, qm):
        """本地模型前缀检测"""
        sem = qm._get_semaphore("llama-3.2-8b")
        assert qm._get_model_max_concurrent("llama-3.2-8b") == 1

    def test_unknown_prefix_glm(self, qm):
        """未知模型但 glm 前缀 → _get_semaphore 用 prefix 匹配"""
        sem = qm._get_semaphore("glm-99-turbo")
        assert "glm-99-turbo" in qm._model_semaphores
        # _get_model_max_concurrent doesn't repeat prefix logic, returns cloud_default
        assert qm._get_model_max_concurrent("glm-99-turbo") == 3

    def test_unknown_prefix_deepseek(self, qm):
        """未知模型但 deepseek 前缀 → _get_semaphore 用 prefix 匹配"""
        sem = qm._get_semaphore("deepseek-v99")
        assert "deepseek-v99" in qm._model_semaphores

    def test_unknown_prefix_gpt5(self, qm):
        """未知模型但 gpt-5 前缀 → chatgpt backend"""
        sem = qm._get_semaphore("gpt-5.9-turbo")
        assert "gpt-5.9-turbo" in qm._model_semaphores

    def test_unknown_prefix_gemini(self, qm):
        """未知模型但 gemini 前缀 → _get_semaphore 用 prefix 匹配"""
        sem = qm._get_semaphore("gemini-3.0-ultra")
        assert "gemini-3.0-ultra" in qm._model_semaphores

    def test_completely_unknown_model(self, qm):
        """完全未知模型 → cloud_default"""
        sem = qm._get_semaphore("claude-opus-99")
        assert qm._get_model_max_concurrent("claude-opus-99") == 3

    def test_phi_prefix_is_local(self, qm):
        """phi 前缀识别为本地模型"""
        assert qm._get_model_max_concurrent("phi-4-mini") == 1

    def test_mistral_prefix_is_local(self, qm):
        """mistral 前缀识别为本地模型"""
        assert qm._get_model_max_concurrent("mistral-7b") == 1


# ========== Stream Wrapping ==========


class TestStreamWrapping:

    @pytest.mark.asyncio
    async def test_stream_request_returns_streaming_response(self, qm):
        """流式请求正常返回"""
        from fastapi.responses import StreamingResponse

        await qm.start()
        try:
            async def stream_handler():
                async def gen():
                    yield "chunk1"
                    yield "chunk2"
                return StreamingResponse(gen(), media_type="text/event-stream")

            req = ScheduledRequest(
                request_id="stream-1", model="glm-5", priority=1,
                is_stream=True,
                duration_estimate=DurationEstimate.MEDIUM,
            )
            result = await qm.submit(req, stream_handler)
            assert isinstance(result, StreamingResponse)
        finally:
            await qm.stop()


# ========== Worker Edge Cases ==========


class TestWorkerEdgeCases:

    @pytest.mark.asyncio
    async def test_stop_cancels_workers(self, qm):
        """stop() 取消所有 worker"""
        await qm.start()
        assert len(qm._workers) > 0
        await qm.stop()
        assert len(qm._workers) == 0

    @pytest.mark.asyncio
    async def test_multiple_requests_same_model(self, qm):
        """多个请求同一个模型, 信号量正确工作"""
        await qm.start()
        try:
            call_count = 0

            async def handler():
                nonlocal call_count
                call_count += 1
                return f"result-{call_count}"

            tasks = []
            for i in range(5):
                req = ScheduledRequest(
                    request_id=f"multi-{i}", model="glm-5", priority=1,
                    duration_estimate=DurationEstimate.FAST,
                )
                tasks.append(asyncio.create_task(qm.submit(req, handler)))

            results = await asyncio.gather(*tasks)
            assert len(results) == 5
            assert call_count == 5
        finally:
            await qm.stop()

    @pytest.mark.asyncio
    async def test_rejected_count_increments(self, qm):
        """准入拒绝计数增加"""
        await qm.start()
        try:
            blocker = asyncio.Event()

            async def blocking_handler():
                await blocker.wait()
                return "done"

            # 填满队列
            tasks = []
            for i in range(10):
                req = ScheduledRequest(
                    request_id=f"fill-{i}", model="glm-5", priority=1,
                    duration_estimate=DurationEstimate.MEDIUM,
                )
                tasks.append(asyncio.create_task(qm.submit(req, blocking_handler)))

            await asyncio.sleep(0.1)
            assert qm._total_rejected == 0

            # 尝试提交被拒绝的请求
            for _ in range(3):
                try:
                    req = ScheduledRequest(
                        request_id="overflow", model="glm-5", priority=1,
                        duration_estimate=DurationEstimate.MEDIUM,
                    )
                    await qm.submit(req, blocking_handler)
                except AdmissionError:
                    pass

            assert qm._total_rejected == 3

            blocker.set()
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            await qm.stop()
