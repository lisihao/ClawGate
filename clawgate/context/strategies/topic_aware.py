"""Topic-Aware Compression Strategy - 话题感知的差异化压缩

根据 TopicSegmenter 的分段结果，对工作段和闲聊段应用不同的压缩策略:
- 最近工作段: 完整保留
- 较早工作段: 选择性保留关键消息
- 闲聊段: 丢弃或一句话概括
"""

import logging
from typing import List, Dict, Optional

from ..topic_segmenter import TopicSegmenter

logger = logging.getLogger("clawgate.context.topic_aware")


class TopicAwareStrategy:
    """话题感知压缩策略"""

    def __init__(self):
        self.segmenter = TopicSegmenter()

    def compress(
        self,
        messages: List[Dict],
        target_tokens: int,
        agent_type: Optional[str] = None,
        tokenizer=None,
    ) -> List[Dict]:
        """
        话题感知压缩

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

        # 计算当前 token 数
        current_tokens = self._count_tokens(messages, tokenizer)
        if current_tokens <= target_tokens:
            return messages

        logger.info(
            f"[话题压缩] 开始 | 消息={len(messages)} tokens={current_tokens} → 目标={target_tokens}"
        )

        # 1. 分段
        segments = self.segmenter.segment(messages)

        # 2. 生成压缩计划
        plan = self.segmenter.get_compression_plan(segments, len(messages))

        # 3. 执行压缩计划
        compressed = []
        for item in plan:
            seg = item["segment"]
            action = item["action"]

            if action == "keep_full":
                # 完整保留
                compressed.extend(seg.messages)
                logger.debug(
                    f"  [{seg.start}:{seg.end}] {seg.topic_type} → 完整保留 ({seg.length}条)"
                )

            elif action == "keep_selective":
                # 选择性保留: 保留 system + 含代码/错误/决策的消息 + 最近几条
                kept = self._selective_keep(seg.messages, item["keep_ratio"], tokenizer)
                compressed.extend(kept)
                logger.debug(
                    f"  [{seg.start}:{seg.end}] {seg.topic_type} → 选择性保留 ({len(kept)}/{seg.length}条)"
                )

            elif action == "summarize":
                # 压缩为一句话摘要
                summary = self._generate_segment_summary(seg)
                if summary:
                    compressed.append({
                        "role": "system",
                        "content": f"[上下文摘要] {summary}",
                    })
                logger.debug(
                    f"  [{seg.start}:{seg.end}] {seg.topic_type} → 摘要 ({seg.length}条→1句)"
                )

            elif action == "summarize_one_line":
                # 闲聊段: 用一句话概括（可选）
                logger.debug(
                    f"  [{seg.start}:{seg.end}] casual → 概括丢弃 ({seg.length}条)"
                )
                # 不添加任何内容，直接丢弃

            elif action == "drop":
                # 直接丢弃
                logger.debug(
                    f"  [{seg.start}:{seg.end}] casual → 丢弃 ({seg.length}条)"
                )

        # 4. 检查压缩后是否仍然超限
        compressed_tokens = self._count_tokens(compressed, tokenizer)
        if compressed_tokens > target_tokens:
            # 二次压缩: 对保留的工作段使用滑动窗口
            logger.warning(
                f"[话题压缩] 一次压缩后仍超限 ({compressed_tokens} > {target_tokens})，启用二次压缩"
            )
            compressed = self._fallback_sliding_window(
                compressed, target_tokens, tokenizer
            )

        final_tokens = self._count_tokens(compressed, tokenizer)
        logger.info(
            f"[话题压缩] 完成 | {len(messages)}→{len(compressed)}条 | "
            f"{current_tokens}→{final_tokens} tokens | "
            f"压缩率={final_tokens/current_tokens:.1%}"
        )

        return compressed

    def _selective_keep(
        self, messages: List[Dict], keep_ratio: float, tokenizer
    ) -> List[Dict]:
        """选择性保留消息"""
        if not messages:
            return []

        kept = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "user")
            content = str(msg.get("content", ""))

            # 始终保留 system
            if role == "system":
                kept.append(msg)
                continue

            # 始终保留含代码、错误、决策的消息
            if any(
                kw in content.lower()
                for kw in [
                    "```", "error", "exception", "traceback",
                    "决定", "选择", "方案", "结论",
                    "def ", "class ", "function ",
                ]
            ):
                kept.append(msg)
                continue

            # 保留最后 30% 的消息
            if i >= len(messages) * (1 - keep_ratio * 0.5):
                kept.append(msg)
                continue

        # 至少保留最后 2 条
        if len(kept) < 2 and len(messages) >= 2:
            kept = messages[-2:]

        return kept

    def _generate_segment_summary(self, segment) -> Optional[str]:
        """生成段落摘要 (基于规则的简单版本，未来对接 LLM)"""
        if not segment.messages:
            return None

        # 提取关键信息
        topics = set()
        for msg in segment.messages:
            content = str(msg.get("content", ""))
            # 提取文件名
            import re
            files = re.findall(r"[\w\-]+\.(py|ts|js|go|rs|java|yaml|json|md|sh)\b", content)
            for f in files:
                topics.add(f".{f}")

            # 提取关键动词
            for kw in ["实现", "修复", "优化", "设计", "分析", "测试", "部署", "讨论"]:
                if kw in content:
                    topics.add(kw)

        if topics:
            return f"早期讨论内容涉及: {', '.join(list(topics)[:8])}"
        else:
            return f"早期对话 ({segment.length} 条消息)"

    def _fallback_sliding_window(
        self, messages: List[Dict], target_tokens: int, tokenizer
    ) -> List[Dict]:
        """二次压缩: 滑动窗口"""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        content_msgs = [m for m in messages if m.get("role") != "system"]

        # 计算 system 消息占用
        sys_tokens = self._count_tokens(system_msgs, tokenizer)
        remaining = target_tokens - sys_tokens

        # 从后往前保留
        kept = []
        used = 0
        for msg in reversed(content_msgs):
            msg_tokens = self._count_tokens([msg], tokenizer)
            if used + msg_tokens > remaining:
                break
            kept.insert(0, msg)
            used += msg_tokens

        return system_msgs + kept

    def _count_tokens(self, messages: List[Dict], tokenizer) -> int:
        """计算 token 数"""
        total = 0
        for msg in messages:
            content = str(msg.get("content", ""))
            if tokenizer:
                total += len(tokenizer.encode(content))
            else:
                total += len(content) // 4  # 粗略估算
        return total
