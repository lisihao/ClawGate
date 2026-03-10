# ClawGate - Agent-Aware LLM Gateway

**为多 Agent 协作场景设计的智能 LLM 路由与调度引擎**

不是又一个 LLM Proxy。ClawGate 理解你的任务意图、追踪每个 Agent 的行为、控制每个模型的并发，然后做出最聪明的调度决策。

```
请求 → TaskClassifier(类型+复杂度+敏感度)
  → ModelSelector(质量/成本/agent偏好/负载感知)
  → QueueManager(三车道优先级 + per-model 信号量 + agent 公平性)
  → CloudDispatcher(重试+fallback链+熔断器) / 本地引擎(MLX/llama.cpp)
  → 响应 + SQLite 日志 + Dashboard
```

---

## 竞品对比

### 通用能力

| 维度 | ClawGate | LiteLLM | OpenRouter | Portkey | Helicone | Kong/KrakenD |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Provider 数量** | 5 云端 + 本地 | ✅ 100+ | ✅ 200+ | ✅ 多 | ✅ 多 | 🔶 插件扩展 |
| **OpenAI 兼容** | ✅ | ✅ | ✅ | ✅ | ✅ | 🔶 需配置 |
| **重试 + Fallback** | ✅ 跨 provider 链 | ✅ | ✅ | ✅ | ✅ | 🔶 需配置 |
| **熔断器** | ✅ per-backend | ✅ | ⚠️ | ✅ | ⚠️ | ✅ |
| **可观测性** | ⚠️ SQLite + 6 API | ⚠️ 回调/日志 | ✅ SaaS 平台 | ✅ 完整链路 | ✅ 深度分析 | ⚠️ 插件依赖 |
| **成本管理** | ⚠️ 仅记录 | ✅ 追踪+预算 | ✅ 统一计费 | ✅ 预算+告警 | ✅ 看板 | ❌ |
| **多租户** | ❌ | ⚠️ Virtual Key | ✅ 项目/Key | ✅ 团队/RBAC | ✅ 组织 | ✅ Workspace |
| **缓存** | ⚠️ 语义缓存 | ✅ | ⚠️ | ✅ | ✅ | ✅ |
| **安全 / 鉴权** | ❌ | ⚠️ 基础 Auth | ✅ 平台级 | ✅ Guardrails | ✅ | ✅ 企业级 |
| **部署方式** | ❌ 单机脚本 | ✅ Docker/K8s | ✅ SaaS | ✅ Docker/Cloud | ✅ | ✅ |
| **SDK / 客户端** | ❌ | ✅ Python/JS | ✅ REST | ✅ Python/JS | ✅ REST | 🔶 通用 HTTP |

> ✅ = 完整实现 / ⚠️ = 部分实现 / ❌ = 缺失 / 🔶 = 需外部扩展

**结论**：通用能力层面 ClawGate 落后于所有竞品。缺少鉴权、多租户、Docker 部署、SDK。

---

### 智能调度能力 (ClawGate 核心差异化)

| 维度 | ClawGate | LiteLLM | OpenRouter | Portkey | Helicone | Kong/KrakenD |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **任务感知路由** | ✅ 类型+复杂度+敏感度 | ❌ | ❌ | 🔶 条件路由可模拟 | ❌ | 🔶 需复杂配置 |
| **Agent 感知调度** | ✅ per-agent 追踪+公平性降级 | ❌ | ❌ | ❌ | ❌ | ❌ |
| **优先级队列** | ✅ 三车道+时长估算 | ❌ | ❌ | ❌ | ❌ | ⚠️ 限流策略 |
| **敏感内容语义路由** | ✅ 检测→宽容模型路由 | ❌ | ⚠️ :exacto 非语义 | 🔶 Guardrails 外挂 | ❌ | 🔶 需外部审查 |
| **混合引擎统一调度** | ✅ 本地+云端同一队列 | ⚠️ 支持但调度分离 | ❌ SaaS 无本地 | ❌ | ❌ | 🔶 可路由但非 LLM 专用 |
| **上下文压缩引擎** | ✅ 5 策略+语义缓存 | ❌ | ❌ | ❌ | ❌ | ❌ |
| **负载感知模型选择** | ✅ 实时负载→选择权重 | ⚠️ least-busy | ⚠️ 系统级 | ❌ | ❌ | ⚠️ 负载均衡 |
| **Per-model 并发控制** | ✅ 信号量精细控制 | ⚠️ 全局并发 | ❌ | ❌ | ❌ | ✅ 高级限流 |

