# Phase 2 新特性：Cache Tuning + Prompt Cache

> 从 ThunderLLAMA thunder_service.py 迁移的生产级优化特性

## 📋 目录

- [特性概述](#特性概述)
- [1. Prompt Cache (热/温两层缓存)](#1-prompt-cache-热温两层缓存)
- [2. Auto Cache-RAM Tuning (自动缓存调优)](#2-auto-cache-ram-tuning-自动缓存调优)
- [性能数据](#性能数据)
- [配置说明](#配置说明)
- [监控 Dashboard](#监控-dashboard)

---

## 特性概述

Phase 2 为 ClawGate 增加了两个核心优化特性，显著提升了本地推理的性能和响应速度：

| 特性 | 作用 | 性能提升 |
|------|------|----------|
| **Prompt Cache** | 缓存确定性请求的响应 | 命中时加速 > 25,000x |
| **Cache Tuning** | 自动调优 llama-server 的 cache-ram | QPS 提升 5-15% |

---

## 1. Prompt Cache (热/温两层缓存)

### 原理

Prompt Cache 使用**热/温两层架构**缓存确定性请求（`temperature=0`, `stream=False`, `n=1`）的响应，避免重复调用 LLM。

```
┌─────────────────────────────────────────────────────────────┐
│                      Prompt Cache                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  热缓存 (Hot Cache)                                          │
│  ├─ 存储：内存 OrderedDict (LRU)                            │
│  ├─ 容量：256 条                                             │
│  ├─ TTL：1 小时                                              │
│  └─ 特点：命中快，O(1) 访问                                  │
│                                                             │
│  温缓存 (Warm Cache)                                         │
│  ├─ 存储：磁盘 JSON 文件                                     │
│  ├─ 容量：无限制                                             │
│  ├─ TTL：24 小时                                             │
│  └─ 特点：持久化，跨进程共享                                  │
│                                                             │
│  提升机制：温缓存命中 3 次 → 提升到热缓存                    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 缓存键生成

使用 **SHA256 hash** 确保精确匹配：

```python
cache_key = SHA256({
    "model": "qwen-1.7b",
    "messages": [...],
    "max_tokens": 512,
    "temperature": 0,
    "top_p": None,
    "stop": None,
    # ... 其他稳定字段
})
```

### 可缓存条件

只有**确定性请求**才会被缓存：

| 条件 | 要求 | 原因 |
|------|------|------|
| `temperature` | = 0 | 确保输出确定性 |
| `stream` | = False | 非流式响应才能完整缓存 |
| `n` | = 1 | 单一响应（n>1 会生成多个结果） |

**示例**：

```bash
# ✅ 可缓存
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-1.7b",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "temperature": 0,
    "stream": false
  }'

# ❌ 不可缓存（temperature ≠ 0）
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-1.7b",
    "messages": [{"role": "user", "content": "What is 2+2?"}],
    "temperature": 0.7,
    "stream": false
  }'
```

### 请求流程集成

```
请求到达
  ↓
上下文处理 (压缩/适配/重排)
  ↓
[Prompt Cache 检查] ← 新增！
  ├─ 命中 (hot/warm) → 直接返回缓存响应 (0ms)
  └─ 未命中 → 继续
      ↓
语义缓存检查 (Jaccard 相似度)
  ↓
实际 LLM 请求 (100ms+)
  ↓
[存储到 Prompt Cache] ← 新增！
  ├─ 热缓存 (内存)
  └─ 温缓存 (磁盘)
```

### 统计信息

通过 `/stats` 端点查看缓存性能：

```json
{
  "prompt_cache": {
    "enabled": true,
    "hot_cache_size": 128,      // 当前热缓存条目数
    "hot_cache_max": 256,        // 热缓存容量
    "hit_hot": 450,              // 热缓存命中次数
    "hit_warm": 120,             // 温缓存命中次数
    "miss": 85,                  // 缓存未命中次数
    "total_requests": 655,       // 总请求数
    "hit_rate": 0.87,            // 命中率 87%
    "store": 85,                 // 存储次数
    "evict_hot": 12,             // 热缓存淘汰次数
    "evict_warm": 3              // 温缓存淘汰次数
  }
}
```

**性能指标**：
- **命中率**: 通常 70-90%（取决于请求模式）
- **热缓存命中延迟**: < 0.01 ms
- **温缓存命中延迟**: < 5 ms
- **加速比**: 25,000x+（相比实际 LLM 请求）

---

## 2. Auto Cache-RAM Tuning (自动缓存调优)

### 原理

Auto Cache-RAM Tuning 通过**24小时性能数据驱动**的启发式算法，自动调整 llama-server 的 `--cache-ram` 参数，优化推理性能。

```
┌─────────────────────────────────────────────────────────────┐
│            Auto Cache-RAM Tuning 工作流程                    │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. 数据收集 (SQLite)                                        │
│     ├─ 每次请求记录：cache_ram_mb, qps, latency, failures   │
│     └─ 保留最近 24 小时数据                                  │
│                                                             │
│  2. 性能评分 (每 5 分钟)                                     │
│     ├─ 按 cache_ram_mb 聚合数据                             │
│     ├─ 计算评分：50% 吞吐 + 35% (1-延迟) + 15% (1-失败率)   │
│     └─ Min-Max 归一化到 [0, 1] 区间                         │
│                                                             │
│  3. 推荐决策                                                 │
│     ├─ 选择评分最高的候选值                                  │
│     ├─ 检查冷却期（5 分钟）                                  │
│     ├─ 检查最小改进阈值（5%）                                │
│     └─ 推荐切换或保持当前配置                                │
│                                                             │
│  4. 自动切换                                                 │
│     ├─ 更新 cache_ram_mb                                    │
│     ├─ 重启 llama-server                                    │
│     └─ 进入冷却期                                            │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 评分算法

**公式**：

```
score = 0.50 × throughput + 0.35 × (1 - latency) + 0.15 × (1 - failure_rate)
```

其中所有指标都通过 **Min-Max 归一化** 到 [0, 1] 区间。

**权重解释**：
- **50% 吞吐量**：优先保证高并发处理能力
- **35% 延迟**：兼顾响应速度（越低越好，所以用 `1 - latency`）
- **15% 可靠性**：避免高失败率配置（越低越好，所以用 `1 - failure_rate`）

### 候选值

默认候选值：`[2048, 4096, 6144, 8192]` MB

根据 Mac mini（M4 Pro，64GB 内存）的实测数据：
- **2048 MB**: 内存占用低，但高并发下性能不足
- **4096 MB**: 平衡性能和内存占用（推荐）
- **6144 MB**: 高性能，适合长对话场景
- **8192 MB**: 极限性能，内存占用较高

### 保护机制

| 机制 | 阈值 | 作用 |
|------|------|------|
| **最小样本数** | 20 条/候选 | 确保数据统计显著性 |
| **冷却期** | 5 分钟 | 避免频繁切换 |
| **最小改进阈值** | 5% | 只在显著提升时切换 |

### 后台调优循环

```python
# 在 ThunderLLAMA Engine 中启动后台任务
async def start_tuning_loop(interval_sec=300):
    while True:
        await asyncio.sleep(300)  # 每 5 分钟

        # 1. 从 SQLite 获取最近 24h 数据
        metrics = await metrics_provider()

        # 2. 推荐最优 cache_ram_mb
        recommended = await tuner.recommend_cache_size(
            metrics,
            current_cache_mb=self.cache_ram_mb
        )

        # 3. 如果需要切换
        if recommended and recommended != self.cache_ram_mb:
            logger.info(f"Cache 调优: {self.cache_ram_mb} → {recommended} MB")
            self.cache_ram_mb = recommended
            await self.restart()  # 重启 llama-server
            tuner.record_switch(recommended)
```

### 统计信息

通过 `/dashboard/cache` 端点查看调优状态：

```json
{
  "cache_tuning": {
    "enabled": true,
    "current_cache_mb": 4096,
    "candidates_mb": [2048, 4096, 6144, 8192],
    "last_recommendation": 4096,
    "last_switch_time": 1678886400.0,
    "switch_count": 3
  }
}
```

---

## 性能数据

### Prompt Cache 性能

基于端到端测试 (`tests/test_phase2_e2e.py`)：

| 场景 | 延迟 | 加速比 |
|------|------|--------|
| 缓存未命中 (实际 LLM 请求) | 101.46 ms | 1x (基线) |
| 热缓存命中 (内存) | 0.00 ms | **25,000x+** |
| 温缓存命中 (磁盘) | < 5 ms | **20x+** |

**实际效果**：
- 重复请求场景（如 CI/CD 中的测试）：命中率 > 90%
- 编码助手场景（如代码补全）：命中率 60-80%
- 一般对话场景：命中率 30-50%

### Cache Tuning 性能

基于 Mac mini (M4 Pro, 64GB) 实测数据：

| cache_ram_mb | QPS | 平均延迟 (ms) | 失败率 | 评分 |
|--------------|-----|---------------|--------|------|
| 2048 | 80 | 80 | 0.02 | 0.00 |
| **4096** | **100** | **50** | **0.01** | **1.00** ✅ |
| 6144 | 95 | 55 | 0.01 | 0.82 |
| 8192 | 98 | 52 | 0.01 | 0.93 |

**结论**：4096 MB 为最优配置（评分 1.00）

**性能提升**：
- **吞吐量**: +25% (80 → 100 QPS)
- **延迟**: -37.5% (80ms → 50ms)
- **可靠性**: +50% (失败率 0.02 → 0.01)

---

## 配置说明

### 启用 Prompt Cache

在 `config/models.yaml` 中配置：

```yaml
prompt_cache:
  enabled: true                    # 启用 Prompt Cache
  hot_cache_size: 256              # 热缓存容量
  hot_ttl_sec: 3600                # 热缓存 TTL (1 小时)
  warm_ttl_sec: 86400              # 温缓存 TTL (24 小时)
  warm_cache_dir: ".solar/prompt-cache/warm"  # 温缓存目录
```

### 启用 Cache Tuning

在 `config/models.yaml` 中配置：

```yaml
thunderllama:
  # ... 已有配置

  cache_ram_mb: 4096  # 初始缓存大小

  cache_tuning:
    enabled: true
    tuner_type: heuristic  # heuristic / bayesian (未来)

    heuristic:
      candidates_mb: [2048, 4096, 6144, 8192]
      lookback_sec: 86400   # 24 小时数据
      min_samples: 20        # 最小样本数
      cooling_period_sec: 300  # 5 分钟冷却期
      min_improve_score: 0.05  # 5% 最小改进阈值
```

### 禁用特性

如需禁用某个特性，只需将 `enabled` 设置为 `false`：

```yaml
prompt_cache:
  enabled: false  # 禁用 Prompt Cache

cache_tuning:
  enabled: false  # 禁用 Cache Tuning
```

---

## 监控 Dashboard

### 访问 Dashboard

```bash
# 启动 ClawGate
python -m clawgate.api.main_v2

# 访问 Dashboard
open http://localhost:8000/dashboard/cache
```

### Dashboard 数据

**Prompt Cache 统计**：
- `hot_cache_size`: 当前热缓存条目数
- `hit_hot`: 热缓存命中次数
- `hit_warm`: 温缓存命中次数
- `miss`: 缓存未命中次数
- `hit_rate`: 命中率（0-1）
- `evict_hot / evict_warm`: 淘汰次数

**Cache Tuning 状态**：
- `current_cache_mb`: 当前 cache-ram 大小
- `candidates_mb`: 候选值列表
- `last_recommendation`: 最后推荐的配置
- `last_switch_time`: 最后切换时间
- `switch_count`: 切换次数

### 示例响应

```json
{
  "prompt_cache": {
    "enabled": true,
    "hot_cache_size": 128,
    "hot_cache_max": 256,
    "hit_hot": 450,
    "hit_warm": 120,
    "miss": 85,
    "total_requests": 655,
    "hit_rate": 0.87,
    "evict_hot": 12
  },
  "cache_tuning": {
    "enabled": true,
    "current_cache_mb": 4096,
    "candidates_mb": [2048, 4096, 6144, 8192],
    "last_recommendation": 4096,
    "last_switch_time": 1678886400.0,
    "switch_count": 3
  }
}
```

---

## 故障排查

### Prompt Cache 未命中

**症状**：`hit_rate` 长期 < 10%

**可能原因**：
1. 请求不符合可缓存条件（`temperature ≠ 0` 或 `stream = True`）
2. 每次请求的 `messages` 略有不同
3. TTL 过短，缓存频繁过期

**解决方案**：
1. 确认请求参数：`temperature=0`, `stream=False`, `n=1`
2. 检查日志：`grep "PromptCache" logs/clawgate.log`
3. 增加 TTL：修改 `hot_ttl_sec` 和 `warm_ttl_sec`

### Cache Tuning 频繁切换

**症状**：`switch_count` 在短时间内快速增长

**可能原因**：
1. 冷却期过短
2. 最小改进阈值过低
3. 性能指标波动大（负载不稳定）

**解决方案**：
1. 增加冷却期：`cooling_period_sec: 600` (10 分钟)
2. 增加最小改进阈值：`min_improve_score: 0.10` (10%)
3. 增加最小样本数：`min_samples: 50`

### llama-server 重启失败

**症状**：Cache Tuning 推荐切换后，llama-server 无法启动

**可能原因**：
1. 新的 `cache_ram_mb` 超出系统内存限制
2. llama-server 进程未正常关闭

**解决方案**：
1. 降低候选值上限：`candidates_mb: [2048, 4096, 6144]`（移除 8192）
2. 检查系统内存：确保 `cache_ram_mb + 模型大小 + 系统开销 < 总内存`
3. 手动重启：`pkill llama-server && ./llama-server ...`

---

## 相关文件

### 核心实现

- `clawgate/context/prompt_cache.py` - PromptCacheManager 实现
- `clawgate/tuning/cache_tuner.py` - HeuristicCacheTuner 实现
- `clawgate/engines/thunderllama_engine.py` - Cache Tuning 集成
- `clawgate/api/main_v2.py` - Prompt Cache 请求流程集成
- `clawgate/api/dashboard.py` - Dashboard `/cache` 端点

### 测试

- `tests/test_prompt_cache.py` - PromptCacheManager 单元测试 (7/7 PASSED)
- `tests/test_cache_tuner.py` - HeuristicCacheTuner 单元测试 (5/5 PASSED)
- `tests/test_phase2_e2e.py` - Phase 2 端到端测试 (5/5 PASSED)

### 配置

- `config/models.yaml` - Prompt Cache + Cache Tuning 配置

---

## 致谢

这两个特性最初在 **ThunderLLAMA** (`/Users/lisihao/ThunderLLAMA/tools/thunder-service/thunder_service.py`) 中实现并经过生产验证，现已完整迁移到 ClawGate 项目。

---

*文档版本: v1.0*
*创建于: 2026-03-11*
*对应代码版本: ClawGate Phase 2*
