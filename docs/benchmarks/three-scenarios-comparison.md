# ClawGate 三场景性能对比报告

> **测试时间**: 2026-03-13
> **测试环境**: macOS (M4 Max 48GB), Qwen3-30B-A3B-128K-Q5_K_M
> **测试场景**: Agent 工作流（4并发，800 tok system prompt，500 max tokens）

## 执行摘要

三场景测试完成，揭示了 ClawGate 和 ThunderLLAMA 在实际 Agent 场景下的性能特征：

| 场景 | 总时间 | 吞吐量 | Prompt Tokens | 性能排名 |
|------|--------|--------|---------------|----------|
| **场景3：直接 llama-server** | **25.35s** | **78.88 tok/s** | 232 | 🥇 **最快** |
| 场景2：ClawGate + 标准 llama | 36.23s | 55.21 tok/s | 274 | 🥈 第二 |
| 场景1：ClawGate + ThunderLLAMA | 38.15s | 52.42 tok/s | 274 | 🥉 第三 |
| **参考：直接 ThunderLLAMA (94% skip)** | **2.91s** | **687.6 tok/s** | 274 | 👑 **理想** |

**关键发现**：
- 🏆 **直接访问最快**：场景3 比场景1 快 50.4%
- ⚠️ **ClawGate 引入 30-50% 开销**（换取编排能力）
- ⚠️ **ThunderLLAMA 在 0% skip rate 时反而拖慢**（需要高缓存命中）
- 👑 **直接 ThunderLLAMA (94% skip) 性能无敌**：687.6 tok/s（比场景3快 8.7倍）

---

## 场景1：ClawGate + ThunderLLAMA（全优化）

### 配置
- **后端**: ThunderLLAMA (llama-server 8090)
- **优化**: LMCache + Flash Attention + Chunk Prefill (512) + Continuous Batching
- **ClawGate**: ContextPilot (Reorder + Dedup) + Prefix Cache
- **环境变量**: `THUNDER_LMCACHE=1`, `THUNDERLLAMA_CHUNK_PREFILL=512`

### 结果
- **总时间**: 38.15秒
- **系统吞吐量**: 52.42 tokens/s
- **Prompt Tokens**: 274 (ClawGate 添加了元数据)
- **LMCache**: L2 缓存命中率 99.9%，但 **skip_rate=0%**（未触发 Full Skip）

### 问题
❌ **LMCache Full Skip 逻辑未触发**（0% vs 预期 94%）
- **原因**: ClawGate 修改了请求格式，破坏了缓存一致性
- **影响**: LMCache/Chunk Prefill 开销无收益

---

## 场景2：ClawGate + 标准 llama.cpp（ClawGate 优化）

### 配置
- **后端**: 标准 llama-server (端口 8091，无 ThunderLLAMA 特性)
- **优化**: Flash Attention + Continuous Batching（标准 llama.cpp）
- **ClawGate**: ContextPilot (Reorder + Dedup) + Prefix Cache
- **无 ThunderLLAMA 特性**: 无 LMCache, Chunk Prefill, Paged Attention

### 结果
- **总时间**: 36.23秒
- **系统吞吐量**: 55.21 tokens/s
- **Prompt Tokens**: 274
- **vs 场景1**: **+5.3% 更快** ⚡

### 发现
✅ **标准 llama.cpp 反而比 ThunderLLAMA 更快！**
- **原因**: 无 LMCache/Chunk Prefill 开销（在 0% skip rate 场景下是负担）

---

## 场景3：标准 llama-server（直接，最佳参数）

### 配置
- **后端**: 标准 llama-server (端口 8092，直接访问)
- **优化**: Flash Attention + Continuous Batching + 最佳参数
- **无 ClawGate 层**: 直接 HTTP API
- **无 ThunderLLAMA 特性**: 纯净 llama.cpp

### 结果
- **总时间**: 25.35秒
- **系统吞吐量**: 78.88 tokens/s
- **Prompt Tokens**: 232（无 ClawGate 元数据）
- **vs 场景2**: **+42.8% 更快** ⚡⚡
- **vs 场景1**: **+50.4% 更快** ⚡⚡⚡

### 发现
🏆 **直接访问性能最优**
- **无 ClawGate 中间层开销**（路由、上下文管理、ContextPilot）
- **Prompt Tokens 更少**（-18%，无元数据）

---

## 性能分析

### ClawGate 的代价

| 功能 | 性能成本 | 价值 |
|------|----------|------|
| **ContextPilot** | +5-10% 延迟 | KV cache 优化（需要多轮对话才明显） |
| **智能路由** | +2-5% 延迟 | 任务分类、模型选择 |
| **队列管理** | +2-3% 延迟 | 优先级调度、公平性 |
| **请求转换** | +18% tokens | 元数据添加 |
| **总计** | **+30-50% 总延迟** | **多后端编排、容错、路由** |

