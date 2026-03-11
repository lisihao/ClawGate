"""ContextPilot integration for ClawGate.

Wraps ContextPilot's reorder/deduplicate APIs to work with OpenAI-format
messages. Provides KV-cache-aware context optimization that complements
ClawGate's existing ContextEngine (compression/summarization).

Pipeline position:
    ContextEngine.auto_fit() → ContextPilotOptimizer.optimize() → engine dispatch

ContextEngine reduces *what* to send (compression, summarization).
ContextPilot optimizes *how* to send it (reorder for KV cache reuse).
"""

import logging
import os
import sys
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("clawgate.context.pilot")

# ---------------------------------------------------------------------------
# Lazy import of vendor ContextPilot
# ---------------------------------------------------------------------------

_cp_available = None
_ContextPilot = None


def _ensure_contextpilot():
    """Lazily import ContextPilot from vendor directory."""
    global _cp_available, _ContextPilot
    if _cp_available is not None:
        return _cp_available

    try:
        # Add vendor path if not already present
        vendor_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "vendor", "contextpilot",
        )
        if vendor_path not in sys.path:
            sys.path.insert(0, vendor_path)

        from contextpilot.server.live_index import ContextPilot
        _ContextPilot = ContextPilot
        _cp_available = True
        logger.info("[ContextPilot] 库加载成功 (v0.3.5)")
    except ImportError as e:
        _cp_available = False
        logger.warning(f"[ContextPilot] 库不可用: {e}，跳过上下文重排优化")

    return _cp_available


