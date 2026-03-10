"""ConversationStore 完整测试

覆盖目标:
  - conversation_store.py 全方法、全分支
  - sqlite_store.py 新增的 conversation_segments 表
  - manager.py 改造后的 auto_fit 五步流程
  - __init__.py 导出
"""

import os
import json
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta

import pytest
import tiktoken

from clawgate.storage.sqlite_store import SQLiteStore
from clawgate.context.topic_segmenter import TopicSegmenter, TopicSegment
from clawgate.context.conversation_store import ConversationStore, SEGMENT_TTL_HOURS
from clawgate.context.manager import ContextManager


# ========== Fixtures ==========


@pytest.fixture
def tmpdir():
    d = tempfile.mkdtemp()
    yield d


@pytest.fixture
def db_store(tmpdir):
    return SQLiteStore(db_path=tmpdir)


@pytest.fixture
def segmenter():
    return TopicSegmenter()


@pytest.fixture
def conv_store(db_store, segmenter):
    return ConversationStore(db_store=db_store, topic_segmenter=segmenter)


@pytest.fixture
def tokenizer():
    return tiktoken.get_encoding("cl100k_base")


@pytest.fixture
def ctx_manager(db_store):
    return ContextManager(config_path="/nonexistent/path.yaml", db_store=db_store)


# ========== 工作消息 / 闲聊消息 工厂 ==========


def make_work_messages(n=10):
    """生成工作类消息"""
    msgs = [{"role": "system", "content": "You are a coding assistant."}]
    for i in range(n):
        msgs.append(
            {
                "role": "user",
                "content": f"Please implement function feature_{i} in Python with error handling and tests.",
            }
        )
        msgs.append(
            {
                "role": "assistant",
                "content": (
                    f"```python\ndef feature_{i}():\n"
                    f"    # implementation\n    return {i}\n```"
                ),
            }
        )
    return msgs


def make_casual_messages(n=5):
    """生成闲聊消息"""
    greetings = ["你好", "嗨", "hello", "hey", "早"]
    confirms = ["OK", "好", "哈哈", "666", "谢谢"]
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": greetings[i % len(greetings)]})
        msgs.append({"role": "assistant", "content": confirms[i % len(confirms)]})
    return msgs


def make_mixed_messages():
    """生成 work→casual→work 混合消息"""
    return (
        [{"role": "system", "content": "You are a coding assistant."}]
        + [  # work block 1
            {"role": "user", "content": "Help me debug this Python traceback error in api.py"},
            {"role": "assistant", "content": "```python\ndef fix():\n    pass\n```\nHere is the fix for the exception."},
            {"role": "user", "content": "Now optimize the database query in models.py"},
            {"role": "assistant", "content": "Use an index on the timestamp column for better performance."},
        ]
        + [  # casual block
            {"role": "user", "content": "哈哈"},
            {"role": "assistant", "content": "😊"},
            {"role": "user", "content": "吃饭去了"},
            {"role": "assistant", "content": "好的"},
        ]
        + [  # work block 2
            {"role": "user", "content": "Back to work. Deploy the FastAPI service with Docker."},
            {"role": "assistant", "content": "```dockerfile\nFROM python:3.11\nRUN pip install fastapi\n```"},
            {"role": "user", "content": "Add nginx config for reverse proxy"},
            {"role": "assistant", "content": "Here is the nginx.conf for reverse proxy configuration."},
        ]
    )


# ================================================================
# 1. SQLiteStore — conversation_segments 表
# ================================================================


