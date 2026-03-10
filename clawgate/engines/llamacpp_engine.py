"""llama.cpp 引擎（跨平台）"""

import asyncio
from typing import List, Dict, AsyncIterator

from .base import BaseEngine, GenerationRequest, GenerationResponse

# llama-cpp-python 是可选依赖
try:
    from llama_cpp import Llama

    LLAMACPP_AVAILABLE = True
except ImportError:
    LLAMACPP_AVAILABLE = False
    Llama = None


class LlamaCppEngine(BaseEngine):
    """llama.cpp 引擎（跨平台高性能）"""

    def __init__(self, model_path: str, **kwargs):
        super().__init__("llamacpp", model_path, **kwargs)

        if not LLAMACPP_AVAILABLE:
            raise RuntimeError(
                "llama-cpp-python not available. "
                "Install with: pip install llama-cpp-python"
            )

        # llama.cpp 配置
        n_ctx = kwargs.get("n_ctx", 32768)  # 上下文长度
        n_gpu_layers = kwargs.get("n_gpu_layers", -1)  # -1 = 全部 GPU
        n_threads = kwargs.get("n_threads", 8)  # CPU 线程数

        print(f"📦 [llama.cpp] 加载模型: {model_path}")
        print(f"  - 上下文: {n_ctx}")
        print(f"  - GPU 层数: {n_gpu_layers}")
        print(f"  - CPU 线程: {n_threads}")

        # 加载模型
        self.llm = Llama(
            model_path=model_path,
            n_ctx=n_ctx,
            n_gpu_layers=n_gpu_layers,
            n_threads=n_threads,
            verbose=False,
        )

        print(f"✅ [llama.cpp] 模型加载完成")

    async def generate(
        self, request: GenerationRequest
    ) -> GenerationResponse:
        """生成响应（非流式）"""

        start_time = asyncio.get_event_loop().time()

        # llama.cpp 生成（同步，在 executor 中运行）
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(
            None,
            lambda: self.llm.create_chat_completion(
                messages=request.messages,
                max_tokens=request.max_tokens or 2048,
                temperature=request.temperature or 0.7,
                stream=False,
            ),
        )

        end_time = asyncio.get_event_loop().time()

        # 提取结果
        content = output["choices"][0]["message"]["content"]
        usage = output["usage"]

        return GenerationResponse(
            content=content,
            model=self.model_path,
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],
            total_time=end_time - start_time,
            ttft=None,
        )

    async def generate_stream(
        self, request: GenerationRequest
    ) -> AsyncIterator[str]:
        """流式生成"""

        # llama.cpp 流式生成（同步转异步）
        loop = asyncio.get_event_loop()

        def _create_stream():
            return self.llm.create_chat_completion(
                messages=request.messages,
                max_tokens=request.max_tokens or 2048,
                temperature=request.temperature or 0.7,
                stream=True,
            )

        stream = await loop.run_in_executor(None, _create_stream)

        # 异步 yield
        for chunk in stream:
            if "choices" in chunk and len(chunk["choices"]) > 0:
                delta = chunk["choices"][0].get("delta", {})
                if "content" in delta:
                    yield delta["content"]

    def get_stats(self) -> Dict:
        """获取引擎统计信息"""
        return {
            "engine": "llamacpp",
            "model": self.model_path,
            "n_ctx": self.llm.n_ctx(),
            "n_gpu_layers": getattr(self.llm, "n_gpu_layers", -1),
        }
