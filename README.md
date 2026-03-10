<p align="center">
  <h1 align="center">ClawGate</h1>
  <p align="center">
    <strong>The All-in-One Inference Service Layer for <a href="https://github.com/lisihao">Claw</a></strong>
  </p>
  <p align="center">
    One API. Every model. Zero headaches.
  </p>
  <p align="center">
    <a href="#quick-start">Quick Start</a> &bull;
    <a href="#features">Features</a> &bull;
    <a href="#architecture">Architecture</a> &bull;
    <a href="#vs-alternatives">vs Alternatives</a> &bull;
    <a href="#roadmap">Roadmap</a>
  </p>
  <p align="center">
    <img src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square" alt="Python 3.10+">
    <img src="https://img.shields.io/badge/tests-196%20passed-brightgreen?style=flat-square" alt="Tests">
    <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
    <img src="https://img.shields.io/badge/API-OpenAI%20compatible-orange?style=flat-square" alt="OpenAI Compatible">
  </p>
</p>

---

**ClawGate** is an agent-aware LLM inference gateway purpose-built for multi-agent orchestration systems. It sits between your agents and every LLM provider — local or cloud — providing intelligent routing, priority scheduling, context management, and automatic failover through a single OpenAI-compatible API.

```
Your Agents ─── POST /v1/chat/completions ───▶ ClawGate
                                                  │
              ┌───────────────────────────────────┤
              │                                   │
         Local Engines                      Cloud Providers
    ┌─────────┴─────────┐            ┌────────────┴────────────┐
    │  MLX (Apple M1+)  │            │  DeepSeek  ·  OpenAI    │
    │  ThunderLLAMA     │            │  GLM  ·  Gemini         │
    │  (llama.cpp)      │            │  ChatGPT (subscription) │
    └───────────────────┘            └─────────────────────────┘
```

Drop in `model="auto"` and ClawGate figures out the rest: what kind of task it is, which model fits best, which lane to queue it in, and what to do when things go wrong.

---

## Why ClawGate?

Most LLM gateways treat every request the same — a dumb proxy that forwards traffic. That works fine for single-user chatbots, but **multi-agent systems have fundamentally different needs:**

| Problem | What happens without ClawGate | What ClawGate does |
| :--- | :--- | :--- |
| **Agent starvation** | One busy agent hogs all model concurrency | Per-agent tracking + fairness degradation |
| **Wrong model for the job** | `gpt-4o` for a yes/no question burns money | Task classification → auto-selects cheapest qualified model |
| **Provider outages** | Your whole system goes down | Circuit breaker + cross-provider fallback chains |
| **Context explosion** | 200-turn conversations blow up token limits | 5 compression strategies + semantic caching |
| **Priority inversion** | Urgent requests stuck behind batch jobs | 3-lane priority queue with duration estimation |
| **Local vs cloud chaos** | Separate code paths, separate configs | Unified queue — same API for MLX, llama.cpp, and cloud |

**ClawGate is the missing infrastructure layer between your agent framework and the LLM providers.**

---

<a id="features"></a>
## Features

### Task-Aware Routing

Every request is analyzed for **task type**, **complexity**, and **sensitivity** before model selection:

```python
# Just say "auto" — ClawGate handles the rest
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Analyze the security vulnerabilities in this code"}]
)
# → reasoning + high complexity → deepseek-r1
```

| Task Type | Complexity | Routed To |
| :--- | :--- | :--- |
| reasoning | high | deepseek-r1, gpt-4o |
| coding | medium | glm-5, deepseek-v3 |
| qa | low | glm-4-flash, qwen-1.7b (local) |
| creative (sensitive) | any | gemini-2.5-pro (permissive provider) |

Sensitive content is automatically detected and routed to providers with more permissive policies — no manual intervention needed.

### Agent-Aware Scheduling

Each agent gets its own tracking, quota, and fairness guarantee:

