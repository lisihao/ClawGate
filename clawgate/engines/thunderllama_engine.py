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

        resp = await self.client.post(
            "/v1/chat/completions",
            json=payload,
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
        return {
            "engine": "thunderllama",
            "model": self.model_name,
            "model_path": self.model_path,
            "endpoint": self.base_url,
            "n_gpu_layers": self.n_gpu_layers,
            "n_parallel": self.n_parallel,
            "n_ctx": self.n_ctx,
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
        }

    async def health_check(self) -> bool:
        """健康检查"""
        return await self._health_ok()

    # ------------------------------------------------------------------
    # Shutdown & watchdog
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """优雅停止子进程"""
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

    def __del__(self):
        self.shutdown()

    def __repr__(self):
        return (
            f"ThunderLlamaEngine(model={self.model_name}, "
            f"endpoint={self.base_url})"
        )
