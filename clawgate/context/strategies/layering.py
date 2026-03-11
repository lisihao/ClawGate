"""Three-tier Layering Strategy - 四层上下文分层压缩

从 ThunderLLAMA thunder_service.py 迁移的核心优化特性。

分层结构:
- Layer 1: Must-have (1536 tokens) - system/developer 消息
- Layer 2: Nice-to-have (768 tokens) - 最近 8 条摘要
- Layer 3: History-tail (512 tokens) - Context Shift 摘要或简单压缩
- Layer 4: Tail - 最后 N 轮原文（preserve_last_turns）

参考: ThunderLLAMA/tools/thunder-service/thunder_service.py L281-435
"""

import asyncio
from typing import List, Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class ThreeTierLayeringStrategy:
    """四层上下文分层策略"""

    def __init__(
        self,
        must_have_cap: int = 1536,
        nice_to_have_cap: int = 768,
        history_tail_cap: int = 512,
        preserve_last_turns: int = 6,
        context_shift_enabled: bool = False,
        context_shift_client=None,  # ContextShiftClient 实例
    ):
        """
        初始化分层策略

        Args:
            must_have_cap: Must-have 层 token 上限 (system/developer 消息)
            nice_to_have_cap: Nice-to-have 层 token 上限 (最近 8 条摘要)
            history_tail_cap: History-tail 层 token 上限 (历史摘要)
            preserve_last_turns: 保留最后 N 轮完整对话
            context_shift_enabled: 是否启用 Context Shift 摘要
            context_shift_client: ContextShiftClient 实例（来自 context_shift_client.py）
        """
        self.must_have_cap = must_have_cap
        self.nice_to_have_cap = nice_to_have_cap
        self.history_tail_cap = history_tail_cap
        self.preserve_last_turns = max(1, preserve_last_turns)
        self.context_shift_enabled = context_shift_enabled
        self.context_shift_client = context_shift_client

        # 最后一次压缩的统计信息（供调用者访问）
        self.last_stats: Dict = {}

        logger.info(
            f"Layering 策略初始化: must={must_have_cap}, nice={nice_to_have_cap}, "
            f"history={history_tail_cap}, tail_turns={preserve_last_turns}, "
            f"context_shift={'enabled' if context_shift_enabled else 'disabled'}"
        )

    def compress(
        self,
        messages: List[Dict],
        target_tokens: int,
        agent_type: Optional[str] = None,
        tokenizer=None,
    ) -> List[Dict]:
        """
        四层分层压缩

        Args:
            messages: 消息列表
            target_tokens: 目标 token 数（未使用，分层策略有固定预算）
            agent_type: Agent 类型（未使用）
            tokenizer: Tokenizer

        Returns:
            压缩后的消息列表
        """
        if not messages:
            self.last_stats = {}
            return []

        # 1. 分离 system/developer 消息和对话消息
        sys_dev = [
            msg
            for msg in messages
            if msg.get("role") in ("system", "developer")
        ]
        convo = [
            msg
            for msg in messages
            if msg.get("role") not in ("system", "developer")
        ]

        # 2. 提取最后 N 轮（Tail Layer）
        preserve_n = self.preserve_last_turns
        tail = convo[-preserve_n:] if len(convo) >= preserve_n else convo
        middle = convo[:-preserve_n] if len(convo) > preserve_n else []

        # 3. Layer 1: Must-have (system/developer 消息)
        must_text, must_tokens = self._build_must_have(sys_dev, tokenizer)

        # 4. Layer 2: Nice-to-have (最近 8 条摘要)
        nice_text, nice_tokens = self._build_nice_to_have(middle, tokenizer)

        # 5. Layer 3: History-tail (Context Shift 摘要 or 简单压缩)
        hist_text, hist_tokens = self._build_history_tail(middle, tokenizer)

        # 6. Layer 4: Tail (最后 N 轮原文)
        tail_messages, tail_tokens = self._build_tail(tail, tokenizer)

        # 7. 组装最终消息列表
        out = []

        # Must-have layer (如果有内容)
        if must_text:
            out.append({
                "role": "system",
                "content": f"Condensed instruction context:\n{must_text}"
            })

        # Nice-to-have layer (如果有内容)
        if nice_text:
            out.append({
                "role": "system",
                "content": f"Condensed recent context:\n{nice_text}"
            })

        # History-tail layer (如果有内容)
        if hist_text:
            out.append({
                "role": "system",
                "content": f"Conversation memory tail:\n{hist_text}"
            })

        # Tail layer (最后 N 轮原文)
        out.extend(tail_messages)

        # 如果最终为空，返回一个占位符
        if not out:
            out = [{"role": "user", "content": "Please continue based on latest user intent."}]

        # 8. 记录统计信息
        total_tokens = must_tokens + nice_tokens + hist_tokens + tail_tokens
        self.last_stats = {
            "must_have_tokens": must_tokens,
            "nice_to_have_tokens": nice_tokens,
            "history_tail_tokens": hist_tokens,
            "tail_tokens": tail_tokens,
            "total_output_tokens": total_tokens,
            "layers": {
                "must_have": len(must_text) > 0,
                "nice_to_have": len(nice_text) > 0,
                "history_tail": len(hist_text) > 0,
                "tail": len(tail_messages),
            },
        }

        return out

    def _build_must_have(
        self, sys_dev: List[Dict], tokenizer
    ) -> Tuple[str, int]:
        """构建 Must-have 层（system/developer 消息）"""
        if not sys_dev:
            return "", 0

        chunks = []
        for msg in sys_dev:
            role = msg.get("role", "system")
            content = self._flatten_content(msg.get("content", "")).strip()
            if content:
                chunks.append(f"[{role}]\n{content}")

        must_text = "\n\n".join(chunks)
        must_text = self._trim_text_tokens(must_text, self.must_have_cap, tokenizer)
        must_tokens = self._estimate_tokens(must_text, tokenizer)

        return must_text, must_tokens

    def _build_nice_to_have(
        self, middle: List[Dict], tokenizer
    ) -> Tuple[str, int]:
        """构建 Nice-to-have 层（最近 8 条摘要）"""
        if not middle:
            return "", 0

        # 取最近 8 条消息
        recent_8 = middle[-8:]

        chunks = []
        for msg in recent_8:
            role = msg.get("role", "user")
            content = self._flatten_content(msg.get("content", "")).strip()
            if content:
                # 每条消息截断到 240 字符
                chunks.append(f"[{role}] {content[:240]}")

        nice_text = "\n".join(chunks)
        nice_text = self._trim_text_tokens(nice_text, self.nice_to_have_cap, tokenizer)
        nice_tokens = self._estimate_tokens(nice_text, tokenizer)

        return nice_text, nice_tokens

    def _build_history_tail(
        self, middle: List[Dict], tokenizer
    ) -> Tuple[str, int]:
        """构建 History-tail 层（Context Shift 摘要 or 简单压缩）"""
        if not middle:
            return "", 0

        hist_text = ""
        context_shift_used = False

        # Context Shift 两阶段 LLM 摘要
        if self.context_shift_enabled and self.context_shift_client:
            try:
                # 调用异步 Context Shift 客户端（在同步方法中调用）
                logger.debug(f"调用 Context Shift 摘要（{len(middle)} 条消息）...")

                # 使用 asyncio.run() 在同步上下文中调用异步方法
                summary = asyncio.run(
                    self.context_shift_client.summarize(
                        messages=middle,
                        target_tokens=self.history_tail_cap
                    )
                )

                if summary:
                    hist_text = summary
                    context_shift_used = True
                    logger.info(
                        f"Context Shift 摘要成功: {len(middle)} 条 → "
                        f"{len(summary)} 字符"
                    )
                else:
                    logger.warning(
                        "Context Shift 返回 None（Circuit Breaker 或服务不可用），"
                        "降级到简单压缩"
                    )

            except Exception as e:
                logger.warning(
                    f"Context Shift 摘要失败，fallback 到简单压缩: {e}",
                    exc_info=True
                )

        # Fallback: 简单字符截断（兼容原逻辑）
        if not hist_text:
            hist_text = self._simple_compact_history(middle, max_lines=12)
            logger.debug(
                f"使用简单压缩: {len(middle)} 条 → {len(hist_text)} 字符"
            )

        hist_text = self._trim_text_tokens(hist_text, self.history_tail_cap, tokenizer)
        hist_tokens = self._estimate_tokens(hist_text, tokenizer)

        # 记录统计信息（供外部访问）
        if not hasattr(self, "_context_shift_stats"):
            self._context_shift_stats = {"used": 0, "fallback": 0}

        if context_shift_used:
            self._context_shift_stats["used"] += 1
        else:
            self._context_shift_stats["fallback"] += 1

        return hist_text, hist_tokens

    def _build_tail(
        self, tail: List[Dict], tokenizer
    ) -> Tuple[List[Dict], int]:
        """构建 Tail 层（最后 N 轮原文）"""
        if not tail:
            return [], 0

        # 计算每条消息的 token 预算
        tail_budget = max(256, self.must_have_cap // 2)
        per_msg_cap = max(64, tail_budget // max(1, len(tail)))

        tail_messages = []
        tail_trimmed = 0

        for msg in tail:
            role = msg.get("role", "user")
            if role not in ("user", "assistant", "tool", "system"):
                role = "user"

            content = self._flatten_content(msg.get("content", "")).strip()
            if not content:
                continue

            # 截断到预算内
            trimmed_content = self._trim_text_tokens(content, per_msg_cap, tokenizer)
            tail_trimmed += self._estimate_tokens(trimmed_content, tokenizer)

            tail_messages.append({"role": role, "content": trimmed_content})

        return tail_messages, tail_trimmed

    # ------------------------------------------------------------------
    # 辅助函数（从 thunder_service.py 迁移）
    # ------------------------------------------------------------------

    def _flatten_content(self, content) -> str:
        """展平内容（处理字符串或列表）"""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict):
                    txt = c.get("text")
                    if isinstance(txt, str):
                        parts.append(txt)
                elif isinstance(c, str):
                    parts.append(c)
            return "\n".join(parts)
        return ""

    def _estimate_tokens(self, text: str, tokenizer) -> int:
        """估算 token 数"""
        if tokenizer:
            try:
                return len(tokenizer.encode(text))
            except Exception:
                pass
        # Fallback: 4 字符 ≈ 1 token
        return max(1, len(text) // 4)

    def _trim_text_tokens(self, text: str, max_tokens: int, tokenizer) -> str:
        """截断文本到指定 token 数"""
        if max_tokens <= 0:
            return ""

        current_tokens = self._estimate_tokens(text, tokenizer)
        if current_tokens <= max_tokens:
            return text

        # 简单截断：按字符数估算
        max_chars = max_tokens * 4
        out = text[:max_chars]

        # 尝试在换行或空格处截断
        cut = max(out.rfind("\n"), out.rfind(" "))
        if cut > int(max_chars * 0.7):
            out = out[:cut]

        return out.rstrip() + "\n[truncated]"

    def _simple_compact_history(self, messages: List[Dict], max_lines: int = 12) -> str:
        """简单压缩历史（字符截断，与 thunder_service.py 兼容）"""
        lines = []
        for msg in messages[-24:]:  # 最后 24 条
            role = msg.get("role", "user")
            content = self._flatten_content(msg.get("content", "")).strip()
            if not content:
                continue

            # 压缩空白符
            content = " ".join(content.split())
            lines.append(f"- {role}: {content[:140]}")

        if not lines:
            return ""

        return "History tail summary:\n" + "\n".join(lines[-max_lines:])

    def get_context_shift_stats(self) -> Dict:
        """获取 Context Shift 使用统计"""
        if not hasattr(self, "_context_shift_stats"):
            return {"used": 0, "fallback": 0}

        stats = self._context_shift_stats.copy()
        total = stats["used"] + stats["fallback"]
        if total > 0:
            stats["usage_rate"] = stats["used"] / total
        else:
            stats["usage_rate"] = 0.0

        return stats