**结论**：智能调度层面 ClawGate 全面领先。8 个维度中 8 个 ✅，竞品最多 2 个 ⚠️。

---

### 一句话定位

| 产品 | 定位 |
| :--- | :--- |
| **ClawGate** | Agent 感知的智能 LLM 调度引擎 — 理解任务意图、追踪 Agent 行为、控制模型并发 |
| **LiteLLM** | 轻量级 LLM 代理层 — 100+ Provider 统一接入，开箱即用 |
| **OpenRouter** | 模型聚合 SaaS — 一键访问 200+ 模型，自动成本优化 |
| **Portkey** | 企业级 AI Gateway — 可控性与合规性优先，Guardrails + A/B 测试 |
| **Helicone** | 可观测性驱动的 LLM 运维平台 — 深度分析、追踪、缓存洞察 |
| **Kong/KrakenD** | 通用 API Gateway + AI 插件 — 企业级流量治理，非 LLM 专用 |

---

## 架构

```
                              ClawGate Gateway
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  POST /v1/chat/completions                                          │
│       │                                                             │
│       ▼                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────────┐        │
│  │TaskClassifier│───▶│ModelSelector │───▶│  QueueManager   │        │
│  │              │    │              │    │                 │        │
│  │ task_type    │    │ quality/cost │    │ ┌─────────────┐ │        │
│  │ complexity   │    │ agent_prefs  │    │ │  fast lane  │ │        │
│  │ sensitivity  │    │ load_info ◀──────│ │  2 workers  │ │        │
│  │ duration_est │    │ reranking    │    │ ├─────────────┤ │        │
│  └──────────────┘    └──────────────┘    │ │ normal lane │ │        │
│                                          │ │  3 workers  │ │        │
│                                          │ ├─────────────┤ │        │
│                                          │ │  bg lane    │ │        │
│                                          │ │  2 workers  │ │        │
│                                          │ └──────┬──────┘ │        │
│                                          │ per-model sem   │        │
│                                          │ agent fairness  │        │
│                                          └────────┬────────┘        │
│                                                   │                 │
│                              ┌────────────────────┼──────────┐      │
│                              ▼                    ▼          ▼      │
│                      ┌──────────────┐    ┌────────────┐  ┌────────────┐  │
│                      │CloudDispatcher│    │ MLX Engine │  │ThunderLLAMA│  │
│                      │              │    │(Apple M1+) │  │(llama.cpp) │  │
│                      │ retry(3x)    │    └────────────┘  └────────────┘  │
│                      │ fallback     │         本地引擎 (零成本)            │
│                      │ circuit_break│                               │
│                      │ in_flight    │                               │
│                      └──────┬───────┘                               │
│                             │                                       │
│              ┌──────┬───────┼───────┬──────────┐                    │
│              ▼      ▼       ▼       ▼          ▼                    │
│            GLM  DeepSeek  OpenAI  ChatGPT   Gemini                  │
│                                   (订阅复用)  (宽容路由)              │
│                                                                     │
│  Observability: SQLite 全量日志 + 6 Dashboard API                    │
│  Context: 5 种压缩策略 + 语义缓存 + 话题分段                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 核心特性

### 1. 任务感知路由

TaskClassifier 分析每条消息的任务类型、复杂度和敏感度，驱动模型选择：

```python
# 自动分析 → 选最合适的模型
response = client.chat.completions.create(
    model="auto",  # TaskClassifier → ModelSelector 自动决策
    messages=[{"role": "user", "content": "分析这段代码的安全漏洞"}]
)
# → reasoning + high complexity → deepseek-r1
```

| 任务类型 | 复杂度 | 路由结果 |
| :--- | :--- | :--- |
| reasoning | high | deepseek-r1 / gpt-4o |
| coding | medium | glm-5 / deepseek-v3 |
| qa | low | glm-4-flash / qwen-1.7b (本地) |
| creative (NSFW) | any | gemini-2.5-pro (宽容) |

### 2. Agent 感知调度

每个 Agent 有独立的追踪和配额，防止一个忙碌 Agent 饿死其他 Agent：

```python
# Agent judge 发请求 → 带 agent_id
response = client.chat.completions.create(
    model="deepseek-r1",
    messages=[...],
    extra_body={
        "agent_id": "judge-001",
        "agent_type": "judge",
        "priority": 0,  # urgent → fast lane
    }
)
```

- `agent_id` 追踪 in-flight 请求数
- 超过公平份额(模型并发 * 60%)时，请求自动降级到 background 车道
- 每个 Agent 类型有偏好模型配置(judge → deepseek-r1, builder → glm-5)

### 3. 三车道优先级队列

基于优先级和时长估算，请求分流到不同车道：

```
Fast Lane (2 workers)     ← priority=0 或 FAST 任务 (< 5s)
Normal Lane (3 workers)   ← priority=1 + MEDIUM 任务 (5-30s)
Background Lane (2 workers) ← priority=2 或 LONG 任务 (30s+)
```

- 时长估算基于 TaskClassifier 输出：QA+短消息 → FAST，高复杂度+长消息 → LONG
- 每个模型有独立的 asyncio.Semaphore 控制并发
- 队列满(200)时返回 HTTP 429

### 4. 混合引擎统一调度

本地模型和云端 API 在同一个队列系统中调度：

| 引擎 | 模型 | 信号量 | 成本 |
| :--- | :--- | :--- | :--- |
| MLX (Apple Silicon) | qwen2.5-7b-mlx, llama3.1-8b-mlx | 1 | 零 |
| ThunderLLAMA (llama.cpp) | qwen-1.7b, qwen2.5-7b-q4/q8 | 1 | 零 |
| DeepSeek API | deepseek-r1, deepseek-v3 | 5 | $0.0014/1K |
| GLM API | glm-5, glm-4-flash | 5 | $0.0001-0.001/1K |
| OpenAI API | gpt-4o | 3 | $0.005/1K |
| ChatGPT 订阅 | gpt-5.2, gpt-5.1 | 2 | 零 (订阅) |
| Gemini API | gemini-2.5-pro/flash | 5 | $0.00125/1K |

### 5. CloudDispatcher (重试 + Fallback + 熔断)

```
GLM 请求 → glm backend → 失败?
  → deepseek fallback (自动 remap glm-4-flash → deepseek-chat)
  → 3 次连续失败 → 熔断器 OPEN (60s 冷却)
  → per-backend in-flight 计数
