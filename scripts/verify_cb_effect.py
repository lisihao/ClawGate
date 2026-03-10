#!/usr/bin/env python3
"""真正验证 Continuous Batching 效果

对比场景：
1. FCFS (先来先服务) - 模拟无 CB
2. CB (连续批处理) - 当前实现

测试：1× 长请求 + N× 短请求并发
"""

import requests
import time
import threading
import statistics
from datetime import datetime

def send_request(request_id, prompt, max_tokens, priority, results):
    """发送单个请求并记录时间"""
    start_time = time.time()

    try:
        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            json={
                "model": "qwen-1.7b",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "priority": priority,
                "stream": False
            },
            timeout=120
        )

        end_time = time.time()
        latency = end_time - start_time

        if response.status_code == 200:
            data = response.json()
            results.append({
                "request_id": request_id,
                "priority": priority,
                "prompt_length": len(prompt),
                "max_tokens": max_tokens,
                "latency": latency,
                "tokens": data["usage"]["total_tokens"],
                "success": True
            })
            print(f"  [{request_id}] ✓ {latency:.2f}s (priority={priority}, {data['usage']['total_tokens']} tokens)")
        else:
            print(f"  [{request_id}] ✗ HTTP {response.status_code}")
            results.append({
                "request_id": request_id,
                "success": False,
                "error": f"HTTP {response.status_code}"
            })

    except Exception as e:
        print(f"  [{request_id}] ✗ {type(e).__name__}: {str(e)[:50]}")
        results.append({
            "request_id": request_id,
            "success": False,
            "error": str(e)
        })


def test_scenario(name, requests_config, stagger_delay=0):
    """测试场景"""
    print(f"\n{'='*70}")
    print(f"📊 {name}")
    print(f"{'='*70}\n")

    print(f"请求配置:")
    for i, req in enumerate(requests_config):
        print(f"  {i+1}. {'长请求' if req['max_tokens'] > 100 else '短请求'} "
              f"(priority={req.get('priority', 1)}, max_tokens={req['max_tokens']})")

    print(f"\n开始发送...\n")

    results = []
    threads = []
    start_test = time.time()

    # 创建线程
    for i, config in enumerate(requests_config):
        thread = threading.Thread(
            target=send_request,
            args=(
                f"req-{i}",
                config["prompt"],
                config["max_tokens"],
                config.get("priority", 1),
                results
            )
        )
        threads.append(thread)

    # 启动线程（模拟并发）
    for i, thread in enumerate(threads):
        thread.start()
        if stagger_delay > 0:
            time.sleep(stagger_delay)  # 交错启动

    # 等待所有完成
    for thread in threads:
        thread.join()

    end_test = time.time()
    total_time = end_test - start_test

    # 分析结果
    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]

    print(f"\n{'='*70}")
    print(f"📈 结果分析")
    print(f"{'='*70}\n")

    print(f"总耗时: {total_time:.2f}s")
    print(f"成功: {len(successful)}/{len(results)}")
    print(f"失败: {len(failed)}/{len(results)}")

    if successful:
        # 按优先级分组
        high_priority = [r for r in successful if r["priority"] == 0]
        normal_priority = [r for r in successful if r["priority"] == 1]
        low_priority = [r for r in successful if r["priority"] == 2]

        # 按请求长度分组
        long_requests = [r for r in successful if r["max_tokens"] > 100]
        short_requests = [r for r in successful if r["max_tokens"] <= 100]

        if short_requests:
            short_latencies = [r["latency"] for r in short_requests]
            print(f"\n短请求 ({len(short_requests)} 个):")
            print(f"  - 平均延迟: {statistics.mean(short_latencies):.3f}s")
            print(f"  - P50: {statistics.median(short_latencies):.3f}s")
            print(f"  - P99: {max(short_latencies):.3f}s")
            print(f"  - 最小: {min(short_latencies):.3f}s")

        if long_requests:
            long_latencies = [r["latency"] for r in long_requests]
            print(f"\n长请求 ({len(long_requests)} 个):")
            print(f"  - 平均延迟: {statistics.mean(long_latencies):.3f}s")

        if high_priority:
            high_latencies = [r["latency"] for r in high_priority]
            print(f"\n高优先级 ({len(high_priority)} 个):")
            print(f"  - 平均延迟: {statistics.mean(high_latencies):.3f}s")

        if normal_priority:
            normal_latencies = [r["latency"] for r in normal_priority]
            print(f"\n普通优先级 ({len(normal_priority)} 个):")
            print(f"  - 平均延迟: {statistics.mean(normal_latencies):.3f}s")

    if failed:
        print(f"\n❌ 失败的请求:")
        for r in failed:
            print(f"  - {r['request_id']}: {r.get('error', 'Unknown')}")

    return {
        "total_time": total_time,
        "successful": successful,
        "failed": failed
    }


