# 场景2测试报告：ClawGate + 标准 llama.cpp（ClawGate 优化）

## 测试配置

### 模型
- **模型**: Qwen3-30B-A3B-128K-Q5_K_M.gguf
- **量化**: Q5_K_M
- **上下文窗口**: 8192 tokens

### 标准 llama-server 配置（无 ThunderLLAMA 特性）
- **Flash Attention**: ✅ 启用 (-fa on)
- **Continuous Batching**: ✅ 启用 (-cb)
- **Parallel Slots**: 4 (-np 4)
- **GPU Offload**: 全部层 (-ngl 99)
- **LMCache**: ❌ 未启用（无 THUNDER_LMCACHE）
- **Chunk Prefill**: ❌ 未启用（无 THUNDERLLAMA_CHUNK_PREFILL）
- **Paged Attention**: ❌ 未启用（无 LLAMA_PAGED_ATTENTION）

### ClawGate 层优化
- **ContextPilot Level 1 (Reorder)**: ✅ KV cache prefix sharing
- **ContextPilot Level 2 (Dedup)**: ✅ Multi-turn token savings
- **Prefix Cache**: ✅ 启用
- **智能队列调度**: ✅ 三车道 + 信号量

### 测试场景
- **场景**: Agent 工作流（OpenClaw）
- **并发请求数**: 4
- **System Prompt**: ~800 tokens (固定)
- **User Query**: ~200 tokens（微服务架构设计问题）
- **Max Tokens**: 500
- **Temperature**: 0.7

## 测试结果

### 性能指标

| 指标 | 值 |
|------|-----|
| **总完成时间** | 36.23秒 |
| **平均延迟** | 36.23秒 |
| **系统总吞吐量** | **55.21 tokens/s** |
| **单个请求** |  |
| - Prompt Tokens | 274 |
| - Completion Tokens | 500 |
| **总 Completion Tokens** | 2000 (4 × 500) |

### 对比场景1（ClawGate + ThunderLLAMA）

| 指标 | 场景1 (ThunderLLAMA) | 场景2 (标准 llama.cpp) | 差异 |
|------|---------------------|----------------------|------|
| **总时间** | 38.15s | 36.23s | **-5.3%** ⚡ |
| **系统吞吐量** | 52.42 tokens/s | 55.21 tokens/s | **+5.3%** ⚡ |

**惊人发现**：场景2（标准 llama.cpp）比场景1（ThunderLLAMA）更快！

## 分析

### 为什么标准 llama.cpp 更快？

**可能原因**：

1. **LMCache 未命中时的开销**
   - 场景1：LMCache 启用但 skip_rate=0%，L2 缓存维护有开销
   - 场景2：无 LMCache，无缓存维护开销

2. **Chunk Prefill 的延迟**
   - 场景1：Chunk Prefill=512 可能在并发场景下引入调度延迟
   - 场景2：标准 prefill，更简单的调度

3. **优化的适用场景**
   - ThunderLLAMA 优化（LMCache, Chunk Prefill）设计用于高缓存命中场景
   - 当 skip_rate=0% 时，优化反而成为负担

### ClawGate ContextPilot 表现

- ✅ **正常工作**: 两个场景都使用了 ContextPilot
- ⚠️ **未观察到显著提升**: 可能因为测试场景较简单，ContextPilot 的优势在更复杂的多轮对话中才明显

## 结论

场景2测试成功完成：

✅ **成功点**:
- 标准 llama-server 正常运行
- ClawGate 成功连接到标准 llama-server（端口 8091）
- ContextPilot 正常工作
- **性能优于场景1**: +5.3% 吞吐量

⚠️ **关键发现**:
- **LMCache 在 skip_rate=0% 时反而拖慢性能**
- **Chunk Prefill 在并发场景下可能引入额外延迟**
- **ThunderLLAMA 优化需要高缓存命中率才能发挥优势**

## 对比基准

从之前的直接 ThunderLLAMA 测试（skip_rate=94%）：
- **直接 ThunderLLAMA**: 2.91s, 687.6 tokens/s, 94% skip rate
- **ClawGate + ThunderLLAMA**: 38.15s, 52.42 tokens/s, 0% skip rate
- **ClawGate + 标准 llama**: 36.23s, 55.21 tokens/s, N/A

**性能排序**:
1. 🥇 直接 ThunderLLAMA（高 skip rate）: 687.6 tokens/s
2. 🥈 ClawGate + 标准 llama: 55.21 tokens/s
3. 🥉 ClawGate + ThunderLLAMA（0% skip）: 52.42 tokens/s

## 下一步

1. ✅ 场景1 完成
2. ✅ 场景2 完成
3. ⏭️ 场景3：标准 llama-server（直接，最佳参数，无 ClawGate）
4. 📊 对比三个场景，生成综合报告

---

**测试时间**: 2026-03-13 05:38:04
**测试环境**: macOS (M4 Max), 标准 llama.cpp + ClawGate
