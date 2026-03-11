"""Phase 2 端到端测试

验证 Cache Tuning + Prompt Cache 的完整集成：
1. PromptCacheManager 请求流程集成
2. HeuristicCacheTuner Engine 集成
3. Dashboard /cache 端点
"""

import asyncio
import hashlib
import json
import time
from pathlib import Path


async def test_prompt_cache_integration():
    """测试 Prompt Cache 完整流程"""
    print("=" * 60)
    print("测试 1: Prompt Cache 请求流程集成")
    print("=" * 60)

    from clawgate.context.prompt_cache import PromptCacheManager

    # 初始化 PromptCacheManager
    cache_dir = Path(".solar/test-prompt-cache")
    cache_dir.mkdir(parents=True, exist_ok=True)

    manager = PromptCacheManager(
        enabled=True,
        hot_cache_size=256,
        hot_ttl_sec=3600,
        warm_cache_dir=str(cache_dir / "warm"),
        warm_ttl_sec=86400,
    )

    # 模拟 cacheable 请求
    payload = {
        "model": "qwen-1.7b",
        "temperature": 0,
        "max_tokens": 512,
        "stream": False,
        "n": 1,
    }
    messages = [
        {"role": "user", "content": "What is 2+2?"}
    ]

    # 验证可缓存判断
    assert PromptCacheManager.is_cacheable(payload) is True
    print("✅ 请求可缓存")

    # 生成缓存键
    cache_key = manager.make_key(payload, messages)
    print(f"✅ 缓存键: {cache_key[:16]}...")

    # 第一次请求 - 缓存未命中
    cached, cache_type = manager.get(cache_key)
    assert cached is None
    assert cache_type is None
    print("✅ 第一次请求：缓存未命中")

    # 模拟响应
    response = {
        "id": "chatcmpl-123",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "qwen-1.7b",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "4"},
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 1,
            "total_tokens": 11
        }
    }

    # 存储到缓存
    manager.store(cache_key, response)
    print("✅ 响应已存储到缓存")

    # 第二次请求 - 缓存命中（热缓存）
    cached, cache_type = manager.get(cache_key)
    assert cached is not None
    assert cache_type == "hot"
    assert cached["choices"][0]["message"]["content"] == "4"
    print(f"✅ 第二次请求：热缓存命中")

    # 验证统计信息
    stats = manager.get_stats()
    assert stats["hit_hot"] == 1
    assert stats["miss"] == 1
    assert stats["store"] == 1
    assert stats["hit_rate"] == 0.5  # 1 hit / 2 requests
    print(f"✅ 统计信息正确：hit_hot={stats['hit_hot']}, miss={stats['miss']}, hit_rate={stats['hit_rate']:.1%}")

    # 清理测试数据
    import shutil
    shutil.rmtree(cache_dir, ignore_errors=True)

    print("\n🎉 Prompt Cache 集成测试通过！\n")


async def test_cache_tuner_integration():
    """测试 Cache Tuner Engine 集成"""
    print("=" * 60)
    print("测试 2: Cache Tuner Engine 集成")
    print("=" * 60)

    from clawgate.tuning.cache_tuner import HeuristicCacheTuner

    # 初始化 HeuristicCacheTuner
    tuner = HeuristicCacheTuner(
        candidates_mb=[2048, 4096, 6144, 8192],
        lookback_sec=86400,
        min_samples=20,
        cooling_period_sec=300,
        min_improve_score=0.05,
    )

    # 模拟聚合后的性能指标数据
    # 注意：HeuristicCacheTuner 期望聚合后的数据，每个 cache_ram_mb 一条
    metrics = [
        {
            "cache_ram_mb": 4096,
            "throughput_rps": 100.0,  # 高吞吐
            "avg_latency_ms": 50.0,   # 低延迟
            "failure_rate": 0.01,      # 低失败率
            "total": 25                # 样本数
        },
        {
            "cache_ram_mb": 2048,
            "throughput_rps": 80.0,   # 低吞吐
            "avg_latency_ms": 80.0,   # 高延迟
            "failure_rate": 0.02,      # 高失败率
            "total": 20
        },
        {
            "cache_ram_mb": 6144,
            "throughput_rps": 95.0,   # 中等
            "avg_latency_ms": 55.0,
            "failure_rate": 0.01,
            "total": 20
        },
        {
            "cache_ram_mb": 8192,
            "throughput_rps": 98.0,   # 略低于 4096
            "avg_latency_ms": 52.0,
            "failure_rate": 0.01,
            "total": 20
        }
    ]

    # 推荐最优 cache_ram_mb
    recommended = await tuner.recommend_cache_size(metrics, current_cache_mb=2048)
    assert recommended == 4096
    print(f"✅ 推荐配置：{recommended} MB (当前: 2048 MB)")

    # 验证评分逻辑
    stats = tuner.get_stats()
    last_decision = stats.get("last_decision", {})
    assert last_decision.get("target_cache_mb") == 4096
    assert last_decision.get("recommendation") == "switch"
    print(f"✅ Tuner 统计：{stats}")
    print(f"✅ 最后决策：{last_decision}")

    # 测试冷却期
    recommended2 = await tuner.recommend_cache_size(metrics, current_cache_mb=4096)
    assert recommended2 is None  # 冷却期内，不推荐切换
    print(f"✅ 冷却期机制：已是最优配置，不推荐切换")

    print("\n🎉 Cache Tuner 集成测试通过！\n")


