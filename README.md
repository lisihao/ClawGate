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
    <img src="https://img.shields.io/badge/tests-201%20passed-brightgreen?style=flat-square" alt="Tests">
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
    │  ThunderLLAMA      │            │  DeepSeek  ·  OpenAI    │
    │  (Paged Attention  │            │  GLM  ·  Gemini         │
    │   + Decode-First)  │            │  ChatGPT (subscription) │
    │  MLX (Apple M1+)  │            └─────────────────────────┘
    └───────────────────┘
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
| **Context explosion** | 200-turn conversations blow up token limits | 5 compression strategies + semantic caching + ContextPilot dedup |
| **Priority inversion** | Urgent requests stuck behind batch jobs | 3-lane priority queue with duration estimation |
| **Local vs cloud chaos** | Separate code paths, separate configs | Unified queue — same API for ThunderLLAMA, MLX, and cloud |

**ClawGate is the missing infrastructure layer between your agent framework and the LLM providers.**

---

<a id="features"></a>
## Features

### ThunderLLAMA Integration (Local Inference)

ClawGate manages [ThunderLLAMA](https://github.com/lisihao/ThunderLLAMA) as its primary local inference engine via HTTP, with full support for ThunderLLAMA's custom Apple Silicon optimizations:

| Feature | Description | Config |
| :--- | :--- | :--- |
| **Flash Attention** | Fused attention kernels for Metal GPU | `-fa 1` |
| **Paged KV Cache** | Block-based KV cache with Copy-on-Write | `LLAMA_PAGED_ATTENTION=1` |
| **Decode-First Scheduling** | Decode tokens batched before prefill to reduce TTFT | Built-in |
| **Adaptive Chunk Prefill** | Limit prefill per slot per iteration for interleaving | `THUNDERLLAMA_CHUNK_PREFILL=512` |

```yaml
# config/engines.yaml
thunderllama:
  enabled: true
  server_binary: "~/ThunderLLAMA/build/bin/llama-server"
  port: 8090
  flash_attention: true
  paged_attention: true
  chunk_prefill: 512
```

ClawGate auto-detects a running llama-server and reuses it; if none is found, it starts one as a managed subprocess with watchdog restart.

### API Authentication & Budget Control

```python
# Bearer token auth
curl -H "Authorization: Bearer $CLAWGATE_API_KEY" http://localhost:8000/v1/chat/completions ...

# Budget enforcement — automatic 429 when limits exceeded
# Configured via environment:
#   CLAWGATE_BUDGET_DAILY=5.00
#   CLAWGATE_BUDGET_MONTHLY=100.00
```

- **API Key auth** with constant-time comparison (timing-attack resistant)
- **Daily & monthly budget limits** with automatic enforcement (HTTP 429)
- **Per-request cost tracking** wired to all 4 logging sites
- **CJK-aware token estimation** for streaming cost calculation

### ContextPilot (KV Cache-Aware Context Optimization)

Integrated from [ContextPilot (MLSys 2026)](https://github.com/EfficientContext/ContextPilot), this two-level system optimizes context before sending to the model:

**Level 1 — Reorder**: Rearranges context blocks to maximize KV cache prefix sharing across requests, achieving up to 3x prefill speedup.

**Level 2 — Dedup**: On Turn 2+ of multi-turn conversations, repeated document blocks are replaced with compact reference hints, saving ~25-30% prompt tokens.

```
Turn 1: [doc_A, doc_B, doc_C] → reorder for KV prefix sharing → register docs
Turn 2: [doc_A, doc_B, doc_D] → dedup doc_A/B as hints → only doc_D sent in full
         ↳ ~29% token savings
```

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
| **ThunderLLAMA** (HTTP) | qwen-1.7b (Paged Attn + Flash Attn) | 4 slots | Free |
| **MLX** (Apple Silicon) | qwen2.5-7b, llama3.1-8b | 1 | Free |
| **DeepSeek** | deepseek-r1, deepseek-v3 | 5 | $0.0014/1K |
| **GLM** | glm-5, glm-4-flash | 5 | $0.0001-0.001/1K |
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

### Prompt Cache + Auto Cache-RAM Tuning (Phase 2)

**Prompt Cache** — Two-tier response caching for deterministic requests (temperature=0, stream=False, n=1):

```
Hot Cache (in-memory LRU): 256 entries, 1h TTL  → ~0.1ms lookup
Warm Cache (disk JSON): 24h TTL, promotion after 3 hits
```

- **25,000x speedup** for cache hits (100ms → 0.004ms)
- SHA256-based cache keys from stable payload fields (model, messages, temperature, max_tokens)
- Automatic promotion: warm cache entries → hot cache after 3 hits
- Hit rate tracking via `/dashboard/cache`

**Auto Cache-RAM Tuning** — Data-driven optimization of ThunderLLAMA's cache-ram size:

```
Every 5 minutes:
  1. Query last 24h performance data (SQLite)
  2. Score each candidate: 50% throughput + 35% (1-latency) + 15% (1-failure)
  3. Recommend best cache-ram size
  4. Restart llama-server with new config (if significant improvement)
```

- **Heuristic scoring** with min-max normalization for fair comparison
- **Cooling period** (5 minutes) to prevent frequent restarts
- **Automatic fallback** to static config if tuning disabled
- Candidates: [2048, 4096, 6144, 8192] MB (configurable)

See [PHASE2_FEATURES.md](docs/PHASE2_FEATURES.md) for detailed architecture and configuration.

### Observability Dashboard

```
GET /dashboard/overview    → 24h summary: requests, success rate, avg latency
GET /dashboard/models      → Per-model: TTFT P50/P99, tokens, cost
GET /dashboard/backends    → Per-backend: circuit breaker state, error rate
GET /dashboard/context     → Context engine: cache hits, compression ratio
GET /dashboard/costs       → Budget progress (daily/monthly) + spend breakdown
GET /dashboard/sessions    → Active sessions, segments, messages per agent
GET /dashboard/scheduler   → Queue: lane depth, agent fairness, concurrency
GET /dashboard/timeline    → Time series: requests/min (last 1h)
GET /dashboard/cache       → Prompt cache + Cache tuning status  [NEW]
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
│  ┌──────────┐   ┌──────────┐   ┌──────────────┐   ┌─────────────┐  │
│  │  Auth +   │──▶│  Task    │──▶│    Model     │──▶│   Queue     │  │
│  │  Budget   │   │Classifier│   │  Selector    │   │  Manager    │  │
│  └──────────┘   └──────────┘   └──────────────┘   └──────┬──────┘  │
│                                                          │         │
│                                  ┌───────────────────────┤         │
│                                  │                       │         │
│                          ContextPilot               Cloud Path     │
│                        ┌─────────┴──────────┐            │         │
│                        │ L1: Reorder (KV$)  │            │         │
│                        │ L2: Dedup (~29%)   │            ▼         │
│                        └─────────┬──────────┘    ┌──────────────┐  │
│                                  │               │CloudDispatcher│  │
│                                  ▼               │ retry(3x)    │  │
│                          ┌──────────────┐        │ fallback     │  │
│                          │ThunderLLAMA  │        │ circuit_break│  │
│                          │ (HTTP:8090)  │        └──────┬───────┘  │
│                          │ Paged Attn   │              │          │
│                          │ Flash Attn   │   ┌────┬─────┼────┬───┐ │
│                          │ Decode-First │   ▼    ▼     ▼    ▼   ▼ │
│                          ├──────────────┤  GLM  DS  OpenAI GPT Gem │
│                          │  MLX Engine  │                          │
│                          │ (Apple M1+)  │  Storage: SQLite         │
│                          └──────────────┘  Monitor: 8 endpoints    │
└─────────────────────────────────────────────────────────────────────┘
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
#   CLAWGATE_API_KEY=your-key     (for auth)
#   CLAWGATE_BUDGET_DAILY=5.00    (optional)
#   CLAWGATE_BUDGET_MONTHLY=100   (optional)
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

client = openai.OpenAI(base_url="http://localhost:8000/v1", api_key="your-clawgate-key")

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

### Local Inference via ThunderLLAMA

```python
# Zero cost, runs on your Mac — Paged Attention + Flash Attention enabled
response = client.chat.completions.create(
    model="qwen-1.7b",  # ThunderLLAMA local model
    messages=[{"role": "user", "content": "Summarize this text"}]
)
```

---

## Configuration

### config/engines.yaml

```yaml
# Auto-detect platform and select best local engine
auto_select: true

platform_priority:
  darwin_arm64: [thunderllama, mlx, llamacpp]
  linux: [thunderllama, llamacpp, vllm]

thunderllama:
  enabled: true
  server_binary: "~/ThunderLLAMA/build/bin/llama-server"
  port: 8090
  n_gpu_layers: 99
  n_parallel: 4
  n_ctx: 8192
  flash_attention: true       # Flash Attention (-fa 1)
  paged_attention: true       # Paged KV Cache (LLAMA_PAGED_ATTENTION=1)
  chunk_prefill: 512          # Decode-First Scheduling
  models:
    - name: "qwen-1.7b"
      path: "~/models/Qwen3-1.7B-Q4_K_M.gguf"
      quality_score: 0.75

llamacpp:
  enabled: false  # Fallback when ThunderLLAMA binary unavailable
```

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

# Prompt Cache configuration (Phase 2)
prompt_cache:
  enabled: true
  hot_cache_size: 256
  hot_ttl_sec: 3600
  warm_ttl_sec: 86400
  warm_cache_dir: ".solar/prompt-cache/warm"

# Cache Tuning configuration (Phase 2)
thunderllama:
  cache_tuning:
    enabled: true
    tuner_type: heuristic  # heuristic / bayesian
    heuristic:
      candidates_mb: [2048, 4096, 6144, 8192]
      lookback_sec: 86400
      min_samples: 20
      cooling_period: 300
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
| **Context optimization** (ContextPilot reorder + dedup) | **Yes** | No | No | No |
| **Auth + Budget control** (API key + daily/monthly limits) | **Yes** | Basic | Yes | Yes |
| **Load-aware model selection** (realtime reranking) | **Yes** | Partial | Partial | No |
| **Per-model concurrency control** (semaphore isolation) | **Yes** | Partial | No | No |

### Where Others Win

| Capability | LiteLLM | OpenRouter | Portkey |
| :--- | :---: | :---: | :---: |
| Provider count | 100+ | 200+ | Many |
| Multi-tenant | Partial | Yes | Yes |
| Docker / K8s | Yes | SaaS | Yes |
| Python/JS SDK | Yes | REST | Yes |

**Bottom line**: If you need a simple proxy for 100+ providers, use LiteLLM. If you're building a multi-agent system that needs to intelligently schedule work across models while managing context, fairness, and resilience — that's what ClawGate was built for.

---

## Project Structure

```
clawgate/
├── api/
│   ├── main_v2.py                     Main router + QueueManager integration
│   ├── auth.py                        API Key authentication
│   ├── budget.py                      Daily/monthly budget enforcement
│   ├── sessions.py                    Session tracking per agent
│   └── dashboard.py                   9 observability endpoints (incl. /cache)
├── backends/cloud/
│   ├── dispatcher.py                  Retry + fallback + circuit breaker
│   ├── glm.py                         Zhipu GLM
│   ├── deepseek.py                    DeepSeek
│   ├── openai.py                      OpenAI
│   ├── chatgpt_backend.py             ChatGPT subscription reuse
│   └── gemini.py                      Google Gemini
├── context/
│   ├── context_pilot.py               ContextPilot: L1 reorder + L2 dedup
│   ├── manager.py                     Context compression manager
│   ├── semantic_cache.py              Semantic cache (Jaccard similarity)
│   ├── prompt_cache.py                Prompt cache (hot + warm tiers)  [PHASE 2]
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
├── engines/
│   ├── thunderllama_engine.py         ThunderLLAMA (HTTP → llama-server) + cache tuning
│   ├── mlx_engine.py                  MLX-LM (Apple Silicon)
│   ├── llamacpp_engine.py             llama-cpp-python (fallback)
│   ├── manager.py                     Platform-aware engine initialization
│   └── base.py                        BaseEngine interface
├── tuning/                            Performance optimization  [PHASE 2]
│   └── cache_tuner.py                 Heuristic cache-ram tuner
├── config/                            YAML configuration
│   ├── engines.yaml                   Local engine config (ThunderLLAMA/MLX)
│   └── models.yaml                    Cloud models + agent profiles + cache config
├── vendor/
│   └── contextpilot/                  ContextPilot v0.3.5
├── docs/
│   └── PHASE2_FEATURES.md             Prompt Cache + Cache Tuning documentation
└── tests/                             201 tests (including 19 E2E)
```

---

## Testing

```bash
# Run all tests
pytest tests/ --ignore=tests/test_engines.py

# E2E tests only
pytest tests/test_queue_manager_e2e.py -v
pytest tests/test_phase2_e2e.py -v  # Phase 2 integration tests

# Scheduler unit tests
pytest tests/test_queue_manager.py -v

# With coverage
pytest tests/ --ignore=tests/test_engines.py --cov=clawgate
```

Current status: **201 passed** across 11 test modules (including 5 Phase 2 E2E tests).

---

<a id="roadmap"></a>
## Roadmap

### Shipped

- [x] **Intelligent Routing** — Task classification + quality/cost model selection
- [x] **Agent-Aware Scheduling** — Per-agent tracking, fairness, priority lanes
- [x] **3-Lane Priority Queue** — Fast/Normal/Background with duration estimation
- [x] **Cloud Dispatcher** — Retry, fallback chains, circuit breaker, in-flight tracking
- [x] **Context Engine** — 5 compression strategies + semantic cache + topic segmentation
- [x] **Hybrid Engines** — ThunderLLAMA + MLX + 5 cloud providers in one queue
- [x] **Observability** — 8 dashboard endpoints + full SQLite request logging
- [x] **API Authentication** — Bearer token auth with constant-time comparison
- [x] **Budget Control** — Daily/monthly spending limits with automatic 429 enforcement
- [x] **Cost Tracking** — Per-request cost wired to all logging paths, CJK-aware estimation
- [x] **ContextPilot L1** — KV cache-aware context reordering (up to 3x prefill speedup)
- [x] **ContextPilot L2** — Multi-turn document deduplication (~29% token savings)
- [x] **ThunderLLAMA Engine** — HTTP-based engine with Paged Attention + Flash Attention + Decode-First
- [x] **Prompt Cache (Phase 2)** — Two-tier response caching (hot + warm) with 25,000x cache hit speedup
- [x] **Auto Cache-RAM Tuning (Phase 2)** — Heuristic tuner for data-driven ThunderLLAMA cache-ram optimization

### Next Up

- [ ] **Docker & Compose** — One-command deployment with pre-configured providers
- [ ] **Python SDK** — `pip install clawgate-client` with async support
- [ ] **Streaming Metrics** — Real-time TTFT, TPS, and queue depth via WebSocket
- [ ] **Web Dashboard** — Real-time monitoring UI with charts and alerts

### Future

- [ ] **PostgreSQL + Redis** — Replace SQLite for production-grade deployments
- [ ] **Multi-Tenant** — Team/project isolation with RBAC
- [ ] **Provider Plugins** — Drop-in support for new providers (Anthropic, Cohere, Mistral, ...)
- [ ] **A/B Testing** — Route percentage of traffic to model variants, compare quality
- [ ] **vLLM / SGLang** — High-throughput local inference backends (interface ready)
- [ ] **Bayesian Cache Tuner** — Gaussian Process-based auto-search for optimal cache-ram
- [ ] **Provider-Level Prompt Cache** — KV cache reuse for cloud providers (when supported)

---

## Tech Stack

| Component | Technology |
| :--- | :--- |
| **Runtime** | Python 3.10+ / asyncio |
| **API Framework** | FastAPI |
| **Local Inference** | ThunderLLAMA (HTTP → llama-server), MLX-LM (Apple Silicon) |
| **Context Optimization** | ContextPilot (KV cache reorder + multi-turn dedup) |
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