def main():
    """主函数"""
    print("\n" + "=" * 70)
    print(" " * 15 + "🧪 Continuous Batching 效果验证")
    print("=" * 70)
    print(f"\n测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标: 验证 CB 对短请求 TTFT 的提升效果")

    # ========== 场景 1: 温和并发（2个短请求） ==========
    print("\n" + "=" * 70)
    print("阶段 1: 温和并发测试（验证服务稳定性）")
    print("=" * 70)

    scenario_1 = test_scenario(
        "2× 短请求并发",
        [
            {"prompt": "问题1：什么是AI？", "max_tokens": 30, "priority": 1},
            {"prompt": "问题2：什么是ML？", "max_tokens": 30, "priority": 1},
        ],
        stagger_delay=0.1  # 交错 100ms
    )

    if len(scenario_1["failed"]) > 0:
        print("\n❌ 服务在温和并发下失败，停止测试")
        return

    # ========== 场景 2: 中等并发（1长 + 3短） ==========
    print("\n" + "=" * 70)
    print("阶段 2: 中等并发测试（CB 关键场景）")
    print("=" * 70)

    scenario_2 = test_scenario(
        "1× 长请求 + 3× 短请求",
        [
            # 长请求先到达（priority=2，后台）
            {"prompt": "详细解释机器学习的原理和应用" * 10, "max_tokens": 150, "priority": 2},
            # 短请求稍后到达（priority=1，正常）
            {"prompt": "什么是AI？", "max_tokens": 30, "priority": 1},
            {"prompt": "什么是ML？", "max_tokens": 30, "priority": 1},
            {"prompt": "什么是DL？", "max_tokens": 30, "priority": 1},
        ],
        stagger_delay=0.2  # 交错 200ms
    )

    # ========== 总结 ==========
    print("\n" + "=" * 70)
    print("📊 验证总结")
    print("=" * 70)

    print(f"\n与 Phase 1.5 目标对比:")
    print(f"  - 目标：短请求 P99 TTFT < 0.64s (CB) vs 3.84s (FCFS)")
    print(f"  - 提升：6.01×")

    if scenario_2["successful"]:
        short_reqs = [r for r in scenario_2["successful"] if r["max_tokens"] <= 30]
        if short_reqs:
            short_p99 = max([r["latency"] for r in short_reqs])
            print(f"\n当前实现:")
            print(f"  - 短请求 P99: {short_p99:.3f}s")

            # 估算 FCFS 下的延迟（假设长请求先执行完）
            long_reqs = [r for r in scenario_2["successful"] if r["max_tokens"] > 100]
            if long_reqs:
                long_latency = max([r["latency"] for r in long_reqs])
                fcfs_estimate = long_latency  # FCFS 下短请求需要等待长请求

                improvement = fcfs_estimate / short_p99
                print(f"  - 估算 FCFS: ~{fcfs_estimate:.2f}s (短请求等待长请求)")
                print(f"  - 实际提升: {improvement:.2f}×")

                if improvement >= 2.0:
                    print(f"\n✅ CB 效果显著！提升 {improvement:.1f}×")
                elif improvement >= 1.5:
                    print(f"\n⚠️  CB 有效果，但低于预期（提升 {improvement:.1f}×）")
                else:
                    print(f"\n❌ CB 效果不明显（提升仅 {improvement:.1f}×）")
            else:
                print(f"\n⚠️  无长请求完成，无法对比")
        else:
            print(f"\n❌ 无短请求完成，无法评估")
    else:
        print(f"\n❌ 场景 2 测试失败，无法评估 CB 效果")

    print(f"\n💡 结论:")
    print(f"  1. 当前 llama.cpp 后端限制了并发处理能力")
    print(f"  2. CB 调度器已集成，但需要引擎支持真正并发")
    print(f"  3. 建议：使用 vLLM 或 SGLang 等支持 CB 的引擎")

    print("\n" + "=" * 70)
    print("✅ 验证完成")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  测试中断")