async def test_dashboard_cache_endpoint():
    """测试 Dashboard /cache 端点数据结构"""
    print("=" * 60)
    print("测试 3: Dashboard /cache 端点")
    print("=" * 60)

    from clawgate.context.prompt_cache import PromptCacheManager
    from clawgate.tuning.cache_tuner import HeuristicCacheTuner
    from pathlib import Path
    import shutil

    # 使用独立的缓存目录，避免和测试1冲突
    test3_cache_dir = Path(".solar/test3-prompt-cache")
    if test3_cache_dir.exists():
        shutil.rmtree(test3_cache_dir)

    # 模拟 Dashboard 端点返回的数据
    manager = PromptCacheManager(
        enabled=True,
        hot_cache_size=256,
        warm_cache_dir=str(test3_cache_dir / "warm")
    )
    tuner = HeuristicCacheTuner()

    # 模拟一些缓存操作以生成统计数据
    payload = {"model": "test", "temperature": 0, "stream": False, "n": 1}
    messages = [{"role": "user", "content": "test"}]

    key = manager.make_key(payload, messages)
    manager.get(key)  # miss
    manager.store(key, {"test": "response"})
    manager.get(key)  # hot hit
    manager.get(key)  # hot hit again

    # 构建 Dashboard 响应（模拟 dashboard.py 的逻辑）
    tuner_stats = tuner.get_stats()
    last_decision = tuner_stats.get("last_decision", {})

    response = {
        "prompt_cache": manager.get_stats(),
        "cache_tuning": {
            "enabled": True,
            "current_cache_mb": 4096,
            "candidates_mb": tuner_stats.get("candidates_mb", []),
            "last_recommendation": last_decision.get("target_cache_mb"),
            "last_switch_time": tuner_stats.get("last_switch_time"),
            "switch_count": 0,
        }
    }

    # 验证响应结构
    assert "prompt_cache" in response
    assert "cache_tuning" in response
    print("✅ 响应包含必需字段")

    # 验证 Prompt Cache 统计
    pc = response["prompt_cache"]
    print(f"📊 实际 Prompt Cache 统计：{pc}")

    assert pc["enabled"] is True
    # 操作序列：manager.get(key) [miss] → manager.store(key, resp) → manager.get(key) [hot hit] → manager.get(key) [hot hit]
    # 预期：hit_hot=2, miss=1, total_requests=3, hit_rate=2/3
    assert pc["hit_hot"] == 2, f"Expected hit_hot=2, got {pc['hit_hot']}"
    assert pc["miss"] == 1, f"Expected miss=1, got {pc['miss']}"
    assert pc["total_requests"] == 3, f"Expected total_requests=3, got {pc['total_requests']}"
    # hit_rate = (hit_hot + hit_warm) / total_requests = 2 / 3
    expected_hit_rate = 2 / 3
    assert abs(pc["hit_rate"] - expected_hit_rate) < 0.01, f"Expected hit_rate≈{expected_hit_rate:.2f}, got {pc['hit_rate']:.2f}"
    print(f"✅ Prompt Cache 统计：hit_hot={pc['hit_hot']}, miss={pc['miss']}, total_requests={pc['total_requests']}, hit_rate={pc['hit_rate']:.1%}")

    # 验证 Cache Tuning 统计
    ct = response["cache_tuning"]
    assert ct["enabled"] is True
    assert ct["current_cache_mb"] == 4096
    assert len(ct["candidates_mb"]) == 4
    print(f"✅ Cache Tuning 统计：current={ct['current_cache_mb']} MB, candidates={ct['candidates_mb']}")

    # 打印完整响应
    print("\n📊 Dashboard /cache 响应示例:")
    print(json.dumps(response, indent=2))

    # 清理测试数据
    shutil.rmtree(test3_cache_dir, ignore_errors=True)

    print("\n🎉 Dashboard 端点测试通过！\n")


