#!/bin/bash
# 模型下载脚本

set -e

echo "================================================"
echo "🦙 OpenClaw Gateway 模型下载脚本"
echo "================================================"

# 检测平台
OS=$(uname -s)
ARCH=$(uname -m)

echo ""
echo "📋 系统信息:"
echo "  - OS: $OS"
echo "  - Architecture: $ARCH"

# 创建模型目录
mkdir -p models
cd models

echo ""
echo "📥 开始下载模型..."

# Apple Silicon - MLX 格式
if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    echo ""
    echo "🍎 检测到 Apple Silicon，下载 MLX 格式模型..."

    # Qwen2.5-7B-Instruct (MLX 4-bit)
    echo "  - Qwen2.5-7B-Instruct (MLX 4-bit)"
    huggingface-cli download mlx-community/Qwen2.5-7B-Instruct-4bit \
        --local-dir qwen2.5-7b-instruct-mlx \
        --quiet

    echo "✅ MLX 模型下载完成"
fi

# 跨平台 - GGUF 格式
echo ""
echo "🦙 下载 llama.cpp GGUF 格式模型（跨平台）..."

# Qwen2.5-7B-Instruct (GGUF Q8)
echo "  - Qwen2.5-7B-Instruct (GGUF Q8)"
if [ ! -f "qwen2.5-7b-instruct-q8_0.gguf" ]; then
    wget https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q8_0.gguf \
        -O qwen2.5-7b-instruct-q8_0.gguf
fi

# 可选：更小的 Q4 版本（3.5GB vs 7.5GB）
# echo "  - Qwen2.5-7B-Instruct (GGUF Q4)"
# wget https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/qwen2.5-7b-instruct-q4_0.gguf \
#     -O qwen2.5-7b-instruct-q4_0.gguf

echo ""
echo "================================================"
echo "✅ 模型下载完成！"
echo "================================================"

echo ""
echo "📖 模型列表:"
ls -lh

echo ""
echo "📖 下一步:"
echo "  1. 编辑 config/engines.yaml 配置模型路径"
echo "  2. 启动服务: ./scripts/start.sh"
echo ""