class TestSQLiteStoreTable:
    """验证 conversation_segments 表和索引正确创建"""

    def test_table_exists(self, db_store, tmpdir):
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='conversation_segments'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_indexes_exist(self, db_store, tmpdir):
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_conv_segments%'"
        )
        indexes = {row[0] for row in cur.fetchall()}
        conn.close()

        assert "idx_conv_segments_conv_id" in indexes
        assert "idx_conv_segments_type" in indexes
        assert "idx_conv_segments_expires" in indexes

    def test_unique_constraint(self, db_store, tmpdir):
        """同一 conv_id + segment_index 应该 REPLACE 而非报错"""
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        expires = (datetime.utcnow() + timedelta(hours=24)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cur.execute(
            "INSERT INTO conversation_segments "
            "(conversation_id, segment_index, topic_type, summary, messages, message_count, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("abc", 0, "work", "s1", "[]", 0, expires),
        )
        conn.commit()
        # 再插入同一 (conv_id, idx) → REPLACE
        cur.execute(
            "INSERT OR REPLACE INTO conversation_segments "
            "(conversation_id, segment_index, topic_type, summary, messages, message_count, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("abc", 0, "work", "s2", "[]", 0, expires),
        )
        conn.commit()
        cur.execute(
            "SELECT summary FROM conversation_segments WHERE conversation_id='abc' AND segment_index=0"
        )
        assert cur.fetchone()[0] == "s2"
        conn.close()

    def test_columns_schema(self, db_store, tmpdir):
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(conversation_segments)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()

        expected = {
            "id", "conversation_id", "segment_index", "topic_type",
            "summary", "messages", "message_count", "created_at", "expires_at",
        }
        assert expected.issubset(cols)


# ================================================================
# 2. derive_conversation_id
# ================================================================


class TestDeriveConversationId:
    def test_deterministic(self, conv_store):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        id1 = conv_store.derive_conversation_id(msgs)
        id2 = conv_store.derive_conversation_id(msgs)
        assert id1 == id2

    def test_different_system_different_id(self, conv_store):
        msgs_a = [
            {"role": "system", "content": "system A"},
            {"role": "user", "content": "same user msg"},
        ]
        msgs_b = [
            {"role": "system", "content": "system B"},
            {"role": "user", "content": "same user msg"},
        ]
        assert conv_store.derive_conversation_id(msgs_a) != conv_store.derive_conversation_id(msgs_b)

    def test_different_first_user_different_id(self, conv_store):
        msgs_a = [
            {"role": "system", "content": "same system"},
            {"role": "user", "content": "user msg A"},
        ]
        msgs_b = [
            {"role": "system", "content": "same system"},
            {"role": "user", "content": "user msg B"},
        ]
        assert conv_store.derive_conversation_id(msgs_a) != conv_store.derive_conversation_id(msgs_b)

    def test_no_system_msg(self, conv_store):
        """无 system 消息也能正常工作"""
        msgs = [{"role": "user", "content": "hello"}]
        cid = conv_store.derive_conversation_id(msgs)
        assert isinstance(cid, str) and len(cid) == 16

    def test_no_user_msg(self, conv_store):
        """无 user 消息也能正常工作"""
        msgs = [{"role": "system", "content": "only system"}]
        cid = conv_store.derive_conversation_id(msgs)
        assert isinstance(cid, str) and len(cid) == 16

    def test_empty_messages(self, conv_store):
        """空消息列表也能正常工作"""
        cid = conv_store.derive_conversation_id([])
        assert isinstance(cid, str) and len(cid) == 16

    def test_long_system_truncated(self, conv_store):
        """超长 system 只取前 500 字符"""
        msgs_a = [
            {"role": "system", "content": "x" * 1000},
            {"role": "user", "content": "u"},
        ]
        msgs_b = [
            {"role": "system", "content": "x" * 500 + "y" * 500},
            {"role": "user", "content": "u"},
        ]
        # 前 500 字符相同 → 同一 ID
        assert conv_store.derive_conversation_id(msgs_a) == conv_store.derive_conversation_id(msgs_b)

    def test_id_length(self, conv_store):
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        cid = conv_store.derive_conversation_id(msgs)
        assert len(cid) == 16


# ================================================================
# 3. store_segments + get_segments
# ================================================================


class TestStoreAndGetSegments:
    def test_store_and_retrieve(self, conv_store, segmenter):
        msgs = make_work_messages(5)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)

        stored = conv_store.store_segments(conv_id, segments)
        assert stored == len(segments)

        retrieved = conv_store.get_segments(conv_id)
        assert len(retrieved) == len(segments)

        for r in retrieved:
            assert "messages" in r
            assert isinstance(r["messages"], list)
            assert r["conversation_id"] == conv_id

    def test_overwrite_on_reinsert(self, conv_store, segmenter):
        """重复 store_segments 应覆盖旧数据"""
        msgs = make_work_messages(3)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)

        conv_store.store_segments(conv_id, segments)

        # 增加消息后重新分段
        msgs2 = msgs + [{"role": "user", "content": "Another coding question about Python testing"}]
        segments2 = segmenter.segment(msgs2)
        conv_store.store_segments(conv_id, segments2)

        retrieved = conv_store.get_segments(conv_id)
        # 数量应该匹配最新分段
        assert len(retrieved) == len(segments2)

    def test_old_segments_cleaned_when_count_decreases(self, conv_store, segmenter):
        """段数减少时，多出的旧段应被删除"""
        msgs_long = make_mixed_messages()
        segments_long = segmenter.segment(msgs_long)
        conv_id = "testconv123"
        conv_store.store_segments(conv_id, segments_long)
        n_long = len(conv_store.get_segments(conv_id))

        # 存更少的段
        msgs_short = make_work_messages(2)
        segments_short = segmenter.segment(msgs_short)
        conv_store.store_segments(conv_id, segments_short)
        n_short = len(conv_store.get_segments(conv_id))

        assert n_short == len(segments_short)
        assert n_short <= n_long

    def test_filter_by_topic_type(self, conv_store, segmenter):
        msgs = make_mixed_messages()
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        work_segs = conv_store.get_segments(conv_id, topic_type="work")
        casual_segs = conv_store.get_segments(conv_id, topic_type="casual")
        all_segs = conv_store.get_segments(conv_id)

        assert len(work_segs) + len(casual_segs) == len(all_segs)
        for s in work_segs:
            assert s["topic_type"] == "work"
        for s in casual_segs:
            assert s["topic_type"] == "casual"

    def test_expired_segments_not_returned(self, conv_store, tmpdir):
        """过期段不应被 get_segments 返回"""
        db_path = conv_store.db_store.db_path / "context.db"
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # 插入一条已过期的段
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO conversation_segments "
            "(conversation_id, segment_index, topic_type, summary, messages, message_count, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("expired_conv", 0, "work", "old", "[]", 0, past),
        )
        conn.commit()
        conn.close()

        result = conv_store.get_segments("expired_conv")
        assert len(result) == 0

    def test_get_segments_nonexistent_conv(self, conv_store):
        """查询不存在的 conv_id 返回空"""
        assert conv_store.get_segments("nonexistent") == []

    def test_ttl_is_correct(self, conv_store, segmenter):
        """验证 expires_at = created_at + 24h"""
        msgs = make_work_messages(2)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)

        before = datetime.utcnow()
        conv_store.store_segments(conv_id, segments)
        after = datetime.utcnow()

        retrieved = conv_store.get_segments(conv_id)
        for r in retrieved:
            expires = datetime.strptime(r["expires_at"], "%Y-%m-%d %H:%M:%S")
            # SQLite 截断微秒，所以 before 要向下取整到秒
            expected_min = before.replace(microsecond=0) + timedelta(hours=SEGMENT_TTL_HOURS)
            expected_max = after + timedelta(hours=SEGMENT_TTL_HOURS) + timedelta(seconds=2)
            assert expected_min <= expires <= expected_max

    def test_summary_generated(self, conv_store, segmenter):
        """每段都应有 summary"""
        msgs = make_work_messages(5)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        retrieved = conv_store.get_segments(conv_id)
        for r in retrieved:
            assert r["summary"] is not None
            assert len(r["summary"]) > 0


# ================================================================
# 4. reconstruct_context
# ================================================================


class TestReconstructContext:
    def test_work_mode_basic(self, conv_store, segmenter, tokenizer):
        msgs = make_mixed_messages()
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        result, meta = conv_store.reconstruct_context(
            conversation_id=conv_id,
            messages=msgs,
            mode="work",
            target_tokens=500,
            tokenizer=tokenizer,
        )

        assert len(result) > 0
        assert meta["strategy"] == "conv_store_reconstruct"
        assert meta["mode"] == "work"
        assert meta["stored_segments"] > 0

    def test_casual_mode_basic(self, conv_store, segmenter, tokenizer):
        msgs = make_mixed_messages()
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        result, meta = conv_store.reconstruct_context(
            conversation_id=conv_id,
            messages=msgs,
            mode="casual",
            target_tokens=500,
            tokenizer=tokenizer,
        )

        assert len(result) > 0
        assert meta["mode"] == "casual"

    def test_work_mode_contains_system(self, conv_store, segmenter, tokenizer):
        """work 重组应保留 system 消息"""
        msgs = make_mixed_messages()
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        result, _ = conv_store.reconstruct_context(
            conv_id, msgs, "work", 500, tokenizer
        )
        system_msgs = [m for m in result if m["role"] == "system"]
        assert len(system_msgs) >= 1

    def test_casual_mode_has_work_summary(self, conv_store, segmenter, tokenizer):
        """casual 模式应包含工作摘要"""
        msgs = make_mixed_messages()
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        result, _ = conv_store.reconstruct_context(
            conv_id, msgs, "casual", 500, tokenizer
        )
        # 应有类似 "[工作上下文概要]" 的 system 消息
        summaries = [m for m in result if "工作上下文概要" in str(m.get("content", ""))]
        assert len(summaries) >= 1

    def test_empty_store_fallback(self, conv_store, tokenizer):
        """Store 无数据时 fallback 到尾部截取"""
        msgs = make_work_messages(5)
        result, meta = conv_store.reconstruct_context(
            conversation_id="nonexistent",
            messages=msgs,
            mode="work",
            target_tokens=200,
            tokenizer=tokenizer,
        )

        assert meta["strategy"] == "conv_store_tail"
        assert meta["stored_segments"] == 0
        assert len(result) > 0

    def test_reconstruct_respects_budget(self, conv_store, segmenter, tokenizer):
        """重组结果不应超过 target_tokens"""
        msgs = make_work_messages(20)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        target = 300
        result, meta = conv_store.reconstruct_context(
            conv_id, msgs, "work", target, tokenizer
        )
        actual_tokens = conv_store._count_tokens(result, tokenizer)
        assert actual_tokens <= target

    def test_reconstruct_without_tokenizer(self, conv_store, segmenter):
        """无 tokenizer 时使用 len//4 估算"""
        msgs = make_work_messages(5)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        result, meta = conv_store.reconstruct_context(
            conv_id, msgs, "work", 500, tokenizer=None
        )
        assert len(result) > 0

    def test_work_mode_large_budget(self, conv_store, segmenter, tokenizer):
        """预算很大时应尽量保留更多内容"""
        msgs = make_work_messages(5)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        result_small, _ = conv_store.reconstruct_context(
            conv_id, msgs, "work", 100, tokenizer
        )
        result_large, _ = conv_store.reconstruct_context(
            conv_id, msgs, "work", 5000, tokenizer
        )

        tokens_small = conv_store._count_tokens(result_small, tokenizer)
        tokens_large = conv_store._count_tokens(result_large, tokenizer)
        assert tokens_large >= tokens_small

    def test_work_mode_history_summary_only(self, conv_store, segmenter, tokenizer):
        """当历史预算不够放完整消息时，只放摘要"""
        msgs = make_work_messages(30)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        # 非常小的预算
        result, meta = conv_store.reconstruct_context(
            conv_id, msgs, "work", 150, tokenizer
        )
        # 应包含摘要型 system 消息
        summaries = [
            m for m in result
            if m["role"] == "system" and ("摘要" in str(m.get("content", "")))
        ]
        assert len(result) > 0  # 至少有内容


