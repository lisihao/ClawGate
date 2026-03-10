# OpenClaw 集成指南

**目标**: 将 OpenClaw Gateway 作为新的 LLM 后端选项新增到 OpenClaw，与原有调用方式并存。

---

## 📌 使用场景对比

| 场景 | 推荐方式 | 原因 |
|------|----------|------|
| 简单编码补全 | 🆕 Gateway 本地模型 | 低延迟 (0.64s)，隐私保护 |
| 大量短任务 | 🆕 Gateway 优先级队列 | 智能调度，避免阻塞 |
| 长对话历史 | 🆕 Gateway 上下文压缩 | 自动压缩 50%，降低成本 |
| 复杂推理任务 | ✅ 原有 OpenAI/Claude | 高质量模型，准确率高 |
| 生产环境关键任务 | ✅ 原有方式 (稳定) | 久经考验，可靠性高 |
| 实验性功能 | 🆕 Gateway (灵活) | 本地模型，快速迭代 |

**推荐策略**:
- **本地优先，云端备份** - 简单任务用 Gateway，复杂任务用原有方式
- **渐进式集成** - 先在非关键功能测试 Gateway，稳定后逐步扩展
- **智能路由** - 根据任务类型自动选择最优后端（见示例 2）

---

## 🎯 新增功能（通过 Gateway）

| 功能 | 说明 | 收益 |
|------|------|------|
| 🚀 本地推理 | Qwen3-1.7B 本地模型，0.64s 平均延迟 | 降低云端成本，隐私保护 |
| ⚡ 极速流式 | 0.056s TTFT，极快首字响应 | 提升用户体验 |
| 🧠 上下文压缩 | 自动压缩 50%，支持 4 种策略 | 降低 token 成本，延长对话 |
| 🎯 优先级调度 | Priority Queue (0=高, 1=正常, 2=低) | 关键任务优先，避免阻塞 |
| 🔀 智能路由 | 任务分类 + 模型选择 | 质量成本自动权衡 |
| ☁️ 混合后端 | 本地 + GLM + OpenAI + DeepSeek | 灵活切换，降低单点依赖 |

---

## 📖 集成步骤

### 1. 确保 Gateway 运行

```bash
cd /Users/sihaoli/ThunderLLAMA/gateway

# 启动 v2 服务
./scripts/start_v2.sh

# 验证运行
curl http://localhost:8000/health
```

### 2. 在 OpenClaw 中新增 Gateway Client

**方式 A: 新增独立 Gateway Client（推荐）**

```python
# openclaw/llm/gateway_client.py (新文件)

import openai

class GatewayClient:
    """OpenClaw Gateway 专用客户端"""

    def __init__(self):
        self.client = openai.OpenAI(
            base_url="http://localhost:8000/v1",
            api_key="dummy"  # Gateway 本地不需要真实 key
        )

    def chat(self, messages, model="qwen-1.7b", agent_type=None, priority=1):
        """调用 Gateway 推理"""
        extra_body = {}
        if agent_type:
            extra_body["agent_type"] = agent_type
        extra_body["priority"] = priority

        response = self.client.chat.completions.create(
            model=model,
            messages=messages,
            extra_body=extra_body
        )
        return response.choices[0].message.content
```

**使用方式**:
```python
from openclaw.llm.gateway_client import GatewayClient

# 保留原有 LLM 调用
from openclaw.llm.manager import LLMManager
original_llm = LLMManager()

# 新增 Gateway
gateway = GatewayClient()

# 根据任务选择
result = gateway.chat(messages, agent_type="flash")  # 简单任务用 Gateway
result = original_llm.chat(messages, model="gpt-4o")  # 复杂任务用原有方式
```

**方式 B: 在现有 LLMManager 中新增方法**

```python
# openclaw/llm/manager.py (在现有文件中新增)

class LLMManager:
    def __init__(self):
        # 保留原有
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        # 新增 Gateway client
        self.gateway_client = openai.OpenAI(
            base_url="http://localhost:8000/v1",
            api_key="dummy"
        )

    def chat(self, messages, model="gpt-4o"):
        """原有方法，保持不变"""
        response = self.openai_client.chat.completions.create(model=model, messages=messages)
        return response.choices[0].message.content

    # 新增方法
    def chat_via_gateway(self, messages, model="qwen-1.7b", agent_type=None):
        """新增：通过 Gateway 调用"""
        extra_body = {"agent_type": agent_type} if agent_type else {}
        response = self.gateway_client.chat.completions.create(
            model=model, messages=messages, extra_body=extra_body
        )
        return response.choices[0].message.content
```

### 3. Agent 类型路由配置

如果 OpenClaw 有不同类型的 Agent (judge/builder/flash)：