```python
response = client.chat.completions.create(
    model="deepseek-r1",
    messages=[...],
    extra_body={
        "agent_id": "judge-001",
        "agent_type": "judge",    # gets preferred models from config
        "priority": 0,            # → fast lane
    }
)
```

- **Per-agent in-flight tracking** — know exactly who's using what
- **Fairness degradation** — agents exceeding 60% of model concurrency get demoted to background lane
- **Agent profiles** — each agent type has preferred models, fallbacks, and priority presets

### 3-Lane Priority Queue

Requests are classified by priority and estimated duration, then dispatched to the appropriate lane:

```
┌─────────────────────────────────────────────────────┐
│                    QueueManager                      │
│                                                      │
│  Fast Lane ⚡   (2 workers)  ← urgent + short tasks  │
│  Normal Lane 🔄 (3 workers)  ← standard requests     │
│  Background Lane 🐢 (2 workers)  ← batch + long jobs │
│                                                      │
│  Per-model semaphores: deepseek(5) glm(5) local(1)  │
│  Queue capacity: 200 (HTTP 429 when full)            │
└─────────────────────────────────────────────────────┘
```

### Hybrid Engine Unification

Local models and cloud APIs share the same queue, the same API, the same monitoring:

| Engine | Models | Concurrency | Cost |
| :--- | :--- | :--- | :--- |
| **MLX** (Apple Silicon) | qwen2.5-7b, llama3.1-8b | 1 | Free |
| **ThunderLLAMA** (llama.cpp) | qwen-1.7b, qwen2.5-7b-q4/q8 | 1 | Free |
| **DeepSeek** | deepseek-r1, deepseek-v3 | 5 | $0.0014/1K |
| **GLM** | glm-5, glm-4-flash | 5 | $0.0001–0.001/1K |
| **OpenAI** | gpt-4o | 3 | $0.005/1K |
| **ChatGPT** (subscription) | gpt-5.2, gpt-5.1, codex | 2 | Free (subscription) |
| **Gemini** | gemini-2.5-pro/flash | 5 | $0.00125/1K |

### Resilient Cloud Dispatch

Every cloud request goes through a battle-tested dispatch pipeline:

```
Request → Primary Provider → Failed?
  → Retry with exponential backoff (3x)
  → Fallback to next provider in chain
  → 3 consecutive failures → Circuit breaker OPEN (60s cooldown)
  → Recovery → HALF_OPEN → test request → CLOSED
```

Cross-provider fallback chains are fully configurable. Model names are auto-remapped between providers.

### Context Compression Engine

5 strategies to keep conversations within token limits without losing important context:

| Strategy | Best For | How It Works |
| :--- | :--- | :--- |
| **Sliding Window** | Fast agents | Keep last N messages |
| **Summarization** | Long conversations | LLM-generated summary replaces old messages |
| **Selective Retain** | Decision tracking | Keep code blocks, errors, decisions; drop filler |
| **Adaptive** | General use | Auto-selects strategy based on agent type |
| **Topic-Aware** | Multi-topic dialogs | Segment by topic, compress differentially |

Plus **semantic caching** (Jaccard similarity, 0.85 threshold) to avoid redundant compression, and an **anti-hallucination conversation store** inspired by MIT research on LLM self-generated content.

### Observability Dashboard

6 API endpoints for full visibility into your inference layer:

```
GET /dashboard/overview    → 24h summary: requests, success rate, avg latency
GET /dashboard/models      → Per-model: TTFT P50/P99, tokens, cost
GET /dashboard/backends    → Per-backend: circuit breaker state, error rate
GET /dashboard/context     → Context engine: cache hits, compression ratio
GET /dashboard/scheduler   → Queue: lane depth, agent fairness, concurrency
GET /dashboard/timeline    → Time series: requests/min (last 1h)
```

Every request is logged to SQLite with full metadata: agent_id, model, TTFT, tokens, cost, compression_ratio.