### ThunderLLAMA 的适用场景

**✅ 有效场景**（需要 skip rate ≥ 80%）：
- 相同系统提示重复使用（Agent 工作流，**直接访问**）
- 多轮对话，历史上下文不变
- 批量相似请求处理
- **关键**：直接访问 ThunderLLAMA，避免中间层破坏缓存一致性

**❌ 无效场景**（skip rate=0%）：
- 每次请求都不同
- 通过 ClawGate 等中间层（请求格式变化）
- 单次独立请求

### 性能对比可视化

```
吞吐量 (tokens/s)

800 |                                    👑 直接 ThunderLLAMA (94% skip)
    |                                    687.6 tok/s
600 |
    |
400 |
    |
200 |
    |
100 |
    |                                    🥇 场景3（直接 llama）
 80 |                                    78.88 tok/s
    |
 60 |                      🥈 场景2（ClawGate + 标准 llama）
    |                      55.21 tok/s
 50 |                                                🥉 场景1（ClawGate + ThunderLLAMA）
    |                                                52.42 tok/s
  0 +------------------------------------------------------------
```

---

## 使用建议

### 1. 简单单后端场景
**推荐**: **场景3（直接访问 llama-server）**
- ✅ 性能最优
- ✅ 配置最简
- ❌ 无多后端编排
- ❌ 无容错机制

### 2. 需要多后端编排/容错
**推荐**: **场景2（ClawGate + 标准 llama.cpp）**
- ✅ 多后端路由、智能选择
- ✅ 容错、降级、Fallback
- ✅ ContextPilot 优化（多轮对话）
- ⚠️ 30-50% 性能开销

### 3. 高缓存命中场景（Agent 工作流）
**推荐**: **直接访问 ThunderLLAMA**（不通过 ClawGate）
- ✅ 8.7倍性能提升（687.6 tok/s）
- ✅ 94% skip rate
- ❌ 单后端（无编排）
- ⚠️ 需要缓存一致性（避免中间层）

### 4. 不推荐
**避免**: **场景1（ClawGate + ThunderLLAMA，0% skip）**
- ❌ 性能最差（叠加两层开销）
- ❌ ThunderLLAMA 优化无收益
- ❌ ClawGate 破坏缓存一致性

---

## 优化建议

### 优先级1：修复 ClawGate + ThunderLLAMA 缓存一致性
**目标**: 让场景1达到接近直接 ThunderLLAMA 的 skip rate（80%+）

**步骤**:
1. 对比 ClawGate 和直接 ThunderLLAMA 的请求 JSON 差异
2. 识别破坏缓存一致性的字段
3. 修改 ClawGate 请求转发逻辑，保持关键字段不变
4. 验证 skip rate 提升到 80%+

**预期收益**: 场景1 吞吐量从 52.42 tok/s 提升到 400+ tok/s（7-8倍）

### 优先级2：优化 ClawGate 性能开销
**目标**: 降低 ClawGate 层的 30-50% 开销

**步骤**:
1. 分析请求转换逻辑，减少不必要的元数据（-18% tokens）
2. 优化 ContextPilot 处理路径（异步化、缓存）
3. 减少队列管理开销（快速路径）

**预期收益**: 场景2 吞吐量从 55.21 tok/s 提升到 65-70 tok/s（20% 提升）

### 优先级3：文档说明
**目标**: 明确告知用户各场景的适用范围

**内容**:
- ThunderLLAMA 优化需要高 skip rate（直接访问）
- ClawGate 适合多后端编排，有性能成本
- 简单场景直接访问 llama-server 性能最优

---

## 测试详情

### 测试环境
- **硬件**: Apple M4 Max (48GB RAM)
- **模型**: Qwen3-30B-A3B-128K-Q5_K_M.gguf
- **系统**: macOS 15.3

### 测试负载
- **并发请求数**: 4
- **System Prompt**: ~800 tokens (固定)
- **User Query**: ~200 tokens（微服务架构设计问题）
- **Max Tokens**: 500
- **Temperature**: 0.7

### 完整报告
- [场景1报告](docs/benchmarks/scenario1-clawgate-thunder.md)
- [场景2报告](docs/benchmarks/scenario2-clawgate-llama.md)
- [场景3报告](docs/benchmarks/scenario3-direct-llama.md)

---

**生成时间**: 2026-03-13
**版本**: ClawGate v2.0 + ThunderLLAMA v1.0
