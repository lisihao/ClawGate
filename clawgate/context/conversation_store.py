"""ConversationStore - 持久化会话记忆 (24h TTL)

先存后压：在压缩之前将分段存入 SQLite，
按当前对话模式（work/casual）智能重组上下文。

v2: 结构化摘要 + 抗污染
  - User 消息 → 提取意图、操作、文件（可信主干）
  - Assistant 消息 → 仅提取代码块、工具输出、状态词（可验证产物）
  - 基于 MIT "Do LLMs Benefit From Their Own Words?" 论文
    避免 assistant 幻觉在摘要中传播

流程:
  请求进来 → derive_conversation_id → segment → store_segments → reconstruct_context
"""

import hashlib
import json
import logging
import re
import sqlite3 as _sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple, Set, Callable

from ..storage.sqlite_store import SQLiteStore
from .topic_segmenter import TopicSegmenter, TopicSegment

logger = logging.getLogger("clawgate.context.convstore")

# 段过期时间
SEGMENT_TTL_HOURS = 24


class ConversationStore:
    """会话段持久化存储，支持按模式智能重组上下文"""

    # 跨会话记忆 TTL (天)
    LTM_TTL_DAYS = 7

    # Background thread pool for async LLM summaries (F3)
    _summary_executor = ThreadPoolExecutor(max_workers=2)

    def __init__(
        self,
        db_store: SQLiteStore,
        topic_segmenter: TopicSegmenter,
        llm_summarizer: Optional[Callable[[str], str]] = None,
    ):
        self.db_store = db_store
        self.segmenter = topic_segmenter
        self.llm_summarizer = llm_summarizer

    # ========== 会话 ID 推导 ==========

    def derive_conversation_id(self, messages: List[Dict]) -> str:
        """从消息列表自动推导会话 ID

        使用 system_prompt 前 500 字符 + 第一条 user 消息前 200 字符的 hash。
        同一个 OpenClaw 会话每次传入的 system_prompt 和首条 user 消息相同，
        所以能稳定推导出同一个 conversation_id。
        """
        system_part = ""
        first_user_part = ""

        for msg in messages:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))
            if role == "system" and not system_part:
                system_part = content[:500]
            elif role == "user" and not first_user_part:
                first_user_part = content[:200]
            if system_part and first_user_part:
                break

        raw = f"{system_part}||{first_user_part}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ========== 段存储 ==========

    def store_segments(
        self, conversation_id: str, segments: List[TopicSegment]
    ) -> int:
        """将分段存入 SQLite (INSERT OR REPLACE)

        每次 OpenClaw 传全量消息，所以每次重新分段后覆盖存储。
        存储前先清理过期段。

        Returns:
            存储的段数
        """
        # 先清过期
        self.cleanup_expired()

        expires_at = (
            datetime.utcnow() + timedelta(hours=SEGMENT_TTL_HOURS)
        ).strftime("%Y-%m-%d %H:%M:%S")

        db_path = self.db_store.db_path / "context.db"
        conn = _sqlite3.connect(db_path)
        cursor = conn.cursor()

        stored = 0
        for idx, seg in enumerate(segments):
            summary = self._generate_segment_summary(seg)
            messages_json = json.dumps(seg.messages, ensure_ascii=False)

            cursor.execute(
                """
                INSERT OR REPLACE INTO conversation_segments
                    (conversation_id, segment_index, topic_type, summary,
                     messages, message_count, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)
                """,
                (
                    conversation_id,
                    idx,
                    seg.topic_type,
                    summary,
                    messages_json,
                    seg.length,
                    expires_at,
                ),
            )
            stored += 1

        # 删除该会话中索引 >= len(segments) 的旧段（本次分段数减少时清理）
        cursor.execute(
            "DELETE FROM conversation_segments WHERE conversation_id = ? AND segment_index >= ?",
            (conversation_id, len(segments)),
        )

        conn.commit()
        conn.close()

        # P4: 将 work 段提升到 long_term_memories
        for seg in segments:
            if seg.topic_type == "work":
                summary = self._generate_segment_summary(seg)
                self._promote_to_long_term(
                    segment=seg,
                    summary=summary,
                    conversation_id=conversation_id,
                )

        # F3: 后台异步 LLM 摘要（fire-and-forget）
        # 规则摘要已存入 DB，LLM 摘要在后台更新，下次 reconstruct 时用上
        if self.llm_summarizer:
            for idx, seg in enumerate(segments):
                if seg.topic_type == "work" and seg.length > 5:
                    self._summary_executor.submit(
                        self._background_llm_summary,
                        conversation_id,
                        idx,
                        seg,
                    )

        logger.info(
            f"[ConvStore] 存储 conv={conversation_id[:8]}… | "
            f"{stored} 段 | TTL={SEGMENT_TTL_HOURS}h"
        )
        return stored

    # ========== 段查询 ==========

    def get_segments(
        self, conversation_id: str, topic_type: Optional[str] = None
    ) -> List[Dict]:
        """查询会话的已存段，过滤已过期的

        Returns:
            [{conversation_id, segment_index, topic_type, summary,
              messages (parsed), message_count, created_at, expires_at}]
        """
        db_path = self.db_store.db_path / "context.db"
        conn = _sqlite3.connect(db_path)
        conn.row_factory = _sqlite3.Row
        cursor = conn.cursor()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        if topic_type:
            cursor.execute(
                """
                SELECT * FROM conversation_segments
                WHERE conversation_id = ? AND topic_type = ? AND expires_at > ?
                ORDER BY segment_index ASC
                """,
                (conversation_id, topic_type, now),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM conversation_segments
                WHERE conversation_id = ? AND expires_at > ?
                ORDER BY segment_index ASC
                """,
                (conversation_id, now),
            )

        rows = [dict(row) for row in cursor.fetchall()]
        conn.close()

        # 解析 messages JSON
        for row in rows:
            row["messages"] = json.loads(row["messages"])

        return rows

    # ========== 智能重组 ==========

    def reconstruct_context(
        self,
        conversation_id: str,
        messages: List[Dict],
        mode: str,
        target_tokens: int,
        tokenizer=None,
        model_tier: str = "medium",
    ) -> Tuple[List[Dict], Dict]:
        """按模式从 Store 智能重组上下文

        Args:
            conversation_id: 会话 ID
            messages: 当前完整消息列表（用于取尾部）
            mode: "work" 或 "casual"
            target_tokens: 目标 token 数
            tokenizer: tiktoken tokenizer
            model_tier: 模型能力分级 ("weak"/"medium"/"strong")

        Returns:
            (重组后的消息列表, 元数据)
        """
        stored_segments = self.get_segments(conversation_id)

        if not stored_segments:
            # 没有存储段（可能是新会话）→ 尝试跨会话记忆召回
            tail_result = self._tail_fit(messages, target_tokens, tokenizer, model_tier)
            ltm_msgs = self._recall_long_term(messages)
            meta = {
                "strategy": "conv_store_tail",
                "stored_segments": 0,
                "ltm_recall": len(ltm_msgs),
            }
            if ltm_msgs:
                # 将 LTM 消息注入到 system 消息之后
                system_msgs = [m for m in tail_result if m.get("role") == "system"]
                non_system = [m for m in tail_result if m.get("role") != "system"]
                tail_result = system_msgs + ltm_msgs + non_system
                logger.info(
                    f"[ConvStore] 跨会话记忆召回: {len(ltm_msgs)} 条记忆注入"
                )
            return tail_result, meta

        if mode == "work":
            result = self._reconstruct_work(
                stored_segments, messages, target_tokens, tokenizer, model_tier
            )
        else:
            result = self._reconstruct_casual(
                stored_segments, messages, target_tokens, tokenizer, model_tier
            )

        final_tokens = self._count_tokens(result, tokenizer)
        logger.info(
            f"[ConvStore] 重组 conv={conversation_id[:8]}… | mode={mode} | "
            f"段={len(stored_segments)} | 结果={len(result)}条 {final_tokens}tokens"
        )

        return result, {
            "strategy": "conv_store_reconstruct",
            "mode": mode,
            "model_tier": model_tier,
            "stored_segments": len(stored_segments),
            "result_tokens": final_tokens,
        }

    def _reconstruct_work(
        self,
        stored_segments: List[Dict],
        messages: List[Dict],
        target_tokens: int,
        tokenizer,
        model_tier: str = "medium",
    ) -> List[Dict]:
        """工作模式重组: 旧工作摘要 + 最近工作完整 + 最新尾部

        预算分配:
          - 40% 尾部（最新消息，完整保留）
          - 60% 历史工作段（摘要优先，近段完整）
        """
        result = []

        # 1. 保留 system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        result.extend(system_msgs)
        sys_tokens = self._count_tokens(system_msgs, tokenizer)

        available = target_tokens - sys_tokens
        tail_budget = int(available * 0.4)
        history_budget = available - tail_budget

        # 2. 历史工作段（从存储中获取）
        work_segments = [s for s in stored_segments if s["topic_type"] == "work"]
        history_msgs = []

        if work_segments:
            # 最后一个工作段：尽量完整保留
            last_work = work_segments[-1]
            earlier_work = work_segments[:-1]

            # 早期工作段：用摘要
            for seg in earlier_work:
                if seg.get("summary"):
                    history_msgs.append({
                        "role": "system",
                        "content": f"[历史工作摘要] {seg['summary']}",
                    })

            # 最近工作段：完整消息
            last_work_msgs = last_work.get("messages", [])
            last_work_tokens = self._count_tokens(last_work_msgs, tokenizer)
            summary_tokens = self._count_tokens(history_msgs, tokenizer)

            if summary_tokens + last_work_tokens <= history_budget:
                history_msgs.extend(last_work_msgs)
            else:
                # 预算不够，只放摘要
                if last_work.get("summary"):
                    history_msgs.append({
                        "role": "system",
                        "content": f"[最近工作摘要] {last_work['summary']}",
                    })

        # 裁剪历史到预算内
        history_msgs = self._fit_to_budget(history_msgs, history_budget, tokenizer)
        result.extend(history_msgs)

        # 3. 尾部（最新消息）
        non_system = [m for m in messages if m.get("role") != "system"]
        tail = self._tail_messages(non_system, tail_budget, tokenizer, model_tier)
        result.extend(tail)

        return result

    def _reconstruct_casual(
        self,
        stored_segments: List[Dict],
        messages: List[Dict],
        target_tokens: int,
        tokenizer,
        model_tier: str = "medium",
    ) -> List[Dict]:
        """闲聊模式重组: 工作一句话概括 + 最近闲聊"""
        result = []

        # 1. system 消息
        system_msgs = [m for m in messages if m.get("role") == "system"]
        result.extend(system_msgs)
        sys_tokens = self._count_tokens(system_msgs, tokenizer)

        available = target_tokens - sys_tokens

        # 2. 工作段一句话概括
        work_segments = [s for s in stored_segments if s["topic_type"] == "work"]
        if work_segments:
            topics = []
            for seg in work_segments:
                if seg.get("summary"):
                    topics.append(seg["summary"])
            if topics:
                combined = "; ".join(topics[:3])
                result.append({
                    "role": "system",
                    "content": f"[工作上下文概要] {combined}",
                })

        summary_tokens = self._count_tokens(result, tokenizer) - sys_tokens
        remaining = available - summary_tokens

        # 3. 最近闲聊（从尾部取）
        non_system = [m for m in messages if m.get("role") != "system"]
        tail = self._tail_messages(non_system, remaining, tokenizer, model_tier)
        result.extend(tail)

        return result

    # ========== 过期清理 ==========

    def cleanup_expired(self) -> int:
        """清理过期段

        Returns:
            删除的行数
        """
        db_path = self.db_store.db_path / "context.db"
        conn = _sqlite3.connect(db_path)
        cursor = conn.cursor()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "DELETE FROM conversation_segments WHERE expires_at < ?", (now,)
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        if deleted > 0:
            logger.info(f"[ConvStore] 清理过期段: {deleted} 条")

        return deleted

    # ========== 结构化摘要生成 ==========

    # 操作动词归一化映射
    _ACTION_SYNONYMS = {
        "实现": ["实现", "开发", "编写", "新增", "添加", "新建", "implement", "create", "add"],
        "修复": ["修复", "解决", "修正", "fix", "debug", "resolve"],
        "优化": ["优化", "重构", "提升", "加速", "改进", "refactor", "optimize", "improve"],
        "测试": ["测试", "验证", "确保", "test", "verify", "validate"],
        "分析": ["分析", "设计", "研究", "调研", "讨论", "analyze", "design", "research"],
        "配置": ["配置", "部署", "设置", "config", "deploy", "setup"],
        "删除": ["删除", "移除", "清理", "delete", "remove", "clean"],
    }

    # 意图引导词
    _INTENT_PATTERN = re.compile(
        r"(我[想需要得]|请帮我|请你|目标是|如何|怎样|怎么|为什么|"
        r"帮我|麻烦|能不能|可以.{0,2}吗|implement|fix|add|create|how to|please)",
        re.IGNORECASE,
    )

    # 状态关键词
    _STATUS_DONE = re.compile(
        r"(完成|搞定|已修复|已实现|成功|通过|ok|done|solved|fixed|passed)",
        re.IGNORECASE,
    )
    _STATUS_BLOCKED = re.compile(
        r"(失败|错误|问题|无法|阻塞|不行|报错|failed|error|blocked|broken)",
        re.IGNORECASE,
    )

    # 文件路径提取
    _FILE_PATTERN = re.compile(
        r"([\w\-\./]+\.(py|ts|js|go|rs|java|yaml|json|md|sh|sql|toml|cfg|proto))\b",
        re.IGNORECASE,
    )

    # 代码块提取（assistant 可验证产物）
    _CODE_BLOCK_PATTERN = re.compile(r"```[\w]*\n[\s\S]*?```")

    def _generate_segment_summary(self, segment: TopicSegment) -> str:
        """生成结构化段摘要（抗污染版，始终同步返回规则摘要）

        核心原则（基于 MIT context pollution 论文）:
        - User 消息 → 提取意图、操作、文件（可信主干）
        - Assistant 消息 → 仅提取可验证产物（代码块数、状态词）
        - 不传播 assistant 的解释、推理、分析（潜在幻觉）

        v4 (F3): 始终同步返回规则摘要，LLM 摘要在 store_segments 后异步执行。
                  这避免了 ~200ms 的同步阻塞。

        输出格式:
          Work: [WORK] 文件: a.py, b.ts | 操作: 实现, 测试
                意图: 用户要求实现 xxx
                产物: 2 代码块 | 状态: 完成

          Casual: [CASUAL] 话题: 项目进度 (3条消息)
                  要点: 用户确认下午测试
        """
        if not segment.messages:
            return f"空段 ({segment.topic_type})"

        if segment.topic_type == "work":
            return self._summarize_work_segment(segment)
        else:
            return self._summarize_casual_segment(segment)

    def _summarize_work_segment(self, segment: TopicSegment) -> str:
        """生成工作段的结构化摘要"""
        files: Set[str] = set()
        actions: Set[str] = set()
        user_intent = ""
        code_block_count = 0
        status = ""

        for msg in segment.messages:
            role = msg.get("role", "")
            content = str(msg.get("content", ""))

            # --- 所有消息: 提取文件名 ---
            for match in self._FILE_PATTERN.finditer(content):
                # 取文件名部分（去掉路径前缀中过长的部分）
                filepath = match.group(1)
                # 只保留文件名（最后一段）
                filename = filepath.split("/")[-1]
                files.add(filename)

            if role == "user":
                # --- User 消息: 提取意图和操作 ---
                if not user_intent and self._INTENT_PATTERN.search(content):
                    # 取第一行或前 80 字符作为意图
                    first_line = content.split("\n")[0].strip()
                    user_intent = first_line[:80]

                # 归一化操作动词
                for main_action, synonyms in self._ACTION_SYNONYMS.items():
                    for syn in synonyms:
                        if syn in content.lower() if syn.isascii() else syn in content:
                            actions.add(main_action)
                            break

            elif role == "assistant":
                # --- Assistant 消息: 仅提取可验证产物 ---
                # 1. 代码块数量（可验证）
                code_block_count += len(self._CODE_BLOCK_PATTERN.findall(content))

                # 2. 状态词（完成/失败，可验证）
                if not status:
                    if self._STATUS_DONE.search(content):
                        status = "完成"
                    elif self._STATUS_BLOCKED.search(content):
                        status = "阻塞"

                # 不提取 assistant 的解释、推理、分析（防污染）

        # --- 组装结构化摘要 ---
        parts = ["[WORK]"]

        # 第一行: 文件 + 操作
        line1_parts = []
        if files:
            line1_parts.append(f"文件: {', '.join(sorted(files)[:5])}")
        if actions:
            line1_parts.append(f"操作: {', '.join(sorted(actions))}")
        if line1_parts:
            parts.append(" | ".join(line1_parts))

        # 第二行: 意图（仅来自 user）
        if user_intent:
            parts.append(f"意图: {user_intent}")

        # 第三行: 产物 + 状态
        line3_parts = []
        if code_block_count > 0:
            line3_parts.append(f"{code_block_count}个代码块")
        if status:
            line3_parts.append(f"状态: {status}")
        if line3_parts:
            parts.append(" | ".join(line3_parts))

        # fallback: 如果什么都没提取到
        if len(parts) == 1:
            parts.append(f"工作段 ({segment.length}条消息)")

        return "\n".join(parts)

    def _llm_summarize_work_segment(self, segment: TopicSegment) -> str:
        """使用 LLM 生成长工作段摘要（>5 msgs）

        失败时 fallback 到规则摘要，保证不中断。
        """
        # 构造对话文本（截断过长内容）
        formatted_lines = []
        for msg in segment.messages:
            role = msg.get("role", "unknown")
            content = str(msg.get("content", ""))[:300]
            formatted_lines.append(f"[{role}]: {content}")
        formatted_messages = "\n".join(formatted_lines)

        prompt = (
            "你是上下文压缩专家。从以下对话中提取关键事实，严格遵守规则：\n\n"
            "规则：\n"
            "1. 只提取用户明确说的事实（意图、文件、操作、决策）\n"
            "2. 从助手回复中只提取：涉及的文件名、函数名、完成状态\n"
            "3. 不要推测、不要补充、不要解释原因\n"
            "4. 保持简洁，总共不超过 150 字\n\n"
            "输出格式：\n"
            "文件: [文件列表]\n"
            "操作: [实现/修复/优化/测试/分析/配置/删除]\n"
            "用户意图: [一句话]\n"
            "关键决策: [如有，没有则省略]\n"
            "状态: [完成/进行中/阻塞]\n\n"
            f"对话：\n{formatted_messages}"
        )

        try:
            llm_result = self.llm_summarizer(prompt)
            # 加 [WORK] 前缀保持与下游一致
            return f"[WORK]\n{llm_result.strip()}"
        except Exception as e:
            logger.warning(
                f"[ConvStore] LLM 摘要失败，降级到规则摘要: {e}"
            )
            return self._summarize_work_segment(segment)

    def _background_llm_summary(
        self, conversation_id: str, segment_index: int, segment: TopicSegment
    ):
        """Background task: generate LLM summary and UPDATE the DB row.

        Called via ThreadPoolExecutor.submit() after store_segments commits.
        If LLM fails, the rule-based summary remains intact.
        """
        try:
            llm_summary = self._llm_summarize_work_segment(segment)
            # Update the summary in SQLite
            db_path = self.db_store.db_path / "context.db"
            conn = _sqlite3.connect(db_path)
            conn.execute(
                "UPDATE conversation_segments SET summary = ? "
                "WHERE conversation_id = ? AND segment_index = ?",
                (llm_summary, conversation_id, segment_index),
            )
            conn.commit()
            conn.close()
            logger.debug(
                f"[ConvStore] Background LLM summary updated: "
                f"conv={conversation_id[:8]}… seg={segment_index}"
            )
        except Exception as e:
            # Rule-based summary already saved, LLM failure is non-critical
            logger.warning(
                f"[ConvStore] Background LLM summary failed: {e}"
            )

    def _summarize_casual_segment(self, segment: TopicSegment) -> str:
        """生成闲聊段的结构化摘要"""
        user_points: List[str] = []

        for msg in segment.messages:
            role = msg.get("role", "")
            content = str(msg.get("content", "")).strip()

            if role == "user" and content:
                # 只收集 user 的发言要点（前 60 字符）
                point = content.split("\n")[0][:60]
                if point and len(point) > 2:
                    user_points.append(point)

        parts = [f"[CASUAL] ({segment.length}条消息)"]

        if user_points:
            # 最多取 2 个要点
            for point in user_points[:2]:
                parts.append(f"- {point}")

        return "\n".join(parts)

    def _count_tokens(self, messages: List[Dict], tokenizer) -> int:
        """计算 token 数"""
        total = 0
        for msg in messages:
            content = str(msg.get("content", ""))
            if tokenizer:
                total += len(tokenizer.encode(content))
            else:
                total += len(content) // 4
        return total

    def _tail_fit(
        self, messages: List[Dict], target_tokens: int, tokenizer,
        model_tier: str = "medium",
    ) -> List[Dict]:
        """简单尾部截取"""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        sys_tokens = self._count_tokens(system_msgs, tokenizer)
        remaining = target_tokens - sys_tokens

        tail = self._tail_messages(non_system, remaining, tokenizer, model_tier)
        return system_msgs + tail

    def _strip_assistant_to_artifacts(self, content: str) -> str:
        """弱模型: 将 assistant 消息剥离到仅保留可验证产物（代码块）

        基于 MIT 论文: 弱模型更易受 context pollution 影响，
        assistant 的叙述性文字（解释/推理）是主要污染源。
        只保留代码块这类可验证的结构化输出。

        Returns:
            仅含代码块的内容，或空字符串（无代码块时整条丢弃）
        """
        code_blocks = self._CODE_BLOCK_PATTERN.findall(content)
        if code_blocks:
            return "\n\n".join(code_blocks)
        return ""

    def _tail_messages(
        self, messages: List[Dict], budget: int, tokenizer,
        model_tier: str = "medium",
    ) -> List[Dict]:
        """从尾部取消息，User-First 填充（抗污染）

        基于 MIT 论文发现：user 消息是上下文主干，assistant 消息是注解。
        1. 先填入尾部的 user 消息（主干，必须保留）
        2. 再将 assistant 消息插回对应位置（注解，预算内补充）
        3. 保持消息原始顺序

        model_tier 分级过滤:
        - weak:   assistant 消息仅保留代码块（激进过滤）
        - medium: 完整 assistant 消息（当前行为）
        - strong: 完整 assistant 消息（当前行为）
        """
        if not messages:
            return []

        # 从尾部扫描，确定候选范围
        # 先用简单尾部截取确定大致范围
        candidate_indices = []
        scan_budget = 0
        for i in range(len(messages) - 1, -1, -1):
            msg_tokens = self._count_tokens([messages[i]], tokenizer)
            # 扫描范围放宽到 budget 的 1.5 倍（因为后面会过滤 assistant）
            if scan_budget + msg_tokens > budget * 1.5:
                break
            candidate_indices.insert(0, i)
            scan_budget += msg_tokens

        if not candidate_indices:
            # 连一条都放不下，至少保留最后一条（messages 非空已由上方检查保证）
            return [messages[-1]]

        # 第一遍: 填入 user 消息
        user_filled = []
        used = 0
        for i in candidate_indices:
            msg = messages[i]
            if msg.get("role") == "user":
                msg_tokens = self._count_tokens([msg], tokenizer)
                if used + msg_tokens <= budget:
                    user_filled.append((i, msg))
                    used += msg_tokens

        # 第二遍: 补充 assistant 消息（保持顺序）
        all_filled = list(user_filled)  # copy
        for i in candidate_indices:
            msg = messages[i]
            if msg.get("role") != "user":
                actual_msg = msg
                if model_tier == "weak" and msg.get("role") == "assistant":
                    # 弱模型: assistant 消息仅保留代码块
                    stripped = self._strip_assistant_to_artifacts(
                        str(msg.get("content", ""))
                    )
                    if not stripped:
                        continue  # 无代码块 → 整条丢弃
                    actual_msg = {**msg, "content": stripped}
                msg_tokens = self._count_tokens([actual_msg], tokenizer)
                if used + msg_tokens <= budget:
                    all_filled.append((i, actual_msg))
                    used += msg_tokens

        # 按原始顺序排列
        all_filled.sort(key=lambda x: x[0])
        return [msg for _, msg in all_filled]

    def _fit_to_budget(
        self, messages: List[Dict], budget: int, tokenizer
    ) -> List[Dict]:
        """从头开始保留消息，不超过 budget tokens"""
        kept = []
        used = 0
        for msg in messages:
            msg_tokens = self._count_tokens([msg], tokenizer)
            if used + msg_tokens > budget:
                break
            kept.append(msg)
            used += msg_tokens
        return kept

    # ========== P4: 跨会话长期记忆 ==========

    def _extract_files_from_messages(self, messages: List[Dict]) -> Set[str]:
        """从消息列表中提取文件名"""
        files: Set[str] = set()
        for msg in messages:
            content = str(msg.get("content", ""))
            for match in self._FILE_PATTERN.finditer(content):
                filepath = match.group(1)
                filename = filepath.split("/")[-1]
                files.add(filename)
        return files

    def _promote_to_long_term(
        self,
        segment: TopicSegment,
        summary: str,
        conversation_id: str,
    ) -> bool:
        """将 work 段提升到 long_term_memories

        提取 key_files → 计算 memory_key → INSERT OR REPLACE (7 天 TTL)

        Returns:
            是否成功写入
        """
        key_files = self._extract_files_from_messages(segment.messages)
        if not key_files:
            return False

        sorted_files = sorted(key_files)
        memory_key = hashlib.sha256(
            "|".join(sorted_files).encode()
        ).hexdigest()[:16]

        key_files_json = json.dumps(sorted_files, ensure_ascii=False)
        expires_at = (
            datetime.utcnow() + timedelta(days=self.LTM_TTL_DAYS)
        ).strftime("%Y-%m-%d %H:%M:%S")

        db_path = self.db_store.db_path / "context.db"
        conn = _sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO long_term_memories
                (memory_key, key_files, summary, conversation_id,
                 access_count, updated_at, expires_at)
            VALUES (?, ?, ?, ?, COALESCE(
                (SELECT access_count FROM long_term_memories WHERE memory_key = ?), 0
            ), CURRENT_TIMESTAMP, ?)
            """,
            (memory_key, key_files_json, summary, conversation_id,
             memory_key, expires_at),
        )

        conn.commit()
        conn.close()

        logger.debug(
            f"[ConvStore] LTM 提升: key={memory_key[:8]}… | "
            f"files={sorted_files} | TTL={self.LTM_TTL_DAYS}d"
        )
        return True

    def _recall_long_term(
        self, messages: List[Dict], limit: int = 3
    ) -> List[Dict]:
        """按文件名匹配召回跨会话记忆

        从当前消息提取 key_files，查 long_term_memories 中有交集的记录，
        按 updated_at DESC 排序取 top-N，返回 system 消息列表。

        Returns:
            注入用的 system 消息列表（可能为空）
        """
        current_files = self._extract_files_from_messages(messages)
        if not current_files:
            return []

        db_path = self.db_store.db_path / "context.db"
        conn = _sqlite3.connect(db_path)
        cursor = conn.cursor()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # 对每个文件名用 LIKE 查询，收集匹配的 memory_key
        matched_keys: Set[str] = set()
        for filename in current_files:
            cursor.execute(
                "SELECT memory_key FROM long_term_memories "
                "WHERE key_files LIKE ? AND expires_at > ?",
                (f"%{filename}%", now),
            )
            for row in cursor.fetchall():
                matched_keys.add(row[0])

        if not matched_keys:
            conn.close()
            return []

        # 按 updated_at DESC 排序取 top-N
        placeholders = ",".join("?" for _ in matched_keys)
        cursor.execute(
            f"SELECT memory_key, key_files, summary FROM long_term_memories "
            f"WHERE memory_key IN ({placeholders}) AND expires_at > ? "
            f"ORDER BY updated_at DESC LIMIT ?",
            (*matched_keys, now, limit),
        )
        rows = cursor.fetchall()

        # 更新 access_count
        for row in rows:
            cursor.execute(
                "UPDATE long_term_memories SET access_count = access_count + 1 "
                "WHERE memory_key = ?",
                (row[0],),
            )
        conn.commit()
        conn.close()

        # 构造 system 消息
        result = []
        for memory_key, key_files_json, summary in rows:
            files_list = json.loads(key_files_json)
            files_str = ", ".join(files_list)
            result.append({
                "role": "system",
                "content": f"[Prior session context] 你之前在 {files_str} 上的工作: {summary}",
            })

        return result

    def cleanup_expired_ltm(self) -> int:
        """清理过期的长期记忆

        Returns:
            删除的行数
        """
        db_path = self.db_store.db_path / "context.db"
        conn = _sqlite3.connect(db_path)
        cursor = conn.cursor()

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute(
            "DELETE FROM long_term_memories WHERE expires_at < ?", (now,)
        )
        deleted = cursor.rowcount
        conn.commit()
        conn.close()

        if deleted > 0:
            logger.info(f"[ConvStore] 清理过期长期记忆: {deleted} 条")

        return deleted
