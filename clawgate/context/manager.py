"""Context Manager - OpenClaw 3.7 ContextEngine 实现"""

import hashlib
import logging
import os
import tiktoken
from typing import List, Dict, Optional, Tuple, Callable
from pathlib import Path
import yaml

from .strategies.sliding_window import SlidingWindowStrategy
from .strategies.summarization import SummarizationStrategy
from .strategies.selective import SelectiveRetainStrategy
from .strategies.adaptive import AdaptiveStrategy
from .strategies.topic_aware import TopicAwareStrategy
from .topic_segmenter import TopicSegmenter
from .conversation_store import ConversationStore
from ..storage.sqlite_store import SQLiteStore


class ContextManager:
    """上下文管理器 - 多策略压缩、缓存、摘要"""

    def __init__(
        self,
        config_path: str = "config/models.yaml",
        db_store: Optional[SQLiteStore] = None,
    ):
        # 加载配置
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file) as f:
                config = yaml.safe_load(f)
                self.context_config = config.get("context_engine", {})
        else:
            self.context_config = {}

        # 数据库
        self.db_store = db_store or SQLiteStore()

        # Tokenizer
        self.tokenizer = tiktoken.get_encoding("cl100k_base")

        # 初始化策略
        self.strategies = {
            "sliding_window": SlidingWindowStrategy(),
            "summarization": SummarizationStrategy(),
            "selective": SelectiveRetainStrategy(),
            "adaptive": AdaptiveStrategy(),
            "topic_aware": TopicAwareStrategy(),
        }

        # Topic segmenter (用于自动适配)
        self.topic_segmenter = TopicSegmenter()

        # LLM 摘要器 (P3: glm-4-flash)
        self._llm_summarizer = self._create_llm_summarizer()

        # 会话段持久化存储
        self.conversation_store = ConversationStore(
            db_store=self.db_store,
            topic_segmenter=self.topic_segmenter,
            llm_summarizer=self._llm_summarizer,
        )

        # 默认策略
        self.default_strategy = self.context_config.get("default_strategy", "adaptive")

    def compress(
        self,
        messages: List[Dict],
        target_tokens: int,
        strategy: Optional[str] = None,
        agent_type: Optional[str] = None,
    ) -> Tuple[List[Dict], Dict]:
        """
        压缩上下文

        Args:
            messages: 消息列表
            target_tokens: 目标 token 数
            strategy: 压缩策略 (sliding_window/summarization/selective/adaptive)
            agent_type: Agent 类型 (judge/builder/flash)

        Returns:
            (压缩后的消息, 元数据)
        """
        # 选择策略
        strategy_name = strategy or self.default_strategy
        if strategy_name not in self.strategies:
            raise ValueError(f"Unknown strategy: {strategy_name}")

        strategy_obj = self.strategies[strategy_name]

        # 计算当前 token 数
        current_tokens = self._count_tokens(messages)

        # 如果已经满足要求，直接返回
        if current_tokens <= target_tokens:
            return messages, {
                "original_tokens": current_tokens,
                "compressed_tokens": current_tokens,
                "compression_ratio": 1.0,
                "strategy": "none",
            }

        # 执行压缩
        compressed_messages = strategy_obj.compress(
            messages=messages,
            target_tokens=target_tokens,
            agent_type=agent_type,
            tokenizer=self.tokenizer,
        )

        # 计算压缩后 token 数
        compressed_tokens = self._count_tokens(compressed_messages)

        metadata = {
            "original_tokens": current_tokens,
            "compressed_tokens": compressed_tokens,
            "compression_ratio": compressed_tokens / current_tokens,
            "strategy": strategy_name,
        }

        return compressed_messages, metadata

    def get_cached_context(
        self, messages: List[Dict]
    ) -> Optional[Tuple[List[Dict], Dict]]:
        """
        获取缓存的压缩上下文

        Args:
            messages: 原始消息

        Returns:
            (压缩后的消息, 元数据) 或 None
        """
        # 生成缓存 key
        cache_key = self._generate_cache_key(messages)

        # 从数据库获取
        cached = self.db_store.get_cached_context(cache_key)

        if cached:
            return cached["compressed_messages"], {
                "hit": True,
                "hit_count": cached["hit_count"],
                "compression_strategy": cached["compression_strategy"],
            }

        return None

    def cache_context(
        self,
        messages: List[Dict],
        compressed_messages: List[Dict],
        metadata: Dict,
    ):
        """
        缓存压缩结果

        Args:
            messages: 原始消息
            compressed_messages: 压缩后的消息
            metadata: 压缩元数据
        """
        cache_key = self._generate_cache_key(messages)

        self.db_store.cache_context(
            cache_key=cache_key,
            messages=messages,
            compressed_messages=compressed_messages,
            token_count=metadata["original_tokens"],
            compressed_token_count=metadata["compressed_tokens"],
            compression_strategy=metadata["strategy"],
        )

    def summarize(
        self, messages: List[Dict], session_id: str, summary_level: str = "brief"
    ) -> Dict:
        """
        生成上下文摘要

        Args:
            messages: 消息列表
            session_id: 会话 ID
            summary_level: 摘要级别 (brief/detailed/full)

        Returns:
            摘要数据
        """
        # 使用 summarization 策略
        summary_strategy = self.strategies["summarization"]

        # 生成摘要
        summary_data = summary_strategy.generate_summary(
            messages=messages,
            level=summary_level,
            tokenizer=self.tokenizer,
        )

        # 保存到数据库
        self.db_store.save_summary(
            session_id=session_id,
            summary_data={
                **summary_data,
                "summary_level": summary_level,
            },
        )

        return summary_data

    def auto_fit(
        self,
        messages: List[Dict],
        model: str,
        reserve_tokens: int = 512,
    ) -> Tuple[List[Dict], Dict]:
        """
        自动适配上下文到模型窗口

        流程 (ConversationStore + Prompt Cache 集成):
        0. Prompt Cache: 检查 system 消息是否有缓存的压缩版
        1. derive_conversation_id
        2. segment + store_segments (先存)
        3. 检测 current_mode (最后一段的 topic_type)
        4. 超限 → reconstruct_context 从 Store 重组
        5. 仍超限 → fallback 到 topic_aware 压缩

        Args:
            messages: 消息列表
            model: 目标模型名称
            reserve_tokens: 为输出预留的 token 数

        Returns:
            (适配后的消息, 元数据)
        """
        import logging
        logger = logging.getLogger("clawgate.context")

        # 获取模型上下文限制
        context_limit = self.topic_segmenter.get_context_limit(model)
        target_tokens = context_limit - reserve_tokens

        # Step 0: Prompt Cache - check for cached compressed system messages (F5)
        prompt_cache_hit = False
        system_msgs = [m for m in messages if m.get("role") == "system"]
        if system_msgs:
            system_content = "".join(m.get("content", "") for m in system_msgs)
            prompt_key = hashlib.sha256(system_content.encode()).hexdigest()[:16]
            cached_prompt = self.db_store.get_prompt_cache(prompt_key)
            if cached_prompt:
                # Replace system messages with cached compressed version
                non_system = [m for m in messages if m.get("role") != "system"]
                messages = cached_prompt["compressed_system"] + non_system
                prompt_cache_hit = True
                logger.info(
                    f"[自动适配] Prompt Cache HIT: "
                    f"{cached_prompt.get('token_count', 0)} -> "
                    f"{cached_prompt.get('compressed_token_count', 0)} tokens"
                )

        # 计算当前 token 数
        current_tokens = self._count_tokens(messages)

        logger.info(
            f"[自动适配] model={model} | 消息={len(messages)} | "
            f"tokens={current_tokens}/{context_limit} | 预留={reserve_tokens}"
            f"{' | prompt_cache=HIT' if prompt_cache_hit else ''}"
        )

        # Step 1: 推导会话 ID
        conv_id = self.conversation_store.derive_conversation_id(messages)

        # Step 2: 分段 + 先存后压
        segments = self.topic_segmenter.segment(messages)
        if segments:
            self.conversation_store.store_segments(conv_id, segments)

        # Step 3: 检测当前模式（最后一段的 topic_type）+ 模型能力分级
        current_mode = "work"
        if segments:
            current_mode = segments[-1].topic_type

        model_tier = self.topic_segmenter.get_model_tier(model)

        logger.info(
            f"[自动适配] conv={conv_id[:8]}… | 段={len(segments)} | "
            f"模式={current_mode} | tier={model_tier}"
        )

        if current_tokens <= target_tokens:
            # 不需要压缩（但段已存储，下次可用）
            logger.info(f"[自动适配] 无需压缩 ({current_tokens} <= {target_tokens})")
            return messages, {
                "original_tokens": current_tokens,
                "compressed_tokens": current_tokens,
                "compression_ratio": 1.0,
                "strategy": "none",
                "model": model,
                "context_limit": context_limit,
                "conversation_id": conv_id,
                "mode": current_mode,
                "model_tier": model_tier,
            }

        # Step 4: 超限 → 从 Store 智能重组
        logger.warning(
            f"[自动适配] 需要压缩! {current_tokens} > {target_tokens} | "
            f"尝试 ConversationStore 重组 (mode={current_mode})"
        )

        reconstructed, recon_meta = self.conversation_store.reconstruct_context(
            conversation_id=conv_id,
            messages=messages,
            mode=current_mode,
            target_tokens=target_tokens,
            tokenizer=self.tokenizer,
            model_tier=model_tier,
        )

        reconstructed_tokens = self._count_tokens(reconstructed)

        if reconstructed_tokens <= target_tokens:
            logger.info(
                f"[自动适配] ConvStore 重组成功 | "
                f"{current_tokens}→{reconstructed_tokens} tokens"
            )
            # F5: Cache compressed system prompt for reuse
            self._maybe_cache_system_prompt(
                reconstructed, system_msgs, prompt_cache_hit
            )
            return reconstructed, {
                "original_tokens": current_tokens,
                "compressed_tokens": reconstructed_tokens,
                "compression_ratio": reconstructed_tokens / current_tokens if current_tokens else 1.0,
                "strategy": "conv_store",
                "model": model,
                "context_limit": context_limit,
                "conversation_id": conv_id,
                "mode": current_mode,
                "model_tier": model_tier,
                **recon_meta,
            }

        # Step 5: 仍超限 → fallback 到 topic_aware 压缩
        logger.warning(
            f"[自动适配] ConvStore 重组后仍超限 ({reconstructed_tokens} > {target_tokens}) | "
            f"fallback 到 topic_aware"
        )

        compressed_result = self.compress(
            messages=reconstructed,
            target_tokens=target_tokens,
            strategy="topic_aware",
        )
        # F5: Cache compressed system prompt for reuse
        self._maybe_cache_system_prompt(
            compressed_result[0], system_msgs, prompt_cache_hit
        )
        return compressed_result

    def _maybe_cache_system_prompt(
        self,
        result_messages: List[Dict],
        original_system_msgs: List[Dict],
        already_cached: bool,
    ):
        """F5: Cache compressed system prompt if compression happened and not already cached"""
        if already_cached or not original_system_msgs:
            return
        compressed_system = [m for m in result_messages if m.get("role") == "system"]
        if not compressed_system:
            return
        # Only cache if system actually got smaller
        orig_tokens = self._count_tokens(original_system_msgs)
        comp_tokens = self._count_tokens(compressed_system)
        if comp_tokens < orig_tokens:
            system_content = "".join(m.get("content", "") for m in original_system_msgs)
            prompt_key = hashlib.sha256(system_content.encode()).hexdigest()[:16]
            self.db_store.set_prompt_cache(
                prompt_key=prompt_key,
                system_messages=original_system_msgs,
                compressed_system=compressed_system,
                token_count=orig_tokens,
                compressed_token_count=comp_tokens,
            )

    def _create_llm_summarizer(self) -> Optional[Callable[[str], str]]:
        """创建 LLM 摘要器 callable (glm-4-flash)

        当 GLM_API_KEY 存在时返回 summarizer 函数，否则返回 None。
        """
        logger = logging.getLogger("clawgate.context")
        api_key = os.environ.get("GLM_API_KEY")
        if not api_key:
            logger.info("[ContextManager] GLM_API_KEY 未设置，LLM 摘要不可用")
            return None

        try:
            import httpx
        except ImportError:
            logger.warning("[ContextManager] httpx 未安装，LLM 摘要不可用")
            return None

        def summarizer(prompt: str) -> str:
            resp = httpx.post(
                "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": "glm-4-flash",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_tokens": 300,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

        logger.info("[ContextManager] LLM 摘要器已创建 (glm-4-flash)")
        return summarizer

    def _count_tokens(self, messages: List[Dict]) -> int:
        """计算消息列表的 token 数"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            total += len(self.tokenizer.encode(content))
        return total

    def _generate_cache_key(self, messages: List[Dict]) -> str:
        """生成缓存 key"""
        # 使用消息内容的 hash
        content = "".join([msg.get("content", "") for msg in messages])
        return hashlib.sha256(content.encode()).hexdigest()[:16]