```

### 6. 上下文压缩引擎

5 种策略适配不同场景：

| 策略 | 适用场景 | 压缩方式 |
| :--- | :--- | :--- |
| Sliding Window | flash agent (快速任务) | 保留最近 N 条 |
| Summarization | 长对话 | LLM 生成摘要替换旧消息 |
| Selective Retain | judge agent (决策追踪) | 保留代码块/错误/决策，丢弃填充 |
| Adaptive | 通用 | 根据 agent 类型自动选策略 |
| Topic-Aware | 混合话题对话 | 按话题分段，差异化压缩 |

语义缓存(Jaccard 0.85 阈值)避免重复压缩相同消息模式。

---

## Observability

6 个 Dashboard API 端点：

```
GET /dashboard/overview    → 24h 总览: 请求数、成功率、平均延迟
GET /dashboard/models      → Per-model: TTFT P50/P99、token 量、成本
GET /dashboard/backends    → Per-backend: 熔断器状态、成功率、in-flight
GET /dashboard/context     → 上下文: 缓存命中率、压缩比
GET /dashboard/scheduler   → 调度器: 车道深度、agent 公平性、并发余量
GET /dashboard/timeline    → 时序: 每分钟请求数 (最近 1h)
```

全量请求日志写入 SQLite，包含 agent_id, model, TTFT, tokens, cost, compression_ratio。

---

## 快速开始

### 系统要求

- Python 3.9+
- 推荐：Apple Silicon (M1/M2/M3) 或 NVIDIA GPU
- 最低：8GB RAM，20GB 硬盘

### 安装与启动

```bash
cd ~/ClawGate

# 安装依赖
pip install -r requirements.txt

# 配置 API Key (按需)
export GLM_API_KEY="your-key"
export DEEPSEEK_API_KEY="your-key"

