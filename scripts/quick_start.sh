#!/bin/bash
# 快速启动脚本 - ThunderLLAMA + 云端模型验证

set -e

echo "================================================"
echo "🚀 OpenClaw Gateway 快速验证"
echo "================================================"

# 检查是否已初始化
if [ ! -d "venv" ]; then
    echo ""
    echo "❌ 未找到虚拟环境！"
    echo "请先运行: ./scripts/setup.sh"
    exit 1
fi

# 激活环境
source venv/bin/activate

# 检查 API Keys
echo ""
echo "🔑 检查 API Keys..."

missing_keys=""

if [ -z "$GLM_API_KEY" ]; then
    echo "  ⚠️  GLM_API_KEY 未设置（可选）"
    missing_keys="$missing_keys GLM"
fi

if [ -z "$OPENAI_API_KEY" ]; then
    echo "  ⚠️  OPENAI_API_KEY 未设置（可选）"
    missing_keys="$missing_keys OpenAI"
fi

if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "  ⚠️  DEEPSEEK_API_KEY 未设置（可选）"
    missing_keys="$missing_keys DeepSeek"
fi

if [ -n "$missing_keys" ]; then
    echo ""
    echo "💡 提示：部分云端 API Key 未设置，将仅测试本地模型"
    echo "   设置方法: export GLM_API_KEY='your-key'"
fi

# 检查本地模型
echo ""
echo "🦙 检查本地模型..."

if [ ! -d "models" ] || [ -z "$(ls -A models 2>/dev/null)" ]; then
    echo "  ⚠️  未找到本地模型"
    echo ""
    echo "💡 下载轻量级测试模型（推荐）："
    echo "   mkdir -p models && cd models"
    echo "   wget https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q8_0.gguf"
    echo ""
    read -p "是否现在下载？(y/N) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        mkdir -p models
        cd models
        echo "  下载 Qwen2.5-1.5B-Instruct (Q8, ~1.7GB)..."
        wget https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q8_0.gguf
        cd ..
        echo "  ✅ 下载完成"
    else
        echo "  跳过下载，将仅测试云端模型"
    fi
else
    echo "  ✅ 已有本地模型"
    ls -lh models/*.gguf 2>/dev/null || ls -lh models/
fi

# 启动服务（后台）
echo ""
echo "🚀 启动 OpenClaw Gateway..."

# 检查端口是否被占用
if lsof -Pi :8000 -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "  ℹ️  端口 8000 已被占用，使用现有服务"
else
    # 后台启动
    nohup python3 -m uvicorn clawgate.api.main:app \
        --host 0.0.0.0 \
        --port 8000 \
        > logs/server.log 2>&1 &

    echo "  ⏳ 等待服务启动..."
    sleep 5
fi

# 运行验证测试
echo ""
echo "🧪 运行验证测试..."
python3 scripts/validate_setup.py

echo ""
echo "================================================"
echo "✅ 验证完成！"
echo "================================================"
echo ""
echo "📖 后续步骤:"
echo "  - API 文档: http://localhost:8000/docs"
echo "  - 健康检查: curl http://localhost:8000/health"
echo "  - 性能测试: python3 scripts/benchmark.py"
echo ""
