# 订阅账户配置说明

## ⚠️ 重要提示

用户使用的是 **OpenAI 订阅账户**（ChatGPT Plus/Pro），而不是 API 账户。

## 🔧 配置方式

### 方案 1: 使用第三方代理（推荐）

由于订阅账户无法直接通过 OpenAI API 访问，需要使用第三方代理服务：

```bash
# 设置代理 URL（替换为实际代理地址）
export OPENAI_API_BASE="https://your-proxy-service.com/v1"
export OPENAI_API_KEY="your_subscription_token"
```

**常见代理服务**：
- ChatGPT to API（需自行搭建）
- OpenAI Proxy（第三方服务）
- 自建代理（推荐，安全）

### 方案 2: 使用 Cookie 认证

如果有 ChatGPT 的 cookie，可以使用 cookie 认证：

```python
# 在 openai.py 中修改认证方式
headers = {
    "Cookie": "your_chatgpt_cookie",
    "Content-Type": "application/json"
}
```

### 方案 3: 暂时禁用 OpenAI（使用其他模型）

如果无法配置代理，可以暂时只使用其他云端模型：

```bash
# 只配置 GLM 和 DeepSeek
export GLM_API_KEY="your_glm_key"
export DEEPSEEK_API_KEY="your_deepseek_key"
# 不设置 OPENAI_API_KEY
```

## 📝 当前模型配置

### 已更新
- ✅ GLM URL: 添加 `/coding` 路径（Coding Plan）
- ✅ OpenAI 模型: `gpt-4o` → `gpt-5.2`, `gpt-4-turbo` → `gpt-5.1`

### 当前云端模型
```
🇨🇳 GLM（Coding Plan）:
  - glm-5
  - glm-4-flash

🇨🇳 DeepSeek:
  - deepseek-r1
  - deepseek-v3

🇺🇸 OpenAI（订阅账户）:
  - gpt-5.2 (最新)
  - gpt-5.1
```

## 🚀 建议

1. **主力模型**: GLM + DeepSeek（直接 API，稳定）
2. **备用模型**: GPT-5.x（需要代理，质量最高）
3. **本地模型**: Qwen3-1.7B（零成本）

---

**如需帮助配置代理，请告知具体使用场景。**
