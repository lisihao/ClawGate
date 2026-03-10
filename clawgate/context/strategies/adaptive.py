"""Adaptive Strategy - 自适应多策略组合"""

from typing import List, Dict, Optional
from .sliding_window import SlidingWindowStrategy
from .selective import SelectiveRetainStrategy


class AdaptiveStrategy:
    """自适应策略 - 根据场景选择最佳压缩策略"""

    def __init__(self):
        self.sliding_window = SlidingWindowStrategy()
        self.selective = SelectiveRetainStrategy()

        # Agent 类型偏好
        self.agent_preferences = {
            "judge": "selective",  # 审判官需要保留决策上下文
            "builder": "sliding_window",  # 建设者只需要最近代码
            "flash": "sliding_window",  # 闪电侠快速任务
        }

    def compress(
        self,
        messages: List[Dict],
        target_tokens: int,
        agent_type: Optional[str] = None,
        tokenizer=None,
    ) -> List[Dict]:
        """
        自适应压缩

        根据 Agent 类型和消息特征选择最佳策略

        Args:
            messages: 消息列表
            target_tokens: 目标 token 数
            agent_type: Agent 类型
            tokenizer: Tokenizer

        Returns:
            压缩后的消息
        """
        if not messages:
            return []

        # 1. 根据 Agent 类型选择策略
        if agent_type and agent_type in self.agent_preferences:
            strategy_name = self.agent_preferences[agent_type]
        else:
            # 2. 根据消息特征选择策略
            strategy_name = self._auto_select_strategy(messages)

        # 3. 执行对应策略
        if strategy_name == "selective":
            return self.selective.compress(messages, target_tokens, agent_type, tokenizer)
        else:
            return self.sliding_window.compress(messages, target_tokens, agent_type, tokenizer)

    def _auto_select_strategy(self, messages: List[Dict]) -> str:
        """
        自动选择策略

        分析消息特征：
        - 如果包含大量决策/错误 → selective
        - 如果主要是代码/问答 → sliding_window
        """
        decision_count = 0
        error_count = 0

        for msg in messages:
            content = msg.get("content", "").lower()

            if any(
                keyword in content
                for keyword in ["decision", "choose", "选择", "决定"]
            ):
                decision_count += 1

            if any(
                keyword in content
                for keyword in ["error", "exception", "failed", "bug"]
            ):
                error_count += 1

        # 如果决策/错误消息占比 > 20%，使用 selective
        if (decision_count + error_count) / len(messages) > 0.2:
            return "selective"
        else:
            return "sliding_window"
