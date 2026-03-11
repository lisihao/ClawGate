"""Context Shift Client - httpx 异步适配器

从 ThunderLLAMA context_shift_summarizer.py 迁移，改用 httpx 异步实现。

两阶段 LLM 摘要压缩：
- Stage 1 (0.6B @ 18083): 抽取 FACTS/DECISIONS/OPEN_ISSUES
- Stage 2 (1.7B @ 18084): 压缩成短记忆，添加 GOAL

迁移说明：
- requests → httpx.AsyncClient
- 同步函数 → async/await
- 添加 Circuit Breaker 降级机制
- 添加重试逻辑
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Circuit Breaker 降级机制"""

    def __init__(
        self,
        failure_threshold: int = 3,
        reset_timeout: float = 300.0,  # 5 分钟
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.is_open = False

    def record_success(self):
        """记录成功，重置失败计数"""
        self.failure_count = 0
        self.is_open = False
        logger.debug("Circuit Breaker: 记录成功，重置计数")

    def record_failure(self):
        """记录失败，可能打开断路器"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.is_open = True
            logger.warning(
                f"Circuit Breaker 打开: 连续 {self.failure_count} 次失败，"
                f"将在 {self.reset_timeout}s 后尝试恢复"
            )

    def can_attempt(self) -> bool:
        """是否可以尝试请求"""
        if not self.is_open:
            return True

        # 检查是否到了重置时间
        if self.last_failure_time is None:
            return True

        elapsed = time.time() - self.last_failure_time
        if elapsed > self.reset_timeout:
            logger.info("Circuit Breaker 尝试恢复（半开状态）")
            self.is_open = False
            self.failure_count = 0
            return True

        return False


class ContextShiftClient:
    """Context Shift 异步客户端（httpx）"""

    def __init__(
        self,
        stage1_endpoint: str = "http://127.0.0.1:18083/completion",
        stage2_endpoint: str = "http://127.0.0.1:18084/completion",
        mode: str = "auto",  # auto/fast/quality/simple
        auto_threshold: int = 10,  # auto 模式的轮数阈值
        timeout: float = 60.0,
        max_retries: int = 3,
        circuit_breaker_enabled: bool = True,
        circuit_breaker_threshold: int = 3,
        circuit_breaker_reset_timeout: float = 300.0,
    ):
        """
        初始化 Context Shift 客户端

        Args:
            stage1_endpoint: Stage 1 端点（0.6B 抽取）
            stage2_endpoint: Stage 2 端点（1.7B 压缩）
            mode: 摘要模式
                - auto: 根据对话轮数自动选择（< 10 轮 fast, >= 10 轮 quality）
                - fast: 快速模式（0.6B + 0.6B）
                - quality: 质量模式（0.6B + 1.7B）
                - simple: 简单模式（字符截断，不调用 LLM）
            auto_threshold: auto 模式的轮数阈值
            timeout: 请求超时（秒）
            max_retries: 最大重试次数
            circuit_breaker_enabled: 是否启用 Circuit Breaker
            circuit_breaker_threshold: Circuit Breaker 失败阈值
            circuit_breaker_reset_timeout: Circuit Breaker 重置超时（秒）
        """
        self.stage1_endpoint = stage1_endpoint
        self.stage2_endpoint = stage2_endpoint
        self.mode = mode
        self.auto_threshold = auto_threshold
        self.timeout = timeout
        self.max_retries = max_retries

        # Circuit Breaker
        self.circuit_breaker: Optional[CircuitBreaker] = None
        if circuit_breaker_enabled:
            self.circuit_breaker = CircuitBreaker(
                failure_threshold=circuit_breaker_threshold,
                reset_timeout=circuit_breaker_reset_timeout,
            )

        # httpx 客户端（延迟初始化）
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """获取或创建 httpx 客户端"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self):
        """关闭客户端"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def summarize(
        self,
        messages: List[Dict[str, Any]],
        target_tokens: int = 200,
    ) -> Optional[str]:
        """
        对消息列表进行两阶段摘要

        Args:
            messages: 消息列表 [{"role": "user", "content": "..."}]
            target_tokens: 目标摘要长度（tokens，暂未使用）

        Returns:
            摘要文本，失败时返回 None（调用方应 fallback 到简单压缩）
        """
        if not messages:
            return None

        # 根据模式选择实际执行方式
        effective_mode = self._resolve_mode(len(messages))

        if effective_mode == "simple":
            # 简单模式：不调用 LLM
            return None

        # Circuit Breaker 检查
        if self.circuit_breaker and not self.circuit_breaker.can_attempt():
            logger.warning("Circuit Breaker 打开，跳过 Context Shift，降级到简单压缩")
            return None

        # 构建对话文本
        conversation = "\n\n".join(
            [
                f"{msg.get('role', 'user')}: {self._flatten_content(msg.get('content', ''))}"
                for msg in messages
            ]
        )

        try:
            # 阶段 1：抽取事实、决策、问题
            raw_extract = await self._extract_raw(conversation)
            if not raw_extract or raw_extract.startswith("ERROR:"):
                self._record_failure()
                return None

            # 阶段 2：压缩成短记忆，添加 GOAL
            # fast 模式使用 stage1_endpoint（0.6B），quality 使用 stage2_endpoint（1.7B）
            stage2_endpoint = (
                self.stage1_endpoint if effective_mode == "fast" else self.stage2_endpoint
            )
            final_memory = await self._compress_to_memory(raw_extract, stage2_endpoint)
            if not final_memory or final_memory.startswith("ERROR:"):
                # 降级：返回阶段 1 结果
                logger.warning("Stage 2 失败，返回 Stage 1 结果")
                self._record_success()  # 阶段 1 成功
                return raw_extract

            self._record_success()
            return final_memory

        except Exception as e:
            logger.error(f"Context Shift 摘要失败: {e}", exc_info=True)
            self._record_failure()
            return None

    def _resolve_mode(self, num_messages: int) -> str:
        """根据 mode 和轮数解析实际模式"""
        if self.mode == "auto":
            # auto 模式：根据轮数自动选择
            if num_messages < self.auto_threshold:
                return "fast"
            else:
                return "quality"
        return self.mode

    async def _extract_raw(self, conversation: str) -> str:
        """阶段 1：抽取事实、决策、问题（0.6B 模型）"""
        prompt = f"""Extract only the key information from this conversation.

Do not summarize. Just list the facts, decisions, and issues.

Format:

[FACTS]
- fact 1
- fact 2

[DECISIONS]
- decision 1
- decision 2

[OPEN_ISSUES]
- issue 1
- issue 2

<END_EXTRACT>

Conversation:
{conversation}

Extracted:
"""
        sampling_params = {
            "prompt": prompt,
            "n_predict": 150,
            "temperature": 0.0,
            "top_p": 0.9,
            "repeat_penalty": 1.15,
            "stop": ["<END_EXTRACT>", "Conversation:", "User:", "Assistant:"],
        }

        return await self._call_endpoint(self.stage1_endpoint, sampling_params)

    async def _compress_to_memory(self, raw_extract: str, endpoint: str) -> str:
        """阶段 2：压缩成短记忆，添加 GOAL"""
        prompt = f"""Add a GOAL summary to this extracted information.

Keep all facts, decisions, and issues. Just add a GOAL at the beginning.

Extracted information:
{raw_extract}

Output with GOAL:

[GOAL]
- (write a brief summary of what user wants)

{raw_extract}

<END_SUMMARY>

Output:
"""
        sampling_params = {
            "prompt": prompt,
            "n_predict": 180,
            "temperature": 0.0,
            "top_p": 0.9,
            "repeat_penalty": 1.15,
            "stop": ["<END_SUMMARY>", "Extracted information:"],
        }

        return await self._call_endpoint(endpoint, sampling_params)

    async def _call_endpoint(
        self,
        endpoint: str,
        sampling_params: Dict[str, Any],
    ) -> str:
        """调用端点，支持重试"""
        client = await self._get_client()
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                response = await client.post(endpoint, json=sampling_params)
                response.raise_for_status()
                data = response.json()
                content = data.get("content", "").strip()
                return content

            except httpx.HTTPStatusError as e:
                last_error = e
                logger.warning(
                    f"HTTP 错误 (尝试 {attempt + 1}/{self.max_retries}): "
                    f"{e.response.status_code}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))  # 指数退避

            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    f"请求错误 (尝试 {attempt + 1}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))

            except Exception as e:
                last_error = e
                logger.error(f"未知错误 (尝试 {attempt + 1}/{self.max_retries}): {e}")
                break  # 未知错误，不重试

        return f"ERROR: {str(last_error)}"

    def _flatten_content(self, content: Any) -> str:
        """展平内容（处理字符串或列表）"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: List[str] = []
            for c in content:
                if isinstance(c, dict):
                    txt = c.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
                elif isinstance(c, str):
                    parts.append(c)
            return "\n".join(parts)
        return ""

    def _record_success(self):
        """记录成功"""
        if self.circuit_breaker:
            self.circuit_breaker.record_success()

    def _record_failure(self):
        """记录失败"""
        if self.circuit_breaker:
            self.circuit_breaker.record_failure()

    async def health_check(self) -> Tuple[bool, str]:
        """
        检查两个摘要服务是否就绪

        Returns:
            (is_healthy, message)
        """
        client = await self._get_client()

        try:
            # 检查阶段 1 服务
            resp1 = await client.get(
                self.stage1_endpoint.replace("/completion", "/health"),
                timeout=5.0,
            )
            if resp1.status_code != 200:
                return (False, f"Stage 1 服务 (0.6B) 未就绪: {resp1.status_code}")

            # 检查阶段 2 服务
            resp2 = await client.get(
                self.stage2_endpoint.replace("/completion", "/health"),
                timeout=5.0,
            )
            if resp2.status_code != 200:
                return (False, f"Stage 2 服务 (1.7B) 未就绪: {resp2.status_code}")

            return (True, "两个摘要服务都就绪")

        except Exception as e:
            return (False, f"健康检查失败: {str(e)}")


# 简单的 fallback 摘要函数（字符截断）
def simple_compact_history(messages: List[Dict[str, Any]], max_lines: int = 12) -> str:
    """简单压缩历史（字符截断）"""
    lines: List[str] = []
    for m in messages[-24:]:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            txt = content.strip()
        else:
            txt = str(content)
        if not txt:
            continue
        lines.append(f"- {role}: {txt[:140]}")
    if not lines:
        return ""
    return "History tail summary:\n" + "\n".join(lines[-max_lines:])