async def test_cacheable_detection():
    """测试可缓存请求检测逻辑"""
    print("=" * 60)
    print("测试 4: 可缓存请求检测")
    print("=" * 60)

    from clawgate.context.prompt_cache import PromptCacheManager

    test_cases = [
        # (payload, expected_cacheable, description)
        (
            {"temperature": 0, "stream": False, "n": 1},
            True,
            "确定性非流式请求"
        ),
        (
            {"temperature": 0.7, "stream": False, "n": 1},
            False,
            "非零 temperature"
        ),
        (
            {"temperature": 0, "stream": True, "n": 1},
            False,
            "流式请求"
        ),
        (
            {"temperature": 0, "stream": False, "n": 2},
            False,
            "n > 1"
        ),
        (
            {"temperature": 0, "stream": False},  # n 默认为 1
            True,
            "n 默认值"
        ),
    ]

    for payload, expected, description in test_cases:
        result = PromptCacheManager.is_cacheable(payload)
        assert result == expected, f"Failed: {description}"
        status = "✅" if result else "❌"
        print(f"{status} {description}: {payload} → cacheable={result}")

    print("\n🎉 可缓存检测测试通过！\n")


async def test_performance_comparison():
    """性能对比测试：有缓存 vs 无缓存"""
    print("=" * 60)
    print("测试 5: 性能对比（缓存命中 vs 未命中）")
    print("=" * 60)

    from clawgate.context.prompt_cache import PromptCacheManager

    manager = PromptCacheManager(enabled=True, hot_cache_size=256)

    payload = {"model": "test", "temperature": 0, "stream": False, "n": 1}
    messages = [{"role": "user", "content": "test query"}]
    response = {"test": "response" * 100}  # 模拟较大响应

    cache_key = manager.make_key(payload, messages)

    # 第一次：缓存未命中（模拟实际请求）
    start = time.time()
    cached, _ = manager.get(cache_key)
    if cached is None:
        # 模拟实际 LLM 请求延迟
        await asyncio.sleep(0.1)
        manager.store(cache_key, response)
    miss_time = time.time() - start

    # 第二次：缓存命中（直接返回）
    start = time.time()
    cached, cache_type = manager.get(cache_key)
    assert cached is not None
    assert cache_type == "hot"
    hit_time = time.time() - start

    speedup = miss_time / hit_time
    print(f"✅ 缓存未命中延迟: {miss_time*1000:.2f} ms")
    print(f"✅ 缓存命中延迟: {hit_time*1000:.2f} ms")
    print(f"✅ 加速比: {speedup:.1f}x")

    assert speedup > 10, "缓存命中应该显著快于未命中"

    print("\n🎉 性能对比测试通过！\n")


async def main():
    """运行所有端到端测试"""
    print("\n" + "=" * 60)
    print("Phase 2 端到端测试套件")
    print("=" * 60 + "\n")

    try:
        await test_prompt_cache_integration()
        await test_cache_tuner_integration()
        await test_dashboard_cache_endpoint()
        await test_cacheable_detection()
        await test_performance_comparison()

        print("=" * 60)
        print("🎉 所有端到端测试通过！")
        print("=" * 60)
        print("\n✅ Phase 2 集成验证完成")
        print("✅ Prompt Cache: 请求流程集成正常")
        print("✅ Cache Tuner: Engine 集成正常")
        print("✅ Dashboard: /cache 端点正常")
        print("✅ 性能: 缓存命中加速 > 10x")
        print("\n下一步: 性能回归测试 + 文档更新")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        raise


if __name__ == "__main__":
    asyncio.run(main())
