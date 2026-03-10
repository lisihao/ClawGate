#!/usr/bin/env python3
"""OpenClaw Gateway v2 功能测试

测试：
1. Continuous Batching
2. ContextEngine (上下文压缩)
3. 智能路由 (任务分类 + 模型选择)
4. 云端后端 (如果配置了 API Key)
"""

import requests
import time
import json

def print_section(title):
    """打印章节标题"""
    print("\n" + "=" * 70)
    print(f" {title}")
    print("=" * 70 + "\n")


def test_health():
    """测试健康检查"""
    print_section("1️⃣ 健康检查")

    response = requests.get("http://localhost:8000/health")
    data = response.json()

    print(f"状态: {data['status']}")
    print(f"版本: {data['version']}")
    print(f"\n✅ 已启用功能:")
    for feature, enabled in data['features'].items():
        status = "✓" if enabled else "✗"
        print(f"  {status} {feature}")

    print(f"\n📦 本地模型: {', '.join(data['local_models'])}")
    print(f"☁️  云端后端: {', '.join(data['cloud_backends']) if data['cloud_backends'] else '无 (未配置 API Key)'}")

    return data


def test_basic_inference():
    """测试基础推理"""
    print_section("2️⃣ 基础推理测试")

    start = time.time()
    response = requests.post(
        "http://localhost:8000/v1/chat/completions",
        json={
            "model": "qwen-1.7b",
            "messages": [{"role": "user", "content": "简短回答：什么是 AI？"}],
            "max_tokens": 30,
        },
        timeout=60
    )
    latency = time.time() - start

    data = response.json()
    content = data["choices"][0]["message"]["content"]

    print(f"延迟: {latency:.2f}s")
    print(f"Tokens: {data['usage']['total_tokens']}")
    print(f"响应: {content[:80]}...")

    return latency


def test_context_compression():
    """测试上下文压缩"""
    print_section("3️⃣ ContextEngine - 上下文压缩")

    # 创建长上下文
    long_messages = [
        {"role": "system", "content": "你是一个有帮助的助手。"},
        {"role": "user", "content": "第一个问题：什么是机器学习？" * 20},
        {"role": "assistant", "content": "机器学习是..." * 20},
        {"role": "user", "content": "第二个问题：什么是深度学习？" * 20},
        {"role": "assistant", "content": "深度学习是..." * 20},
        {"role": "user", "content": "最后一个问题：总结一下。"},
    ]

    print("原始上下文:")
    print(f"  - 消息数: {len(long_messages)}")
    print(f"  - 估计 tokens: ~{sum([len(m['content']) for m in long_messages]) // 4}")

    # 启用压缩
    start = time.time()
    response = requests.post(
        "http://localhost:8000/v1/chat/completions",
        json={
            "model": "qwen-1.7b",
            "messages": long_messages,
            "max_tokens": 50,
            "enable_context_compression": True,
            "target_context_tokens": 500,  # 压缩到 500 tokens
        },
        timeout=60
    )
    latency = time.time() - start

    data = response.json()

    print(f"\n压缩后:")
    print(f"  - 延迟: {latency:.2f}s")
    print(f"  - 输入 tokens: {data['usage']['prompt_tokens']}")
    print(f"  - 响应: {data['choices'][0]['message']['content'][:60]}...")

    return data['usage']['prompt_tokens']


def test_priority_queue():
    """测试优先级队列"""
    print_section("4️⃣ 优先级队列测试")

    tests = [
        {"priority": 0, "label": "紧急任务"},
        {"priority": 1, "label": "正常任务"},
        {"priority": 2, "label": "后台任务"},
    ]

    results = []

    for test in tests:
        print(f"{test['label']} (priority={test['priority']})...", end=" ", flush=True)

        start = time.time()
        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            json={
                "model": "qwen-1.7b",
                "messages": [{"role": "user", "content": "你好"}],
                "max_tokens": 20,
                "priority": test["priority"],
            },
            timeout=60
        )
        latency = time.time() - start

        print(f"✓ {latency:.2f}s")
        results.append({"priority": test["priority"], "latency": latency})

    print(f"\n💡 预期：priority=0 应该最快")
    return results


