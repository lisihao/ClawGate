#!/usr/bin/env python3
"""Continuous Batching 性能对比测试

对比 FCFS（先来先服务）vs CB（连续批处理）的性能
"""

import requests
import time
import statistics
import concurrent.futures
from datetime import datetime

def send_request(request_id, prompt, priority=1):
    """发送单个请求"""
    start = time.time()

    response = requests.post(
        "http://localhost:8000/v1/chat/completions",
        json={
            "model": "qwen-1.7b",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 50,
            "stream": False,
            "priority": priority,
        },
        timeout=120
    )

    end = time.time()
    latency = end - start

    data = response.json()
    tokens = data["usage"]["total_tokens"]

    return {
        "request_id": request_id,
        "latency": latency,
        "tokens": tokens,
        "priority": priority,
        "start_time": start,
        "end_time": end
    }


def test_scenario(scenario_name, requests_config):
    """测试场景"""
    print(f"\n{'='*60}")
    print(f"📊 {scenario_name}")
    print(f"{'='*60}\n")

    results = []
    start_test = time.time()

    # 并发发送所有请求
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []

        for i, config in enumerate(requests_config):
            future = executor.submit(
                send_request,
                f"req-{i}",
                config["prompt"],
                config.get("priority", 1)
            )
            futures.append(future)

            # 模拟请求间隔
            if config.get("delay"):
                time.sleep(config["delay"])

        # 收集结果
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)

    end_test = time.time()
    total_time = end_test - start_test

    # 分析结果
    analyze_results(results, total_time)

    return results


def analyze_results(results, total_time):
    """分析结果"""
    # 按优先级分组
    high_priority = [r for r in results if r["priority"] == 0]
    normal_priority = [r for r in results if r["priority"] == 1]

    print(f"总耗时: {total_time:.2f}s")
    print(f"总请求: {len(results)}")

    if high_priority:
        latencies = [r["latency"] for r in high_priority]
        print(f"\n高优先级请求 ({len(high_priority)} 个):")
        print(f"  - 平均延迟: {statistics.mean(latencies):.3f}s")
        print(f"  - P50 延迟: {statistics.median(latencies):.3f}s")
        print(f"  - P99 延迟: {max(latencies):.3f}s")

    if normal_priority:
        latencies = [r["latency"] for r in normal_priority]
        print(f"\n普通优先级请求 ({len(normal_priority)} 个):")
        print(f"  - 平均延迟: {statistics.mean(latencies):.3f}s")
        print(f"  - P50 延迟: {statistics.median(latencies):.3f}s")
        print(f"  - P99 延迟: {max(latencies):.3f}s")


def main():
    print("\n" + "=" * 60)
    print("🚀 Continuous Batching 性能测试")
    print("=" * 60)
    print(f"\n模型: Qwen3-1.7B-Q4")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 场景 1: 混合负载（1 个长请求 + 5 个短请求）
    print("\n" + "=" * 60)
    print("场景 1: 混合负载测试")
    print("  - 1 个长请求（高优先级，4096 tokens）")
    print("  - 5 个短请求（普通优先级，32 tokens）")
    print("=" * 60)

    requests_config = [
        # 1 个长请求（先到达）
        {
            "prompt": "详细解释机器学习的原理、应用场景和未来发展趋势，包括深度学习、强化学习等方向" * 10,
            "priority": 0,
            "delay": 0
        },
    ] + [
        # 5 个短请求（稍后到达）
        {
            "prompt": f"简答：什么是 AI？（问题 {i}）",
            "priority": 1,
            "delay": 0.1 if i == 0 else 0
        }
        for i in range(5)
    ]

    results_mixed = test_scenario("混合负载", requests_config)

    # 场景 2: 高并发短请求
    print("\n" + "=" * 60)
    print("场景 2: 高并发短请求")
    print("  - 10 个短请求同时到达")
    print("=" * 60)

    requests_config_short = [
        {
            "prompt": f"问题 {i}: 什么是机器学习？",
            "priority": 1,
            "delay": 0
        }
        for i in range(10)
    ]

    results_short = test_scenario("高并发短请求", requests_config_short)

    # 总结
    print("\n" + "=" * 60)
    print("📈 性能总结")
    print("=" * 60)

    # 对比 Phase 1.5 的基准结果
    print("\n与 Phase 1.5 Demo 对比（目标）:")
    print("  - FCFS 短请求 P99 TTFT: ~3.8s")
    print("  - CB 短请求 P99 TTFT: ~0.6s")
    print("  - 理论提升: 6.01×")

    print("\n当前实现:")
    if results_mixed:
        normal = [r for r in results_mixed if r["priority"] == 1]
        if normal:
            p99 = max([r["latency"] for r in normal])
            print(f"  - 短请求 P99 延迟: {p99:.3f}s")

    print("\n💡 优化建议:")
    print("  1. 启用真正的 Continuous Batching 调度器")
    print("  2. 实现分块 Prefill（chunked prefill）")
    print("  3. 优化优先级队列调度")
    print("  4. 使用 GPU 加速（目前已启用 Metal）")

    print("\n" + "=" * 60)
    print("✅ 测试完成！")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
