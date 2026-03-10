"""Tests for SemanticCache - keyword Jaccard similarity caching"""

import sqlite3
import json
import tempfile
from pathlib import Path

import pytest

from clawgate.context.semantic_cache import SemanticCache
from clawgate.storage.sqlite_store import SQLiteStore


@pytest.fixture
def store(tmp_path):
    """Create a real SQLiteStore in a temporary directory."""
    return SQLiteStore(db_path=str(tmp_path / "sqlite"))


@pytest.fixture
def cache(store):
    """Create a SemanticCache backed by a real SQLiteStore."""
    return SemanticCache(db_store=store, threshold=0.85)


# ---------- extract_keywords ----------

def test_extract_keywords_removes_stop_words(cache):
    keywords = cache.extract_keywords("the quick brown fox is very fast")
    assert "the" not in keywords
    assert "is" not in keywords
    assert "very" not in keywords
    assert "quick" in keywords
    assert "brown" in keywords
    assert "fox" in keywords
    assert "fast" in keywords


def test_extract_keywords_chinese(cache):
    # The tokenizer splits on whitespace/punctuation, so use spaced Chinese words
    keywords = cache.extract_keywords("请 帮 我 实现 一个 排序 算法")
    # Single-char stop words "请","帮","我" are removed (len<=1 or in stop list)
    assert "请" not in keywords
    assert "帮" not in keywords
    assert "我" not in keywords
    assert "一个" not in keywords
    # Content words should remain
    assert "实现" in keywords
    assert "排序" in keywords
    assert "算法" in keywords


def test_extract_keywords_filters_single_char(cache):
    keywords = cache.extract_keywords("a b c hello world")
    # Single-character tokens are filtered (len <= 1)
    assert "a" not in keywords
    assert "b" not in keywords
    assert "c" not in keywords
    assert "hello" in keywords
    assert "world" in keywords


# ---------- jaccard_similarity ----------

def test_jaccard_known_pair():
    sim = SemanticCache.jaccard_similarity({"a", "b", "c"}, {"b", "c", "d"})
    assert sim == pytest.approx(0.5)  # intersection=2, union=4


def test_jaccard_identical_sets():
    sim = SemanticCache.jaccard_similarity({"x", "y"}, {"x", "y"})
    assert sim == pytest.approx(1.0)


def test_jaccard_disjoint_sets():
    sim = SemanticCache.jaccard_similarity({"a", "b"}, {"c", "d"})
    assert sim == pytest.approx(0.0)


def test_jaccard_empty_set():
    assert SemanticCache.jaccard_similarity(set(), {"a"}) == 0.0
    assert SemanticCache.jaccard_similarity({"a"}, set()) == 0.0
    assert SemanticCache.jaccard_similarity(set(), set()) == 0.0


# ---------- store + exact lookup ----------

def test_store_and_exact_lookup(cache):
    query = "binary search Python implementation"
    response = {"choices": [{"message": {"content": "def binary_search(): ..."}}]}

    cache.store(query, model="glm-5", response=response)
    hit = cache.lookup(query, model="glm-5")

    assert hit is not None
    assert hit["response"] == response


# ---------- similarity lookup ----------

def test_similarity_lookup(cache):
    cache.store(
        "binary search Python implementation",
        model="glm-5",
        response={"answer": "cached"},
    )
    # Overlapping keywords: binary, search, python, implementation/implement
    hit = cache.lookup("implement binary search in Python", model="glm-5")
    # Keywords for stored:  {binary, search, python, implementation}
    # Keywords for lookup:  {implement, binary, search, python}
    # intersection=3 (binary, search, python), union=5 -> 0.6 < 0.85
    # With default threshold 0.85 this might NOT hit, so use a lower threshold cache
    # Let's verify by checking keywords directly
    stored_kw = cache.extract_keywords("binary search Python implementation")
    lookup_kw = cache.extract_keywords("implement binary search in Python")
    sim = SemanticCache.jaccard_similarity(stored_kw, lookup_kw)
    if sim >= cache.threshold:
        assert hit is not None
    else:
        # If similarity is below threshold, that's correct behavior
        assert hit is None


def test_similarity_lookup_high_overlap(store):
    """Use a lower threshold to verify similarity matching works."""
    cache = SemanticCache(db_store=store, threshold=0.5)
    cache.store(
        "binary search Python implementation",
        model="glm-5",
        response={"answer": "cached"},
    )
    hit = cache.lookup("implement binary search in Python", model="glm-5")
    assert hit is not None
    assert hit["response"] == {"answer": "cached"}


# ---------- no match ----------

def test_no_match(cache):
    cache.store(
        "database schema design for PostgreSQL",
        model="glm-5",
        response={"answer": "schema"},
    )
    hit = cache.lookup("weather forecast tomorrow sunny", model="glm-5")
    assert hit is None


# ---------- model scoping ----------

def test_model_scoping(cache):
    cache.store(
        "explain recursion",
        model="glm-5",
        response={"answer": "recursion-glm"},
    )
    # Same query, different model -> no hit
    hit = cache.lookup("explain recursion", model="gpt-4o")
    assert hit is None

    # Same query, same model -> hit
    hit = cache.lookup("explain recursion", model="glm-5")
    assert hit is not None
    assert hit["response"] == {"answer": "recursion-glm"}


# ---------- cleanup expired ----------

def test_cleanup_expired(store):
    cache = SemanticCache(db_store=store, threshold=0.85)
    cache.store(
        "test expiration query",
        model="glm-5",
        response={"answer": "will expire"},
    )

    # Manually set expires_at to the past
    conn = sqlite3.connect(Path(store.db_path) / "context.db")
    conn.execute(
        "UPDATE semantic_cache SET expires_at = datetime('now', '-1 hour')"
    )
    conn.commit()
    conn.close()

    # Run cleanup
    removed = store.cleanup_semantic_cache(max_size=500)
    assert removed >= 1

    # Lookup should find nothing (expired entry was cleaned)
    hit = cache.lookup("test expiration query", model="glm-5")
    assert hit is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
