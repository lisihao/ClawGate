# 🚀 快速开始 - ThunderLLAMA + 云端验证

## 一键验证

```bash
# 1. 初始化环境
./scripts/setup.sh

# 2. 配置 API Keys（可选）
export GLM_API_KEY='your-key'
export OPENAI_API_KEY='your-key'

# 3. 快速验证（自动下载模型、启动服务、运行测试）
./scripts/quick_start.sh
```

## 手动步骤

### 1. 下载本地模型

**推荐：Qwen2.5-1.5B-Instruct（轻量级，适合快速测试）**

```bash
mkdir -p models && cd models

# 下载 Q8 量化版本（约 1.7GB）
wget https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q8_0.gguf

cd ..
```

**可选：更大的模型（更高质量）**

```bash
# Qwen2.5-7B-Instruct (Q8, 约 7.5GB)
wget https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q8_0.gguf
```

### 2. 启动服务

```bash
./scripts/start.sh
```

服务启动后访问:
- **API 文档**: http://localhost:8000/docs
- **健康检查**: http://localhost:8000/health

### 3. 运行验证测试

```bash
# 完整验证（本地 + 云端）
python3 scripts/validate_setup.py

# 性能基准测试
python3 scripts/benchmark.py --model qwen-1.7b
```

## 验证内容

验证脚本会测试：

1. **服务健康检查**
   - ✓ 服务是否正常运行
   - ✓ 可用引擎列表

2. **本地推理**
   - ✓ Qwen-1.7B（llama.cpp）
   - ✓ TTFT（首字符延迟）
   - ✓ 吞吐量

3. **云端 API**
   - ✓ GLM-4-Flash
   - ✓ GPT-4o-mini
   - ✓ 延迟对比

4. **场景测试**
   - ✓ 简单问答
   - ✓ 代码生成
   - ✓ 推理分析

## 预期结果

```
📊 测试结果
┌──────────┬─────────────┬───────┬──────────┬────────┬──────────────────┐
│ 测试场景 │ 模型        │ 状态  │ 延迟     │ Tokens │ 响应预览         │
├──────────┼─────────────┼───────┼──────────┼────────┼──────────────────┤
│ 简单问答 │ qwen-1.7b   │ ✓     │ 0.35s    │ 45     │ 机器学习是一种...│
│ 简单问答 │ glm-4-flash │ ✓     │ 1.20s    │ 52     │ 机器学习是让计...│
│ 代码生成 │ qwen-1.7b   │ ✓     │ 0.68s    │ 120    │ def fib(n):...   │
│ 代码生成 │ glm-4-flash │ ✓     │ 2.10s    │ 135    │ def fibonacci... │
└──────────┴─────────────┴───────┴──────────┴────────┴──────────────────┘

📈 性能对比
  本地 qwen-1.7b: 平均延迟 0.52s
  云端 glm-4-flash: 平均延迟 1.65s

✅ 验证完成！
```

## API 使用示例

### Python

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"
)

# 本地模型
response = client.chat.completions.create(
    model="qwen-1.7b",
    messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)

# 云端模型
response = client.chat.completions.create(
    model="glm-4-flash",
    messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)
```

### cURL

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen-1.7b",
    "messages": [{"role": "user", "content": "你好"}],
    "max_tokens": 100
  }'
```

## 故障排除

### 1. 服务启动失败

```bash
# 检查端口占用
lsof -i :8000

# 查看日志
tail -f logs/server.log
```

### 2. 本地模型加载失败

- 检查模型文件路径是否正确：`ls -lh models/`
- 检查 `config/engines.yaml` 中的路径配置
- 确认有足够内存（1.7B 模型需要约 2GB RAM）

### 3. 云端 API 失败

- 确认 API Key 已正确设置
- 检查网络连接
- 查看错误日志

## 下一步

- 📖 阅读完整文档：[README.md](README.md)
- 🔧 配置高级功能：[config/models.yaml](config/models.yaml)
- 🧪 运行性能测试：`python3 scripts/benchmark.py`
- 🚀 集成到 OpenClaw：参考 [OpenAI 兼容接口](#api-使用示例)
