"""GLM (ZhipuAI) API Backend"""

import os
import httpx
from typing import List, Dict, AsyncGenerator, Optional
import time
import logging

from ...engines.base import BaseEngine, GenerationRequest, GenerationResponse

logger = logging.getLogger("clawgate.backend.glm")


class GLMBackend(BaseEngine):
    """GLM API 后端

    支持模型:
    - glm-5
    - glm-4-plus
    - glm-4-flash
    """

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GLM_API_KEY")
        # 标准 API URL
        self.base_url = "https://open.bigmodel.cn/api/paas/v4"

        if not self.api_key:
            raise ValueError("GLM_API_KEY not found")

        self.client = httpx.AsyncClient(timeout=60.0)

    async def generate(self, request: GenerationRequest, model: str = "glm-4-plus") -> GenerationResponse:
        """非流式生成"""
        start_time = time.time()
        logger.debug(f"[GLM] POST {self.base_url}/chat/completions | model={model} temp={request.temperature} max_tokens={request.max_tokens}")

        # 构建请求
        payload = {
            "model": model,
            "messages": request.messages,
            "temperature": request.temperature or 0.7,
            "max_tokens": request.max_tokens or 2048,
            "stream": False,
        }

        # 调用 API
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            logger.debug(f"[GLM] HTTP {response.status_code} | 耗时={time.time()-start_time:.2f}s")
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"[GLM] ❌ HTTP 错误: {e.response.status_code} | body={e.response.text[:200]}")
            raise
        except Exception as e:
            logger.error(f"[GLM] ❌ 请求异常: {type(e).__name__}: {e}")
            raise

        data = response.json()

        # 解析响应
        choice = data["choices"][0]
        content = choice["message"]["content"]

        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        end_time = time.time()

        return GenerationResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            ttft=end_time - start_time,
            total_time=end_time - start_time,
        )

    async def generate_stream(
        self, request: GenerationRequest, model: str = "glm-4-plus"
    ) -> AsyncGenerator[str, None]:
        """流式生成"""
        payload = {
            "model": model,
            "messages": request.messages,
            "temperature": request.temperature or 0.7,
            "max_tokens": request.max_tokens or 2048,
            "stream": True,
        }

        async with self.client.stream(
            "POST",
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:]
                if data_str == "[DONE]":
                    break

                import json

                try:
                    data = json.loads(data_str)
                    choice = data["choices"][0]
                    delta = choice.get("delta", {})
                    content = delta.get("content", "")

                    if content:
                        yield content
                except Exception:
                    continue

    def get_stats(self) -> Dict:
        """获取引擎统计信息"""
        return {
            "engine_type": "glm_backend",
            "base_url": self.base_url,
            "authenticated": bool(self.api_key),
        }

    async def close(self):
        """关闭客户端"""
        await self.client.aclose()
