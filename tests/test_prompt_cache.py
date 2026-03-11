#!/usr/bin/env python3
"""Prompt Cache 单元测试

测试 PromptCacheManager 的核心功能：
- 基本缓存读写
- 热缓存 LRU 淘汰
- 温缓存持久化
- 提升机制
- TTL 过期清理
- is_cacheable 判断
- 统计信息
"""

import asyncio
import logging
import sys
import time
import tempfile
from pathlib import Path
from typing import Dict, Any, List

# 添加项目根目录
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from clawgate.context.prompt_cache import PromptCacheManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


def make_response(content: str, model: str = "gpt-4") -> Dict[str, Any]:
    """构造模拟的 LLM 响应"""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content
            },
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "total_tokens": 30
        }
    }


def make_payload(messages: List[Dict], model: str = "gpt-4", temperature: float = 0.0) -> Dict[str, Any]:
    """构造请求 payload"""
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "stream": False,
        "n": 1
    }


async def test_basic_cache():
    """测试基本缓存功能"""
    logger.info("=" * 60)
    logger.info("测试 1: 基本缓存功能")
    logger.info("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = str(Path(tmpdir) / "warm_cache")

        manager = PromptCacheManager(
            hot_cache_size=100,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=3600,
            warm_ttl_sec=7200
        )

        # 准备测试数据
        messages = [{"role": "user", "content": "Hello, world!"}]
        payload = make_payload(messages)
        response = make_response("Hi there!")

        # 生成缓存键并存储
        cache_key = manager.make_key(payload, messages)
        manager.store(cache_key, response)
        logger.info(f"存储缓存条目: key={cache_key[:16]}...")

        # 从热缓存读取
        cached, cache_type = manager.get(cache_key)
        assert cached is not None, "热缓存应该返回存储的响应"
        assert cache_type == "hot", f"应该从热缓存读取，实际: {cache_type}"
        assert cached["choices"][0]["message"]["content"] == "Hi there!", "缓存内容应该匹配"
        logger.info(f"热缓存命中: content={cached['choices'][0]['message']['content']}")

        # 验证统计信息
        stats = manager.get_stats()
        assert stats["hit_hot"] == 1, f"hit_hot 应为 1，实际为 {stats['hit_hot']}"
        assert stats["store"] == 1, f"store 应为 1，实际为 {stats['store']}"
        logger.info(f"统计信息验证通过: hit_hot={stats['hit_hot']}, store={stats['store']}")

    logger.info("✅ 测试通过")
    return True


async def test_hot_cache_lru_eviction():
    """测试热缓存 LRU 淘汰"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 2: 热缓存 LRU 淘汰")
    logger.info("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = str(Path(tmpdir) / "warm_cache")

        manager = PromptCacheManager(
            hot_cache_size=3,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=3600,
            warm_ttl_sec=7200
        )

        # 存储 5 个条目
        for i in range(5):
            messages = [{"role": "user", "content": f"Prompt {i}"}]
            payload = make_payload(messages)
            response = make_response(f"Response {i}")
            cache_key = manager.make_key(payload, messages)
            manager.store(cache_key, response)
            logger.info(f"存储条目 {i}: key={cache_key[:16]}...")

        # 验证最旧的 2 个被淘汰（热缓存大小为 3）
        stats = manager.get_stats()
        assert stats["evict_hot"] == 2, f"evict_hot 应为 2，实际为 {stats['evict_hot']}"
        logger.info(f"热缓存淘汰验证通过: evict_hot={stats['evict_hot']}")

        # 验证热缓存中只有 3 个条目
        hot_cache_size = len(manager.hot_cache)
        assert hot_cache_size == 3, f"热缓存大小应为 3，实际为 {hot_cache_size}"
        logger.info(f"热缓存大小验证通过: size={hot_cache_size}")

    logger.info("✅ 测试通过")
    return True


async def test_warm_cache_persistence():
    """测试温缓存持久化"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 3: 温缓存持久化")
    logger.info("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = str(Path(tmpdir) / "warm_cache")

        cache_key = None

        # 第一阶段：存储数据
        manager = PromptCacheManager(
            hot_cache_size=100,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=3600,
            warm_ttl_sec=7200
        )

        messages = [{"role": "user", "content": "Persistent test"}]
        payload = make_payload(messages)
        response = make_response("Saved to warm cache")
        cache_key = manager.make_key(payload, messages)
        manager.store(cache_key, response)
        logger.info(f"存储条目: key={cache_key[:16]}...")

        # 验证温缓存文件存在
        warm_path = manager._warm_path(cache_key)
        assert warm_path.exists(), f"温缓存文件应该存在: {warm_path}"
        logger.info(f"温缓存文件验证通过: {warm_path.name}")

        # 第二阶段：清空热缓存，从温缓存读取
        manager2 = PromptCacheManager(
            hot_cache_size=100,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=3600,
            warm_ttl_sec=7200
        )

        # 热缓存为空，应该从温缓存读取
        cached, cache_type = manager2.get(cache_key)
        assert cached is not None, "温缓存应该返回存储的响应"
        assert cache_type == "warm", f"应该从温缓存读取，实际: {cache_type}"
        assert cached["choices"][0]["message"]["content"] == "Saved to warm cache", "缓存内容应该匹配"
        logger.info(f"温缓存命中: content={cached['choices'][0]['message']['content']}")

    logger.info("✅ 测试通过")
    return True


async def test_promotion_to_hot():
    """测试提升机制"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 4: 提升到热缓存")
    logger.info("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = str(Path(tmpdir) / "warm_cache")

        cache_key = None

        # 第一阶段：存储数据
        manager = PromptCacheManager(
            hot_cache_size=100,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=3600,
            warm_ttl_sec=7200,
            hot_hit_threshold=3
        )

        messages = [{"role": "user", "content": "Promotion test"}]
        payload = make_payload(messages)
        response = make_response("Will be promoted")
        cache_key = manager.make_key(payload, messages)
        manager.store(cache_key, response)
        logger.info(f"存储条目: key={cache_key[:16]}...")

        # 第二阶段：新实例（空热缓存），连续读取 3 次触发提升
        manager2 = PromptCacheManager(
            hot_cache_size=100,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=3600,
            warm_ttl_sec=7200,
            hot_hit_threshold=3
        )

        for i in range(3):
            cached, cache_type = manager2.get(cache_key)
            assert cached is not None, f"第 {i+1} 次读取应该成功"
            logger.info(f"第 {i+1} 次读取: cache_type={cache_type}, hit_warm={manager2.get_stats()['hit_warm']}")

        # 验证 hit_warm=3
        stats = manager2.get_stats()
        assert stats["hit_warm"] == 3, f"hit_warm 应为 3，实际为 {stats['hit_warm']}"
        logger.info(f"hit_warm 验证通过: {stats['hit_warm']}")

        # 验证条目被提升到热缓存
        assert cache_key in manager2.hot_cache, "条目应该被提升到热缓存"
        logger.info("提升机制验证通过: 条目已在热缓存中")

    logger.info("✅ 测试通过")
    return True


async def test_ttl_expiration():
    """测试 TTL 过期清理"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 5: TTL 过期清理")
    logger.info("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = str(Path(tmpdir) / "warm_cache")

        manager = PromptCacheManager(
            hot_cache_size=100,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=30,  # 30 秒（最小值）
            warm_ttl_sec=30  # 30 秒（最小值）
        )

        messages = [{"role": "user", "content": "TTL test"}]
        payload = make_payload(messages)
        response = make_response("Will expire")
        cache_key = manager.make_key(payload, messages)
        manager.store(cache_key, response)
        logger.info(f"存储条目: hot_ttl=30s, warm_ttl=30s")

        # 手动修改时间戳模拟过期（直接操作内部状态进行测试）
        logger.info("手动修改时间戳模拟过期...")

        # 修改热缓存条目的时间戳（向前推 31 秒）
        if cache_key in manager.hot_cache:
            manager.hot_cache[cache_key]["last_access"] = int(time.time()) - 31
            logger.info("热缓存条目时间戳已修改")

        # 修改温缓存文件的时间戳
        warm_path = manager._warm_path(cache_key)
        if warm_path.exists():
            import json
            with warm_path.open("r", encoding="utf-8") as f:
                warm_entry = json.load(f)
            warm_entry["last_access"] = int(time.time()) - 31
            with warm_path.open("w", encoding="utf-8") as f:
                json.dump(warm_entry, f, ensure_ascii=False, indent=2)
            logger.info("温缓存文件时间戳已修改")

        # 执行清理
        pruned = manager.prune_expired()
        logger.info(f"执行 prune_expired(): 清理了 {pruned} 条")

        # 验证清理成功
        assert pruned > 0, f"应该清理至少 1 条过期条目，实际清理了 {pruned} 条"

        # 验证统计信息
        stats = manager.get_stats()
        total_evict = stats.get("evict_hot", 0) + stats.get("evict_warm", 0)
        assert total_evict > 0, f"应该有过期清理记录，实际 evict_hot={stats.get('evict_hot')}, evict_warm={stats.get('evict_warm')}"
        logger.info(f"过期清理验证通过: evict_hot={stats.get('evict_hot')}, evict_warm={stats.get('evict_warm')}")

        # 验证缓存已过期（再次获取应该 miss）
        cached, cache_type = manager.get(cache_key)
        assert cached is None, "清理后应该无法获取缓存"
        logger.info("验证缓存已过期: get() 返回 None")

    logger.info("✅ 测试通过")
    return True


async def test_is_cacheable():
    """测试 is_cacheable 判断"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 6: is_cacheable 判断")
    logger.info("=" * 60)

    # Case 1: temperature=0, stream=False, n=1 → True
    payload1 = {"temperature": 0, "stream": False, "n": 1}
    result1 = PromptCacheManager.is_cacheable(payload1)
    assert result1 is True, f"temperature=0, stream=False, n=1 应返回 True，实际为 {result1}"
    logger.info(f"Case 1: temperature=0, stream=False, n=1 → {result1} ✓")

    # Case 2: stream=True → False
    payload2 = {"temperature": 0, "stream": True, "n": 1}
    result2 = PromptCacheManager.is_cacheable(payload2)
    assert result2 is False, f"stream=True 应返回 False，实际为 {result2}"
    logger.info(f"Case 2: stream=True → {result2} ✓")

    # Case 3: n=2 → False
    payload3 = {"temperature": 0, "stream": False, "n": 2}
    result3 = PromptCacheManager.is_cacheable(payload3)
    assert result3 is False, f"n=2 应返回 False，实际为 {result3}"
    logger.info(f"Case 3: n=2 → {result3} ✓")

    # Case 4: temperature=0.7 → False
    payload4 = {"temperature": 0.7, "stream": False, "n": 1}
    result4 = PromptCacheManager.is_cacheable(payload4)
    assert result4 is False, f"temperature=0.7 应返回 False，实际为 {result4}"
    logger.info(f"Case 4: temperature=0.7 → {result4} ✓")

    logger.info("✅ 测试通过")
    return True


async def test_cache_stats():
    """测试统计信息准确性"""
    logger.info("\n" + "=" * 60)
    logger.info("测试 7: 统计信息准确性")
    logger.info("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        cache_dir = str(Path(tmpdir) / "warm_cache")

        manager = PromptCacheManager(
            hot_cache_size=100,
            warm_cache_dir=cache_dir,
            hot_ttl_sec=3600,
            warm_ttl_sec=7200
        )

        # 执行一系列操作
        # 1. 存储 3 个条目
        keys = []
        for i in range(3):
            messages = [{"role": "user", "content": f"Stats test {i}"}]
            payload = make_payload(messages)
            response = make_response(f"Response {i}")
            cache_key = manager.make_key(payload, messages)
            manager.store(cache_key, response)
            keys.append(cache_key)
        logger.info(f"存储了 {len(keys)} 个条目")

        # 2. 命中热缓存 5 次
        for _ in range(5):
            manager.get(keys[0])
        logger.info("执行 5 次热缓存命中")

        # 3. 未命中 2 次
        manager.get("nonexistent_key_1")
        manager.get("nonexistent_key_2")
        logger.info("执行 2 次未命中")

        # 验证统计信息
        stats = manager.get_stats()

        assert stats["store"] == 3, f"store 应为 3，实际为 {stats['store']}"
        assert stats["hit_hot"] == 5, f"hit_hot 应为 5，实际为 {stats['hit_hot']}"
        assert stats["miss"] == 2, f"miss 应为 2，实际为 {stats['miss']}"

        # 验证 hit_rate 计算
        total_requests = stats["hit_hot"] + stats["hit_warm"] + stats["miss"]
        expected_hit_rate = (stats["hit_hot"] + stats["hit_warm"]) / total_requests if total_requests > 0 else 0
        actual_hit_rate = stats.get("hit_rate", 0)

        assert abs(actual_hit_rate - expected_hit_rate) < 0.01, f"hit_rate 应接近 {expected_hit_rate}，实际为 {actual_hit_rate}"

        logger.info(f"统计信息验证通过:")
        logger.info(f"  - store: {stats['store']}")
        logger.info(f"  - hit_hot: {stats['hit_hot']}")
        logger.info(f"  - miss: {stats['miss']}")
        logger.info(f"  - hit_rate: {actual_hit_rate:.2%}")

    logger.info("✅ 测试通过")
    return True


async def main():
    """运行所有测试"""
    logger.info("\n" + "=" * 60)
    logger.info("Prompt Cache 单元测试")
    logger.info("=" * 60)

    results = {}

    # 运行测试
    tests = [
        ("basic_cache", test_basic_cache),
        ("hot_cache_lru_eviction", test_hot_cache_lru_eviction),
        ("warm_cache_persistence", test_warm_cache_persistence),
        ("promotion_to_hot", test_promotion_to_hot),
        ("ttl_expiration", test_ttl_expiration),
        ("is_cacheable", test_is_cacheable),
        ("cache_stats", test_cache_stats),
    ]

    for test_name, test_func in tests:
        try:
            results[test_name] = await test_func()
        except AssertionError as e:
            logger.error(f"断言失败: {e}")
            results[test_name] = False
        except Exception as e:
            logger.error(f"测试异常: {e}", exc_info=True)
            results[test_name] = False

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
    asyncio.run(main())