---

<a id="architecture"></a>
## Architecture

```
                              ClawGate
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  POST /v1/chat/completions (OpenAI-compatible)                      │
│       │                                                             │
│       ▼                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌─────────────────┐        │
│  │TaskClassifier│───▶│ModelSelector │───▶│  QueueManager   │        │
│  │              │    │              │    │                 │        │
│  │ task_type    │    │ quality/cost │    │ ┌─────────────┐ │        │
│  │ complexity   │    │ agent_prefs  │    │ │  fast lane  │ │        │
│  │ sensitivity  │    │ load_aware   │    │ │  2 workers  │ │        │
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
│                      ┌──────────────┐    ┌────────────┐ ┌─────────┐ │
│                      │CloudDispatcher│    │ MLX Engine │ │Thunder- │ │
│                      │              │    │(Apple M1+) │ │ LLAMA   │ │
│                      │ retry(3x)    │    └────────────┘ └─────────┘ │
│                      │ fallback     │        Local Engines (free)    │
│                      │ circuit_break│                                │
│                      └──────┬───────┘                                │
│                             │                                        │
│              ┌──────┬───────┼───────┬──────────┐                     │
│              ▼      ▼       ▼       ▼          ▼                     │
│            GLM  DeepSeek  OpenAI  ChatGPT   Gemini                   │
│                                                                      │
│  Storage: SQLite (full request logging)                              │
│  Context: 5 compression strategies + semantic cache                  │
│  Monitor: 6 dashboard endpoints                                     │
└──────────────────────────────────────────────────────────────────────┘
```

---

<a id="quick-start"></a>
## Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/lisihao/ClawGate.git
cd ClawGate
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys:
#   GLM_API_KEY=your-key
#   DEEPSEEK_API_KEY=your-key
#   OPENAI_API_KEY=your-key       (optional)
```

### 3. Launch

```bash
./scripts/start.sh
```

That's it. ClawGate is now running at `http://localhost:8000`.

- **API Docs**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **Scheduler Dashboard**: http://localhost:8000/dashboard/scheduler

### 4. Send Your First Request

```python
import openai

client = openai.OpenAI(base_url="http://localhost:8000/v1", api_key="any")

# Auto-routing: ClawGate picks the best model
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Write a quicksort in Python"}]
)
print(response.choices[0].message.content)
```

Works with **any OpenAI-compatible client** — LangChain, LlamaIndex, CrewAI, AutoGen, or just plain `curl`.

---

## Usage Examples

### Auto-Routing (Let ClawGate Decide)

```python
# Simple question → routed to cheapest model (glm-4-flash or local)
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "What is 2+2?"}]
)

# Complex reasoning → routed to strongest model (deepseek-r1)
response = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Prove that the halting problem is undecidable"}]
)
```

### Agent-Aware Scheduling

```python
# High-priority agent request → fast lane
response = client.chat.completions.create(
    model="deepseek-r1",
    messages=[{"role": "user", "content": "Review this PR for security issues"}],
    extra_body={
        "agent_id": "security-reviewer-01",
        "agent_type": "judge",
        "priority": 0,  # 0=urgent, 1=normal, 2=background
    }
)
```

### Streaming

```python
stream = client.chat.completions.create(
    model="glm-5",
    messages=[{"role": "user", "content": "Tell me a story"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

### Specify Provider Directly

```python
# Force local inference (zero cost)
response = client.chat.completions.create(
    model="qwen-1.7b",  # ThunderLLAMA local model
    messages=[{"role": "user", "content": "Summarize this text"}]
)

# Force specific cloud provider
response = client.chat.completions.create(
    model="deepseek-r1",  # Direct to DeepSeek
    messages=[{"role": "user", "content": "Debug this code"}]
)
```

---

## Configuration

### config/models.yaml

```yaml
cloud_models:
  - name: "deepseek-r1"
    provider: "deepseek"
    cost_per_1k: 0.0014
    quality_score: 0.95
    use_cases: ["reasoning", "coding"]

