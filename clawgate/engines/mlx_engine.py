"""MLX-LM 引擎（Apple Silicon 优化）"""

import asyncio
import platform
from typing import List, Dict, AsyncIterator

from .base import BaseEngine, GenerationRequest, GenerationResponse

# MLX 是可选依赖，只在 Apple Silicon 上可用
try:
    import mlx.core as mx
    from mlx_lm import load, generate

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None
    load = None
    generate = None


class MLXEngine(BaseEngine):
    """MLX-LM 引擎（Apple Silicon 专用）"""

    def __init__(self, model_path: str, **kwargs):
        super().__init__("mlx", model_path, **kwargs)

        # 检查平台
        if not MLX_AVAILABLE:
            raise RuntimeError(
                "MLX not available. Install with: pip install mlx-lm"
            )

        if platform.system() != "Darwin" or "arm" not in platform.machine():
            raise RuntimeError(
                "MLX is only supported on Apple Silicon (M1/M2/M3)"
            )

        # 加载模型
        print(f"📦 [MLX] 加载模型: {model_path}")
        self.model, self.tokenizer = load(model_path)
        print(f"✅ [MLX] 模型加载完成")

        # 配置
        self.max_tokens = kwargs.get("max_tokens", 2048)
        self.temperature = kwargs.get("temperature", 0.7)

    async def generate(
        self, request: GenerationRequest
    ) -> GenerationResponse:
        """生成响应（非流式）"""

        # 格式化 prompt
        prompt = self._format_messages(request.messages)

        # 生成
        start_time = asyncio.get_event_loop().time()

        # MLX generate 是同步的，在 executor 中运行避免阻塞
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(
            None,
            lambda: generate(
                self.model,
                self.tokenizer,
                prompt=prompt,
                max_tokens=request.max_tokens or self.max_tokens,
                temp=request.temperature or self.temperature,
                verbose=False,
            ),
        )

        end_time = asyncio.get_event_loop().time()

        # 计算 tokens
        input_tokens = len(self.tokenizer.encode(prompt))
        output_tokens = len(self.tokenizer.encode(output))

        return GenerationResponse(
            content=output,
            model=self.model_path,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_time=end_time - start_time,
            ttft=None,  # 非流式无 TTFT
        )

    async def generate_stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[str]:
        """流式生成"""

        prompt = self._format_messages(request.messages)

        # MLX 流式生成（同步转异步）
        loop = asyncio.get_event_loop()

        # 使用生成器包装
        def _generate():
            for token in generate(
                self.model,
                self.tokenizer,
                prompt=prompt,
                max_tokens=request.max_tokens or self.max_tokens,
                temp=request.temperature or self.temperature,
                verbose=False,
            ):
                yield token

        # 异步yield
        gen = _generate()
        while True:
            try:
                token = await loop.run_in_executor(None, lambda: next(gen))
                yield token
            except StopIteration:
                break

    def _format_messages(self, messages: List[Dict]) -> str:
        """格式化消息为 prompt（ChatML 格式）"""

        prompt_parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")

            if role == "system":
                prompt_parts.append(
                    f"<|im_start|>system\n{content}<|im_end|>"
                )
            elif role == "user":
                prompt_parts.append(f"<|im_start|>user\n{content}<|im_end|>")
            elif role == "assistant":
                prompt_parts.append(
                    f"<|im_start|>assistant\n{content}<|im_end|>"
                )

        prompt_parts.append("<|im_start|>assistant\n")

        return "\n".join(prompt_parts)

    def get_stats(self) -> Dict:
        """获取引擎统计信息"""
        try:
            memory_usage = mx.metal.get_active_memory() / 1e9  # GB
        except Exception:
            memory_usage = 0

        return {
            "engine": "mlx",
            "model": self.model_path,
            "device": "Apple Silicon (Metal)",
            "memory_usage_gb": round(memory_usage, 2),
        }
