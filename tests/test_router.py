"""测试 Router"""

import pytest
from clawgate.router.classifier import TaskClassifier
from clawgate.router.selector import ModelSelector


def test_task_classification():
    """测试任务分类"""
    classifier = TaskClassifier()

    # 测试推理任务
    messages = [{"role": "user", "content": "分析一下这个算法的时间复杂度"}]
    result = classifier.classify(messages)

    print(f"\nTask classification result:")
    print(f"  Type: {result['task_type']}")
    print(f"  Complexity: {result['complexity']}")
    print(f"  Features: {result['features']}")

    assert result["task_type"] in ["reasoning", "coding", "qa"]
    assert result["complexity"] in ["high", "medium", "low"]


def test_coding_task():
    """测试编码任务识别"""
    classifier = TaskClassifier()

    messages = [
        {
            "role": "user",
            "content": "写一个 Python 函数实现快速排序\n\n```python\ndef quicksort(arr):\n    ...\n```",
        }
    ]

    result = classifier.classify(messages)

    print(f"\nCoding task:")
    print(f"  Type: {result['task_type']}")
    print(f"  Has code: {result['features']['has_code']}")

    assert result["task_type"] == "coding"
    assert result["features"]["has_code"] is True


def test_model_selection():
    """测试模型选择"""
    selector = ModelSelector()

    # 高复杂度推理任务
    task_info = {"task_type": "reasoning", "complexity": "high", "priority": 0}

    model = selector.select(task_info, optimize_for="quality")

    print(f"\nSelected model for high complexity reasoning: {model}")

    assert model in [
        "deepseek-r1",
        "gpt-4o",
        "gemini-2.5-pro",
    ]


def test_agent_based_selection():
    """测试基于 Agent 的选择"""
    selector = ModelSelector()

    # Judge agent
    task_info = {"task_type": "reasoning", "complexity": "medium", "priority": 1}

    model = selector.select(task_info, agent_type="judge")

    print(f"\nSelected model for judge agent: {model}")

    # Should prefer judge's preferred models
    assert model in ["deepseek-r1", "gpt-4o"]


def test_cost_optimization():
    """测试成本优化"""
    selector = ModelSelector()

    # 低复杂度任务
    task_info = {"task_type": "qa", "complexity": "low", "priority": 2}

    model = selector.select(task_info, optimize_for="cost")

    print(f"\nSelected model for low cost: {model}")

    # Should prefer cheaper models
    assert model in ["glm-4-flash", "gemini-2.5-flash"]


if __name__ == "__main__":
    test_task_classification()
    test_coding_task()
    test_model_selection()
    test_agent_based_selection()
    test_cost_optimization()
    print("\n✅ All router tests passed!")