# Agent profiles — preferred models per agent type
agent_profiles:
  judge:
    preferred_models: ["deepseek-r1", "gpt-5.2"]
    fallback_models: ["deepseek-v3", "glm-5"]
    priority: 1

  builder:
    preferred_models: ["glm-5", "qwen2.5-7b-mlx"]
    fallback_models: ["glm-4-flash"]
    priority: 2

# Queue scheduling parameters
scheduling:
  max_total_queue: 200
  agent_fair_share: 0.6
  workers:
    fast: 2
    normal: 3
    background: 2
  concurrency:
    per_backend:
      deepseek: 5
      glm: 5
      openai: 3
```

### config/engines.yaml

```yaml
# Auto-detect platform and select best local engine
auto_select: true

platform_priority:
  darwin_arm64: [mlx, llamacpp]    # Apple Silicon → MLX first
  linux: [llamacpp, vllm]          # Linux → llama.cpp, vLLM ready

llamacpp:
  enabled: true
  n_ctx: 32768
  n_gpu_layers: -1
  models:
    - name: "qwen-1.7b"
      path: "models/qwen3-1.7b-q4.gguf"
      quality_score: 0.75
```

---

<a id="vs-alternatives"></a>
## vs Alternatives

### Agent-Aware Capabilities (ClawGate's Differentiator)

| Capability | ClawGate | LiteLLM | OpenRouter | Portkey |
| :--- | :---: | :---: | :---: | :---: |
| **Task-aware routing** (type + complexity + sensitivity) | **Yes** | No | No | Partial |
| **Agent-aware scheduling** (per-agent tracking + fairness) | **Yes** | No | No | No |
| **Priority queue** (3-lane + duration estimation) | **Yes** | No | No | No |
| **Sensitive content routing** (detect → permissive provider) | **Yes** | No | No | Partial |
| **Hybrid local + cloud** (same queue, same API) | **Yes** | Partial | No | No |
| **Context compression** (5 strategies + semantic cache) | **Yes** | No | No | No |
| **Load-aware model selection** (realtime reranking) | **Yes** | Partial | Partial | No |
| **Per-model concurrency control** (semaphore isolation) | **Yes** | Partial | No | No |

### Where Others Win

| Capability | LiteLLM | OpenRouter | Portkey |
| :--- | :---: | :---: | :---: |
| Provider count | 100+ | 200+ | Many |
| Multi-tenant | Partial | Yes | Yes |
| Auth / RBAC | Basic | Yes | Yes |
| Docker / K8s | Yes | SaaS | Yes |
| Python/JS SDK | Yes | REST | Yes |
| Cost budgets | Yes | Yes | Yes |

**Bottom line**: If you need a simple proxy for 100+ providers, use LiteLLM. If you're building a multi-agent system that needs to intelligently schedule work across models while managing context, fairness, and resilience — that's what ClawGate was built for.

---

## Project Structure

```
clawgate/                              7,800+ lines of code
├── api/
│   ├── main_v2.py                     Main router + QueueManager integration
│   └── dashboard.py                   6 observability endpoints
├── backends/cloud/
│   ├── dispatcher.py                  Retry + fallback + circuit breaker
│   ├── glm.py                         Zhipu GLM
│   ├── deepseek.py                    DeepSeek
│   ├── openai.py                      OpenAI
│   ├── chatgpt_backend.py             ChatGPT subscription reuse
│   └── gemini.py                      Google Gemini
├── context/
│   ├── manager.py                     Context compression manager
│   ├── semantic_cache.py              Semantic cache (Jaccard similarity)
│   ├── conversation_store.py          Persistent memory + anti-hallucination
│   ├── topic_segmenter.py             Topic segmentation
│   └── strategies/                    5 compression strategies
├── router/
│   ├── classifier.py                  Task classification + sensitivity detection
│   └── selector.py                    Model selection + load-aware reranking
├── scheduler/
│   ├── queue_manager.py               3-lane scheduling + agent fairness
│   └── continuous_batching.py         Continuous batching scheduler
├── storage/
│   └── sqlite_store.py                Full request logging + analytics
├── engines/                           Local inference (MLX / ThunderLLAMA)
├── config/                            YAML configuration
└── tests/                             196 tests (including 14 E2E)
```

---

## Testing

```bash
# Run all tests
pytest tests/ --ignore=tests/test_engines.py

