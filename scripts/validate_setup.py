#!/usr/bin/env python3
"""验证 OpenClaw Gateway 设置

测试本地模型（ThunderLLAMA/llama.cpp）+ 云端模型（GLM/OpenAI）
"""

import asyncio
import time
import os
from pathlib import Path
import yaml
import httpx
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


async def test_health():
    """测试服务健康"""
    console.print("\n[bold blue]🔍 检查服务状态...[/bold blue]")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/health", timeout=5.0)
            data = response.json()

            console.print(f"[green]✓[/green] 服务状态: {data['status']}")
            console.print(f"[green]✓[/green] 版本: {data['version']}")

            if data.get("engines"):
                console.print(f"[green]✓[/green] 可用引擎: {list(data['engines'].keys())}")

            return True
    except Exception as e:
        console.print(f"[red]✗[/red] 服务不可用: {e}")
        console.print(
            "\n[yellow]💡 请先启动服务:[/yellow] ./scripts/start.sh\n"
        )
        return False


async def test_models():
    """测试可用模型"""
    console.print("\n[bold blue]📋 检查可用模型...[/bold blue]")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8000/models", timeout=5.0)
            data = response.json()

            local_models = data.get("local_models", [])
            cloud_models = data.get("cloud_models", [])

            console.print(f"\n[cyan]本地模型:[/cyan]")
            if local_models:
                for model in local_models:
                    console.print(f"  [green]✓[/green] {model}")
            else:
                console.print("  [yellow]⚠[/yellow] 无本地模型（需要下载）")

            console.print(f"\n[cyan]云端模型:[/cyan]")
            for model in cloud_models:
                console.print(f"  [green]✓[/green] {model}")

            return local_models, cloud_models
    except Exception as e:
        console.print(f"[red]✗[/red] 获取模型列表失败: {e}")
        return [], []


async def test_inference(model: str, prompt: str):
    """测试单个模型推理"""
    start_time = time.time()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8000/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 100,
                    "stream": False,
                },
                timeout=60.0,
            )

            end_time = time.time()
            latency = end_time - start_time

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                tokens = data["usage"]["total_tokens"]

                return {
                    "success": True,
                    "latency": latency,
                    "content": content,
                    "tokens": tokens,
                }
            else:
                return {"success": False, "error": f"HTTP {response.status_code}"}

    except Exception as e:
        return {"success": False, "error": str(e)}


async def run_validation():
    """运行完整验证"""
    console.print("\n[bold magenta]🚀 OpenClaw Gateway 验证测试[/bold magenta]")
    console.print("=" * 60)

    # 1. 检查服务
    if not await test_health():
        return

    # 2. 检查模型
    local_models, cloud_models = await test_models()

    # 3. 运行推理测试
    console.print("\n[bold blue]🧪 运行推理测试...[/bold blue]\n")

    # 测试场景
    test_cases = [
        {
            "name": "简单问答",
            "prompt": "什么是机器学习？用一句话回答。",
        },
        {
            "name": "代码生成",
            "prompt": "写一个 Python 函数计算斐波那契数列的第 n 项",
        },
    ]

    # 准备测试模型
    test_models = []
    if local_models:
        test_models.append(local_models[0])  # 第一个本地模型
    if "glm-4-flash" in cloud_models:
        test_models.append("glm-4-flash")
    if "gpt-4o-mini" in cloud_models:
        test_models.append("gpt-4o-mini")

    if not test_models:
        console.print("[yellow]⚠ 没有可测试的模型[/yellow]")
        return

    # 执行测试
    results = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        for test_case in test_cases:
            for model in test_models:
                task = progress.add_task(
                    f"测试 {model} - {test_case['name']}", total=None
                )

                result = await test_inference(model, test_case["prompt"])
                result["model"] = model
                result["test_case"] = test_case["name"]
                results.append(result)

                progress.remove_task(task)

    # 4. 显示结果
    console.print("\n[bold blue]📊 测试结果[/bold blue]\n")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("测试场景", style="cyan")
    table.add_column("模型", style="yellow")
    table.add_column("状态", style="green")
    table.add_column("延迟", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("响应预览", max_width=50)

    for result in results:
        status = "✓" if result["success"] else "✗"
        status_color = "green" if result["success"] else "red"

        latency = f"{result.get('latency', 0):.2f}s" if result["success"] else "-"
        tokens = str(result.get("tokens", "-")) if result["success"] else "-"
        preview = (
            result.get("content", "")[:50] + "..." if result["success"] else result.get("error", "")
        )

        table.add_row(
            result["test_case"],
            result["model"],
            f"[{status_color}]{status}[/{status_color}]",
            latency,
            tokens,
            preview,
        )

    console.print(table)

    # 5. 总结
    console.print("\n[bold blue]📈 性能对比[/bold blue]\n")

    successful_results = [r for r in results if r["success"]]
    if successful_results:
        # 按模型分组
        model_stats = {}
        for result in successful_results:
            model = result["model"]
            if model not in model_stats:
                model_stats[model] = []
            model_stats[model].append(result["latency"])

        # 显示平均延迟
        for model, latencies in model_stats.items():
            avg_latency = sum(latencies) / len(latencies)
            is_local = model in (local_models or [])
            model_type = "[cyan]本地[/cyan]" if is_local else "[yellow]云端[/yellow]"
            console.print(
                f"  {model_type} {model}: 平均延迟 [green]{avg_latency:.2f}s[/green]"
            )

    console.print("\n" + "=" * 60)
    console.print("[bold green]✅ 验证完成！[/bold green]\n")


if __name__ == "__main__":
    try:
        asyncio.run(run_validation())
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ 测试中断[/yellow]\n")
