"""Summarization Strategy - 使用 LLM 摘要压缩"""

from typing import List, Dict, Optional


class SummarizationStrategy:
    """摘要策略 - 使用 LLM 生成摘要"""

    def __init__(self):
        # 摘要配置
        self.summary_templates = {
            "brief": "用 1-2 句话总结以下对话的核心内容：\n\n{content}",
            "detailed": "总结以下对话，保留关键决策、代码示例和重要细节：\n\n{content}",
            "full": "详细总结以下对话，包括：\n1. 主要讨论主题\n2. 关键决策和原因\n3. 代码示例\n4. 待办事项\n\n{content}",
        }

    def compress(
        self,
        messages: List[Dict],
        target_tokens: int,
        agent_type: Optional[str] = None,
        tokenizer=None,
    ) -> List[Dict]:
        """
        摘要压缩

        将旧消息摘要为一条 system 消息，保留最近的消息

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

        # 简化实现：保留最后 1/3 的消息，前面的用占位符表示
        # 生产环境应该调用 LLM 生成真实摘要

        total_messages = len(messages)
        keep_count = max(1, total_messages // 3)

        # 保留最后的消息
        recent_messages = messages[-keep_count:]

        # 为旧消息生成摘要占位符
        summary_msg = {
            "role": "system",
            "content": f"[前 {total_messages - keep_count} 条消息已被压缩] 对话主题涉及多个技术讨论和代码实现。",
        }

        return [summary_msg] + recent_messages

    def generate_summary(
        self, messages: List[Dict], level: str = "brief", tokenizer=None
    ) -> Dict:
        """
        生成摘要数据（用于持久化）

        Args:
            messages: 消息列表
            level: 摘要级别
            tokenizer: Tokenizer

        Returns:
            摘要数据
        """
        # 简化实现：提取关键信息
        summary_text = f"对话包含 {len(messages)} 条消息"

        # 统计 token
        original_tokens = sum(
            [
                len(tokenizer.encode(msg.get("content", "")))
                if tokenizer
                else len(msg.get("content", "")) // 4
                for msg in messages
            ]
        )

        return {
            "summary_text": summary_text,
            "key_decisions": [],
            "key_code_blocks": [],
            "key_tasks": [],
            "original_message_count": len(messages),
            "original_token_count": original_tokens,
            "summary_token_count": len(tokenizer.encode(summary_text))
            if tokenizer
            else len(summary_text) // 4,
        }