class ContextPilotOptimizer:
    """Optimizes OpenAI-format messages for KV cache reuse.

    Extracts context blocks from messages, runs ContextPilot's reorder
    algorithm, and reconstructs messages with optimized ordering.

    For multi-turn conversations, uses conversation_id-based deduplication
    to skip re-sending context the model already processed.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled and _ensure_contextpilot()
        self._pilot = None
        self._stats = {
            "total_calls": 0,
            "total_reordered": 0,
            "total_deduplicated": 0,
            "total_skipped": 0,
        }

    @property
    def pilot(self):
        """Lazy-init ContextPilot instance."""
        if self._pilot is None and self.enabled:
            self._pilot = _ContextPilot(use_gpu=False)
        return self._pilot

    def optimize(
        self,
        messages: List[Dict],
        *,
        conversation_id: Optional[str] = None,
        min_context_blocks: int = 2,
    ) -> Tuple[List[Dict], Dict]:
        """Optimize messages for KV cache reuse.

        Extracts context blocks (system prompt segments, prior turns) from
        the messages, reorders them via ContextPilot for maximum prefix
        sharing, then reconstructs the message list.

        Args:
            messages: OpenAI-format messages list.
            conversation_id: Session/conversation ID for cross-turn dedup.
            min_context_blocks: Minimum context blocks to trigger optimization.
                With fewer blocks, reordering has no benefit.

        Returns:
            (optimized_messages, metadata) where metadata contains stats
            about what was optimized.
        """
        self._stats["total_calls"] += 1

        if not self.enabled or not messages:
            self._stats["total_skipped"] += 1
            return messages, {"optimized": False, "reason": "disabled_or_empty"}

        # Extract context structure from messages
        system_msg, context_blocks, query, other_messages = self._extract_context(messages)

        # Need at least min_context_blocks to make reordering worthwhile
        if len(context_blocks) < min_context_blocks:
            self._stats["total_skipped"] += 1
            return messages, {
                "optimized": False,
                "reason": f"too_few_blocks ({len(context_blocks)})",
            }

        try:
            # Reorder context blocks for KV cache prefix sharing
            reordered, indices = self.pilot.reorder(
                context_blocks,
                conversation_id=conversation_id,
            )
            reordered_blocks = reordered[0]  # single context → first element

            # Build importance ranking annotation
            pos = {block: i + 1 for i, block in enumerate(reordered_blocks)}
            importance = " > ".join(
                str(pos[b]) for b in context_blocks if b in pos
            )

            # Reconstruct messages with reordered context
            optimized = self._reconstruct_messages(
                system_msg, reordered_blocks, importance, query, other_messages,
            )

            self._stats["total_reordered"] += 1
            logger.info(
                f"[ContextPilot] 重排 {len(context_blocks)} 个上下文块 | "
                f"conv={conversation_id or 'none'}"
            )

            return optimized, {
                "optimized": True,
                "method": "reorder",
                "blocks": len(context_blocks),
                "conversation_id": conversation_id,
            }

        except Exception as e:
            logger.warning(f"[ContextPilot] 优化失败，使用原始消息: {e}")
            self._stats["total_skipped"] += 1
            return messages, {"optimized": False, "reason": f"error: {e}"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_context(
        self, messages: List[Dict]
    ) -> Tuple[Optional[Dict], List[str], str, List[Dict]]:
        """Extract context structure from OpenAI messages.

        Splits messages into:
        - system_msg: The system message (if any)
        - context_blocks: Extractable context chunks (from system + prior turns)
        - query: The last user message
        - other_messages: Messages that shouldn't be reordered (assistant replies etc.)

        Returns:
            (system_msg, context_blocks, query, other_messages)
        """
        system_msg = None
        context_blocks = []
        query = ""
        other_messages = []

        # Find last user message (the query)
        last_user_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                query = messages[i].get("content", "")
                break

        for i, msg in enumerate(messages):
            role = msg.get("role", "")
            content = msg.get("content", "") or ""

            if role == "system":
                system_msg = msg
                # Split system message into blocks if it contains sections
                blocks = self._split_system_into_blocks(content)
                context_blocks.extend(blocks)

            elif role == "user" and i == last_user_idx:
                # This is the query, skip for now
                continue

            elif role in ("user", "assistant") and i < last_user_idx:
                # Prior conversation turns → treat as context blocks
                prefix = "User: " if role == "user" else "Assistant: "
                context_blocks.append(prefix + content)

            else:
                other_messages.append(msg)

        return system_msg, context_blocks, query, other_messages

    def _split_system_into_blocks(self, content: str) -> List[str]:
        """Split a system message into context blocks.

        Detects common separators (double newline, XML-like tags, numbered
        sections) to identify individual context chunks within a system prompt.
        """
        if not content or len(content) < 100:
            return [content] if content else []

        # Try XML-like document tags: <doc>, <document>, <context>, etc.
        import re
        xml_pattern = re.compile(
            r'<(?:doc|document|context|passage|chunk|source)\b[^>]*>(.*?)</(?:doc|document|context|passage|chunk|source)>',
            re.DOTALL | re.IGNORECASE,
        )
        xml_matches = xml_pattern.findall(content)
        if len(xml_matches) >= 2:
            return [m.strip() for m in xml_matches if m.strip()]

        # Try numbered sections: [1], [2], ... or 1., 2., ...
        numbered_pattern = re.compile(r'\n\s*(?:\[\d+\]|\d+\.)\s+')
        sections = numbered_pattern.split(content)
        if len(sections) >= 3:
            return [s.strip() for s in sections if s.strip()]

        # Try double-newline separation
        paragraphs = content.split("\n\n")
        if len(paragraphs) >= 3:
            return [p.strip() for p in paragraphs if p.strip()]

        # No good split found — return as single block
        return [content]

    def _reconstruct_messages(
        self,
        system_msg: Optional[Dict],
        reordered_blocks: List[str],
        importance: str,
        query: str,
        other_messages: List[Dict],
    ) -> List[Dict]:
        """Reconstruct OpenAI messages with reordered context.

        Puts reordered context blocks into the system message, preserves
        importance ranking annotation, and keeps the query as the last
        user message.
        """
        result = []

        # Build system message with reordered context
        if system_msg:
            # Format reordered blocks with index numbers
            docs_section = "\n".join(
                f"[{i + 1}] {block}" for i, block in enumerate(reordered_blocks)
            )
            new_system = (
                f"{docs_section}\n\n"
                f"Read in importance order: {importance}"
            )
            result.append({"role": "system", "content": new_system})
        elif reordered_blocks:
            docs_section = "\n".join(
                f"[{i + 1}] {block}" for i, block in enumerate(reordered_blocks)
            )
            result.append({
                "role": "system",
                "content": f"{docs_section}\n\nRead in importance order: {importance}",
            })

        # Add any other messages (shouldn't normally exist)
        result.extend(other_messages)

        # Query is always last
        if query:
            result.append({"role": "user", "content": query})

        return result

    def get_stats(self) -> Dict:
        """Return optimization statistics."""
        return {
            "enabled": self.enabled,
            "contextpilot_available": bool(_cp_available),
            **self._stats,
        }
