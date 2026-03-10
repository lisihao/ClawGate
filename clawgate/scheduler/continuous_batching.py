"""Continuous Batching Scheduler - Phase 1.5 移植"""

import asyncio
import time
from typing import List, Dict, Optional, AsyncGenerator
from dataclasses import dataclass, field
from collections import deque
import logging

logger = logging.getLogger(__name__)


@dataclass
class Request:
    """请求对象"""

    request_id: str
    messages: List[Dict]
    max_tokens: int
    temperature: float
    priority: int = 1  # 0=高, 1=正常, 2=低
    agent_type: Optional[str] = None
    task_id: Optional[str] = None

    # 内部状态
    prompt_tokens: int = 0
    output_tokens: int = 0
    ttft: Optional[float] = None
    arrival_time: float = field(default_factory=time.time)
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    def __post_init__(self):
        self.arrival_time = time.time()


@dataclass
class Batch:
    """批次对象"""

    batch_id: str
    requests: List[Request] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    chunk_size: int = 256  # 默认 chunk 大小


class ContinuousBatchingScheduler:
    """Continuous Batching 调度器

    核心特性：
    1. 动态批处理 - 新请求随时加入
    2. 分块 Prefill - 长请求分块处理
    3. SJF 调度 - 短请求优先
    """

    def __init__(
        self,
        max_batch_size: int = 16,
        base_chunk_size: int = 256,
        enable_sjf: bool = True,
    ):
        self.max_batch_size = max_batch_size
        self.base_chunk_size = base_chunk_size
        self.enable_sjf = enable_sjf

        # 请求队列
        self.queue: deque[Request] = deque()

        # 当前批次
        self.current_batch: Optional[Batch] = None

        # 统计
        self.stats = {
            "total_requests": 0,
            "completed_requests": 0,
            "avg_ttft": 0.0,
            "p99_ttft": 0.0,
        }

        # 锁
        self.lock = asyncio.Lock()

    async def add_request(self, request: Request):
        """添加请求到队列"""
        async with self.lock:
            self.queue.append(request)
            self.stats["total_requests"] += 1

            logger.info(
                f"Added request {request.request_id}, "
                f"queue_size={len(self.queue)}, "
                f"priority={request.priority}"
            )

    async def schedule(self) -> Optional[Batch]:
        """
        调度下一个批次

        Returns:
            批次对象或 None
        """
        async with self.lock:
            if not self.queue:
                return None

            # 1. 排序队列（优先级 + SJF）
            if self.enable_sjf:
                self.queue = deque(
                    sorted(
                        self.queue,
                        key=lambda r: (r.priority, r.prompt_tokens),
                    )
                )

            # 2. 选择请求加入批次
            batch_requests = []
            while self.queue and len(batch_requests) < self.max_batch_size:
                req = self.queue.popleft()
                req.start_time = time.time()
                batch_requests.append(req)

            # 3. 创建批次
            batch = Batch(
                batch_id=f"batch-{int(time.time()*1000)}",
                requests=batch_requests,
                chunk_size=self._adaptive_chunk_size(batch_requests),
            )

            self.current_batch = batch
            return batch

    def _adaptive_chunk_size(self, requests: List[Request]) -> int:
        """
        自适应 chunk 大小

        短请求多 → 小 chunk (128)
        长请求多 → 大 chunk (512)
        """
        if not requests:
            return self.base_chunk_size

        avg_prompt_tokens = sum([r.prompt_tokens for r in requests]) / len(requests)

        if avg_prompt_tokens < 100:
            return 128  # 短请求，小 chunk
        elif avg_prompt_tokens > 1000:
            return 512  # 长请求，大 chunk
        else:
            return 256  # 中等

    async def process_batch(
        self, batch: Batch, engine
    ) -> AsyncGenerator[Dict, None]:
        """
        处理批次

        Args:
            batch: 批次对象
            engine: 推理引擎

        Yields:
            {
                "request_id": str,
                "chunk": str,
                "ttft": float (首次)
            }
        """
        logger.info(
            f"Processing batch {batch.batch_id}, "
            f"size={len(batch.requests)}, "
            f"chunk_size={batch.chunk_size}"
        )

        # 简化实现：顺序处理每个请求
        # 生产环境应该并行处理批次中的所有请求

        for request in batch.requests:
            first_token_time = None

            # 调用引擎生成
            from ..engines.base import GenerationRequest

            gen_request = GenerationRequest(
                messages=request.messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                stream=True,
                priority=request.priority,
                agent_type=request.agent_type,
                task_id=request.task_id,
            )

            async for chunk in engine.generate_stream(gen_request):
                # 记录 TTFT
                if first_token_time is None:
                    first_token_time = time.time()
                    request.ttft = first_token_time - request.start_time

                    yield {
                        "request_id": request.request_id,
                        "chunk": chunk,
                        "ttft": request.ttft,
                    }
                else:
                    yield {
                        "request_id": request.request_id,
                        "chunk": chunk,
                    }

            # 标记完成
            request.end_time = time.time()
            self.stats["completed_requests"] += 1

    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            **self.stats,
            "queue_size": len(self.queue),
            "current_batch_size": len(self.current_batch.requests)
            if self.current_batch
            else 0,
        }