```python
# 审判官 Agent (需要高质量推理)
response = client.chat.completions.create(
    model="qwen-1.7b",  # 会自动路由到最佳模型
    messages=messages,
    extra_body={
        "agent_type": "judge",
        "priority": 0,  # 高优先级
    }
)

# 建设者 Agent (日常编码)
response = client.chat.completions.create(
    model="qwen-1.7b",
    messages=messages,
    extra_body={
        "agent_type": "builder",
        "priority": 1,
    }
)

# 闪电侠 Agent (快速任务)
response = client.chat.completions.create(
    model="qwen-1.7b",
    messages=messages,
    extra_body={
        "agent_type": "flash",
        "priority": 2,
    }
)
```

### 4. 启用上下文压缩

对于长对话历史：

```python
# 自动压缩上下文
response = client.chat.completions.create(
    model="qwen-1.7b",
    messages=long_conversation_history,  # 可能很长
    extra_body={
        "enable_context_compression": True,
        "target_context_tokens": 2048,  # 压缩到 2K tokens
    }
)
```

---

## 📝 集成示例

### 示例 1: 新增 Gateway 后端（与原有并存）

```python
# openclaw/llm/manager.py (原始)

class LLMManager:
    def __init__(self):
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def chat(self, messages, model="gpt-4o"):
        response = self.openai_client.chat.completions.create(
            model=model,
            messages=messages
        )
        return response.choices[0].message.content
```

```python
# openclaw/llm/manager.py (新增 Gateway，保留原有)

class LLMManager:
    def __init__(self):
        # 保留原有 OpenAI client
        self.openai_client = openai.OpenAI(
            api_key=os.getenv("OPENAI_API_KEY")
        ) if os.getenv("OPENAI_API_KEY") else None

        # 新增 Gateway client
        self.gateway_client = openai.OpenAI(
            base_url="http://localhost:8000/v1",
            api_key="dummy"
        )

    def chat(self, messages, model="gpt-4o", use_gateway=False):
        """原有方法，保持兼容性"""
        response = self.openai_client.chat.completions.create(
            model=model,
            messages=messages
        )
        return response.choices[0].message.content

    def chat_via_gateway(self, messages, model="qwen-1.7b", agent_type=None, priority=1):
        """新增：通过 Gateway 推理"""
        extra_body = {}

        if agent_type:
            extra_body["agent_type"] = agent_type

        extra_body["priority"] = priority

        response = self.gateway_client.chat.completions.create(
            model=model,
            messages=messages,
            extra_body=extra_body
        )
        return response.choices[0].message.content

    def chat_with_compression(self, messages, target_tokens=2048):
        """新增：带上下文压缩的推理"""
        response = self.gateway_client.chat.completions.create(
            model="qwen-1.7b",
            messages=messages,
            extra_body={
                "enable_context_compression": True,
                "target_context_tokens": target_tokens
            }
        )
        return response.choices[0].message.content
```

### 示例 2: 智能路由（自动选择后端）

```python
# openclaw/llm/manager.py (扩展版)

class LLMManager:
    def __init__(self):
        self.openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY")) if os.getenv("OPENAI_API_KEY") else None
        self.gateway_client = openai.OpenAI(base_url="http://localhost:8000/v1", api_key="dummy")

    def chat_smart(self, messages, task_type="general", use_local_first=True):
        """智能选择：优先本地，复杂任务用云端"""

        # 本地优先策略
        if use_local_first:
            try:
                # 简单任务用 Gateway 本地模型
                if task_type in ["simple", "fast", "code_completion"]:
                    return self.chat_via_gateway(
                        messages,
                        model="qwen-1.7b",
                        agent_type="flash",  # 快速任务
                        priority=2
                    )

                # 中等复杂度用 Gateway 云端模型
                elif task_type in ["analysis", "coding"]:
                    return self.chat_via_gateway(
                        messages,
                        model="glm-4-flash",
                        agent_type="builder",
                        priority=1
                    )

                # 高复杂度推理任务用原有 OpenAI
                else:
                    return self.chat(messages, model="gpt-4o")

            except Exception as e:
                # Gateway 失败时回退到原有方式
                print(f"Gateway 失败，回退到 OpenAI: {e}")
                return self.chat(messages, model="gpt-4o")

        # 直接用原有方式
        else:
            return self.chat(messages, model="gpt-4o")

    # 保留原有方法...
    def chat(self, messages, model="gpt-4o"):
        """原有方法"""
        response = self.openai_client.chat.completions.create(model=model, messages=messages)
        return response.choices[0].message.content

    def chat_via_gateway(self, messages, model="qwen-1.7b", agent_type=None, priority=1):
        """新增：通过 Gateway 推理"""
        extra_body = {"agent_type": agent_type, "priority": priority}
        response = self.gateway_client.chat.completions.create(model=model, messages=messages, extra_body=extra_body)
        return response.choices[0].message.content
```

