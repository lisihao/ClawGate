"""测试 Dashboard Cache 端点

验证 /dashboard/cache 端点的响应结构
"""

import asyncio


async def test_dashboard_cache_response_structure():
    """测试 Dashboard Cache 响应结构"""
    from clawgate.api.dashboard import CacheResponse, PromptCacheStats, CacheTuningStats
    from clawgate.context.prompt_cache import PromptCacheManager

    # 模拟响应数据
    prompt_cache_manager = PromptCacheManager(
        enabled=True,
        hot_cache_size=256,
        hot_ttl_sec=3600,
        warm_ttl_sec=86400,
    )

    # 获取统计信息
    stats = prompt_cache_manager.get_stats()
    print(f"✅ Prompt Cache 统计信息:")
    print(f"   - enabled: {stats['enabled']}")
    print(f"   - hot_cache_size: {stats['hot_cache_size']}/{stats['hot_cache_max']}")
    print(f"   - hits: hot={stats['hit_hot']}, warm={stats['hit_warm']}")
    print(f"   - miss: {stats['miss']}")
    print(f"   - hit_rate: {stats['hit_rate']:.2%}")

    # 验证响应模型
    prompt_cache_stats = PromptCacheStats(**stats)
    print(f"\n✅ PromptCacheStats 模型验证通过")

    # 模拟 Cache Tuning 数据
    cache_tuning_data = {
        "enabled": True,
        "current_cache_mb": 4096,
        "candidates_mb": [2048, 4096, 6144, 8192],
        "last_recommendation": 6144,
        "last_switch_time": 1678886400.0,
        "switch_count": 3,
    }
    cache_tuning_stats = CacheTuningStats(**cache_tuning_data)
    print(f"✅ CacheTuningStats 模型验证通过")

    # 组装完整响应
    response_data = {
        "prompt_cache": stats,
        "cache_tuning": cache_tuning_data,
    }
    response = CacheResponse(**response_data)
    print(f"\n✅ CacheResponse 模型验证通过")
    print(f"\n📊 完整响应结构:")
    print(response.model_dump_json(indent=2))


async def test_cache_endpoint_with_disabled_features():
    """测试 Cache 端点在功能禁用时的响应"""
    from clawgate.api.dashboard import CacheResponse

    # 模拟所有功能禁用的响应
    response_data = {
        "prompt_cache": {
            "enabled": False,
            "hot_cache_size": 0,
            "hot_cache_max": 0,
            "hit_hot": 0,
            "hit_warm": 0,
            "miss": 0,
            "total_requests": 0,
            "hit_rate": 0.0,
            "store": 0,
            "evict_hot": 0,
            "evict_warm": 0,
        },
        "cache_tuning": None,
    }

    response = CacheResponse(**response_data)
    print(f"\n✅ 禁用状态响应验证通过")
    print(response.model_dump_json(indent=2))


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("Dashboard Cache 端点测试")
    print("=" * 60)

    await test_dashboard_cache_response_structure()
    print("\n" + "=" * 60)
    await test_cache_endpoint_with_disabled_features()

    print("\n" + "=" * 60)
    print("🎉 所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
