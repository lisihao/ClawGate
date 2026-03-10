#!/usr/bin/env python3
"""简单性能测试"""

import requests
import time
import statistics

def test_inference(prompt, max_tokens=50):
    """单次推理测试"""
    start = time.time()

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

    end = time.time()
    latency = end - start

    data = response.json()
    content = data["choices"][0]["message"]["content"]
    tokens = data["usage"]["total_tokens"]

    return {
        "latency": latency,
        "content": content,
        "tokens": tokens,
        "prompt_tokens": data["usage"]["prompt_tokens"],
        "completion_tokens": data["usage"]["completion_tokens"]
    }


def main():
    print("\n" + "=" * 60)
    print("🚀 OpenClaw Gateway 性能测试")
    print("=" * 60)
    print(f"\n模型: Qwen3-1.7B-Q4 (ThunderLLAMA)")

    # 测试场景
    tests = [
        {"name": "简短问答", "prompt": "什么是机器学习？", "max_tokens": 30},
        {"name": "代码生成", "prompt": "写一个Python函数计算斐波那契数列", "max_tokens": 100},
        {"name": "推理分析", "prompt": "解释为什么本地推理比云端API延迟更低", "max_tokens": 80},
    ]

    results = []

    print("\n开始测试...\n")

    for test in tests:
        print(f"📝 {test['name']}...", end=" ", flush=True)

        try:
            result = test_inference(test["prompt"], test["max_tokens"])
            results.append({"name": test["name"], **result})
            print(f"✓ {result['latency']:.2f}s ({result['tokens']} tokens)")
        except Exception as e:
            print(f"✗ 失败: {e}")

    # 统计
    if results:
        latencies = [r["latency"] for r in results]
        avg_latency = statistics.mean(latencies)
        min_latency = min(latencies)
        max_latency = max(latencies)

        print("\n" + "=" * 60)
        print("📊 性能统计")
        print("=" * 60)
        print(f"\n延迟:")
        print(f"  - 平均: {avg_latency:.3f}s")
        print(f"  - 最小: {min_latency:.3f}s")
        print(f"  - 最大: {max_latency:.3f}s")

        total_tokens = sum(r["tokens"] for r in results)
        total_time = sum(r["latency"] for r in results)
        throughput = total_tokens / total_time

        print(f"\n吞吐量:")
        print(f"  - {throughput:.1f} tokens/s")

        print(f"\n示例输出:")
        for r in results[:2]:
            preview = r["content"][:60].replace("\n", " ")
            print(f"  [{r['name']}] {preview}...")

        print("\n" + "=" * 60)
        print("✅ 测试完成！")
        print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