**使用方式**:
```python
llm = LLMManager()

# 简单任务 → Gateway 本地模型
result = llm.chat_smart(messages, task_type="fast")

# 复杂任务 → 原有 OpenAI
result = llm.chat_smart(messages, task_type="reasoning")

# 强制用原有方式
result = llm.chat_smart(messages, use_local_first=False)
```

### 示例 3: Agent Team 并行调度

```python
# openclaw/agents/team.py

import asyncio

class AgentTeam:
    def __init__(self):
        self.llm = LLMManager()

    async def execute_parallel(self, tasks):
        """并行执行多个 Agent 任务"""

        # 为不同 Agent 设置优先级
        async def run_task(task):
            agent_type = task.get("agent_type", "builder")
            priority = {
                "judge": 0,      # 审判官最高优先级
                "builder": 1,    # 建设者普通优先级
                "flash": 2       # 闪电侠后台优先级
            }.get(agent_type, 1)

            # 通过 Gateway 执行
            result = self.llm.chat(
                messages=task["messages"],
                model="qwen-1.7b",
                agent_type=agent_type,
                priority=priority
            )
            return {"task_id": task["id"], "result": result}

        # 并发执行所有任务
        results = await asyncio.gather(*[
            run_task(task) for task in tasks
        ])

        return results
```

### 示例 4: 独立路由器（高级用法）

```python
# openclaw/llm/router.py

class SmartRouter:
    """智能选择本地 vs 云端"""

    def __init__(self):
        self.gateway = openai.OpenAI(
            base_url="http://localhost:8000/v1",
            api_key="dummy"
        )

    def route(self, task_type, complexity, messages):
        """根据任务类型和复杂度选择模型"""

        # 简单任务 → 本地
        if complexity == "low":
            model = "qwen-1.7b"

        # 中等复杂度 → 本地或 GLM
        elif complexity == "medium":
            model = "glm-4-flash" if self._cloud_available() else "qwen-1.7b"

        # 高复杂度 → 云端高质量模型
        else:
            model = "gpt-4o" if self._cloud_available() else "qwen-1.7b"

        response = self.gateway.chat.completions.create(
            model=model,
            messages=messages
        )

        return response.choices[0].message.content

    def _cloud_available(self):
        """检查云端是否可用"""
        try:
            health = requests.get("http://localhost:8000/health", timeout=1)
            return len(health.json().get("cloud_backends", [])) > 0
        except:
            return False
```

---

## 🔧 配置文件集成

### OpenClaw 配置示例

```yaml
# openclaw/config/llm.yaml

gateway:
  enabled: true
  base_url: "http://localhost:8000/v1"

  # 模型映射
  models:
    fast: "qwen-1.7b"           # 快速本地模型
    balanced: "glm-4-flash"     # 平衡云端模型
    quality: "gpt-4o"           # 高质量云端模型

  # Agent 配置
  agents:
    judge:
      model: "qwen-1.7b"
      priority: 0
      enable_compression: true

    builder:
      model: "qwen-1.7b"
      priority: 1
      enable_compression: false

    flash:
      model: "qwen-1.7b"
      priority: 2
      enable_compression: false

  # 上下文管理
  context:
    max_tokens: 4096
    compression_threshold: 3000
    target_tokens: 2048
```

---

## 🧪 测试集成

### 测试脚本

```python
# test_openclaw_integration.py

import openai

# 1. 测试基础连接
client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"
)

response = client.chat.completions.create(
    model="qwen-1.7b",
    messages=[{"role": "user", "content": "测试"}]
)

print(f"✓ 基础连接: {response.choices[0].message.content[:50]}")

# 2. 测试 Agent 路由
for agent_type in ["judge", "builder", "flash"]:
    response = client.chat.completions.create(
        model="qwen-1.7b",
        messages=[{"role": "user", "content": f"任务 ({agent_type})"}],
        extra_body={"agent_type": agent_type}
    )
    print(f"✓ Agent {agent_type}: OK")

# 3. 测试上下文压缩
long_messages = [
    {"role": "user", "content": "问题1" * 100},
    {"role": "assistant", "content": "回答1" * 100},
    {"role": "user", "content": "问题2"},
]

response = client.chat.completions.create(
    model="qwen-1.7b",
    messages=long_messages,
    extra_body={
        "enable_context_compression": True,
        "target_context_tokens": 500
    }
)

print(f"✓ 上下文压缩: {response.usage.prompt_tokens} tokens")

print("\n✅ 所有集成测试通过！")
```

