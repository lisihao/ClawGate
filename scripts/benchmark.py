#!/usr/bin/env python3
"""性能基准测试脚本

测试 Continuous Batching 的性能提升
"""

import asyncio
import time
import statistics
from typing import List
import httpx


async def send_request(
    client: httpx.AsyncClient,
    model: str,
    prompt_tokens: int,
    priority: int = 1,
) -> dict:
    """发送单个请求"""
    # 生成指定长度的 prompt
    prompt = "测试 " * (prompt_tokens // 2)

    start_time = time.time()

    response = await client.post(
        "http://localhost:8000/v1/chat/completions",
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
            "stream": False,
            "priority": priority,
        },
        timeout=120.0,
    )

    end_time = time.time()

    data = response.json()
    ttft = end_time - start_time

    return {
        "ttft": ttft,
        "total_time": end_time - start_time,
        "prompt_tokens": prompt_tokens,
        "priority": priority,
    }


async def run_benchmark(
    model: str,
    num_short: int = 10,
    num_long: int = 1,
    short_tokens: int = 32,
    long_tokens: int = 4096,
):
    """
    运行基准测试

    Args:
        model: 模型名称
        num_short: 短请求数量
        num_long: 长请求数量
        short_tokens: 短请求 token 数
        long_tokens: 长请求 token 数
    """
    print("\n" + "=" * 60)
    print("🚀 OpenClaw Gateway 性能基准测试")
    print("=" * 60)
    print(f"\n模型: {model}")
    print(f"短请求: {num_short} × {short_tokens} tokens")
    print(f"长请求: {num_long} × {long_tokens} tokens")
    print("\n开始测试...\n")

    async with httpx.AsyncClient() as client:
        # 创建任务列表
        tasks = []

        # 1 个长请求（高优先级）
        for i in range(num_long):
            tasks.append(send_request(client, model, long_tokens, priority=0))

        # 等待一小段时间
        await asyncio.sleep(0.5)

        # 10 个短请求（正常优先级）
        for i in range(num_short):
            tasks.append(send_request(client, model, short_tokens, priority=1))

        # 并发执行
        results = await asyncio.gather(*tasks)

    # 分析结果
    short_results = [r for r in results if r["prompt_tokens"] == short_tokens]
    long_results = [r for r in results if r["prompt_tokens"] == long_tokens]

    # 短请求统计
    short_ttfts = [r["ttft"] for r in short_results]
    short_avg = statistics.mean(short_ttfts)
    short_p50 = statistics.median(short_ttfts)
    short_p99 = statistics.quantiles(short_ttfts, n=100)[98] if len(short_ttfts) > 10 else max(
        short_ttfts
    )

    # 长请求统计
    long_ttfts = [r["ttft"] for r in long_results]
    long_avg = statistics.mean(long_ttfts)

    # 打印结果
    print("\n" + "=" * 60)
    print("📊 测试结果")
    print("=" * 60)

    print(f"\n短请求 ({short_tokens} tokens):")
    print(f"  平均 TTFT: {short_avg:.3f}s")
    print(f"  P50 TTFT:  {short_p50:.3f}s")
    print(f"  P99 TTFT:  {short_p99:.3f}s")

    print(f"\n长请求 ({long_tokens} tokens):")
    print(f"  平均 TTFT: {long_avg:.3f}s")

    # 与 FCFS 对比
    # FCFS 预期：短请求需要等待长请求完成
    # CB 优化：短请求插队，TTFT 大幅降低
    print(f"\n预期提升:")
    print(f"  无 CB: 短请求 TTFT ≈ {long_avg:.1f}s（等待长请求）")
    print(f"  有 CB: 短请求 TTFT ≈ {short_p99:.1f}s（插队执行）")
    print(f"  提升倍数: {long_avg / short_p99:.1f}×")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OpenClaw Gateway 性能基准测试")
    parser.add_argument(
        "--model", type=str, default="qwen2.5-7b-mlx", help="模型名称"
    )
    parser.add_argument(
        "--num-short", type=int, default=10, help="短请求数量"
    )
    parser.add_argument(
        "--num-long", type=int, default=1, help="长请求数量"
    )

    args = parser.parse_args()

    asyncio.run(
        run_benchmark(
            model=args.model,
            num_short=args.num_short,
            num_long=args.num_long,
        )
    )
