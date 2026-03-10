"""测试 ContextEngine"""

import pytest
from clawgate.context.manager import ContextManager


def test_context_compression():
    """测试上下文压缩"""
    manager = ContextManager()

    # 创建测试消息
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
        {"role": "assistant", "content": "I'm doing well, thank you!"},
        {"role": "user", "content": "What's the weather like?"},
        {"role": "assistant", "content": "I don't have access to weather data."},
    ]

    # 压缩到 100 tokens
    compressed, metadata = manager.compress(
        messages=messages,
        target_tokens=100,
        strategy="sliding_window",
    )

    print(f"\nOriginal messages: {len(messages)}")
    print(f"Compressed messages: {len(compressed)}")
    print(f"Metadata: {metadata}")

    assert len(compressed) < len(messages)
    assert metadata["compression_ratio"] < 1.0


def test_adaptive_strategy():
    """测试自适应策略"""
    manager = ContextManager()

    # 包含决策的消息
    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {
            "role": "user",
            "content": "We need to decide between approach A and B. What do you think?",
        },
        {
            "role": "assistant",
            "content": "I choose approach A because it's more efficient.",
        },
        {"role": "user", "content": "Good decision!"},
    ]

    # 使用自适应策略
    compressed, metadata = manager.compress(
        messages=messages, target_tokens=50, strategy="adaptive", agent_type="judge"
    )

    print(f"\nStrategy used: {metadata['strategy']}")
    print(f"Compressed: {len(compressed)} messages")

    assert len(compressed) > 0


def test_context_cache():
    """测试上下文缓存"""
    manager = ContextManager()

    messages = [
        {"role": "user", "content": "Test message"},
        {"role": "assistant", "content": "Test response"},
    ]

    # 压缩并缓存
    compressed, metadata = manager.compress(messages, target_tokens=100)
    manager.cache_context(messages, compressed, metadata)

    # 从缓存获取
    cached = manager.get_cached_context(messages)

    assert cached is not None
    cached_messages, cached_meta = cached
    assert cached_meta["hit"] is True


if __name__ == "__main__":
    test_context_compression()
    test_adaptive_strategy()
    test_context_cache()
    print("\n✅ All context engine tests passed!")
