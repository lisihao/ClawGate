"""ThunderLLAMA 引擎 — 通过 HTTP 与 llama-server 通信"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator, Dict, Optional

import httpx

from .base import BaseEngine, GenerationRequest, GenerationResponse

logger = logging.getLogger(__name__)

THUNDERLLAMA_AVAILABLE = True  # HTTP-based, always available if server reachable


class ThunderLlamaEngine(BaseEngine):
    """ThunderLLAMA 引擎

    通过 HTTP 与 llama-server 进程通信（OpenAI 兼容 API）。
    如果目标端口已有 llama-server 在运行则复用，否则启动子进程。
    """

    def __init__(
        self,
        model_path: str,
        model_name: str = "",
        server_binary: str = "llama-server",
        port: int = 8090,
        host: str = "127.0.0.1",
        n_gpu_layers: int = 99,
        n_parallel: int = 4,
        n_ctx: int = 8192,
        cache_ram_mb: int = 4096,
        cache_tuner_config: Optional[Dict] = None,
        cont_batching: bool = True,
        flash_attention: bool = True,
        paged_attention: bool = True,
        chunk_prefill: Optional[int] = 512,
        startup_timeout: float = 30.0,
        request_timeout: float = 120.0,
    ):
        super().__init__("thunderllama", model_path)

        self.model_name = model_name or Path(model_path).stem
        self.server_binary = os.path.expanduser(server_binary)
        self.host = host
        self.port = port
        self.n_gpu_layers = n_gpu_layers
        self.n_parallel = n_parallel
        self.n_ctx = n_ctx
        self.cache_ram_mb = cache_ram_mb
        self.cont_batching = cont_batching
        self.flash_attention = flash_attention
        self.paged_attention = paged_attention
        self.chunk_prefill = chunk_prefill
        self.startup_timeout = startup_timeout

        self.base_url = f"http://{host}:{port}"
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(request_timeout, connect=10.0),
        )

        self._process: Optional[subprocess.Popen] = None
        self._owns_process = False  # True if we started the process

        # Stats
        self._request_count = 0
        self._total_tokens = 0
        self._total_time = 0.0

        # Cache Tuner
        self.cache_tuner = None
        if cache_tuner_config and cache_tuner_config.get("enabled"):
            self._init_cache_tuner(cache_tuner_config)

        # 后台调优任务句柄
        self._tuning_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def ensure_running(self) -> None:
        """确保 llama-server 可用：复用已有进程或启动新进程"""

        if await self._health_ok():
            logger.info(
                "ThunderLLAMA: 检测到 llama-server 已在 %s 运行，复用",
                self.base_url,
            )
            return

        # 没有可用服务器 → 启动子进程
        await self._start_server()

    async def _health_ok(self) -> bool:
        """检查 /health 是否返回 OK"""
        try:
            resp = await self.client.get("/health", timeout=3.0)
            return resp.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            return False

    async def _start_server(self) -> None:
        """启动 llama-server 子进程并等待就绪"""

        binary = Path(self.server_binary)
        if not binary.exists():
            raise FileNotFoundError(
                f"llama-server 不存在: {binary}. "
                "请先编译 ThunderLLAMA: cd ~/ThunderLLAMA && cmake -B build && cmake --build build -j"
            )

        model = os.path.expanduser(self.model_path)
        if not Path(model).exists():
            raise FileNotFoundError(f"模型文件不存在: {model}")

        cmd = [
            str(binary),
            "-m", model,
            "--host", self.host,
            "--port", str(self.port),
            "-ngl", str(self.n_gpu_layers),
            "-np", str(self.n_parallel),
            "-c", str(self.n_ctx),
            "--cache-ram", str(self.cache_ram_mb),
        ]
        if self.cont_batching:
            cmd.append("--cont-batching")
        if self.flash_attention:
            cmd.extend(["-fa", "1"])

        # ThunderLLAMA 自研特性通过环境变量启用
        env = os.environ.copy()
        if self.paged_attention:
            env["LLAMA_PAGED_ATTENTION"] = "1"
        if self.chunk_prefill is not None:
            env["THUNDERLLAMA_CHUNK_PREFILL"] = str(self.chunk_prefill)

        logger.info("ThunderLLAMA: 启动 llama-server → %s", " ".join(cmd))
        logger.info("ThunderLLAMA: cache-ram = %d MiB", self.cache_ram_mb)
        if self.paged_attention:
            logger.info("ThunderLLAMA: Paged Attention 已启用")
        if self.chunk_prefill is not None:
            logger.info("ThunderLLAMA: Chunk Prefill = %d", self.chunk_prefill)

        self._process = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,
        )
        self._owns_process = True

        # 等待 /health 就绪
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(
                    f"llama-server 启动后立即退出 (exit code {self._process.returncode})"
                )
            if await self._health_ok():
                logger.info(
                    "ThunderLLAMA: llama-server 就绪 (PID %d, port %d)",
                    self._process.pid,
                    self.port,
                )
                return
            await asyncio.sleep(0.5)

        # 超时 → 清理
        self._kill_process()
        raise TimeoutError(
            f"llama-server 在 {self.startup_timeout}s 内未就绪"
        )

    # ------------------------------------------------------------------
    # BaseEngine interface
    # ------------------------------------------------------------------

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """非流式生成"""
        await self.ensure_running()

        start = time.monotonic()

        payload = {
            "messages": request.messages,
            "max_tokens": request.max_tokens or 2048,
            "temperature": request.temperature or 0.7,
            "stream": False,
        }
        if request.top_p is not None:
            payload["top_p"] = request.top_p

        # 🔥 添加 ContextPilot Dedup headers (Phase 3 集成)
        headers = {}
        if hasattr(request, 'contextpilot_metadata') and request.contextpilot_metadata:
            meta = request.contextpilot_metadata

            # New format: X-Context-Signature + X-Context-Chunks
            if meta.get("signature"):
                headers["X-Context-Signature"] = meta["signature"]

            if meta.get("chunk_hashes"):
                headers["X-Context-Chunks"] = json.dumps(meta["chunk_hashes"])

            # Legacy format: x-contextpilot-optimal-order (deprecated)
            if meta.get("optimized") and meta.get("method") == "metadata_reorder":
                headers["x-contextpilot-optimal-order"] = str(meta.get("optimal_order", []))
                headers["x-contextpilot-blocks"] = str(meta.get("blocks", 0))
                headers["x-contextpilot-importance"] = meta.get("importance", "")

        resp = await self.client.post(
            "/v1/chat/completions",
            json=payload,
            headers=headers if headers else None,
        )
        resp.raise_for_status()
        data = resp.json()

        elapsed = time.monotonic() - start

        choice = data["choices"][0]
        message = choice.get("message", {})
        # Qwen3 可能把内容放在 reasoning_content 而非 content
        content = message.get("content") or message.get("reasoning_content", "")

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # 提取 llama-server 的 timings 元数据
        timings = data.get("timings", {})
        metadata = {
            "engine": "thunderllama",
            "port": self.port,
        }
        if timings:
            metadata["tokens_per_second"] = timings.get("predicted_per_second", 0)
            metadata["prompt_ms"] = timings.get("prompt_ms", 0)
            metadata["predicted_ms"] = timings.get("predicted_ms", 0)

        # Stats
        self._request_count += 1
        self._total_tokens += input_tokens + output_tokens
        self._total_time += elapsed

        return GenerationResponse(
            content=content,
            model=self.model_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_time=elapsed,
            ttft=timings.get("prompt_ms", elapsed * 1000) / 1000,
            metadata=metadata,
        )

    async def generate_stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[str]:
        """流式生成 — 解析 SSE 并 yield 文本块"""
        await self.ensure_running()

        payload = {
            "messages": request.messages,
            "max_tokens": request.max_tokens or 2048,
            "temperature": request.temperature or 0.7,
            "stream": True,
        }
        if request.top_p is not None:
            payload["top_p"] = request.top_p

        async with self.client.stream(
            "POST",
            "/v1/chat/completions",
            json=payload,
        ) as resp:
            resp.raise_for_status()

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                    delta = data["choices"][0].get("delta", {})
                    # 优先 content，其次 reasoning_content
                    chunk = delta.get("content") or delta.get("reasoning_content", "")
                    if chunk:
                        yield chunk
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    def get_stats(self) -> Dict:
        """引擎统计"""
        stats = {
            "engine": "thunderllama",
            "model": self.model_name,
            "model_path": self.model_path,
            "endpoint": self.base_url,
            "n_gpu_layers": self.n_gpu_layers,
            "n_parallel": self.n_parallel,
            "n_ctx": self.n_ctx,
            "cache_ram_mb": self.cache_ram_mb,
            "flash_attention": self.flash_attention,
            "paged_attention": self.paged_attention,
            "chunk_prefill": self.chunk_prefill,
            "owns_process": self._owns_process,
            "process_pid": self._process.pid if self._process else None,
            "request_count": self._request_count,
            "total_tokens": self._total_tokens,
            "avg_time": (
                self._total_time / self._request_count
                if self._request_count
                else 0
            ),
            "cache_tuner_enabled": self.cache_tuner is not None,
        }

        # 添加 cache tuner 状态（如果启用）
        if self.cache_tuner:
            stats["cache_tuner"] = self.cache_tuner.get_stats()

        return stats

    async def health_check(self) -> bool:
        """健康检查"""
        return await self._health_ok()

    # ------------------------------------------------------------------
    # Shutdown & watchdog
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """优雅停止子进程"""
        # 停止 cache tuning 任务
        if self._tuning_task and not self._tuning_task.done():
            logger.info("ThunderLLAMA: 停止 Cache Tuning 任务")
            self._tuning_task.cancel()

        if self._process and self._owns_process:
            logger.info(
                "ThunderLLAMA: 停止 llama-server (PID %d)", self._process.pid
            )
            self._kill_process()

    def _kill_process(self) -> None:
        """终止子进程组"""
        if self._process is None:
            return
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
            self._process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        self._process = None

    async def restart(self) -> None:
        """重启 llama-server（watchdog 用）"""
        logger.warning("ThunderLLAMA: 触发重启")
        self.shutdown()
        await self._start_server()

    # ------------------------------------------------------------------
    # Cache Tuning
    # ------------------------------------------------------------------

    def _init_cache_tuner(self, config: Dict) -> None:
        """初始化 Cache Tuner

        Args:
            config: cache_tuner 配置字典
                - tuner_type: "heuristic" | "bayesian"
                - candidates_mb: 候选值列表
                - lookback_sec: 回溯时间窗口（秒）
                - min_samples: 最少样本数
                - cooling_period_sec: 冷却期（秒）
                - min_improve_score: 最小改进阈值
        """
        from clawgate.tuning import HeuristicCacheTuner

        tuner_type = config.get("tuner_type", "heuristic")

        if tuner_type == "heuristic":
            tuner_config = config.get("heuristic", {})
            self.cache_tuner = HeuristicCacheTuner(
                candidates_mb=tuner_config.get("candidates_mb", [2048, 4096, 6144, 8192]),
                lookback_sec=tuner_config.get("lookback_sec", 86400),
                min_samples=tuner_config.get("min_samples", 20),
                cooling_period_sec=tuner_config.get("cooling_period_sec", 300),
                min_improve_score=tuner_config.get("min_improve_score", 0.05)
            )
            logger.info(
                "ThunderLLAMA: Cache Tuner 已初始化 (type=%s, candidates=%s)",
                tuner_type,
                tuner_config.get("candidates_mb", [2048, 4096, 6144, 8192])
            )
        elif tuner_type == "bayesian":
            logger.warning("ThunderLLAMA: BayesianCacheTuner 尚未实现，跳过")
            self.cache_tuner = None
        else:
            logger.warning("ThunderLLAMA: 未知的 tuner_type=%s，跳过", tuner_type)
            self.cache_tuner = None

    async def start_tuning_loop(
        self,
        metrics_provider,
        interval_sec: int = 300
    ) -> None:
        """启动后台 Cache Tuning 循环

        Args:
            metrics_provider: metrics 提供函数 (async callable)
                签名: async () -> List[Dict[str, Any]]
                返回格式: [
                    {
                        "cache_ram_mb": 4096,
                        "throughput_rps": 100.0,
                        "avg_latency_ms": 150.0,
                        "failure_rate": 0.01,
                        "total": 100
                    },
                    ...
                ]
            interval_sec: 检查间隔（秒），默认 300 秒（5 分钟）
        """
        if not self.cache_tuner:
            logger.warning("ThunderLLAMA: Cache Tuner 未启用，无法启动调优循环")
            return

        if not metrics_provider:
            logger.warning("ThunderLLAMA: 未提供 metrics_provider，调优循环将无法运行")
            return

        logger.info(
            "ThunderLLAMA: 启动 Cache Tuning 循环 (interval=%ds, current_cache=%d MB)",
            interval_sec,
            self.cache_ram_mb
        )

        async def tuning_loop():
            while True:
                try:
                    await asyncio.sleep(interval_sec)

                    # 获取性能指标
                    metrics = await metrics_provider()

                    if not metrics:
                        logger.debug("ThunderLLAMA: 无性能数据，跳过本次调优")
                        continue

                    # 推荐新的 cache size
                    recommended_mb = await self.cache_tuner.recommend_cache_size(
                        metrics,
                        current_cache_mb=self.cache_ram_mb
                    )

                    if recommended_mb is None:
                        logger.debug("ThunderLLAMA: Cache Tuner 无推荐（可能已是最优或冷却期内）")
                        continue

                    if recommended_mb != self.cache_ram_mb:
                        logger.info(
                            "ThunderLLAMA: Cache Tuner 推荐切换: %d MB → %d MB",
                            self.cache_ram_mb,
                            recommended_mb
                        )

                        # 应用新配置（需要重启服务）
                        old_cache = self.cache_ram_mb
                        self.cache_ram_mb = recommended_mb

                        try:
                            await self.restart()
                            self.cache_tuner.record_switch(recommended_mb)
                            logger.info("ThunderLLAMA: 已切换到 %d MB", recommended_mb)
                        except Exception as e:
                            logger.error("ThunderLLAMA: 重启失败，回滚到 %d MB: %s", old_cache, e)
                            self.cache_ram_mb = old_cache

                except Exception as e:
                    logger.error("ThunderLLAMA: Cache Tuning 循环异常: %s", e, exc_info=True)

        # 启动后台任务
        self._tuning_task = asyncio.create_task(tuning_loop())
        logger.info("ThunderLLAMA: Cache Tuning 后台任务已启动")

    def __del__(self):
        self.shutdown()

    def __repr__(self):
        return (
            f"ThunderLlamaEngine(model={self.model_name}, "
            f"endpoint={self.base_url})"
        )