# E2E tests only
pytest tests/test_queue_manager_e2e.py -v

# Scheduler unit tests
pytest tests/test_queue_manager.py -v

# With coverage
pytest tests/ --ignore=tests/test_engines.py --cov=clawgate
```

Current status: **196 passed** across 10 test modules.

---

<a id="roadmap"></a>
## Roadmap

### Shipped

- [x] **Intelligent Routing** — Task classification + quality/cost model selection
- [x] **Agent-Aware Scheduling** — Per-agent tracking, fairness, priority lanes
- [x] **3-Lane Priority Queue** — Fast/Normal/Background with duration estimation
- [x] **Cloud Dispatcher** — Retry, fallback chains, circuit breaker, in-flight tracking
- [x] **Context Engine** — 5 compression strategies + semantic cache + topic segmentation
- [x] **Hybrid Engines** — MLX + ThunderLLAMA + 5 cloud providers in one queue
- [x] **Observability** — 6 dashboard endpoints + full SQLite request logging

### Next Up

- [ ] **Docker & Compose** — One-command deployment with pre-configured providers
- [ ] **API Key Authentication** — Multi-user support with per-key rate limits
- [ ] **Python SDK** — `pip install clawgate-client` with async support
- [ ] **Cost Budgets** — Per-agent and per-project spending limits with alerts
- [ ] **Streaming Metrics** — Real-time TTFT, TPS, and queue depth via WebSocket

### Future

- [ ] **PostgreSQL + Redis** — Replace SQLite for production-grade deployments
- [ ] **Multi-Tenant** — Team/project isolation with RBAC
- [ ] **Provider Plugins** — Drop-in support for new providers (Anthropic, Cohere, Mistral, ...)
- [ ] **Web Dashboard** — Real-time monitoring UI with charts and alerts
- [ ] **A/B Testing** — Route percentage of traffic to model variants, compare quality
- [ ] **vLLM / SGLang** — High-throughput local inference backends (interface ready)
- [ ] **Prompt Cache** — Provider-level KV cache reuse for repeated prefixes

---

## Tech Stack

| Component | Technology |
| :--- | :--- |
| **Runtime** | Python 3.10+ / asyncio |
| **API Framework** | FastAPI |
| **Local Inference** | MLX-LM (Apple Silicon), ThunderLLAMA (llama.cpp) |
| **HTTP Client** | httpx (async) |
| **Storage** | SQLite (aiosqlite) |
| **Tokenizer** | tiktoken |
| **Monitoring** | prometheus-client, structlog |

---

## Contributing

Contributions are welcome! Here's how to get started:

```bash
git clone https://github.com/lisihao/ClawGate.git
cd ClawGate
python3 -m venv venv && source venv/bin/activate
pip install -e ".[dev]"
pytest tests/ --ignore=tests/test_engines.py
```

Before submitting a PR, please make sure:
- All tests pass (`pytest tests/ --ignore=tests/test_engines.py`)
- Code follows existing style (`ruff check .`)
- New features include tests

---

## License

[MIT License](LICENSE) — use it however you want.

---

<p align="center">
  <strong>Built for agents that need more than a proxy.</strong>
  <br>
  <sub>If ClawGate helps your project, a star would mean a lot.</sub>
</p>
