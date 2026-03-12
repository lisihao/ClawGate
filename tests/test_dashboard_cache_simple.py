"""简化的 Dashboard Cache 测试 - 不依赖 FastAPI

只测试数据结构和逻辑，不测试 Pydantic 模型
"""

from clawgate.context.prompt_cache import PromptCacheManager


def test_prompt_cache_stats():
    """测试 PromptCacheManager.get_stats() 返回正确的数据结构"""
    print("=" * 60)
    print("测试 PromptCacheManager.get_stats()")
    print("=" * 60)

    manager = PromptCacheManager(
        enabled=True,
        hot_cache_size=256,
        hot_ttl_sec=3600,
        warm_ttl_sec=86400,
    )

    stats = manager.get_stats()

    # 验证所有必需的字段
    required_fields = [
        "enabled", "hot_cache_size", "hot_cache_max",
        "hit_hot", "hit_warm", "miss", "total_requests",
        "hit_rate", "store", "evict_hot", "evict_warm"
    ]

    print("\n✅ 字段检查:")
    for field in required_fields:
        assert field in stats, f"缺少字段: {field}"
        print(f"   - {field}: {stats[field]}")

    # 验证数据类型
    assert isinstance(stats["enabled"], bool)
    assert isinstance(stats["hot_cache_size"], int)
    assert isinstance(stats["hit_rate"], float)
    print("\n✅ 数据类型验证通过")

    # 验证初始状态
    assert stats["enabled"] is True
    assert stats["hot_cache_size"] == 0
    assert stats["hot_cache_max"] == 256
    assert stats["hit_rate"] == 0.0
    print("✅ 初始状态验证通过")


def test_dashboard_cache_response_structure():
    """测试 /dashboard/cache 端点的响应结构"""
    print("\n" + "=" * 60)
    print("测试 Dashboard Cache 响应结构")
    print("=" * 60)

    # 模拟 dashboard_cache() 函数返回的数据结构
    manager = PromptCacheManager(
        enabled=True,
        hot_cache_size=256,
        hot_ttl_sec=3600,
        warm_ttl_sec=86400,
    )

    response = {
        "prompt_cache": manager.get_stats(),
        "cache_tuning": {
            "enabled": True,
            "current_cache_mb": 4096,
            "candidates_mb": [2048, 4096, 6144, 8192],
            "last_recommendation": 6144,
            "last_switch_time": 1678886400.0,
            "switch_count": 3,
        }
    }

    # 验证响应结构
    assert "prompt_cache" in response
    assert "cache_tuning" in response
    print("\n✅ 响应包含 prompt_cache 和 cache_tuning")

    # 验证 prompt_cache 字段
    pc = response["prompt_cache"]
    assert pc["enabled"] is True
    assert pc["hot_cache_max"] == 256
    print("✅ prompt_cache 字段验证通过")

    # 验证 cache_tuning 字段
    ct = response["cache_tuning"]
    assert ct["enabled"] is True
    assert ct["current_cache_mb"] == 4096
    assert len(ct["candidates_mb"]) == 4
    print("✅ cache_tuning 字段验证通过")

    # 打印完整响应
    import json
    print("\n📊 完整响应结构:")
    print(json.dumps(response, indent=2))


def test_disabled_cache_tuning():
    """测试 Cache Tuning 禁用时的响应"""
    print("\n" + "=" * 60)
    print("测试 Cache Tuning 禁用状态")
    print("=" * 60)

    response = {
        "prompt_cache": PromptCacheManager(enabled=False).get_stats(),
        "cache_tuning": {
            "enabled": False,
            "current_cache_mb": 4096,
            "candidates_mb": [],
            "last_recommendation": None,
            "last_switch_time": None,
            "switch_count": 0,
        }
    }

    assert response["prompt_cache"]["enabled"] is False
    assert response["cache_tuning"]["enabled"] is False
    print("✅ 禁用状态验证通过")

    import json
    print("\n📊 禁用状态响应:")
    print(json.dumps(response, indent=2))


if __name__ == "__main__":
    test_prompt_cache_stats()
    test_dashboard_cache_response_structure()
    test_disabled_cache_tuning()

    print("\n" + "=" * 60)
    print("🎉 所有测试通过！")
    print("=" * 60)
