"""Semantic Cache - Keyword-based Jaccard similarity caching for non-streaming requests

Design decisions:
- Uses keyword Jaccard similarity (no embedding dependency)
- Threshold 0.85 (conservative, avoid wrong answers)
- Only for non-streaming requests (streaming responses can't be cached)
- 4h TTL, max 500 entries, LRU eviction
"""

import hashlib
import logging
import re
from typing import Dict, List, Optional, Set

from ..storage.sqlite_store import SQLiteStore

logger = logging.getLogger("clawgate.context.semantic_cache")

# Common stop words (Chinese + English)
_STOP_WORDS = frozenset({
    # English
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "to", "of", "in",
    "for", "on", "with", "at", "by", "from", "as", "into", "through",
    "and", "but", "or", "not", "no", "if", "then", "so", "too", "very",
    "this", "that", "it", "i", "me", "my", "you", "your", "he", "she",
    "we", "they", "what", "how", "when", "where", "which", "who", "why",
    # Chinese
    "的", "了", "在", "是", "我", "你", "他", "她", "它", "们",
    "这", "那", "和", "与", "而", "但", "或", "也", "都", "就",
    "还", "又", "不", "没", "有", "要", "会", "能", "可以",
    "一个", "一些", "什么", "怎么", "如何", "为什么", "哪个",
    "请", "帮", "用", "把", "给", "让", "吗", "呢", "吧",
})

# Pattern to split text into tokens
_TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


class SemanticCache:
    """Keyword-based semantic cache with Jaccard similarity matching"""

    def __init__(
        self,
        db_store: SQLiteStore,
        threshold: float = 0.85,
        max_size: int = 500,
        ttl_hours: int = 4,
    ):
        self.db_store = db_store
        self.threshold = threshold
        self.max_size = max_size
        self.ttl_hours = ttl_hours

    def extract_keywords(self, text: str) -> Set[str]:
        """Extract keywords from text (tokenize + remove stop words)"""
        tokens = _TOKEN_PATTERN.findall(text.lower())
        # Filter stop words and very short tokens
        keywords = {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}
        return keywords

    @staticmethod
    def jaccard_similarity(a: Set[str], b: Set[str]) -> float:
        """Compute Jaccard similarity between two keyword sets"""
        if not a or not b:
            return 0.0
        intersection = len(a & b)
        union = len(a | b)
        return intersection / union if union > 0 else 0.0

    def lookup(self, query: str, model: str) -> Optional[Dict]:
        """Look up a query in the semantic cache

        Returns cached response if a similar query (Jaccard >= threshold) exists.
        """
        query_keywords = self.extract_keywords(query)
        if not query_keywords:
            return None

        # Check exact hash first (fast path)
        query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]
        entries = self.db_store.get_all_semantic_cache(model=model)

        best_match = None
        best_similarity = 0.0

        for entry in entries:
            # Exact hash match
            if entry["query_hash"] == query_hash:
                self.db_store.bump_semantic_cache_hit(entry["query_hash"])
                logger.info(
                    f"[SemanticCache] Exact HIT: hash={query_hash[:8]}…"
                )
                return entry

            # Jaccard similarity check
            cached_keywords = set(entry["keywords"])
            similarity = self.jaccard_similarity(query_keywords, cached_keywords)
            if similarity > best_similarity:
                best_similarity = similarity
                best_match = entry

        if best_match and best_similarity >= self.threshold:
            self.db_store.bump_semantic_cache_hit(best_match["query_hash"])
            logger.info(
                f"[SemanticCache] Similarity HIT: "
                f"score={best_similarity:.3f} >= {self.threshold}"
            )
            return best_match

        return None

    def store(self, query: str, model: str, response: Dict):
        """Store a query-response pair in the semantic cache"""
        keywords = self.extract_keywords(query)
        if not keywords:
            return

        query_hash = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:16]

        self.db_store.set_semantic_cache(
            query_hash=query_hash,
            query_text=query[:500],  # truncate for storage
            keywords=sorted(keywords),
            response=response,
            model=model,
            ttl_hours=self.ttl_hours,
        )

        # Periodic cleanup
        self.db_store.cleanup_semantic_cache(max_size=self.max_size)

        logger.debug(
            f"[SemanticCache] Stored: hash={query_hash[:8]}… "
            f"keywords={len(keywords)} model={model}"
        )
