"""测试推理引擎"""

import pytest
import asyncio
from pathlib import Path

from clawgate.engines.manager import EngineManager
from clawgate.engines.base import GenerationRequest


@pytest.mark.asyncio
async def test_engine_manager_initialization():
    """测试引擎管理器初始化"""
    manager = EngineManager()

    # 检查是否有可用引擎
    available_models = manager.get_available_models()
    print(f"Available models: {available_models}")

    assert isinstance(available_models, list)


@pytest.mark.asyncio
async def test_local_engine_generation():
    """测试本地引擎生成"""
    manager = EngineManager()
    available_models = manager.get_available_models()

    if not available_models:
        pytest.skip("No local models available")

    # 选择第一个可用模型
    model_name = available_models[0]
    engine = manager.get_engine(model_name)

    # 创建请求
    request = GenerationRequest(
        messages=[{"role": "user", "content": "Hello, how are you?"}],
        max_tokens=50,
        temperature=0.7,
        stream=False,
    )

    # 生成
    response = await engine.generate(request)

    print(f"\nModel: {model_name}")
    print(f"Response: {response.content}")
    print(f"TTFT: {response.ttft:.3f}s")
    print(f"Tokens: {response.input_tokens} in, {response.output_tokens} out")

    assert response.content is not None
    assert len(response.content) > 0
    assert response.ttft > 0


@pytest.mark.asyncio
async def test_stream_generation():
    """测试流式生成"""
    manager = EngineManager()
    available_models = manager.get_available_models()

    if not available_models:
        pytest.skip("No local models available")

    model_name = available_models[0]
    engine = manager.get_engine(model_name)

    request = GenerationRequest(
        messages=[{"role": "user", "content": "Count from 1 to 5"}],
        max_tokens=50,
        temperature=0.7,
        stream=True,
    )

    # 收集流式输出
    chunks = []
    async for chunk in engine.generate_stream(request):
        chunks.append(chunk)
        print(chunk, end="", flush=True)

    print()  # 换行

    assert len(chunks) > 0


if __name__ == "__main__":
    # 运行测试
    asyncio.run(test_engine_manager_initialization())
    asyncio.run(test_local_engine_generation())
    asyncio.run(test_stream_generation())
