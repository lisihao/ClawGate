#!/usr/bin/env python3
"""Context Shift 性能对比测试

对比 Context Shift vs 简单占位符的性能差异：
- 延迟对比（P50, P95, P99）
- 摘要质量对比（人工评估）
- Token 使用对比

运行前提:
- Context Shift 服务已启动 (18083, 18084)
"""

import asyncio
import logging
import sys
import time
from pathlib import Path
from statistics import mean, median, stdev
from typing import List, Dict

# 添加项目根目录到 sys.path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from clawgate.context.context_shift_client import ContextShiftClient
from clawgate.context.strategies.layering import ThreeTierLayeringStrategy

logging.basicConfig(
    level=logging.WARNING,  # 减少日志噪音
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger(__name__)


# 测试数据集
TEST_DATASETS = {
    "short_conversation": [
        {"role": "user", "content": "你好，我想了解 Python 的异步编程。"},
        {"role": "assistant", "content": "你好！Python 的异步编程主要使用 asyncio 库，它基于事件循环和协程..."},
        {"role": "user", "content": "asyncio.run() 和 asyncio.create_task() 有什么区别？"},
        {"role": "assistant", "content": "asyncio.run() 用于运行顶层入口函数，而 create_task() 用于创建并发任务..."},
        {"role": "user", "content": "能给个例子吗？"},
        {"role": "assistant", "content": "当然，这是一个并发下载的例子：\n```python\nimport asyncio\n\nasync def download(url):\n    await asyncio.sleep(1)\n    return f'Downloaded {url}'\n\nasync def main():\n    tasks = [asyncio.create_task(download(url)) for url in urls]\n    results = await asyncio.gather(*tasks)\n    return results\n```"},
    ],

    "medium_conversation": [
        {"role": "user", "content": "我想学习机器学习，应该从哪里开始？"},
        {"role": "assistant", "content": "很好的问题！机器学习是一个广阔的领域。建议从以下几个方面入手：\n1. Python 编程基础\n2. 数学基础（线性代数、微积分、概率论）\n3. 机器学习算法理论\n4. 实践项目"},
        {"role": "user", "content": "需要学哪些数学知识？"},
        {"role": "assistant", "content": "核心的数学知识包括：\n1. 线性代数：矩阵运算、特征值、特征向量\n2. 微积分：梯度、导数、优化\n3. 概率论与统计：概率分布、贝叶斯定理、假设检验\n4. 优化理论：梯度下降、凸优化"},
        {"role": "user", "content": "有推荐的学习资源吗？"},
        {"role": "assistant", "content": "推荐以下资源：\n1. 课程：Andrew Ng 的机器学习课程（Coursera）\n2. 书籍：《机器学习》（周志华）、《深度学习》（Ian Goodfellow）\n3. 实践平台：Kaggle、Google Colab\n4. 开源库：scikit-learn、TensorFlow、PyTorch"},
        {"role": "user", "content": "需要多久才能掌握？"},
        {"role": "assistant", "content": "这取决于你的基础和投入时间：\n- 如果有编程和数学基础，每天投入 2-3 小时，大约 3-6 个月可以入门\n- 达到中级水平（能独立完成项目）需要 1-2 年\n- 成为专家需要持续学习和实践 3-5 年"},
        {"role": "user", "content": "第一个项目做什么比较好？"},
        {"role": "assistant", "content": "建议从经典的入门项目开始：\n1. 房价预测（回归问题）\n2. 手写数字识别（分类问题，MNIST 数据集）\n3. 垃圾邮件分类（文本分类）\n4. 推荐系统（协同过滤）\n选择一个你感兴趣的领域，从简单的数据集开始练习。"},
        {"role": "user", "content": "谢谢，我会努力学习的！"},
        {"role": "assistant", "content": "加油！机器学习是一个非常有趣的领域。遇到问题随时可以问我。记住，实践比理论更重要，多动手写代码！"},
    ],

    "long_conversation": [
        {"role": "system", "content": "你是一个专业的编程助手，擅长 Python 和机器学习。"},
        {"role": "user", "content": "我在做一个自然语言处理项目，想了解 Transformer 架构。"},
        {"role": "assistant", "content": "Transformer 是 NLP 领域的革命性架构，由 Google 在 2017 年提出。核心创新是 Self-Attention 机制，解决了 RNN 的长程依赖问题。"},
        {"role": "user", "content": "Self-Attention 是如何工作的？"},
        {"role": "assistant", "content": "Self-Attention 的核心思想是让模型自己学习输入序列中不同位置之间的关系。\n\n具体步骤：\n1. 将输入 embedding 转换为 Query、Key、Value 三个向量\n2. 计算 Query 和所有 Key 的点积，得到注意力分数\n3. 对分数进行 softmax 归一化\n4. 用归一化后的分数对 Value 进行加权求和"},
        {"role": "user", "content": "为什么需要 Multi-Head Attention？"},
        {"role": "assistant", "content": "Multi-Head Attention 允许模型从不同的表示子空间学习信息：\n1. 不同的 head 可以关注不同的语义特征\n2. 增强模型的表达能力\n3. 类似于 CNN 中的多个卷积核\n\n实践中通常使用 8 或 16 个 head。"},
        {"role": "user", "content": "Positional Encoding 的作用是什么？"},
        {"role": "assistant", "content": "因为 Transformer 没有 RNN 的顺序结构，需要通过 Positional Encoding 注入位置信息：\n\n1. 使用正弦和余弦函数编码位置\n2. PE(pos, 2i) = sin(pos / 10000^(2i/d))\n3. PE(pos, 2i+1) = cos(pos / 10000^(2i/d))\n\n这样模型就能区分不同位置的 token。"},
        {"role": "user", "content": "Encoder 和 Decoder 有什么区别？"},
        {"role": "assistant", "content": "Encoder 和 Decoder 的主要区别：\n\n**Encoder**:\n- 双向 Self-Attention（可以看到整个序列）\n- 用于理解输入序列\n- 输出用于 Decoder 的 Cross-Attention\n\n**Decoder**:\n- 单向 Masked Self-Attention（只能看到之前的 token）\n- Cross-Attention 层（关注 Encoder 输出）\n- 用于生成输出序列"},
        {"role": "user", "content": "BERT 和 GPT 的区别是什么？"},
        {"role": "assistant", "content": "BERT 和 GPT 是基于 Transformer 的两种不同架构：\n\n**BERT** (Bidirectional Encoder):\n- 只使用 Encoder\n- 双向上下文（MLM 预训练）\n- 适合理解任务（分类、QA、NER）\n\n**GPT** (Generative Pre-trained Transformer):\n- 只使用 Decoder\n- 单向上下文（从左到右）\n- 适合生成任务（文本生成、对话）"},
        {"role": "user", "content": "如何在 PyTorch 中实现简单的 Transformer？"},
        {"role": "assistant", "content": "这是一个简化的 Transformer Encoder 实现：\n\n```python\nimport torch\nimport torch.nn as nn\n\nclass TransformerEncoder(nn.Module):\n    def __init__(self, d_model=512, nhead=8, num_layers=6):\n        super().__init__()\n        encoder_layer = nn.TransformerEncoderLayer(\n            d_model=d_model,\n            nhead=nhead,\n            dim_feedforward=2048\n        )\n        self.encoder = nn.TransformerEncoder(\n            encoder_layer,\n            num_layers=num_layers\n        )\n        \n    def forward(self, src):\n        return self.encoder(src)\n```\n\nPyTorch 已经提供了 `nn.Transformer` 模块，开箱即用。"},
        {"role": "user", "content": "训练时有哪些技巧？"},
        {"role": "assistant", "content": "Transformer 训练的关键技巧：\n\n1. **Learning Rate Warmup**: 开始时使用较小的学习率，逐渐增大\n2. **Label Smoothing**: 避免过拟合\n3. **Dropout**: 在 Attention 和 FFN 层应用 dropout\n4. **Gradient Clipping**: 防止梯度爆炸\n5. **Layer Normalization**: 每个子层后应用 LayerNorm\n6. **Residual Connection**: 缓解梯度消失问题"},
        {"role": "user", "content": "非常感谢！这些信息很有帮助。"},
        {"role": "assistant", "content": "不客气！Transformer 是一个很深的话题。如果你想深入了解，推荐阅读原始论文《Attention is All You Need》和《The Illustrated Transformer》博客。\n\n实践中，建议先用 HuggingFace Transformers 库，它封装了常见的模型。祝你项目顺利！"},
    ]
}


async def benchmark_context_shift(dataset: List[Dict], mode: str, runs: int = 10) -> Dict:
    """测试 Context Shift 性能"""
    client = ContextShiftClient(mode=mode)
    latencies = []

    try:
        for i in range(runs):
            start = time.time()
            summary = await client.summarize(dataset, target_tokens=200)
            latency = time.time() - start
            latencies.append(latency)

            if i == 0:  # 保存第一次的摘要结果
                first_summary = summary

        # 统计延迟
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        avg = mean(latencies)
        std = stdev(latencies) if len(latencies) > 1 else 0.0

        return {
            "mode": mode,
            "runs": runs,
            "latency_p50": p50,
            "latency_p95": p95,
            "latency_p99": p99,
            "latency_avg": avg,
            "latency_std": std,
            "summary": first_summary,
            "summary_length": len(first_summary) if first_summary else 0
        }

    finally:
        await client.close()


def benchmark_simple_compression(dataset: List[Dict], runs: int = 10) -> Dict:
    """测试简单压缩性能"""
    latencies = []

    # 模拟简单压缩（字符截断）
    def simple_compact(messages: List[Dict], max_lines: int = 12) -> str:
        lines = []
        for msg in messages[-24:]:
            role = msg.get("role", "user")
            content = msg.get("content", "").strip()
            if not content:
                continue
            content = " ".join(content.split())
            lines.append(f"- {role}: {content[:140]}")

        if not lines:
            return ""

        return "History tail summary:\n" + "\n".join(lines[-max_lines:])

    for i in range(runs):
        start = time.time()
        summary = simple_compact(dataset)
        latency = time.time() - start
        latencies.append(latency)

        if i == 0:
            first_summary = summary

    # 统计延迟
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    avg = mean(latencies)
    std = stdev(latencies) if len(latencies) > 1 else 0.0

    return {
        "mode": "simple",
        "runs": runs,
        "latency_p50": p50,
        "latency_p95": p95,
        "latency_p99": p99,
        "latency_avg": avg,
        "latency_std": std,
        "summary": first_summary,
        "summary_length": len(first_summary)
    }


async def run_performance_test():
    """运行性能对比测试"""
    print("\n" + "=" * 80)
    print("Context Shift 性能对比测试")
    print("=" * 80)

    results = {}

    for dataset_name, dataset in TEST_DATASETS.items():
        print(f"\n📊 测试数据集: {dataset_name} ({len(dataset)} 条消息)")
        print("-" * 80)

        # 测试简单压缩
        print("  [1/3] 测试简单压缩...")
        simple_result = benchmark_simple_compression(dataset, runs=10)
        results[f"{dataset_name}_simple"] = simple_result

        # 测试 Context Shift (fast 模式)
        print("  [2/3] 测试 Context Shift (fast 模式)...")
        fast_result = await benchmark_context_shift(dataset, mode="fast", runs=5)
        results[f"{dataset_name}_fast"] = fast_result

        # 测试 Context Shift (quality 模式)
        print("  [3/3] 测试 Context Shift (quality 模式)...")
        quality_result = await benchmark_context_shift(dataset, mode="quality", runs=5)
        results[f"{dataset_name}_quality"] = quality_result

        # 打印结果
        print(f"\n  结果汇总:")
        print(f"    简单压缩:   P50={simple_result['latency_p50']*1000:.2f}ms, P99={simple_result['latency_p99']*1000:.2f}ms, 长度={simple_result['summary_length']} 字符")
        print(f"    CS (fast):  P50={fast_result['latency_p50']*1000:.2f}ms, P99={fast_result['latency_p99']*1000:.2f}ms, 长度={fast_result['summary_length']} 字符")
        print(f"    CS (quality): P50={quality_result['latency_p50']*1000:.2f}ms, P99={quality_result['latency_p99']*1000:.2f}ms, 长度={quality_result['summary_length']} 字符")

    return results


def generate_performance_report(results: Dict):
    """生成性能报告"""
    print("\n" + "=" * 80)
    print("性能报告总结")
    print("=" * 80)

    # 延迟对比表
    print("\n## 延迟对比 (毫秒)")
    print("\n| 数据集 | 方法 | P50 | P95 | P99 | Avg | Std |")
    print("|--------|------|-----|-----|-----|-----|-----|")

    for key, result in results.items():
        dataset_name = key.rsplit("_", 1)[0]
        mode = result["mode"]
        print(f"| {dataset_name} | {mode} | "
              f"{result['latency_p50']*1000:.2f} | "
              f"{result['latency_p95']*1000:.2f} | "
              f"{result['latency_p99']*1000:.2f} | "
              f"{result['latency_avg']*1000:.2f} | "
              f"{result['latency_std']*1000:.2f} |")

    # 摘要长度对比
    print("\n## 摘要长度对比 (字符)")
    print("\n| 数据集 | 简单压缩 | CS (fast) | CS (quality) |")
    print("|--------|----------|-----------|--------------|")

    for dataset_name in TEST_DATASETS.keys():
        simple_len = results[f"{dataset_name}_simple"]["summary_length"]
        fast_len = results[f"{dataset_name}_fast"]["summary_length"]
        quality_len = results[f"{dataset_name}_quality"]["summary_length"]
        print(f"| {dataset_name} | {simple_len} | {fast_len} | {quality_len} |")

    # 摘要示例
    print("\n## 摘要示例")
    for dataset_name in ["short_conversation"]:
        print(f"\n### {dataset_name}")

        print(f"\n**简单压缩** ({results[f'{dataset_name}_simple']['summary_length']} 字符):")
        print("```")
        print(results[f"{dataset_name}_simple"]["summary"][:300])
        print("```")

        print(f"\n**Context Shift (fast)** ({results[f'{dataset_name}_fast']['summary_length']} 字符):")
        print("```")
        print(results[f"{dataset_name}_fast"]["summary"][:300] if results[f"{dataset_name}_fast"]["summary"] else "(None)")
        print("```")

        print(f"\n**Context Shift (quality)** ({results[f'{dataset_name}_quality']['summary_length']} 字符):")
        print("```")
        print(results[f"{dataset_name}_quality"]["summary"][:300] if results[f"{dataset_name}_quality"]["summary"] else "(None)")
        print("```")

    print("\n" + "=" * 80)
    print("性能测试完成")
    print("=" * 80)


async def main():
    """主函数"""
    try:
        # 运行性能测试
        results = await run_performance_test()

        # 生成报告
        generate_performance_report(results)

    except Exception as e:
        logger.error(f"性能测试失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
