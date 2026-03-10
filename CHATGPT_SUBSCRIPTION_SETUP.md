# ChatGPT 订阅账户集成指南

## 🎯 核心原理

OpenClaw 通过访问 **ChatGPT 后端 API** (`https://chatgpt.com/backend-api`) 来使用订阅账户配额，而不是标准的 OpenAI API。

## 📋 获取 Access Token

### 方法 1: 从浏览器获取（推荐）

1. **登录 ChatGPT**
   - 打开 https://chatgpt.com
   - 使用您的 Plus/Pro 账户登录

2. **打开开发者工具**
   - 按 `F12` 或 `Cmd+Option+I` (Mac)
   - 切换到 **Application** 标签

3. **查找 Session Token**
   - 左侧菜单：Storage → Cookies → `https://chatgpt.com`
   - 找到：`__Secure-next-auth.session-token`
   - 复制其 **Value**

4. **配置环境变量**
   ```bash
   export CHATGPT_ACCESS_TOKEN="your_session_token_here"
   ```

### 方法 2: 使用 ChatGPT CLI 工具

如果您已经安装了 OpenClaw，可以使用内置工具：

```bash
# 通过 OpenClaw 获取 token
openclaw auth chatgpt

# 或使用 pi-ai 工具
pi-ai auth login
```

---

## 🔧 配置 Gateway

### 1. 设置环境变量

```bash
# 编辑 .env 文件
cat >> .env << EOF

# ChatGPT 订阅账户（使用后端 API）
CHATGPT_ACCESS_TOKEN=your_session_token_here
EOF
```

### 2. 更新 main_v2.py

在 `clawgate/api/main_v2.py` 中添加 ChatGPT 后端初始化：

```python
# 在 startup_event() 中添加
if os.getenv("CHATGPT_ACCESS_TOKEN"):
    from clawgate.backends.cloud.chatgpt_backend import ChatGPTBackend
    cloud_backends["chatgpt"] = ChatGPTBackend()
    logger.info("✅ ChatGPT 订阅账户后端已启用")
```

### 3. 重启服务

```bash
./scripts/start_v2.sh
```

---

## 📊 可用模型

| 模型 | 说明 | 成本 |
|------|------|------|
| gpt-5.2 | 最新最强 | 订阅包含 |
| gpt-5.1 | 高质量 | 订阅包含 |
| gpt-5.1-codex-max | 代码专用（大） | 订阅包含 |
| gpt-5.1-codex-mini | 代码专用（小） | 订阅包含 |

---

## 🧪 测试集成

```python
import requests

response = requests.post(
    "http://localhost:8000/v1/chat/completions",
    json={
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 50
    }
)

print(response.json())
```

---

## ⚠️ 注意事项

### Token 有效期
- Session Token 会定期过期（通常 7-30 天）
- 过期后需要重新登录并获取新 token

### 更新 Token
```bash
# 快速更新
export CHATGPT_ACCESS_TOKEN="new_token"

# 或编辑 .env
vim .env  # 修改 CHATGPT_ACCESS_TOKEN
./scripts/start_v2.sh  # 重启服务
```

### 速率限制
- ChatGPT Plus: 40 条消息 / 3 小时
- ChatGPT Pro: 100 条消息 / 3 小时（预计）

### 安全建议
- ⚠️ 不要分享你的 Session Token
- ⚠️ Token 可以完全访问你的 ChatGPT 账户
- ✅ 定期更换 Token
- ✅ 使用环境变量，不要硬编码

---

## 🔄 与 OpenClaw 的对比

| 项目 | OpenClaw | Gateway (本实现) |
|------|----------|------------------|
| 后端 API | `https://chatgpt.com/backend-api` | ✅ 相同 |
| 认证方式 | Session Token | ✅ 相同 |
| 请求格式 | ChatGPT 专用格式 | ✅ 兼容 |
| 模型访问 | gpt-5.x 系列 | ✅ 相同 |
| 使用配额 | 订阅账户配额 | ✅ 相同 |

---

## 🚀 优势

1. **零额外成本** - 使用已有的 ChatGPT Plus/Pro 订阅
2. **最新模型** - 访问 GPT-5.2 等最新模型
3. **统一接口** - 通过 Gateway 统一管理
4. **智能路由** - 可以根据任务选择本地/GLM/ChatGPT

---

## 📝 完整配置示例

```bash
# .env 文件
GLM_API_KEY=your_glm_key                    # GLM（可选）
DEEPSEEK_API_KEY=your_deepseek_key          # DeepSeek（可选）
CHATGPT_ACCESS_TOKEN=your_session_token     # ChatGPT 订阅账户

# 启动服务
./scripts/start_v2.sh

# 测试
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.2",
    "messages": [{"role": "user", "content": "测试"}]
  }'
```

---

## 🐛 常见问题

### Q: Token 在哪里找？
A: 浏览器 F12 → Application → Cookies → `__Secure-next-auth.session-token`

### Q: Token 过期了怎么办？
A: 重新登录 ChatGPT，获取新 token，更新 .env 文件

### Q: 可以和 API Key 一起用吗？
A: 可以！GLM/DeepSeek 用 API Key，ChatGPT 用 Session Token，互不影响

### Q: 速率限制是多少？
A: Plus 用户约 40 条/3小时，Pro 更高

---

**现在您可以像 OpenClaw 一样，通过 Gateway 访问 ChatGPT 订阅账户配额了！** 🎉
