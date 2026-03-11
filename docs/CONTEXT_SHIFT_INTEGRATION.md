# Context Shift 集成指南

**版本**: 1.0
**日期**: 2026-03-11
**状态**: ✅ 已完成集成

---

## 目录

- [概述](#概述)
- [快速开始](#快速开始)
- [架构设计](#架构设计)
- [配置说明](#配置说明)
- [使用方式](#使用方式)
- [性能数据](#性能数据)
- [故障排查](#故障排查)
- [常见问题](#常见问题)

---

## 概述

### 什么是 Context Shift？

Context Shift 是一个两阶段 LLM 摘要系统，用于压缩长对话历史。它通过两个小型模型（0.6B 和 1.7B）协同工作，实现高质量的上下文压缩。

**两阶段流程**：

```
原始对话 (N 条消息)
    ↓
Stage 1 (0.6B @ port 18083)
    抽取 FACTS / DECISIONS / OPEN_ISSUES
    ↓
Stage 2 (1.7B @ port 18084)
    添加 GOAL，压缩成短记忆
    ↓
压缩摘要 (~200 tokens)
```

### 为什么需要 Context Shift？

**问题**: ClawGate 原有的上下文压缩使用简单字符截断，缺乏语义理解。

**解决**: Context Shift 使用 LLM 理解对话语义，生成结构化摘要（GOAL/FACTS/DECISIONS），保留关键信息。

**优势**:
- ✅ 语义理解：LLM 理解对话内容，而非简单截断
- ✅ 结构化输出：GOAL/FACTS/DECISIONS，便于后续处理
- ✅ 可配置模式：auto/fast/quality/simple 四种模式
- ✅ 自动降级：Circuit Breaker 保证服务可用性

---

## 快速开始

### 前置条件

1. **ThunderLLAMA 已安装并编译**
   ```bash
   # 检查 llama-server 是否存在
   ls ~/ThunderLLAMA/build/bin/llama-server
   ```

2. **模型已下载**
   ```bash
   # 检查模型文件
   ls ~/models/qwen3-0.6b-gguf/Qwen3-0.6B-Q5_K_M.gguf
   ls ~/models/qwen3-1.7b-gguf/Qwen3-1.7B-Q8_0.gguf
   ```

3. **Python 依赖已安装**
   ```bash
   pip install httpx asyncio
   ```

### 启动 Context Shift 服务

```bash
cd /Users/lisihao/ClawGate
bash scripts/start_context_shift_services.sh
```

**预期输出**:
```
========================================
启动 Context Shift 双模型服务
========================================

▶ 启动 Stage 1 模型 (0.6B - 抽取)
  模型: Qwen3-0.6B-Q5_K_M.gguf
  端口: 18083
  PID: 12345

▶ 启动 Stage 2 模型 (1.7B - 压缩)
  模型: Qwen3-1.7B-Q8_0.gguf
  端口: 18084
  PID: 12346

等待服务启动...
✅ 两个服务端口都已监听

等待模型加载完成...
✅ Stage 1 (0.6B) 模型就绪
✅ Stage 2 (1.7B) 模型就绪

========================================
✅ Context Shift 服务运行中
========================================
Stage 1 (0.6B 抽取): http://127.0.0.1:18083
Stage 2 (1.7B 压缩): http://127.0.0.1:18084

PID:
  Stage 1: 12345
  Stage 2: 12346

停止服务:
  kill 12345 12346
```

### 验证服务

```bash
# 运行集成测试
python3 tests/test_context_shift_integration.py
```

**预期结果**:
```
============================================================
测试结果汇总
============================================================
health_check                   ✅ PASS
summarize                      ✅ PASS
layering_integration           ✅ PASS

============================================================
🎉 所有测试通过！
============================================================
```

### 启用 Context Shift（可选）

编辑 `config/models.yaml`:

```yaml
context_engine:
  context_shift:
    enabled: true  # 改为 true
```

---

## 架构设计

### 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    ClawGate 主服务                       │
│                                                          │
│  ┌─────────────────────────────────────────────────┐   │
│  │        Layering Strategy (四层分层)              │   │
│  │                                                  │   │
│  │  Layer 1: Must-have (system 消息)               │   │
│  │  Layer 2: Nice-to-have (最近 8 条摘要)          │   │
│  │  Layer 3: History-tail (Context Shift 摘要) ◄───┼───┼─┐
│  │  Layer 4: Tail (最后 N 轮原文)                  │   │ │
│  └─────────────────────────────────────────────────┘   │ │
│                                                          │ │
└──────────────────────────────────────────────────────────┘ │
                                                             │
┌────────────────────────────────────────────────────────────┘
│
│  ┌─────────────────────────────────────────────────┐
│  │       ContextShiftClient (异步客户端)            │
│  │                                                  │
│  │  ┌────────────────────────────────────────┐    │
│  │  │       Circuit Breaker (降级机制)        │    │
│  │  │  连续失败 ≥3 次 → 自动降级              │    │
│  │  │  5 分钟后尝试恢复                       │    │
│  │  └────────────────────────────────────────┘    │
│  │                                                  │
│  │  模式选择: auto/fast/quality/simple             │
│  └─────────────────────────────────────────────────┘
│              │                      │
│              ▼                      ▼
│    ┌──────────────────┐  ┌──────────────────┐
│    │ Stage 1 (0.6B)   │  │ Stage 2 (1.7B)   │
│    │ Port: 18083      │  │ Port: 18084      │
│    │ 抽取 FACTS       │  │ 添加 GOAL        │
│    │ DECISIONS        │  │ 压缩摘要         │
│    │ OPEN_ISSUES      │  │                  │
│    └──────────────────┘  └──────────────────┘
```

### 四种模式

| 模式 | Stage 1 | Stage 2 | 使用场景 | 延迟 |
|------|---------|---------|----------|------|
| **auto** | 0.6B | 动态选择 | 默认模式，根据对话轮数自动选择 | 中等 |
| **fast** | 0.6B | 0.6B | 快速响应，对话轮数 < 10 | 低 |
| **quality** | 0.6B | 1.7B | 高质量摘要，对话轮数 >= 10 | 高 |
| **simple** | — | — | 不调用 LLM，字符截断 | 极低 |

**auto 模式逻辑**:
```python
if len(messages) < 10:
    mode = "fast"   # 0.6B + 0.6B
else:
    mode = "quality"  # 0.6B + 1.7B
```

### Circuit Breaker 降级机制

```
正常状态 (is_open=False)
    │
    ├─ 调用成功 → record_success() → 重置失败计数
    │
    └─ 调用失败 → record_failure() → 增加失败计数
                        │
                        ├─ 失败次数 < 3 → 继续尝试
                        │
                        └─ 失败次数 >= 3 → 打开断路器 (is_open=True)
                                              │
                                              ├─ 拒绝新请求（返回 None）
                                              │
                                              └─ 5 分钟后 → 尝试恢复（半开状态）
```

---

## 配置说明

### 配置文件位置

`config/models.yaml` → `context_engine.context_shift`

### 完整配置示例

```yaml
context_engine:
  # ... 其他配置 ...

  # Context Shift 两阶段 LLM 摘要配置
  context_shift:
    enabled: false  # 是否启用（默认禁用）
    mode: "auto"    # 摘要模式
    auto_threshold: 10  # auto 模式的轮数阈值

    # 服务端点配置
    endpoints:
      stage1: "http://127.0.0.1:18083/completion"
      stage2: "http://127.0.0.1:18084/completion"

    # 超时与重试
    timeout: 60.0       # 请求超时（秒）
    max_retries: 3      # 最大重试次数

    # Circuit Breaker 降级机制
    circuit_breaker:
      enabled: true
      failure_threshold: 3    # 连续失败次数阈值
      reset_timeout: 300.0    # 断路器重置超时（秒）

    # 降级策略
    fallback:
      enabled: true
      strategy: "simple"  # 降级到简单字符截断
```

### 配置参数说明

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | false | 是否启用 Context Shift |
| `mode` | str | "auto" | 摘要模式（auto/fast/quality/simple） |
| `auto_threshold` | int | 10 | auto 模式的对话轮数阈值 |
| `endpoints.stage1` | str | — | Stage 1 端点地址（0.6B 抽取） |
| `endpoints.stage2` | str | — | Stage 2 端点地址（1.7B 压缩） |
| `timeout` | float | 60.0 | 请求超时（秒） |
| `max_retries` | int | 3 | 最大重试次数 |
| `circuit_breaker.enabled` | bool | true | 是否启用 Circuit Breaker |
| `circuit_breaker.failure_threshold` | int | 3 | 连续失败次数阈值 |
| `circuit_breaker.reset_timeout` | float | 300.0 | 断路器重置超时（秒） |
| `fallback.enabled` | bool | true | 是否启用降级策略 |
| `fallback.strategy` | str | "simple" | 降级策略（simple: 字符截断） |

---

## 使用方式

### 在代码中使用

#### 1. 创建 ContextShiftClient

```python
from clawgate.context.context_shift_client import ContextShiftClient

# 创建客户端
client = ContextShiftClient(
    stage1_endpoint="http://127.0.0.1:18083/completion",
    stage2_endpoint="http://127.0.0.1:18084/completion",
    mode="quality",  # 使用高质量模式
    timeout=60.0,
    max_retries=3,
    circuit_breaker_enabled=True
)

# 检查健康状态
is_healthy, message = await client.health_check()
print(f"健康检查: {message}")

# 调用摘要
messages = [
    {"role": "user", "content": "我想学习机器学习。"},
    {"role": "assistant", "content": "很好！机器学习是..."},
    # ... 更多消息
]

summary = await client.summarize(messages, target_tokens=200)
print(f"摘要: {summary}")

# 关闭客户端
await client.close()
```

#### 2. 在 Layering 策略中使用

```python
from clawgate.context.strategies.layering import ThreeTierLayeringStrategy
from clawgate.context.context_shift_client import ContextShiftClient

# 创建 Context Shift 客户端
context_shift_client = ContextShiftClient(mode="auto")

# 创建 Layering 策略（启用 Context Shift）
layering = ThreeTierLayeringStrategy(
    must_have_cap=1536,
    nice_to_have_cap=768,
    history_tail_cap=512,
    preserve_last_turns=6,
    context_shift_enabled=True,
    context_shift_client=context_shift_client
)

# 压缩消息
compressed = layering.compress(
    messages=messages,
    target_tokens=2048,
    tokenizer=None
)

# 查看统计信息
print(f"压缩统计: {layering.last_stats}")
print(f"Context Shift 使用统计: {layering.get_context_shift_stats()}")
```

---

## 性能数据

### 测试环境

- **硬件**: Mac mini M4 Pro (14 核 CPU, 20 核 GPU, 48GB RAM)
- **模型**:
  - Stage 1: Qwen3-0.6B-Q5_K_M.gguf (~480MB)
  - Stage 2: Qwen3-1.7B-Q8_0.gguf (~1.8GB)
- **测试数据**: 14 条对话消息（约 600 tokens）

### 性能指标

| 模式 | Stage 1 延迟 | Stage 2 延迟 | 总延迟 | 输出长度 |
|------|-------------|-------------|--------|---------|
| **fast** | ~0.3s | ~0.3s | ~0.6s | ~200 字符 |
| **quality** | ~0.3s | ~0.9s | ~1.2s | ~370 字符 |
| **simple** | — | — | <0.01s | ~480 字符 |

### 压缩效果

**原始消息**: 14 条
**压缩后**: 9 条

**Token 分配**:
- Must-have (Layer 1): 5 tokens
- Nice-to-have (Layer 2): 40 tokens
- **History-tail (Layer 3, Context Shift)**: 93 tokens
- Tail (Layer 4): 18 tokens
- **总计**: 156 tokens

**Context Shift 使用率**: 100% (1 次使用，0 次降级)

### 对比数据

| 方法 | 输出长度 | 语义保留 | 结构化 | 延迟 |
|------|---------|---------|--------|------|
| **简单截断** | ~480 字符 | ❌ 低 | ❌ 无 | <0.01s |
| **Context Shift (fast)** | ~200 字符 | ✅ 中 | ✅ 有 | ~0.6s |
| **Context Shift (quality)** | ~370 字符 | ✅ 高 | ✅ 有 | ~1.2s |

---

## 故障排查

### 问题 1: 服务启动失败

**症状**:
```
❌ 模型不存在: /path/to/model.gguf
```

**解决**:
1. 检查模型文件是否存在：
   ```bash
   ls ~/models/qwen3-0.6b-gguf/Qwen3-0.6B-Q5_K_M.gguf
   ls ~/models/qwen3-1.7b-gguf/Qwen3-1.7B-Q8_0.gguf
   ```

2. 如果不存在，下载模型：
   ```bash
   # 从 HuggingFace 下载
   huggingface-cli download Qwen/Qwen3-0.6B-GGUF --local-dir ~/models/qwen3-0.6b-gguf
   huggingface-cli download Qwen/Qwen3-1.7B-GGUF --local-dir ~/models/qwen3-1.7b-gguf
   ```

### 问题 2: 端口已被占用

**症状**:
```
⚠️  端口 18083 已被占用，尝试终止...
```

**解决**:
1. 查看占用端口的进程：
   ```bash
   lsof -i :18083
   lsof -i :18084
   ```

2. 手动终止进程：
   ```bash
   kill -9 <PID>
   ```

3. 或使用停止脚本（如果存在）：
   ```bash
   bash scripts/stop_context_shift_services.sh
   ```

### 问题 3: 模型加载超时

**症状**:
```
❌ 模型加载超时（90秒）
```

**解决**:
1. 检查系统内存是否充足：
   ```bash
   vm_stat
   ```

2. 检查日志文件：
   ```bash
   tail -50 ~/ClawGate/logs/context-shift/stage1_0.6b.log
   tail -50 ~/ClawGate/logs/context-shift/stage2_1.7b.log
   ```

3. 增加超时时间（修改启动脚本）：
   ```bash
   # 将 90 秒改为 180 秒
   for i in {1..180}; do
   ```

### 问题 4: Context Shift 摘要失败

**症状**:
```
Context Shift 返回 None（Circuit Breaker 或服务不可用），降级到简单压缩
```

**解决**:
1. 检查服务是否运行：
   ```bash
   curl http://127.0.0.1:18083/health
   curl http://127.0.0.1:18084/health
   ```

2. 查看 Circuit Breaker 状态（在日志中）：
   ```
   Circuit Breaker 打开: 连续 3 次失败，将在 300s 后尝试恢复
   ```

3. 等待 5 分钟让 Circuit Breaker 恢复，或重启服务：
   ```bash
   bash scripts/start_context_shift_services.sh
   ```

### 问题 5: 集成测试失败

**症状**:
```
❌ Context Shift 服务不可用，请先启动服务
```

**解决**:
1. 启动 Context Shift 服务：
   ```bash
   bash scripts/start_context_shift_services.sh
   ```

2. 等待服务就绪（约 30-60 秒）

3. 重新运行测试：
   ```bash
   python3 tests/test_context_shift_integration.py
   ```

---

## 常见问题

### Q1: Context Shift 是否必须启用？

**A**: 否。Context Shift 是可选特性，通过 Feature Flag 控制。禁用时，Layering 策略会自动降级到简单字符截断，不影响主流程。

### Q2: Context Shift 会增加多少延迟？

**A**: 根据模式不同：
- **fast 模式**: ~0.6s
- **quality 模式**: ~1.2s
- **simple 模式**: <0.01s（不调用 LLM）

### Q3: 如何选择合适的模式？

**A**:
- **auto 模式**（推荐）: 对话 < 10 轮用 fast，>= 10 轮用 quality
- **fast 模式**: 追求低延迟，对摘要质量要求不高
- **quality 模式**: 追求高质量摘要，可接受较高延迟
- **simple 模式**: 调试或 Context Shift 服务不可用时

### Q4: Circuit Breaker 什么时候会触发？

**A**: 当 Context Shift 连续失败 3 次时，Circuit Breaker 会打开，自动降级到简单压缩。5 分钟后会尝试恢复。

### Q5: 如何监控 Context Shift 使用情况？

**A**: 使用 `layering.get_context_shift_stats()` 方法：
```python
stats = layering.get_context_shift_stats()
print(f"使用次数: {stats['used']}")
print(f"降级次数: {stats['fallback']}")
print(f"使用率: {stats['usage_rate'] * 100:.1f}%")
```

### Q6: Context Shift 服务占用多少内存？

**A**:
- Stage 1 (0.6B Q5_K_M): ~600MB
- Stage 2 (1.7B Q8_0): ~2GB
- **总计**: ~2.6GB

### Q7: 可以同时运行多个 Context Shift 实例吗？

**A**: 可以，但需要修改端口号。建议使用不同的端口（如 18085/18086），并在 `config/models.yaml` 中配置。

### Q8: Context Shift 支持哪些语言？

**A**: 支持中英文。Qwen3 模型对中文支持较好，英文也能正常处理。

---

## 相关资源

### 文档

- [ClawGate README](../README.md)
- [依赖文档](DEPENDENCIES.md)
- [Day 2 总结报告](CONTEXT_SHIFT_DAY2_SUMMARY.md)

### 源代码

- `clawgate/context/context_shift_client.py` - Context Shift 异步客户端
- `clawgate/context/strategies/layering.py` - 四层分层策略
- `tests/test_context_shift_integration.py` - 集成测试

### 启动脚本

- `scripts/start_context_shift_services.sh` - 启动 Context Shift 服务

---

**文档版本**: 1.0
**最后更新**: 2026-03-11
**维护者**: ClawGate Team
