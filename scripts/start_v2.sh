#!/bin/bash
# OpenClaw Gateway v2 启动脚本
# 集成 Continuous Batching + ContextEngine + 云端路由

set -e

echo "================================================"
echo "🚀 OpenClaw Gateway v2 启动"
echo "================================================"

# 激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 虚拟环境不存在！请先运行 ./scripts/setup.sh"
    exit 1
fi

# 加载环境变量
if [ -f ".env" ]; then
    echo "📝 加载 .env 配置..."
    export $(cat .env | grep -v '^#' | xargs)
fi

# 检查 API Keys
echo ""
echo "🔑 云端后端状态:"

if [ -n "$GLM_API_KEY" ]; then
    echo "  ✅ GLM API Key 已设置"
else
    echo "  ⚠️  GLM API Key 未设置（可选）"
fi

if [ -n "$OPENAI_API_KEY" ]; then
    echo "  ✅ OpenAI API Key 已设置"
else
    echo "  ⚠️  OpenAI API Key 未设置（可选）"
fi

if [ -n "$DEEPSEEK_API_KEY" ]; then
    echo "  ✅ DeepSeek API Key 已设置"
else
    echo "  ⚠️  DeepSeek API Key 未设置（可选）"
fi

echo ""
echo "🎯 v2 新特性:"
echo "  - ⚡ Continuous Batching（预期 6× TTFT 提升）"
echo "  - 🧠 ContextEngine（压缩/摘要/缓存）"
echo "  - ☁️  云端路由（自动选择最优后端）"
echo "  - 🎯 智能调度（任务分类 + 模型选择）"
echo ""

# 启动服务
echo "🚀 启动服务..."
python3 -m uvicorn clawgate.api.main_v2:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --log-level info
