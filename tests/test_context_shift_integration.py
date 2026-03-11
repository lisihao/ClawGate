#!/usr/bin/env python3
"""Context Shift 集成测试

测试 Context Shift 客户端与 Layering 策略的集成。

运行前提:
- Context Shift 服务已启动 (18083, 18084)
- 运行: bash scripts/start_context_shift_services.sh
"""

import asyncio
import logging
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from clawgate.context.context_shift_client import ContextShiftClient
from clawgate.context.strategies.layering import ThreeTierLayeringStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


async def test_context_shift_health():
    """测试 Context Shift 服务健康检查"""
    logger.info("=" * 60)
    logger.info("测试 1: Context Shift 服务健康检查")
    logger.info("=" * 60)

    client = ContextShiftClient()

    try:
        is_healthy, message = await client.health_check()
        logger.info(f"健康检查结果: {'✅' if is_healthy else '❌'}")
        logger.info(f"消息: {message}")
        return is_healthy
    except Exception as e:
        logger.error(f"健康检查失败: {e}", exc_info=True)
        return False
    finally:
        await client.close()


async def test_context_shift_summarize():
    """测试 Context Shift 两阶段摘要"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 2: Context Shift 两阶段摘要")
    logger.info("=" * 60)

    # 构造测试消息（模拟长对话）
    messages = [
        {"role": "user", "content": "我想了解 Python 的异步编程。"},
        {"role": "assistant", "content": "Python 的异步编程主要使用 asyncio 库，它基于事件循环..."},
        {"role": "user", "content": "asyncio.run() 和 asyncio.create_task() 有什么区别？"},
        {"role": "assistant", "content": "asyncio.run() 用于运行顶层入口，而 create_task() 用于并发执行..."},
        {"role": "user", "content": "能给个例子吗？"},
        {"role": "assistant", "content": "当然，这是一个并发下载的例子..."},
        {"role": "user", "content": "如果任务失败了怎么办？"},
        {"role": "assistant", "content": "可以使用 try/except 捕获异常，或者用 Task.exception()..."},
        {"role": "user", "content": "超时怎么处理？"},
        {"role": "assistant", "content": "使用 asyncio.wait_for() 可以设置超时..."},
        {"role": "user", "content": "我明白了，谢谢！"},
        {"role": "assistant", "content": "不客气！还有其他问题吗？"},
    ]

    client = ContextShiftClient(mode="quality")  # 使用 quality 模式（0.6B + 1.7B）

    try:
        summary = await client.summarize(messages, target_tokens=200)

        if summary:
            logger.info(f"✅ 摘要成功，长度: {len(summary)} 字符")
            logger.info(f"\n摘要内容:\n{'-' * 60}\n{summary}\n{'-' * 60}")
            return True
        else:
            logger.warning("❌ 摘要返回 None")
            return False

    except Exception as e:
        logger.error(f"❌ 摘要失败: {e}", exc_info=True)
        return False
    finally:
        await client.close()


def test_layering_with_context_shift():
    """测试 Layering 策略集成 Context Shift"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 3: Layering 策略集成 Context Shift")
    logger.info("=" * 60)

    # 创建 Context Shift 客户端
    context_shift_client = ContextShiftClient(mode="quality")

    # 创建 Layering 策略（启用 Context Shift）
    layering = ThreeTierLayeringStrategy(
        must_have_cap=1536,
        nice_to_have_cap=768,
        history_tail_cap=512,
        preserve_last_turns=6,
        context_shift_enabled=True,
        context_shift_client=context_shift_client
    )

    # 构造测试消息
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手。"},
        {"role": "user", "content": "我想学习机器学习。"},
        {"role": "assistant", "content": "很好！机器学习是一个广阔的领域..."},
        {"role": "user", "content": "从哪里开始比较好？"},
        {"role": "assistant", "content": "建议先学习 Python 基础和数学..."},
        {"role": "user", "content": "需要学哪些数学？"},
        {"role": "assistant", "content": "线性代数、微积分、概率论是核心..."},
        {"role": "user", "content": "有推荐的课程吗？"},
        {"role": "assistant", "content": "Andrew Ng 的机器学习课程很经典..."},
        {"role": "user", "content": "需要多久能入门？"},
        {"role": "assistant", "content": "如果每天投入 2-3 小时，大约 3-6 个月..."},
        {"role": "user", "content": "谢谢，我会努力的！"},
        {"role": "assistant", "content": "加油！有问题随时问。"},
        {"role": "user", "content": "好的！"},
    ]

    try:
        # 调用压缩（同步方法会内部调用异步 Context Shift）
        compressed = layering.compress(
            messages=messages,
            target_tokens=2048,
            tokenizer=None
        )

        logger.info(f"✅ 压缩成功")
        logger.info(f"原始消息数: {len(messages)}")
        logger.info(f"压缩后消息数: {len(compressed)}")
        logger.info(f"\n统计信息: {layering.last_stats}")
        logger.info(f"\nContext Shift 使用统计: {layering.get_context_shift_stats()}")

        # 显示压缩后的消息
        logger.info(f"\n压缩后的消息:")
        for i, msg in enumerate(compressed):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            logger.info(f"  [{i+1}] {role}: {content[:100]}...")

        return True

    except Exception as e:
        logger.error(f"❌ 压缩失败: {e}", exc_info=True)
        return False


def main():
    """运行所有测试"""
    logger.info("\n" + "=" * 60)
    logger.info("Context Shift 集成测试")
    logger.info("=" * 60)

    results = {}

    # 测试 1: 健康检查
    results["health_check"] = asyncio.run(test_context_shift_health())

    if not results["health_check"]:
        logger.error("\n❌ Context Shift 服务不可用，请先启动服务:")
        logger.error("   bash scripts/start_context_shift_services.sh")
        sys.exit(1)

    # 测试 2: 两阶段摘要
    results["summarize"] = asyncio.run(test_context_shift_summarize())

    # 测试 3: Layering 集成
    results["layering_integration"] = test_layering_with_context_shift()

    # 汇总结果
    logger.info("\n" + "=" * 60)
    logger.info("测试结果汇总")
    logger.info("=" * 60)
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info(f"{test_name:30s} {status}")

    # 总结
    all_passed = all(results.values())
    logger.info("\n" + "=" * 60)
    if all_passed:
        logger.info("🎉 所有测试通过！")
        logger.info("=" * 60)
        sys.exit(0)
    else:
        logger.error("❌ 部分测试失败")
        logger.info("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
