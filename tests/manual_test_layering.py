#!/usr/bin/env python3
"""Manual test for Three-tier Layering Strategy

不依赖 pytest，可以直接运行的验证脚本
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from clawgate.context.strategies.layering import ThreeTierLayeringStrategy


def test_basic_functionality():
    """测试基本功能"""
    print("=" * 60)
    print("测试 1: 基本功能测试")
    print("=" * 60)

    # 创建策略实例
    strategy = ThreeTierLayeringStrategy(
        must_have_cap=1536,
        nice_to_have_cap=768,
        history_tail_cap=512,
        preserve_last_turns=6,
    )

    # 创建测试消息
    messages = []

    # System 消息
    messages.append({
        "role": "system",
        "content": "You are a helpful AI assistant."
    })

    # 30 轮对话
    for i in range(30):
        messages.append({
            "role": "user",
            "content": f"Question {i+1}: Sample user question."
        })
        messages.append({
            "role": "assistant",
            "content": f"Answer {i+1}: Sample assistant response."
        })

    # 执行压缩
    result = strategy.compress(messages, target_tokens=2000)

    # 验证结果
    print(f"\n✅ 原始消息数: {len(messages)}")
    print(f"✅ 压缩后消息数: {len(result)}")
    print(f"\n统计信息:")
    print(f"  Must-have tokens: {strategy.last_stats.get('must_have_tokens', 0)}")
    print(f"  Nice-to-have tokens: {strategy.last_stats.get('nice_to_have_tokens', 0)}")
    print(f"  History-tail tokens: {strategy.last_stats.get('history_tail_tokens', 0)}")
    print(f"  Tail tokens: {strategy.last_stats.get('tail_tokens', 0)}")
    print(f"  总 tokens: {strategy.last_stats.get('total_output_tokens', 0)}")

    # 验证各层
    system_count = sum(1 for msg in result if msg.get("role") == "system")
    user_count = sum(1 for msg in result if msg.get("role") == "user")
    assistant_count = sum(1 for msg in result if msg.get("role") == "assistant")

    print(f"\n消息分布:")
    print(f"  System 消息: {system_count} (应该有 Must-have/Nice-to-have/History-tail 层)")
    print(f"  User 消息: {user_count}")
    print(f"  Assistant 消息: {assistant_count}")

    # 基本断言
    assert len(result) > 0, "结果不应为空"
    assert system_count > 0, "应该有 system 消息"
    assert user_count > 0 or assistant_count > 0, "应该有对话消息"

    print("\n✅ 测试 1 通过！\n")


def test_empty_messages():
    """测试空消息"""
    print("=" * 60)
    print("测试 2: 空消息处理")
    print("=" * 60)

    strategy = ThreeTierLayeringStrategy()
    result = strategy.compress([], target_tokens=2000)

    print(f"✅ 空消息结果: {result}")
    assert result == [], "空消息应该返回空列表"

    print("✅ 测试 2 通过！\n")


def test_short_conversation():
    """测试短对话"""
    print("=" * 60)
    print("测试 3: 短对话（< preserve_last_turns）")
    print("=" * 60)

    strategy = ThreeTierLayeringStrategy(preserve_last_turns=6)

    messages = [
        {"role": "system", "content": "System message"},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi!"},
    ]

    result = strategy.compress(messages, target_tokens=2000)

    print(f"✅ 原始消息数: {len(messages)}")
    print(f"✅ 压缩后消息数: {len(result)}")
    print(f"✅ 总 tokens: {strategy.last_stats.get('total_output_tokens', 0)}")

    assert len(result) > 0, "结果不应为空"

    print("✅ 测试 3 通过！\n")


def test_token_allocation():
    """测试 token 分配"""
    print("=" * 60)
    print("测试 4: Token 分配验证")
    print("=" * 60)

    strategy = ThreeTierLayeringStrategy(
        must_have_cap=1536,
        nice_to_have_cap=768,
        history_tail_cap=512,
    )

    # 生成大量消息
    messages = [
        {"role": "system", "content": "System message " * 100}
    ]
    for i in range(50):
        messages.append({"role": "user", "content": f"Message {i} " * 20})

    result = strategy.compress(messages, target_tokens=5000)

    stats = strategy.last_stats
    print(f"\n✅ Token 分配:")
    print(f"  Must-have: {stats['must_have_tokens']} / 1536 (cap)")
    print(f"  Nice-to-have: {stats['nice_to_have_tokens']} / 768 (cap)")
    print(f"  History-tail: {stats['history_tail_tokens']} / 512 (cap)")
    print(f"  Tail: {stats['tail_tokens']}")

    # 验证 token 上限
    assert stats['must_have_tokens'] <= 1536, "Must-have 超限"
    assert stats['nice_to_have_tokens'] <= 768, "Nice-to-have 超限"
    assert stats['history_tail_tokens'] <= 512, "History-tail 超限"

    print("\n✅ 测试 4 通过！\n")


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("Three-tier Layering Strategy - 手动测试")
    print("=" * 60 + "\n")

    try:
        test_basic_functionality()
        test_empty_messages()
        test_short_conversation()
        test_token_allocation()

        print("=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