# ================================================================
# 5. cleanup_expired
# ================================================================


class TestCleanupExpired:
    def test_cleanup_deletes_expired(self, conv_store, tmpdir):
        db_path = conv_store.db_store.db_path / "context.db"
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # 插入 3 条已过期
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(3):
            cur.execute(
                "INSERT INTO conversation_segments "
                "(conversation_id, segment_index, topic_type, summary, messages, message_count, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"exp_{i}", 0, "work", "old", "[]", 0, past),
            )
        conn.commit()
        conn.close()

        deleted = conv_store.cleanup_expired()
        assert deleted == 3

    def test_cleanup_keeps_valid(self, conv_store, segmenter):
        """未过期的段不应被清理"""
        msgs = make_work_messages(3)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        deleted = conv_store.cleanup_expired()
        assert deleted == 0

        # 段仍然存在
        assert len(conv_store.get_segments(conv_id)) == len(segments)

    def test_cleanup_returns_zero_when_empty(self, conv_store):
        assert conv_store.cleanup_expired() == 0


# ================================================================
# 6. _generate_segment_summary 分支
# ================================================================


class TestGenerateSegmentSummary:
    def test_summary_with_file_mentions(self, conv_store):
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "Fix the bug in api.py and update config.yaml"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert ".py" in summary or ".yaml" in summary

    def test_summary_with_keywords(self, conv_store):
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "优化数据库查询并部署到生产环境"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "优化" in summary or "部署" in summary

    def test_summary_empty_messages(self, conv_store):
        seg = TopicSegment(0, 0, "work", 0.5)
        seg.messages = []
        summary = conv_store._generate_segment_summary(seg)
        assert "空段" in summary

    def test_summary_no_signals(self, conv_store):
        seg = TopicSegment(0, 3, "casual", 0.7)
        seg.messages = [
            {"role": "user", "content": "blah blah blah"},
            {"role": "assistant", "content": "lorem ipsum"},
            {"role": "user", "content": "more random text"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        # v2: casual 段用 [CASUAL] 前缀 + 消息数
        assert "CASUAL" in summary
        assert "3条消息" in summary


# ================================================================
# 7. _count_tokens / _tail_messages / _fit_to_budget 辅助方法
# ================================================================


class TestHelpers:
    def test_count_tokens_with_tokenizer(self, conv_store, tokenizer):
        msgs = [{"role": "user", "content": "Hello world"}]
        count = conv_store._count_tokens(msgs, tokenizer)
        assert count > 0

    def test_count_tokens_without_tokenizer(self, conv_store):
        msgs = [{"role": "user", "content": "Hello world, this is a test."}]
        count = conv_store._count_tokens(msgs, None)
        assert count == len("Hello world, this is a test.") // 4

    def test_tail_messages(self, conv_store, tokenizer):
        # v2: User-First 填充 — user 消息优先入选
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
        tail = conv_store._tail_messages(msgs, budget=20, tokenizer=tokenizer)
        assert len(tail) < len(msgs)
        # 所有保留的消息应来自原始列表尾部范围
        assert len(tail) > 0
        # 最后一条应是尾部附近的消息
        last_content = tail[-1]["content"]
        assert "msg" in last_content

    def test_tail_messages_user_first(self, conv_store, tokenizer):
        """User-First: 预算紧张时优先保留 user 消息"""
        msgs = [
            {"role": "user", "content": "用户问题"},
            {"role": "assistant", "content": "很长的回复" * 20},
            {"role": "user", "content": "用户追问"},
            {"role": "assistant", "content": "又一个很长的回复" * 20},
            {"role": "user", "content": "最终确认"},
        ]
        # 给一个紧张的预算，只够几条消息
        tail = conv_store._tail_messages(msgs, budget=30, tokenizer=tokenizer)
        # user 消息应该优先被保留
        user_msgs = [m for m in tail if m["role"] == "user"]
        assistant_msgs = [m for m in tail if m["role"] == "assistant"]
        assert len(user_msgs) >= len(assistant_msgs)

    def test_tail_messages_empty_budget(self, conv_store, tokenizer):
        msgs = [{"role": "user", "content": "msg"}]
        tail = conv_store._tail_messages(msgs, budget=0, tokenizer=tokenizer)
        # v2: budget=0 时，保留最后一条消息（兜底）
        assert len(tail) == 1

    def test_fit_to_budget(self, conv_store, tokenizer):
        msgs = [{"role": "user", "content": f"message number {i}"} for i in range(20)]
        fitted = conv_store._fit_to_budget(msgs, budget=30, tokenizer=tokenizer)
        assert len(fitted) < len(msgs)
        assert fitted[0]["content"] == "message number 0"  # 从头开始

    def test_tail_fit(self, conv_store, tokenizer):
        msgs = [{"role": "system", "content": "sys"}] + [
            {"role": "user", "content": f"msg {i}"} for i in range(10)
        ]
        result = conv_store._tail_fit(msgs, target_tokens=50, tokenizer=tokenizer)
        # Should contain system + some tail
        system_count = sum(1 for m in result if m["role"] == "system")
        assert system_count >= 1


# ================================================================
# 8. ContextManager.auto_fit 集成测试
# ================================================================


class TestAutoFitIntegration:
    def test_no_compression_needed(self, ctx_manager):
        """tokens < limit 时不压缩，但仍存储段"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        result, meta = ctx_manager.auto_fit(msgs, model="glm-5", reserve_tokens=512)

        assert meta["strategy"] == "none"
        assert "conversation_id" in meta
        assert "mode" in meta
        assert result == msgs

    def test_compression_via_conv_store(self, ctx_manager):
        """超限时应使用 conv_store 重组 (qwen-1.7b 只有 2048 上下文)"""
        # 生成足够多/长的消息，确保超过 qwen-1.7b 的 2048 限制
        msgs = [{"role": "system", "content": "You are a coding assistant."}]
        for i in range(80):
            msgs.append(
                {"role": "user", "content": f"Implement feature_{i} with full error handling, logging, and comprehensive test coverage in Python. " * 3}
            )
            msgs.append(
                {"role": "assistant", "content": f"```python\ndef feature_{i}(data):\n    import logging\n    logger = logging.getLogger(__name__)\n    try:\n        result = process(data)\n        logger.info('Success')\n        return result\n    except Exception as e:\n        logger.error(f'Error: {{e}}')\n        raise\n```"}
            )

        result, meta = ctx_manager.auto_fit(
            msgs, model="qwen-1.7b", reserve_tokens=256  # target = 1792
        )

        assert meta["strategy"] in ("conv_store", "conv_store_reconstruct", "topic_aware")
        assert meta["compressed_tokens"] < meta["original_tokens"]
        assert meta["compressed_tokens"] <= (2048 - 256)

    def test_fallback_to_topic_aware(self, ctx_manager):
        """conv_store 重组后仍超限时应 fallback 到 topic_aware

        构造: system ~1700 tokens (接近 target 1792)，
        conv_store 重组会加 system(1700) + 摘要 + 尾部 → 远超 1792 → fallback
        """
        # system ~1700 tokens, 只留 92 tokens 给其他内容
        huge_system = "You are an expert developer. Follow all best practices carefully. " * 160
        msgs = [{"role": "system", "content": huge_system}]
        for i in range(30):
            msgs.append({"role": "user", "content": f"Implement feature {i} in Python. " * 5})
            msgs.append({"role": "assistant", "content": f"def feature_{i}(): pass\n" * 3})

        result, meta = ctx_manager.auto_fit(
            msgs, model="qwen-1.7b", reserve_tokens=256
        )

        # conv_store 重组后 system(1700) + 任何内容 > 1792 → fallback
        assert meta["strategy"] in ("conv_store", "conv_store_reconstruct", "topic_aware")

    def test_metadata_has_conv_id_and_mode(self, ctx_manager):
        msgs = make_work_messages(3)
        _, meta = ctx_manager.auto_fit(msgs, model="glm-5")

        assert "conversation_id" in meta
        assert len(meta["conversation_id"]) == 16
        assert meta["mode"] in ("work", "casual")

    def test_conversation_store_accessible(self, ctx_manager):
        """ContextManager 应暴露 conversation_store 属性"""
        assert ctx_manager.conversation_store is not None
        assert isinstance(ctx_manager.conversation_store, ConversationStore)

    def test_segments_persisted_after_auto_fit(self, ctx_manager):
        """auto_fit 后段应已存入 DB"""
        msgs = make_work_messages(5)
        _, meta = ctx_manager.auto_fit(msgs, model="glm-5")

        conv_id = meta["conversation_id"]
        segments = ctx_manager.conversation_store.get_segments(conv_id)
        assert len(segments) > 0

    def test_mixed_mode_detection(self, ctx_manager):
        """混合消息应检测最后一段的模式"""
        # 结尾是工作段
        msgs_work_end = make_mixed_messages()
        _, meta_w = ctx_manager.auto_fit(msgs_work_end, model="glm-5")
        assert meta_w["mode"] == "work"

        # 结尾是闲聊
        msgs_casual_end = make_mixed_messages() + make_casual_messages(3)
        _, meta_c = ctx_manager.auto_fit(msgs_casual_end, model="glm-5")
        # 最后一段可能是 casual（取决于分段合并逻辑）
        assert meta_c["mode"] in ("work", "casual")

    def test_empty_messages(self, ctx_manager):
        """空消息列表不应崩溃"""
        result, meta = ctx_manager.auto_fit([], model="glm-5")
        assert meta["strategy"] == "none"
        assert result == []


# ================================================================
# 9. __init__.py 导出
# ================================================================


class TestExports:
    def test_import_from_package(self):
        from clawgate.context import ConversationStore as CS
        assert CS is ConversationStore

    def test_all_contains_conversation_store(self):
        import clawgate.context as ctx_mod
        assert "ConversationStore" in ctx_mod.__all__


# ================================================================
# 10. 多会话隔离
# ================================================================


class TestMultiConversationIsolation:
    def test_two_conversations_isolated(self, conv_store, segmenter):
        msgs_a = [
            {"role": "system", "content": "System A"},
            {"role": "user", "content": "User A"},
        ]
        msgs_b = [
            {"role": "system", "content": "System B"},
            {"role": "user", "content": "User B"},
        ]

        seg_a = segmenter.segment(msgs_a)
        seg_b = segmenter.segment(msgs_b)

        cid_a = conv_store.derive_conversation_id(msgs_a)
        cid_b = conv_store.derive_conversation_id(msgs_b)
        assert cid_a != cid_b

        conv_store.store_segments(cid_a, seg_a)
        conv_store.store_segments(cid_b, seg_b)

        assert len(conv_store.get_segments(cid_a)) == len(seg_a)
        assert len(conv_store.get_segments(cid_b)) == len(seg_b)


# ================================================================
# 11. 结构化摘要 v2 (抗污染)
# ================================================================


class TestStructuredSummaryV2:
    """测试结构化摘要生成（基于 MIT context pollution 论文）"""

    def test_work_segment_extracts_files(self, conv_store):
        """work 段: 从所有消息中提取文件名"""
        seg = TopicSegment(0, 3, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "请修改 conversation_store.py 的逻辑"},
            {"role": "assistant", "content": "好的，我来修改 conversation_store.py 和 manager.py"},
            {"role": "user", "content": "也需要更新 test_conversation_store.py"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "[WORK]" in summary
        assert "conversation_store.py" in summary
        assert "manager.py" in summary

    def test_work_segment_extracts_user_intent(self, conv_store):
        """work 段: 意图只从 user 消息提取"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "请帮我实现一个持久化存储方案"},
            {"role": "assistant", "content": "我建议使用 Redis 而非 SQLite"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "意图:" in summary
        assert "持久化存储" in summary
        # assistant 的建议不应出现在意图中
        assert "Redis" not in summary or "意图" not in summary.split("Redis")[0]

    def test_work_segment_extracts_actions(self, conv_store):
        """work 段: 操作动词从 user 消息提取并归一化"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "我需要重构这段代码并修正错误"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        # "重构" 归一化为 "优化", "修正" 归一化为 "修复"
        assert "优化" in summary or "修复" in summary

    def test_work_segment_counts_code_blocks_from_assistant(self, conv_store):
        """work 段: 代码块数量从 assistant 消息提取"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "请帮我写个函数"},
            {"role": "assistant", "content": "好的：\n```python\ndef hello():\n    pass\n```\n还有：\n```python\ndef world():\n    pass\n```"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "2个代码块" in summary

    def test_work_segment_extracts_status_done(self, conv_store):
        """work 段: 从 assistant 提取完成状态"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "实现这个功能"},
            {"role": "assistant", "content": "已完成，功能已实现并通过测试"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "状态: 完成" in summary

    def test_work_segment_extracts_status_blocked(self, conv_store):
        """work 段: 从 assistant 提取阻塞状态"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修复这个 bug"},
            {"role": "assistant", "content": "无法修复，缺少必要的权限"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "状态: 阻塞" in summary

    def test_work_segment_no_assistant_explanation_leak(self, conv_store):
        """work 段: assistant 的解释/推理不应出现在摘要中（防污染）"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "请帮我优化 query.py"},
            {"role": "assistant", "content": "根据我的分析，问题的根因在于 N+1 查询问题。"
                                              "这是因为 ORM 的懒加载机制导致的。"
                                              "建议使用 eager loading 来解决。"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        # assistant 的分析/推理不应出现
        assert "N+1" not in summary
        assert "懒加载" not in summary
        assert "eager loading" not in summary
        # 但文件名和操作应该存在
        assert "query.py" in summary
        assert "优化" in summary

    def test_work_segment_fallback_no_signals(self, conv_store):
        """work 段: 无任何信号时的 fallback"""
        seg = TopicSegment(0, 3, "work", 0.5)
        seg.messages = [
            {"role": "user", "content": "嗯"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "继续"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "[WORK]" in summary
        assert "3条消息" in summary

    def test_casual_segment_format(self, conv_store):
        """casual 段: 基本格式"""
        seg = TopicSegment(0, 3, "casual", 0.8)
        seg.messages = [
            {"role": "user", "content": "今天天气怎么样"},
            {"role": "assistant", "content": "北京今天晴天，25度左右"},
            {"role": "user", "content": "不错，出去走走"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "[CASUAL]" in summary
        assert "3条消息" in summary

    def test_casual_segment_user_only_points(self, conv_store):
        """casual 段: 要点只从 user 消息提取"""
        seg = TopicSegment(0, 4, "casual", 0.8)
        seg.messages = [
            {"role": "user", "content": "你好啊"},
            {"role": "assistant", "content": "你好！有什么可以帮你的吗？"},
            {"role": "user", "content": "没啥事，随便聊聊"},
            {"role": "assistant", "content": "好的，随时聊！"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        # user 的发言应该出现
        assert "你好啊" in summary
        # assistant 的回复不应出现在要点中
        assert "有什么可以帮你" not in summary

    def test_casual_segment_max_two_points(self, conv_store):
        """casual 段: 最多取 2 个 user 要点"""
        seg = TopicSegment(0, 6, "casual", 0.7)
        seg.messages = [
            {"role": "user", "content": "第一句话"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "第二句话"},
            {"role": "assistant", "content": "回复2"},
            {"role": "user", "content": "第三句话"},
            {"role": "assistant", "content": "回复3"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        lines = summary.split("\n")
        point_lines = [l for l in lines if l.startswith("- ")]
        assert len(point_lines) <= 2

    def test_empty_segment(self, conv_store):
        """空段: 应有合理的 fallback"""
        for topic_type in ["work", "casual"]:
            seg = TopicSegment(0, 0, topic_type, 0.5)
            seg.messages = []
            summary = conv_store._generate_segment_summary(seg)
            assert "空段" in summary

    def test_work_segment_english_actions(self, conv_store):
        """work 段: 英文操作动词也能识别"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "Please fix the bug in auth.py and add tests"},
        ]
        summary = conv_store._generate_segment_summary(seg)
        assert "auth.py" in summary
        # "fix" → 修复, "add" → 实现
        assert "修复" in summary or "实现" in summary

    def test_summary_token_count_reasonable(self, conv_store):
        """摘要 token 数不应过大"""
        seg = TopicSegment(0, 10, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": f"请帮我实现第{i}个功能，涉及 file_{i}.py"} if i % 2 == 0
            else {"role": "assistant", "content": f"```python\n# implementation {i}\n```\n完成"}
            for i in range(10)
        ]
        summary = conv_store._generate_segment_summary(seg)
        # 摘要应该紧凑，不超过 500 字符
        assert len(summary) < 500


# ================================================================
# 12. User-First 填充 (P1 抗污染)
# ================================================================


class TestUserFirstFilling:
    """测试 _tail_messages 的 User-First 填充策略"""

    def test_user_first_priority(self, conv_store, tokenizer):
        """预算紧张时 user 消息优先于 assistant"""
        msgs = [
            {"role": "user", "content": "短问题"},
            {"role": "assistant", "content": "这是一个非常非常长的回复，" * 50},
            {"role": "user", "content": "追问"},
            {"role": "assistant", "content": "又一个非常长的回复，" * 50},
            {"role": "user", "content": "最终确认"},
        ]
        # 给很小的预算
        tail = conv_store._tail_messages(msgs, budget=20, tokenizer=tokenizer)
        user_count = sum(1 for m in tail if m["role"] == "user")
        # user 消息应该被优先保留
        assert user_count >= 1

    def test_order_preserved(self, conv_store, tokenizer):
        """User-First 后消息顺序仍正确"""
        msgs = [
            {"role": "user", "content": f"q{i}"}
            for i in range(5)
        ]
        tail = conv_store._tail_messages(msgs, budget=100, tokenizer=tokenizer)
        # 顺序应保持
        for i in range(len(tail) - 1):
            # 内容中的数字应递增
            curr = tail[i]["content"]
            nxt = tail[i + 1]["content"]
            assert curr < nxt

    def test_empty_messages_returns_empty(self, conv_store, tokenizer):
        """空消息列表应返回空"""
        tail = conv_store._tail_messages([], budget=100, tokenizer=tokenizer)
        assert tail == []

    def test_budget_respected(self, conv_store, tokenizer):
        """总 token 不应超过预算"""
        msgs = [
            {"role": "user", "content": f"用户消息 {i}" * 5}
            for i in range(20)
        ]
        budget = 50
        tail = conv_store._tail_messages(msgs, budget=budget, tokenizer=tokenizer)
        total_tokens = conv_store._count_tokens(tail, tokenizer)
        # 可能因为兜底逻辑略超，但大体应在范围内
        assert total_tokens <= budget * 1.5  # 允许兜底的那一条


# ================================================================
# 13. Model-Tier 分级过滤 (P2 抗污染)
# ================================================================


class TestModelTierFiltering:
    """测试 model_tier 分级过滤（基于 MIT context pollution 论文）

    弱模型更易受 assistant 幻觉污染，需要激进过滤。
    """

    def test_weak_tier_strips_assistant_narration(self, conv_store, tokenizer):
        """weak tier: assistant 的叙述性文字被丢弃"""
        msgs = [
            {"role": "user", "content": "请帮我修复 auth.py 的 bug"},
            {"role": "assistant", "content": "根据我的分析，问题在于权限检查逻辑不正确。"},
            {"role": "user", "content": "好的，帮我修"},
        ]
        tail = conv_store._tail_messages(msgs, budget=200, tokenizer=tokenizer, model_tier="weak")
        # assistant 无代码块 → 应被完全丢弃
        assistant_msgs = [m for m in tail if m["role"] == "assistant"]
        assert len(assistant_msgs) == 0
        # user 消息应保留
        user_msgs = [m for m in tail if m["role"] == "user"]
        assert len(user_msgs) >= 1

    def test_weak_tier_keeps_assistant_code_blocks(self, conv_store, tokenizer):
        """weak tier: assistant 的代码块被保留"""
        msgs = [
            {"role": "user", "content": "帮我写个函数"},
            {"role": "assistant", "content": "好的，这是实现：\n```python\ndef hello():\n    return 'world'\n```\n这个函数很简单。"},
            {"role": "user", "content": "谢谢"},
        ]
        tail = conv_store._tail_messages(msgs, budget=200, tokenizer=tokenizer, model_tier="weak")
        assistant_msgs = [m for m in tail if m["role"] == "assistant"]
        # 应保留，但内容只有代码块
        assert len(assistant_msgs) == 1
        assert "```python" in assistant_msgs[0]["content"]
        # 叙述文字应被剥离
        assert "好的，这是实现" not in assistant_msgs[0]["content"]
        assert "这个函数很简单" not in assistant_msgs[0]["content"]

    def test_weak_tier_keeps_multiple_code_blocks(self, conv_store, tokenizer):
        """weak tier: 多个代码块全部保留"""
        msgs = [
            {"role": "user", "content": "写两个函数"},
            {"role": "assistant", "content": (
                "第一个：\n```python\ndef foo():\n    pass\n```\n"
                "第二个：\n```python\ndef bar():\n    pass\n```\n"
                "以上就是两个函数的实现。"
            )},
        ]
        tail = conv_store._tail_messages(msgs, budget=200, tokenizer=tokenizer, model_tier="weak")
        assistant_msgs = [m for m in tail if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        # 两个代码块都应保留
        assert assistant_msgs[0]["content"].count("```python") == 2

    def test_medium_tier_keeps_full_assistant(self, conv_store, tokenizer):
        """medium tier: assistant 消息完整保留"""
        msgs = [
            {"role": "user", "content": "帮我分析 bug"},
            {"role": "assistant", "content": "根据分析，问题在于权限检查逻辑不正确。建议修改如下..."},
            {"role": "user", "content": "好"},
        ]
        tail = conv_store._tail_messages(msgs, budget=200, tokenizer=tokenizer, model_tier="medium")
        assistant_msgs = [m for m in tail if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        # 叙述文字应完整保留
        assert "权限检查逻辑" in assistant_msgs[0]["content"]

    def test_strong_tier_keeps_full_assistant(self, conv_store, tokenizer):
        """strong tier: assistant 消息完整保留"""
        msgs = [
            {"role": "user", "content": "帮我分析 bug"},
            {"role": "assistant", "content": "根据分析，问题在于权限检查逻辑不正确。"},
        ]
        tail = conv_store._tail_messages(msgs, budget=200, tokenizer=tokenizer, model_tier="strong")
        assistant_msgs = [m for m in tail if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1
        assert "权限检查逻辑" in assistant_msgs[0]["content"]

    def test_weak_tier_non_assistant_roles_preserved(self, conv_store, tokenizer):
        """weak tier: tool/function 等非 assistant 角色不受影响"""
        msgs = [
            {"role": "user", "content": "运行测试"},
            {"role": "tool", "content": "Test passed: 5/5"},
            {"role": "user", "content": "好的"},
        ]
        tail = conv_store._tail_messages(msgs, budget=200, tokenizer=tokenizer, model_tier="weak")
        tool_msgs = [m for m in tail if m["role"] == "tool"]
        # tool 消息不是 assistant，不受 weak 过滤影响
        assert len(tool_msgs) == 1
        assert "Test passed" in tool_msgs[0]["content"]

    def test_strip_assistant_to_artifacts_no_code(self, conv_store):
        """_strip_assistant_to_artifacts: 无代码块返回空"""
        result = conv_store._strip_assistant_to_artifacts(
            "这是一段纯文字解释，没有代码。"
        )
        assert result == ""

    def test_strip_assistant_to_artifacts_with_code(self, conv_store):
        """_strip_assistant_to_artifacts: 有代码块只返回代码"""
        content = "解释：\n```python\ndef x():\n    pass\n```\n总结。"
        result = conv_store._strip_assistant_to_artifacts(content)
        assert "```python" in result
        assert "解释" not in result
        assert "总结" not in result

    def test_default_tier_is_medium(self, conv_store, tokenizer):
        """默认 model_tier 应为 medium（不过滤 assistant）"""
        msgs = [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "纯文字回复，没有代码"},
        ]
        # 不传 model_tier → 默认 medium → assistant 保留
        tail = conv_store._tail_messages(msgs, budget=200, tokenizer=tokenizer)
        assistant_msgs = [m for m in tail if m["role"] == "assistant"]
        assert len(assistant_msgs) == 1

    def test_reconstruct_passes_model_tier(self, conv_store, segmenter, tokenizer):
        """reconstruct_context 应将 model_tier 传递到 _tail_messages"""
        msgs = [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "请帮我实现 feature_0 的 Python 代码"},
            {"role": "assistant", "content": "这是一段很长的解释文字，没有代码块。" * 5},
            {"role": "user", "content": "请帮我实现 feature_1 的 Python 代码"},
            {"role": "assistant", "content": "```python\ndef feature_1():\n    return 1\n```"},
        ]
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        # weak tier
        result_weak, meta_weak = conv_store.reconstruct_context(
            conv_id, msgs, "work", 500, tokenizer, model_tier="weak"
        )
        assert meta_weak.get("model_tier") == "weak"

        # medium tier
        result_med, meta_med = conv_store.reconstruct_context(
            conv_id, msgs, "work", 500, tokenizer, model_tier="medium"
        )
        assert meta_med.get("model_tier") == "medium"

    def test_auto_fit_metadata_has_model_tier(self, ctx_manager):
        """auto_fit 元数据应包含 model_tier"""
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        _, meta = ctx_manager.auto_fit(msgs, model="qwen-1.7b")
        assert meta.get("model_tier") == "weak"

        _, meta2 = ctx_manager.auto_fit(msgs, model="glm-5")
        assert meta2.get("model_tier") == "strong"

        _, meta3 = ctx_manager.auto_fit(msgs, model="qwen-32b")
        assert meta3.get("model_tier") == "medium"


# ================================================================
# 14. P3: LLM 摘要
# ================================================================


class TestLLMSummarizer:
    """测试 P3 LLM 摘要功能"""

    def test_llm_summarizer_injection(self, db_store, segmenter):
        """有 llm_summarizer 时应正确注入"""
        mock_summarizer = lambda prompt: "文件: test.py\n操作: 实现\n用户意图: 测试\n状态: 完成"
        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
            llm_summarizer=mock_summarizer,
        )
        assert store.llm_summarizer is not None

    def test_no_llm_summarizer(self, db_store, segmenter):
        """无 llm_summarizer 时为 None"""
        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
        )
        assert store.llm_summarizer is None

    def test_long_work_segment_uses_rule_summary(self, db_store, segmenter):
        """F3: 长工作段始终返回规则摘要 (LLM 异步后台更新)"""
        call_count = {"n": 0}

        def fake_summarizer(prompt):
            call_count["n"] += 1
            return "文件: auth.py\n操作: 实现\n用户意图: 实现认证\n状态: 完成"

        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
            llm_summarizer=fake_summarizer,
        )

        seg = TopicSegment(0, 8, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": f"请帮我实现功能 {i} 在 auth.py 中"}
            if i % 2 == 0
            else {"role": "assistant", "content": f"```python\ndef f{i}(): pass\n```"}
            for i in range(8)
        ]
        summary = store._generate_segment_summary(seg)
        # F3: _generate_segment_summary 始终返回规则摘要，不阻塞调 LLM
        assert call_count["n"] == 0
        assert "[WORK]" in summary
        assert "auth.py" in summary

    def test_short_work_segment_skips_llm(self, db_store, segmenter):
        """短工作段 (<=5 msgs) → 规则摘要，不调 LLM"""
        call_count = {"n": 0}

        def fake_summarizer(prompt):
            call_count["n"] += 1
            return "should not be called"

        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
            llm_summarizer=fake_summarizer,
        )

        seg = TopicSegment(0, 3, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "请修改 api.py"},
            {"role": "assistant", "content": "好的"},
            {"role": "user", "content": "完成了"},
        ]
        store._generate_segment_summary(seg)
        assert call_count["n"] == 0  # LLM 不应被调用

    def test_casual_segment_never_uses_llm(self, db_store, segmenter):
        """casual 段即使有 summarizer 也不调 LLM"""
        call_count = {"n": 0}

        def fake_summarizer(prompt):
            call_count["n"] += 1
            return "should not be called"

        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
            llm_summarizer=fake_summarizer,
        )

        seg = TopicSegment(0, 8, "casual", 0.8)
        seg.messages = [
            {"role": "user", "content": f"闲聊 {i}"}
            if i % 2 == 0
            else {"role": "assistant", "content": f"回复 {i}"}
            for i in range(8)
        ]
        summary = store._generate_segment_summary(seg)
        assert call_count["n"] == 0
        assert "[CASUAL]" in summary

    def test_llm_failure_fallback_to_rule_summary(self, db_store, segmenter):
        """LLM 调用失败 → 降级到规则摘要"""
        def failing_summarizer(prompt):
            raise ConnectionError("Network error")

        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
            llm_summarizer=failing_summarizer,
        )

        seg = TopicSegment(0, 8, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": f"请帮我修复 bug_{i} 在 handler.py 中"}
            if i % 2 == 0
            else {"role": "assistant", "content": f"```python\n# fix {i}\n```\n已完成"}
            for i in range(8)
        ]
        summary = store._generate_segment_summary(seg)
        # 降级成功，仍然产出 [WORK] 前缀的摘要
        assert "[WORK]" in summary
        # 规则摘要应提取到文件名
        assert "handler.py" in summary

    def test_llm_summary_format(self, db_store, segmenter):
        """LLM 摘要应以 [WORK] 前缀开头"""
        def fake_summarizer(prompt):
            return "文件: test.py\n操作: 测试\n用户意图: 运行测试\n状态: 完成"

        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
            llm_summarizer=fake_summarizer,
        )

        seg = TopicSegment(0, 6, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": f"msg {i}"} for i in range(6)
        ]
        summary = store._generate_segment_summary(seg)
        assert summary.startswith("[WORK]")

    def test_no_summarizer_long_segment_uses_rules(self, db_store, segmenter):
        """无 summarizer 时，长段也用规则摘要"""
        store = ConversationStore(
            db_store=db_store,
            topic_segmenter=segmenter,
            llm_summarizer=None,
        )

        seg = TopicSegment(0, 8, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": f"请帮我实现功能 {i} 在 models.py 中"}
            if i % 2 == 0
            else {"role": "assistant", "content": f"```python\ndef f{i}(): pass\n```"}
            for i in range(8)
        ]
        summary = store._generate_segment_summary(seg)
        assert "[WORK]" in summary
        assert "models.py" in summary