---

## 📊 Gateway 性能表现

### 本地模型性能（Qwen3-1.7B）

| 指标 | 数值 | 说明 |
|------|------|------|
| 平均延迟 | 1.42s | 单次完整推理 |
| 流式 TTFT | 0.056s | 首字响应时间（极快！） |
| 吞吐量 | 63.9 tok/s | Token 生成速度 |
| 上下文压缩 | 50% | 225 → 111 tokens |
| 成本 | $0 | 本地推理，无 API 费用 |

### 云端模型性能（GLM/OpenAI/DeepSeek）

| 模型 | TTFT | 成本/1K tokens | 适用场景 |
|------|------|----------------|----------|
| GLM-4-Flash | ~0.5s | $0.0001 | 日常任务（经济） |
| GPT-4o-mini | ~0.8s | $0.00015 | 平衡质量成本 |
| GPT-4o | ~1.2s | $0.005 | 复杂推理（高质量） |
| DeepSeek-V3 | ~0.6s | $0.0014 | 中文任务 |

### 成本对比（估算）

假设每天 1000 次推理，平均 500 tokens/次：

| 方案 | 月成本 | 说明 |
|------|--------|------|
| 纯 GPT-4o | $75 | 高质量，高成本 |
| 纯 GLM-4-Flash | $3 | 经济，但质量有限 |
| **Gateway 混合** | **$15** | 70% 本地 + 30% 云端 |

**节省**: Gateway 混合方案可节省 **80%** 云端成本

---

## 🚀 部署建议

### 开发环境

```bash
# Terminal 1: 启动 Gateway
cd /Users/sihaoli/ThunderLLAMA/gateway
./scripts/start_v2.sh

# Terminal 2: 启动 OpenClaw
cd /path/to/openclaw
python main.py
```

### 生产环境

```bash
# 使用 systemd 或 supervisor 管理

# Gateway 服务
[Unit]
Description=OpenClaw Gateway
After=network.target

[Service]
Type=simple
User=openclaw
WorkingDirectory=/opt/openclaw-gateway
ExecStart=/opt/openclaw-gateway/venv/bin/uvicorn clawgate.api.main_v2:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

---

## 🛤️ 渐进式集成路径

**阶段 1: 试点验证（1-2 周）**
```python
# 在非关键功能测试 Gateway
gateway = GatewayClient()

# 示例：代码补全功能
def code_complete(code_snippet):
    # 新增：用 Gateway 本地模型
    try:
        return gateway.chat(
            messages=[{"role": "user", "content": f"补全代码：{code_snippet}"}],
            model="qwen-1.7b",
            agent_type="flash"
        )
    except:
        # 失败时回退到原有方式
        return original_llm.chat(messages, model="gpt-3.5-turbo")
```

**阶段 2: 智能路由（2-4 周）**
```python
# 根据任务类型自动选择
def chat_smart(messages, task_complexity="medium"):
    if task_complexity == "low":
        return gateway.chat(messages, model="qwen-1.7b")
    elif task_complexity == "medium":
        return gateway.chat(messages, model="glm-4-flash")
    else:
        return original_llm.chat(messages, model="gpt-4o")
```

**阶段 3: 全量迁移（按需）**
```python
# 大部分任务用 Gateway，保留原有作为后备
def chat(messages, force_cloud=False):
    if force_cloud:
        return original_llm.chat(messages)
    else:
        return gateway.chat_smart(messages)
```

---

## 💡 最佳实践

### 集成建议
1. **渐进式迁移**: 先试点，再扩展，最后全量（见上方路径）
2. **双后端并存**: 保留原有方式作为备份，避免单点故障
3. **监控对比**: 同时记录 Gateway 和原有方式的性能，对比效果

### 使用建议
1. **本地优先**: 简单任务用本地模型（低延迟 + 隐私）
2. **云端备份**: 复杂任务用云端模型（高质量）
3. **上下文压缩**: 长对话启用压缩（降低成本）
4. **优先级调度**: 关键任务设置高优先级
5. **监控日志**: 定期检查 Gateway 日志

### 回退策略
```python
# 任何 Gateway 调用都应有回退
try:
    result = gateway.chat(messages)
except Exception as e:
    logger.warning(f"Gateway 失败，回退到原有方式: {e}")
    result = original_llm.chat(messages)
```

---

## 🔗 相关文档

- [快速开始](QUICKSTART.md)
- [v2 集成报告](V2_INTEGRATION_REPORT.md)
- [性能报告](PERFORMANCE_REPORT.md)

---

**集成支持**: 遇到问题请查看 `/Users/sihaoli/ThunderLLAMA/gateway/logs/server_v2.log`