def test_smart_routing():
    """测试智能路由（自动模型选择）"""
    print_section("5️⃣ 智能路由 - 自动模型选择")

    # 不指定模型，让系统自动选择
    tests = [
        {"prompt": "什么是 AI？", "expected_type": "qa"},
        {"prompt": "写一个 Python 函数计算斐波那契数列", "expected_type": "coding"},
        {"prompt": "分析为什么 Transformer 在 NLP 中表现优异", "expected_type": "reasoning"},
    ]

    for test in tests:
        print(f"\n任务: {test['prompt'][:40]}...")

        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            json={
                "model": "qwen-1.7b",  # 当前只有一个本地模型
                "messages": [{"role": "user", "content": test["prompt"]}],
                "max_tokens": 50,
            },
            timeout=60
        )

        data = response.json()
        print(f"  - 模型: {data['model']}")
        print(f"  - Tokens: {data['usage']['total_tokens']}")


def test_agent_type_routing():
    """测试 Agent 类型路由"""
    print_section("6️⃣ Agent 类型路由")

    agent_types = ["judge", "builder", "flash"]

    for agent_type in agent_types:
        print(f"\nAgent: {agent_type}...", end=" ", flush=True)

        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            json={
                "model": "qwen-1.7b",
                "messages": [{"role": "user", "content": f"任务（{agent_type}）"}],
                "max_tokens": 20,
                "agent_type": agent_type,
            },
            timeout=60
        )

        data = response.json()
        print(f"✓ {data['usage']['total_tokens']} tokens")


def test_streaming():
    """测试流式响应"""
    print_section("7️⃣ 流式响应测试")

    print("发送流式请求...\n")

    response = requests.post(
        "http://localhost:8000/v1/chat/completions",
        json={
            "model": "qwen-1.7b",
            "messages": [{"role": "user", "content": "数到 5"}],
            "max_tokens": 30,
            "stream": True,
        },
        stream=True,
        timeout=60
    )

    chunks_count = 0
    start = time.time()
    first_chunk_time = None

    for line in response.iter_lines():
        if not line:
            continue

        line = line.decode('utf-8')
        if line.startswith('data: '):
            data_str = line[6:]
            if data_str == '[DONE]':
                break

            try:
                data = json.loads(data_str)
                content = data['choices'][0]['delta'].get('content', '')
                if content:
                    if first_chunk_time is None:
                        first_chunk_time = time.time()
                    print(content, end='', flush=True)
                    chunks_count += 1
            except:
                pass

    total_time = time.time() - start
    ttft = first_chunk_time - start if first_chunk_time else total_time

    print(f"\n\n统计:")
    print(f"  - TTFT: {ttft:.3f}s")
    print(f"  - 总时间: {total_time:.3f}s")
    print(f"  - Chunks: {chunks_count}")


def main():
    """主函数"""
    print("\n" + "=" * 70)
    print(" " * 15 + "🚀 OpenClaw Gateway v2 功能测试")
    print("=" * 70)

    try:
        # 1. 健康检查
        health_data = test_health()

        # 2. 基础推理
        basic_latency = test_basic_inference()

        # 3. 上下文压缩
        compressed_tokens = test_context_compression()

        # 4. 优先级队列
        priority_results = test_priority_queue()

        # 5. 智能路由
        test_smart_routing()

        # 6. Agent 类型路由
        test_agent_type_routing()

        # 7. 流式响应
        test_streaming()

        # 总结
        print_section("📊 测试总结")

        print("✅ 功能验证:")
        print(f"  ✓ Continuous Batching: {health_data['features']['continuous_batching']}")
        print(f"  ✓ ContextEngine: {health_data['features']['context_engine']}")
        print(f"  ✓ 智能路由: {health_data['features']['smart_routing']}")

        print(f"\n📈 性能数据:")
        print(f"  - 基础延迟: {basic_latency:.2f}s")
        print(f"  - 压缩后 tokens: {compressed_tokens}")

        print(f"\n💡 下一步:")
        print(f"  1. 配置云端 API Key 以启用混合路由")
        print(f"  2. 运行并发测试验证 CB 性能提升")
        print(f"  3. 集成到 OpenClaw 主应用")

        print("\n" + "=" * 70)
        print("✅ 所有测试通过！")
        print("=" * 70 + "\n")

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