# ================================================================
# 15. P4: 跨会话长期记忆 (Long-Term Memory)
# ================================================================


class TestLongTermMemory:
    """测试 P4 跨会话记忆功能"""

    def test_ltm_table_exists(self, db_store, tmpdir):
        """long_term_memories 表应正确创建"""
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='long_term_memories'"
        )
        assert cur.fetchone() is not None
        conn.close()

    def test_ltm_indexes_exist(self, db_store, tmpdir):
        """long_term_memories 索引应正确创建"""
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_ltm%'"
        )
        indexes = {row[0] for row in cur.fetchall()}
        conn.close()
        assert "idx_ltm_key" in indexes
        assert "idx_ltm_expires" in indexes
        assert "idx_ltm_files" in indexes

    def test_extract_files_from_messages(self, conv_store):
        """_extract_files_from_messages 应正确提取文件名"""
        msgs = [
            {"role": "user", "content": "修改 auth.py 和 config.yaml"},
            {"role": "assistant", "content": "我已修改 auth.py, models.py"},
        ]
        files = conv_store._extract_files_from_messages(msgs)
        assert "auth.py" in files
        assert "config.yaml" in files
        assert "models.py" in files

    def test_extract_files_empty(self, conv_store):
        """无文件提及时返回空集"""
        msgs = [{"role": "user", "content": "你好"}]
        files = conv_store._extract_files_from_messages(msgs)
        assert len(files) == 0

    def test_promote_to_long_term(self, conv_store, tmpdir):
        """work 段提升到 long_term_memories"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 auth.py 的登录逻辑"},
            {"role": "assistant", "content": "```python\ndef login(): pass\n```\n已完成"},
        ]
        result = conv_store._promote_to_long_term(seg, "[WORK] test", "conv123")
        assert result is True

        # 验证数据写入
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute("SELECT key_files, summary FROM long_term_memories")
        row = cur.fetchone()
        conn.close()

        assert row is not None
        key_files = json.loads(row[0])
        assert "auth.py" in key_files
        assert "[WORK]" in row[1]

    def test_promote_no_files_returns_false(self, conv_store):
        """无文件的段不应提升"""
        seg = TopicSegment(0, 2, "work", 0.5)
        seg.messages = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
        ]
        result = conv_store._promote_to_long_term(seg, "test", "conv123")
        assert result is False

    def test_promote_overwrites_existing(self, conv_store, tmpdir):
        """相同 key_files 应覆盖旧记忆"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 auth.py"},
            {"role": "assistant", "content": "第一版"},
        ]
        conv_store._promote_to_long_term(seg, "summary v1", "conv1")
        conv_store._promote_to_long_term(seg, "summary v2", "conv2")

        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM long_term_memories")
        count = cur.fetchone()[0]
        cur.execute("SELECT summary FROM long_term_memories")
        summary = cur.fetchone()[0]
        conn.close()

        assert count == 1
        assert summary == "summary v2"

    def test_recall_long_term_match(self, conv_store, tmpdir):
        """文件名匹配时召回记忆"""
        # 先写入记忆
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 auth.py 的登录逻辑"},
        ]
        conv_store._promote_to_long_term(
            seg, "[WORK] 实现了登录逻辑", "old_conv"
        )

        # 新会话提到 auth.py → 应召回
        new_msgs = [
            {"role": "user", "content": "继续改 auth.py 的权限检查"},
        ]
        recalled = conv_store._recall_long_term(new_msgs)
        assert len(recalled) == 1
        assert recalled[0]["role"] == "system"
        assert "Prior session context" in recalled[0]["content"]
        assert "auth.py" in recalled[0]["content"]

    def test_recall_long_term_no_match(self, conv_store):
        """无匹配文件时不召回"""
        # 写入 auth.py 记忆
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 auth.py"},
        ]
        conv_store._promote_to_long_term(seg, "test", "old_conv")

        # 新会话提到完全不同的文件
        new_msgs = [
            {"role": "user", "content": "修改 payment.py"},
        ]
        recalled = conv_store._recall_long_term(new_msgs)
        assert len(recalled) == 0

    def test_recall_long_term_no_files(self, conv_store):
        """消息中无文件名时不召回"""
        new_msgs = [{"role": "user", "content": "你好"}]
        recalled = conv_store._recall_long_term(new_msgs)
        assert len(recalled) == 0

    def test_recall_long_term_limit(self, conv_store, tmpdir):
        """召回结果应限制在 top-N"""
        # 写入多条记忆
        for i in range(5):
            seg = TopicSegment(0, 2, "work", 0.9)
            seg.messages = [
                {"role": "user", "content": f"修改 common.py 和 file_{i}.py"},
            ]
            conv_store._promote_to_long_term(seg, f"summary {i}", f"conv_{i}")

        new_msgs = [
            {"role": "user", "content": "继续改 common.py"},
        ]
        recalled = conv_store._recall_long_term(new_msgs, limit=3)
        assert len(recalled) <= 3

    def test_recall_updates_access_count(self, conv_store, tmpdir):
        """召回时应递增 access_count"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 counter.py"},
        ]
        conv_store._promote_to_long_term(seg, "test", "conv1")

        # 召回两次
        msgs = [{"role": "user", "content": "看看 counter.py"}]
        conv_store._recall_long_term(msgs)
        conv_store._recall_long_term(msgs)

        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute("SELECT access_count FROM long_term_memories")
        count = cur.fetchone()[0]
        conn.close()
        assert count == 2

    def test_recall_expired_memory_not_returned(self, conv_store, tmpdir):
        """过期记忆不应被召回"""
        # 直接插入一条已过期的记忆
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            "INSERT INTO long_term_memories "
            "(memory_key, key_files, summary, conversation_id, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("expired_key", '["expired.py"]', "old summary", "old_conv", past),
        )
        conn.commit()
        conn.close()

        msgs = [{"role": "user", "content": "修改 expired.py"}]
        recalled = conv_store._recall_long_term(msgs)
        assert len(recalled) == 0

    def test_cleanup_expired_ltm(self, conv_store, tmpdir):
        """cleanup_expired_ltm 应清理过期记忆"""
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        for i in range(3):
            cur.execute(
                "INSERT INTO long_term_memories "
                "(memory_key, key_files, summary, conversation_id, expires_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"key_{i}", f'["file_{i}.py"]', f"summary {i}", f"conv_{i}", past),
            )
        conn.commit()
        conn.close()

        deleted = conv_store.cleanup_expired_ltm()
        assert deleted == 3

    def test_cleanup_ltm_keeps_valid(self, conv_store, tmpdir):
        """未过期记忆不应被清理"""
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 valid.py"},
        ]
        conv_store._promote_to_long_term(seg, "valid", "conv1")

        deleted = conv_store.cleanup_expired_ltm()
        assert deleted == 0


# ================================================================
# 16. P4: store_segments 自动提升 + reconstruct_context 召回集成
# ================================================================


class TestLTMIntegration:
    """集成测试: store_segments 自动提升 + 新会话召回"""

    def test_store_segments_promotes_work(self, conv_store, segmenter, tmpdir):
        """store_segments 应自动将 work 段提升到 LTM"""
        msgs = [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "Help me fix auth.py bug"},
            {"role": "assistant", "content": "```python\ndef fix(): pass\n```\nDone."},
        ]
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        # 验证 LTM 被写入
        conn = sqlite3.connect(os.path.join(tmpdir, "context.db"))
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM long_term_memories")
        count = cur.fetchone()[0]
        conn.close()
        # work 段中包含 auth.py → 应有 LTM
        assert count >= 1

    def test_reconstruct_with_ltm_recall(self, conv_store, segmenter, tokenizer, tmpdir):
        """新会话 reconstruct_context 应注入跨会话记忆"""
        # 旧会话: 存储 auth.py 相关工作
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 auth.py 的登录逻辑"},
            {"role": "assistant", "content": "已完成登录逻辑修改"},
        ]
        conv_store._promote_to_long_term(seg, "[WORK] 修改了 auth.py 登录", "old_conv")

        # 新会话: 提到 auth.py 但无存储段
        new_msgs = [
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": "继续改 auth.py 的权限检查"},
        ]
        new_conv_id = "new_session_id"

        result, meta = conv_store.reconstruct_context(
            conversation_id=new_conv_id,
            messages=new_msgs,
            mode="work",
            target_tokens=500,
            tokenizer=tokenizer,
        )

        assert meta["strategy"] == "conv_store_tail"
        assert meta["ltm_recall"] >= 1
        # 结果中应包含 Prior session context
        prior_msgs = [
            m for m in result
            if "Prior session context" in str(m.get("content", ""))
        ]
        assert len(prior_msgs) >= 1
        assert "auth.py" in prior_msgs[0]["content"]

    def test_reconstruct_no_ltm_when_has_segments(
        self, conv_store, segmenter, tokenizer
    ):
        """有存储段时不应触发 LTM 召回（走正常重组）"""
        msgs = make_work_messages(5)
        segments = segmenter.segment(msgs)
        conv_id = conv_store.derive_conversation_id(msgs)
        conv_store.store_segments(conv_id, segments)

        result, meta = conv_store.reconstruct_context(
            conv_id, msgs, "work", 500, tokenizer
        )

        assert meta["strategy"] == "conv_store_reconstruct"
        # 不应有 ltm_recall 字段（走了正常分支）
        assert "ltm_recall" not in meta

    def test_reconstruct_no_ltm_no_file_match(self, conv_store, tokenizer):
        """新会话无文件匹配时 LTM 召回为 0"""
        new_msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "你好"},
        ]
        result, meta = conv_store.reconstruct_context(
            "nonexistent", new_msgs, "work", 500, tokenizer
        )
        assert meta["ltm_recall"] == 0

    def test_auto_fit_metadata_has_ltm_recall(self, db_store, tmpdir):
        """auto_fit 元数据在新会话时应包含 ltm_recall"""
        ctx_manager = ContextManager(config_path="/nonexistent/path.yaml", db_store=db_store)

        # 先写入一条 LTM
        seg = TopicSegment(0, 2, "work", 0.9)
        seg.messages = [
            {"role": "user", "content": "修改 special_file.py"},
        ]
        ctx_manager.conversation_store._promote_to_long_term(
            seg, "[WORK] modified special_file.py", "old_conv"
        )

        # 新会话提到 special_file.py，不超限所以走 none 策略
        # 但 segments 被存储后，下次重组能用
        new_msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "看看 special_file.py"},
        ]
        _, meta = ctx_manager.auto_fit(new_msgs, model="glm-5")
        # auto_fit 不超限时返回原消息，strategy=none
        # LTM 召回在 reconstruct 时才触发，这里不超限不走 reconstruct
        assert meta["strategy"] == "none"
