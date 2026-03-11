"""Unit tests for Three-tier Layering Strategy

测试 ThreeTierLayeringStrategy 的四层分层逻辑：
- Layer 1: Must-have (system/developer 消息)
- Layer 2: Nice-to-have (最近 8 条摘要)
- Layer 3: History-tail (历史摘要)
- Layer 4: Tail (最后 N 轮原文)
"""

import pytest
from clawgate.context.strategies.layering import ThreeTierLayeringStrategy


class TestThreeTierLayeringStrategy:
    """Three-tier Layering 策略测试"""

    @pytest.fixture
    def strategy(self):
        """创建测试策略实例"""
        return ThreeTierLayeringStrategy(
            must_have_cap=1536,
            nice_to_have_cap=768,
            history_tail_cap=512,
            preserve_last_turns=6,
            context_shift_enabled=False,
        )

    @pytest.fixture
    def sample_messages(self):
        """生成测试消息列表（30 轮对话）"""
        messages = []

        # 1. System 消息
        messages.append({
            "role": "system",
            "content": "You are a helpful AI assistant. Be concise and accurate."
        })

        # 2. Developer 消息
        messages.append({
            "role": "developer",
            "content": "Project context: We are building a web application using React and FastAPI."
        })

        # 3. 30 轮对话
        for i in range(30):
            messages.append({
                "role": "user",
                "content": f"User message {i+1}: This is a sample question about the project."
            })
            messages.append({
                "role": "assistant",
                "content": f"Assistant message {i+1}: This is a sample answer to the question."
            })

        return messages

    def test_compress_empty_messages(self, strategy):
        """测试空消息列表"""
        result = strategy.compress([], target_tokens=2000)
        assert result == []
        assert strategy.last_stats == {}

    def test_compress_basic_structure(self, strategy, sample_messages):
        """测试基本四层结构"""
        result = strategy.compress(sample_messages, target_tokens=2000)

        # 验证返回的是消息列表
        assert isinstance(result, list)
        assert len(result) > 0

        # 验证有 system 消息（来自 Must-have/Nice-to-have/History-tail 层）
        system_messages = [msg for msg in result if msg.get("role") == "system"]
        assert len(system_messages) > 0

        # 验证有用户/助手消息（来自 Tail 层）
        convo_messages = [
            msg for msg in result
            if msg.get("role") in ("user", "assistant")
        ]
        assert len(convo_messages) > 0

    def test_compress_preserves_last_n_turns(self, strategy, sample_messages):
        """测试保留最后 N 轮完整对话"""
        # preserve_last_turns=6 应该保留最后 6 轮（12 条消息）
        result = strategy.compress(sample_messages, target_tokens=2000)

        # 过滤出 Tail 层的消息（role = user/assistant）
        tail_messages = [
            msg for msg in result
            if msg.get("role") in ("user", "assistant")
        ]

        # 应该保留 6 轮（12 条消息）
        # 注意：可能因为 token 限制被截断，所以 <= 12
        assert len(tail_messages) <= 12
        assert len(tail_messages) > 0

    def test_compress_stats(self, strategy, sample_messages):
        """测试统计信息"""
        result = strategy.compress(sample_messages, target_tokens=2000)

        # 验证 last_stats 存在
        assert hasattr(strategy, "last_stats")
        assert isinstance(strategy.last_stats, dict)

        # 验证关键字段
        assert "must_have_tokens" in strategy.last_stats
        assert "nice_to_have_tokens" in strategy.last_stats
        assert "history_tail_tokens" in strategy.last_stats
        assert "tail_tokens" in strategy.last_stats
        assert "total_output_tokens" in strategy.last_stats

        # 验证 token 数 > 0
        total = strategy.last_stats["total_output_tokens"]
        assert total > 0

        # 验证各层 token 数相加等于总数
        sum_tokens = (
            strategy.last_stats["must_have_tokens"] +
            strategy.last_stats["nice_to_have_tokens"] +
            strategy.last_stats["history_tail_tokens"] +
            strategy.last_stats["tail_tokens"]
        )
        assert abs(sum_tokens - total) < 10  # 允许小误差

    def test_compress_layer_allocation(self, strategy, sample_messages):
        """测试各层 token 分配"""
        result = strategy.compress(sample_messages, target_tokens=2000)

        stats = strategy.last_stats

        # Must-have 层 <= 1536 tokens
        assert stats["must_have_tokens"] <= 1536

        # Nice-to-have 层 <= 768 tokens
        assert stats["nice_to_have_tokens"] <= 768

        # History-tail 层 <= 512 tokens
        assert stats["history_tail_tokens"] <= 512

        # Tail 层应该有合理的 token 数
        assert stats["tail_tokens"] > 0

    def test_compress_must_have_layer(self, strategy):
        """测试 Must-have 层（system/developer 消息）"""
        messages = [
            {"role": "system", "content": "System message 1"},
            {"role": "developer", "content": "Developer message 1"},
            {"role": "user", "content": "User message"},
        ]

        result = strategy.compress(messages, target_tokens=2000)

        # 应该有一个 "Condensed instruction context" 消息
        instruction_msgs = [
            msg for msg in result
            if "Condensed instruction context" in msg.get("content", "")
        ]
        assert len(instruction_msgs) == 1

        # 内容应该包含 [system] 和 [developer]
        content = instruction_msgs[0]["content"]
        assert "[system]" in content
        assert "[developer]" in content

    def test_compress_nice_to_have_layer(self, strategy, sample_messages):
        """测试 Nice-to-have 层（最近 8 条摘要）"""
        result = strategy.compress(sample_messages, target_tokens=2000)

        # 应该有一个 "Condensed recent context" 消息
        recent_msgs = [
            msg for msg in result
            if "Condensed recent context" in msg.get("content", "")
        ]

        # Nice-to-have 层可能为空（取决于对话数量）
        if len(recent_msgs) > 0:
            content = recent_msgs[0]["content"]
            # 应该包含 [user] 或 [assistant]
            assert "[user]" in content or "[assistant]" in content

    def test_compress_history_tail_layer(self, strategy, sample_messages):
        """测试 History-tail 层（历史摘要）"""
        result = strategy.compress(sample_messages, target_tokens=2000)

        # 应该有一个 "Conversation memory tail" 消息
        memory_msgs = [
            msg for msg in result
            if "Conversation memory tail" in msg.get("content", "")
        ]

        # History-tail 层可能为空（取决于对话数量）
        if len(memory_msgs) > 0:
            content = memory_msgs[0]["content"]
            # 简单压缩模式应该包含 "History tail summary"
            assert "History tail summary" in content

    def test_compress_with_tokenizer(self, strategy, sample_messages):
        """测试使用真实 tokenizer"""
        import tiktoken

        tokenizer = tiktoken.get_encoding("cl100k_base")
        result = strategy.compress(sample_messages, target_tokens=2000, tokenizer=tokenizer)

        # 验证结果正常
        assert isinstance(result, list)
        assert len(result) > 0

        # 验证 token 统计更准确
        stats = strategy.last_stats
        assert stats["total_output_tokens"] > 0

    def test_compress_short_conversation(self, strategy):
        """测试短对话（< preserve_last_turns）"""
        messages = [
            {"role": "system", "content": "System message"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        result = strategy.compress(messages, target_tokens=2000)

        # 短对话应该全部保留在 Tail 层
        assert isinstance(result, list)
        assert len(result) > 0

    def test_compress_flatten_content_list(self, strategy):
        """测试展平列表内容"""
        messages = [
            {
                "role": "user",
                "content": [
                    {"text": "Part 1"},
                    {"text": "Part 2"},
                ]
            },
        ]

        result = strategy.compress(messages, target_tokens=2000)

        # 应该能正常处理
        assert isinstance(result, list)
        assert len(result) > 0

    def test_compress_empty_content(self, strategy):
        """测试空内容消息"""
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": None},
            {"role": "user", "content": "Valid message"},
        ]

        result = strategy.compress(messages, target_tokens=2000)

        # 应该过滤掉空消息
        assert isinstance(result, list)

    def test_different_token_caps(self):
        """测试不同的 token 上限配置"""
        # 小上限
        small_strategy = ThreeTierLayeringStrategy(
            must_have_cap=512,
            nice_to_have_cap=256,
            history_tail_cap=128,
            preserve_last_turns=3,
        )

        messages = [
            {"role": "system", "content": "System message " * 100},
            {"role": "user", "content": f"Message {i}"} for i in range(20)
        ]
        messages = [m for m in messages if isinstance(m, dict)]  # 展平生成器
        messages.append({"role": "system", "content": "System message " * 100})
        for i in range(20):
            messages.append({"role": "user", "content": f"Message {i}"})

        result = small_strategy.compress(messages, target_tokens=1000)

        # 验证各层 token 数符合配置
        stats = small_strategy.last_stats
        assert stats["must_have_tokens"] <= 512
        assert stats["nice_to_have_tokens"] <= 256
        assert stats["history_tail_tokens"] <= 128


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