# 启动
./scripts/start.sh
```

服务启动后：
- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health
- 调度看板：http://localhost:8000/dashboard/scheduler

### 使用示例

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"
)

# 自动路由
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "用 Python 写个快速排序"}]
)

# 指定模型 + Agent 调度
response = client.chat.completions.create(
    model="deepseek-r1",
    messages=[{"role": "user", "content": "分析这段代码"}],
    extra_body={
        "agent_id": "judge-001",
        "agent_type": "judge",
        "priority": 0,
    }
)

# 流式响应
stream = client.chat.completions.create(
    model="glm-5",
    messages=[{"role": "user", "content": "讲个故事"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

---

## 配置

### config/models.yaml

```yaml
cloud_models:
  - name: "deepseek-r1"
    provider: "deepseek"
    cost_per_1k: 0.0014
    quality_score: 0.95
    use_cases: ["reasoning", "coding"]

agent_profiles:
  judge:
    preferred_models: ["deepseek-r1", "gpt-5.2"]
    fallback_models: ["deepseek-v3", "gpt-5.1"]
    chunk_size: 512
    priority: 1

scheduling:
  max_total_queue: 200
  agent_fair_share: 0.6
  workers:
    fast: 2
    normal: 3
    background: 2
  concurrency:
    local_default: 1
    cloud_default: 5
    per_backend:
      deepseek: 5
      glm: 5
      openai: 3
      chatgpt: 2
      gemini: 5
```

---

## 项目结构

```
clawgate/                           7,800+ 行
├── api/
│   ├── main_v2.py                 主路由 + QueueManager 集成
│   └── dashboard.py               6 个可观测性端点
├── backends/cloud/
│   ├── dispatcher.py              重试 + fallback + 熔断器 + in-flight
│   ├── glm.py                     智谱 GLM
│   ├── deepseek.py                DeepSeek
│   ├── openai.py                  OpenAI
│   ├── chatgpt_backend.py         ChatGPT 订阅复用
│   └── gemini.py                  Google Gemini
├── context/
│   ├── manager.py                 上下文压缩管理器
│   ├── semantic_cache.py          语义缓存 (Jaccard)
│   ├── conversation_store.py      会话持久化 + LTM
│   ├── topic_segmenter.py         话题分段
│   └── strategies/                5 种压缩策略
├── router/
│   ├── classifier.py              任务分类 + 敏感度检测
│   └── selector.py                模型选择 + 负载感知 reranking
├── scheduler/
│   ├── queue_manager.py           三车道调度 + agent 公平性
│   └── continuous_batching.py     连续批处理调度器
├── storage/
│   └── sqlite_store.py            全量请求日志 + 统计查询
├── engines/                       本地推理引擎 (MLX/ThunderLLAMA)
├── config/                        YAML 配置
└── tests/                         196 个测试 (含 14 E2E)
```

---

## 测试

```bash
# 运行全部测试
pytest tests/ --ignore=tests/test_engines.py

# 只跑 E2E 测试
pytest tests/test_queue_manager_e2e.py -v

# 只跑调度器单元测试
pytest tests/test_queue_manager.py -v
```

当前状态：**196 passed / 1 pre-existing failure**

---

## 已知限制与 Roadmap

### 当前限制

- **单机部署**：无 Docker/K8s，无高可用
- **SQLite 存储**：高并发写入可能成瓶颈
- **无 API Key 鉴权**：不适合多用户场景
- **无多租户**：无项目/团队级隔离
- **无 SDK**：需直接 HTTP 调用
- **5 家 Provider**：远少于 LiteLLM 的 100+

### Roadmap

- [x] Phase 1: MVP 基础路由 + 本地引擎
- [x] Phase 2: Context Engine 上下文压缩
- [x] Phase 3: Smart Routing 任务感知路由
- [x] Phase 4: CloudDispatcher 重试+Fallback+熔断
- [x] Phase 5: QueueManager 三车道调度 + Agent 公平性
- [x] Phase 6: Observability Dashboard
- [ ] Phase 7: Docker 化 + API Key 鉴权
- [ ] Phase 8: 成本预算 + 项目级配额
- [ ] Phase 9: Python SDK
- [ ] Phase 10: PostgreSQL/Redis 替换 SQLite

---

## 技术栈

Python 3.9 / FastAPI 0.109 / asyncio / SQLite / httpx / MLX / llama.cpp

---

## License

MIT License

---

**Made with care by OpenClaw Team**
