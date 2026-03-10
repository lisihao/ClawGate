"""请求队列管理器 - 解决 llama.cpp 并发问题

问题：llama.cpp 不支持真正的并发，多个请求同时到达会导致崩溃
解决：实现请求队列，串行化处理请求，但保持 API 异步接口
"""

import asyncio
import time
from typing import Dict, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class QueuedRequest:
    """队列中的请求"""
    request_id: str
    priority: int  # 0=高, 1=正常, 2=低
    handler: Callable  # 请求处理函数
    args: tuple = field(default_factory=tuple)
    kwargs: dict = field(default_factory=dict)

    # 状态
    enqueue_time: float = field(default_factory=time.time)
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    # 结果
    future: asyncio.Future = field(default_factory=asyncio.Future)


class RequestQueue:
    """请求队列管理器

    特性：
    - 按优先级排序（Priority Queue）
    - 串行执行（避免 llama.cpp 崩溃）
    - 异步接口（不阻塞 API）
    - 统计信息（延迟、吞吐量）
    """

    def __init__(self, max_queue_size: int = 100):
        self.max_queue_size = max_queue_size

        # 队列（按优先级排序）
        self.queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=max_queue_size)

        # 当前执行的请求
        self.current_request: Optional[QueuedRequest] = None

        # 统计
        self.stats = {
            "total_requests": 0,
            "completed_requests": 0,
            "failed_requests": 0,
            "queue_wait_times": [],
            "execution_times": [],
        }

        # 处理任务
        self.worker_task = None
        self.running = False

    async def start(self):
        """启动队列处理"""
        if self.running:
            return

        self.running = True
        self.worker_task = asyncio.create_task(self._worker())
        logger.info("RequestQueue 已启动")

    async def stop(self):
        """停止队列处理"""
        self.running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        logger.info("RequestQueue 已停止")

    async def submit(
        self,
        request_id: str,
        handler: Callable,
        priority: int = 1,
        *args,
        **kwargs
    ) -> Any:
        """提交请求到队列

        Args:
            request_id: 请求 ID
            handler: 处理函数（可以是同步或异步）
            priority: 优先级（0=高, 1=正常, 2=低）
            *args, **kwargs: 传递给 handler 的参数

        Returns:
            handler 的执行结果
        """
        # 创建请求对象
        request = QueuedRequest(
            request_id=request_id,
            priority=priority,
            handler=handler,
            args=args,
            kwargs=kwargs,
        )

        # 加入队列（按优先级排序）
        try:
            await self.queue.put((priority, time.time(), request))
            self.stats["total_requests"] += 1

            logger.debug(
                f"请求 {request_id} 已入队 (priority={priority}, queue_size={self.queue.qsize()})"
            )
        except asyncio.QueueFull:
            logger.error(f"队列已满，拒绝请求 {request_id}")
            raise Exception("Request queue is full")

        # 等待执行完成
        result = await request.future
        return result

    async def _worker(self):
        """后台工作线程 - 串行处理请求"""
        logger.info("RequestQueue worker 已启动")

        while self.running:
            try:
                # 从队列获取请求（阻塞等待）
                priority, enqueue_timestamp, request = await asyncio.wait_for(
                    self.queue.get(),
                    timeout=1.0
                )

                # 记录等待时间
                queue_wait_time = time.time() - request.enqueue_time
                self.stats["queue_wait_times"].append(queue_wait_time)

                logger.debug(
                    f"开始处理请求 {request.request_id} "
                    f"(等待 {queue_wait_time:.3f}s, queue_size={self.queue.qsize()})"
                )

                # 执行请求
                self.current_request = request
                request.start_time = time.time()

                try:
                    # 调用处理函数
                    if asyncio.iscoroutinefunction(request.handler):
                        result = await request.handler(*request.args, **request.kwargs)
                    else:
                        result = request.handler(*request.args, **request.kwargs)

                    # 设置结果
                    request.future.set_result(result)
                    request.end_time = time.time()

                    # 统计
                    execution_time = request.end_time - request.start_time
                    self.stats["execution_times"].append(execution_time)
                    self.stats["completed_requests"] += 1

                    logger.debug(
                        f"请求 {request.request_id} 完成 "
                        f"(执行 {execution_time:.3f}s, 总计 {execution_time + queue_wait_time:.3f}s)"
                    )

                except Exception as e:
                    # 处理错误
                    logger.error(f"请求 {request.request_id} 失败: {e}")
                    request.future.set_exception(e)
                    self.stats["failed_requests"] += 1

                finally:
                    self.current_request = None
                    self.queue.task_done()

            except asyncio.TimeoutError:
                # 队列空闲，继续等待
                continue
            except asyncio.CancelledError:
                logger.info("Worker 被取消")
                break
            except Exception as e:
                logger.error(f"Worker 错误: {e}")

        logger.info("RequestQueue worker 已停止")

    def get_stats(self) -> Dict:
        """获取统计信息"""
        import statistics

        stats = {
            "total_requests": self.stats["total_requests"],
            "completed_requests": self.stats["completed_requests"],
            "failed_requests": self.stats["failed_requests"],
            "queue_size": self.queue.qsize(),
            "current_request": self.current_request.request_id if self.current_request else None,
        }

        # 等待时间统计
        if self.stats["queue_wait_times"]:
            wait_times = self.stats["queue_wait_times"][-100:]  # 最近 100 个
            stats["avg_queue_wait"] = statistics.mean(wait_times)
            stats["p50_queue_wait"] = statistics.median(wait_times)
            stats["p99_queue_wait"] = statistics.quantiles(wait_times, n=100)[98] if len(wait_times) > 10 else max(wait_times)

        # 执行时间统计
        if self.stats["execution_times"]:
            exec_times = self.stats["execution_times"][-100:]
            stats["avg_execution_time"] = statistics.mean(exec_times)
            stats["p50_execution_time"] = statistics.median(exec_times)

        return stats
