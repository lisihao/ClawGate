"""Sliding Window Strategy - 滑动窗口保留最近消息"""

from typing import List, Dict, Optional


class SlidingWindowStrategy:
    """滑动窗口策略 - 保留最近的 N 条消息"""

    def compress(
        self,
        messages: List[Dict],
        target_tokens: int,
        agent_type: Optional[str] = None,
        tokenizer=None,
    ) -> List[Dict]:
        """
        滑动窗口压缩

        保留 system 消息 + 最近的 N 条消息

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

        # 1. 保留 system 消息
        system_messages = [msg for msg in messages if msg.get("role") == "system"]

        # 2. 保留非 system 消息
        content_messages = [msg for msg in messages if msg.get("role") != "system"]

        # 3. 从后往前保留消息，直到达到 token 限制
        compressed = []
        current_tokens = 0

        # 先计算 system 消息的 token
        for msg in system_messages:
            content = msg.get("content", "")
            current_tokens += len(tokenizer.encode(content)) if tokenizer else len(
                content
            ) // 4

        # 从后往前添加消息
        for msg in reversed(content_messages):
            content = msg.get("content", "")
            msg_tokens = len(tokenizer.encode(content)) if tokenizer else len(
                content
            ) // 4

            if current_tokens + msg_tokens > target_tokens:
                break

            compressed.insert(0, msg)
            current_tokens += msg_tokens

        # 4. 合并 system + content
        return system_messages + compressed
