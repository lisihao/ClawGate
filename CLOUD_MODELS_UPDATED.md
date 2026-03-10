# ☁️ 云端模型清单（已更新）

**更新时间**: 2025-03-09
**更新内容**:
- ✅ GLM URL 修正（添加 `/coding` 路径）
- ✅ OpenAI 模型更新（gpt-4o → gpt-5.2/5.1）

---

## 📊 云端模型对比表

| 平台 | 模型 | 成本/1K | 质量分 | 上下文 | 适用场景 |
|------|------|---------|--------|--------|----------|
| **🇺🇸 OpenAI** | **gpt-5.2** ⭐ | $0.008 | 0.98 | 200K | 🎯 最新最强、复杂推理 |
| 🇺🇸 OpenAI | gpt-5.1 | $0.007 | 0.95 | 200K | 🧠 高质量备用 |
| **🇨🇳 DeepSeek** | deepseek-r1 | $0.0014 | 0.95 | 64K | 🧠 深度推理、编码强 |
| 🇨🇳 DeepSeek | deepseek-v3 | $0.0014 | 0.90 | 64K | 💬 日常对话、翻译 |
| **🇨🇳 智谱 GLM** | glm-5 | $0.001 | 0.85 | 128K | 📝 中文任务、编码 |
| 🇨🇳 智谱 GLM | glm-4-flash | $0.0001 | 0.70 | 128K | ⚡ 快速响应、成本极低 |

---

## 🔧 配置更新

### 1. GLM Coding Plan URL

**旧地址**:
```
https://open.bigmodel.cn/api/paas/v4
```

**新地址** ✅:
```
https://open.bigmodel.cn/api/paas/v4/coding
```

**说明**: Coding Plan 专用端点，针对代码任务优化。

### 2. OpenAI 模型更新

**旧模型** (已废弃):
- ❌ gpt-4o
- ❌ gpt-4-turbo

**新模型** ✅:
- ✅ gpt-5.2（最新，质量 0.98）
- ✅ gpt-5.1（高质量备用）

---

## 🎯 Agent 路由配置（已更新）

```yaml
judge (审判官):
  preferred:  deepseek-r1 → gpt-5.2
  fallback:   deepseek-v3 → gpt-5.1 → glm-5

builder (建设者):
  preferred:  glm-5 → 本地模型
  fallback:   glm-4-flash

flash (闪电侠):
  preferred:  glm-4-flash → 本地模型
  fallback:   本地模型
```

---

## 💰 成本对比（每 1000 tokens）

```
glm-4-flash:    $0.0001  █░░░░░░░░░ (最便宜 ⭐)
glm-5:          $0.001   ████░░░░░░
deepseek-r1/v3: $0.0014  █████░░░░░
gpt-5.1:        $0.007   ████████░░
gpt-5.2:        $0.008   █████████░ (最强但最贵)
```

### 成本节省策略

假设每天 1000 次推理（500 tokens/次）：

| 方案 | 月成本 | 说明 |
|------|--------|------|
| 纯 GPT-5.2 | **$120** | 质量最高，成本最高 |
| 纯 DeepSeek-R1 | **$21** | 性价比高 |
| 纯 GLM-5 | **$15** | 中文友好 |
| **混合模式** | **$20** | 80% 本地/GLM + 20% DeepSeek ⭐ |

---

## ⚠️ OpenAI 订阅账户说明

用户使用的是 **订阅账户**（ChatGPT Plus/Pro），不是 API 账户。

### 配置选项

#### 选项 1: 使用代理服务（推荐）
```bash
export OPENAI_API_BASE="https://your-proxy.com/v1"
export OPENAI_API_KEY="subscription_token"
```

#### 选项 2: 暂时只用其他模型
```bash
# 只配置 GLM 和 DeepSeek
export GLM_API_KEY="your_key"
export DEEPSEEK_API_KEY="your_key"
# 不设置 OPENAI_API_KEY
```

详见: `SUBSCRIPTION_SETUP.md`

---

## 🌐 API 端点

| 平台 | API 地址 | 环境变量 |
|------|----------|----------|
| GLM | `https://open.bigmodel.cn/api/paas/v4/coding` ✅ | `GLM_API_KEY` |
| OpenAI | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| DeepSeek | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |

---

## 🚀 推荐使用

### 主力组合（性价比最高）

```
日常编码 (80%):  glm-4-flash      ($0.0001/1K)
中文任务 (15%):  glm-5            ($0.001/1K)
深度推理 (5%):   deepseek-r1      ($0.0014/1K)
```

**月成本**: 约 $15-20（假设每天 1000 次推理）

### 高质量组合（订阅账户可用）

```
简单任务 (60%):  glm-4-flash
中等任务 (30%):  deepseek-r1
复杂任务 (10%):  gpt-5.2 (需代理)
```

**月成本**: 约 $30-40

---

## 📝 配置脚本

```bash
# 运行配置向导
./scripts/configure_cloud.sh

# 手动配置
cat > .env << EOF
GLM_API_KEY=your_glm_key
DEEPSEEK_API_KEY=your_deepseek_key
# OPENAI_API_KEY=需要代理才能用
EOF

# 重启服务
./scripts/start_v2.sh

# 测试云端模型
python3 scripts/test_cloud_apis.py
```

---

## ✅ 总结

### 已修正
1. ✅ GLM URL 添加 `/coding` 路径（Coding Plan）
2. ✅ OpenAI 模型更新为 GPT-5.2/5.1
3. ✅ Agent 路由配置更新
4. ✅ 成本估算更新

### 推荐配置
- **主力**: GLM (Coding Plan) + DeepSeek（性价比高）
- **备用**: GPT-5.x（需代理，质量最高）
- **本地**: Qwen3-1.7B（零成本）

### 注意事项
- OpenAI 订阅账户需要代理才能通过 API 访问
- GLM Coding Plan URL 已更新为 `/coding` 端点
- 建议优先使用 GLM + DeepSeek（直接 API，稳定）

---

**配置完成后，Gateway 将支持 6 个云端模型 + 1 个本地模型！** 🚀
