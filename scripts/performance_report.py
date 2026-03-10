#!/usr/bin/env python3
"""性能测试报告 - 串行测试避免并发问题"""

import requests
import time
import statistics

def test_request(test_name, prompt, max_tokens=50):
    """单个请求测试"""
    print(f"  {test_name}...", end=" ", flush=True)

    start = time.time()
    try:
        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            json={
                "model": "qwen-1.7b",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "stream": False
            },
            timeout=60
        )

        if response.status_code != 200:
            print(f"✗ HTTP {response.status_code}")
            return None

        end = time.time()
        latency = end - start

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        tokens = data["usage"]["total_tokens"]
        prompt_tokens = data["usage"]["prompt_tokens"]
        completion_tokens = data["usage"]["completion_tokens"]

        print(f"✓ {latency:.2f}s ({tokens} tokens)")

        return {
            "test_name": test_name,
            "latency": latency,
            "tokens": tokens,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "content": content,
            "tokens_per_sec": completion_tokens / latency if latency > 0 else 0
        }

    except Exception as e:
        print(f"✗ {e}")
        return None


def main():
    print("\n" + "=" * 70)
    print(" " * 20 + "🚀 性能测试报告")
    print("=" * 70)

    print(f"\n模型信息:")
    print(f"  - 名称: Qwen3-1.7B-Q4 (ThunderLLAMA)")
    print(f"  - 引擎: llama.cpp")
    print(f"  - 加速: Apple Metal (GPU)")
    print(f"  - 上下文: 32K tokens")

    # 测试用例
    tests = [
        ("简短问答", "什么是机器学习？", 30),
        ("中等问答", "解释Python中的装饰器是什么，如何使用？", 80),
        ("代码生成", "写一个Python函数实现快速排序算法", 120),
        ("推理分析", "分析为什么Transformer架构在NLP任务中表现优异", 100),
        ("翻译任务", "Translate to English: 人工智能正在改变世界", 40),
    ]

    print(f"\n" + "=" * 70)
    print("📊 延迟测试（5个场景）")
    print("=" * 70 + "\n")

    results = []
    for test_name, prompt, max_tokens in tests:
        result = test_request(test_name, prompt, max_tokens)
        if result:
            results.append(result)
        time.sleep(0.5)  # 避免过载

    if not results:
        print("\n❌ 所有测试失败")
        return

    # 统计分析
    latencies = [r["latency"] for r in results]
    tokens_per_sec = [r["tokens_per_sec"] for r in results]

    print("\n" + "=" * 70)
    print("📈 性能统计")
    print("=" * 70)

    print(f"\n延迟分析:")
    print(f"  - 平均延迟: {statistics.mean(latencies):.3f}s")
    print(f"  - 中位延迟: {statistics.median(latencies):.3f}s")
    print(f"  - 最小延迟: {min(latencies):.3f}s")
    print(f"  - 最大延迟: {max(latencies):.3f}s")

    print(f"\n吞吐量分析:")
    print(f"  - 平均吞吐: {statistics.mean(tokens_per_sec):.1f} tokens/s")
    print(f"  - 最大吞吐: {max(tokens_per_sec):.1f} tokens/s")

    total_tokens = sum(r["tokens"] for r in results)
    total_time = sum(r["latency"] for r in results)
    overall_throughput = total_tokens / total_time

    print(f"  - 整体吞吐: {overall_throughput:.1f} tokens/s")

    # Token 分析
    avg_prompt = statistics.mean([r["prompt_tokens"] for r in results])
    avg_completion = statistics.mean([r["completion_tokens"] for r in results])

    print(f"\nToken 统计:")
    print(f"  - 平均 Prompt: {avg_prompt:.0f} tokens")
    print(f"  - 平均 Completion: {avg_completion:.0f} tokens")
    print(f"  - 平均总计: {avg_prompt + avg_completion:.0f} tokens")

    # 示例输出
    print(f"\n" + "=" * 70)
    print("💬 示例输出")
    print("=" * 70 + "\n")

    for r in results[:2]:
        preview = r["content"][:80].replace("\n", " ")
        print(f"[{r['test_name']}]")
        print(f"  {preview}...")
        print()

    # Phase 1.5 对比
    print("=" * 70)
    print("📊 与 Phase 1.5 Demo 对比")
    print("=" * 70)

    print(f"\nPhase 1.5 成果（Continuous Batching）:")
    print(f"  - 短请求 P99 TTFT: 0.639s (CB) vs 3.839s (FCFS)")
    print(f"  - 性能提升: 6.01×")
    print(f"  - GPU 利用率: 95% vs 60%")

    print(f"\n当前实现（单请求基准）:")
    print(f"  - 平均延迟: {statistics.mean(latencies):.3f}s")
    print(f"  - 吞吐量: {overall_throughput:.1f} tokens/s")

    print(f"\n💡 下一步优化:")
    print(f"  1. ✅ 集成 Continuous Batching 调度器")
    print(f"  2. ✅ 实现优先级队列（已支持 priority 0/1/2）")
    print(f"  3. ⏳ 启用分块 Prefill（需要引擎支持）")
    print(f"  4. ✅ GPU 加速（Metal 已启用）")
    print(f"  5. ⏳ 多模型并行服务")

    print("\n" + "=" * 70)
    print("✅ 测试完成！")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
