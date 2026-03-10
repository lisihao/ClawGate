"""Selective Retain Strategy - 选择性保留重要消息"""

from typing import List, Dict, Optional


class SelectiveRetainStrategy:
    """选择性保留策略 - 基于重要性保留消息"""

    def __init__(self):
        # 重要性权重
        self.importance_weights = {
            "system": 10,  # System 消息最重要
            "error": 8,  # 错误消息
            "decision": 7,  # 决策消息
            "code": 6,  # 代码消息
            "question": 5,  # 问题
            "answer": 5,  # 回答
            "normal": 1,  # 普通消息
        }

    def compress(
        self,
        messages: List[Dict],
        target_tokens: int,
        agent_type: Optional[str] = None,
        tokenizer=None,
    ) -> List[Dict]:
        """
        选择性保留压缩

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

        # 1. 计算每条消息的重要性分数
        scored_messages = []
        for idx, msg in enumerate(messages):
            score = self._calculate_importance(msg, idx, len(messages))
            msg_tokens = len(tokenizer.encode(msg.get("content", ""))) if tokenizer else len(
                msg.get("content", "")
            ) // 4

            scored_messages.append(
                {"message": msg, "score": score, "tokens": msg_tokens, "index": idx}
            )

        # 2. 按分数排序（降序）
        scored_messages.sort(key=lambda x: x["score"], reverse=True)

        # 3. 贪心选择，直到达到 token 限制
        selected = []
        current_tokens = 0

        for item in scored_messages:
            if current_tokens + item["tokens"] > target_tokens:
                # 如果是 system 消息，强制保留
                if item["message"].get("role") == "system":
                    selected.append(item)
                    current_tokens += item["tokens"]
                continue

            selected.append(item)
            current_tokens += item["tokens"]

        # 4. 按原始顺序排序
        selected.sort(key=lambda x: x["index"])

        return [item["message"] for item in selected]

    def _calculate_importance(self, msg: Dict, index: int, total: int) -> float:
        """
        计算消息重要性分数

        考虑因素：
        1. 角色类型 (system > assistant > user)
        2. 内容特征 (错误/决策/代码)
        3. 位置 (最近的消息更重要)
        """
        score = 0.0

        # 1. 角色权重
        role = msg.get("role", "user")
        if role == "system":
            score += self.importance_weights["system"]
        elif role == "assistant":
            score += 3
        else:
            score += 2

        # 2. 内容特征
        content = msg.get("content", "").lower()

        if any(keyword in content for keyword in ["error", "exception", "failed"]):
            score += self.importance_weights["error"]

        if any(
            keyword in content
            for keyword in ["decision", "choose", "选择", "决定"]
        ):
            score += self.importance_weights["decision"]

        if any(keyword in content for keyword in ["```", "def ", "function", "class "]):
            score += self.importance_weights["code"]

        # 3. 位置权重（最近的消息更重要）
        recency_score = (index / total) * 5
        score += recency_score

        return score
