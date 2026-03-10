#!/bin/bash
# OpenClaw Gateway 初始化脚本

set -e

echo "================================================"
echo "🚀 OpenClaw Gateway 初始化脚本"
echo "================================================"

# 检测平台
OS=$(uname -s)
ARCH=$(uname -m)

echo ""
echo "📋 系统信息:"
echo "  - OS: $OS"
echo "  - Architecture: $ARCH"

# 1. 检查 Python 版本
echo ""
echo "🐍 检查 Python 版本..."
python3 --version

if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 未安装！"
    exit 1
fi

# 2. 创建虚拟环境
echo ""
echo "📦 创建虚拟环境..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo "✅ 虚拟环境创建完成"
else
    echo "ℹ️  虚拟环境已存在"
fi

# 3. 激活虚拟环境
echo ""
echo "🔧 激活虚拟环境..."
source venv/bin/activate

# 4. 升级 pip
echo ""
echo "⬆️  升级 pip..."
pip install --upgrade pip

# 5. 安装依赖
echo ""
echo "📥 安装依赖..."

# 基础依赖
pip install fastapi uvicorn pydantic pydantic-settings
pip install httpx aiohttp
pip install tiktoken pyyaml python-dotenv
pip install aiosqlite
pip install prometheus-client structlog

# 推理引擎（根据平台）
if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ]; then
    echo ""
    echo "🍎 检测到 Apple Silicon，安装 MLX..."
    pip install mlx mlx-lm
fi

echo ""
echo "🦙 安装 llama-cpp-python..."
# 根据是否有 GPU 选择安装方式
if command -v nvcc &> /dev/null; then
    echo "  - 检测到 CUDA，编译 GPU 版本..."
    CMAKE_ARGS="-DLLAMA_CUBLAS=on" pip install llama-cpp-python
else
    echo "  - 安装 CPU 版本..."
    pip install llama-cpp-python
fi

# Tantivy（可选）
echo ""
echo "🔍 安装 Tantivy..."
pip install tantivy || echo "⚠️  Tantivy 安装失败（可选依赖）"

# 6. 创建必要目录
echo ""
echo "📁 创建目录结构..."
mkdir -p data/sqlite
mkdir -p data/tantivy
mkdir -p models
mkdir -p logs

echo "✅ 目录创建完成"

# 7. 初始化数据库
echo ""
echo "🗄️  初始化数据库..."
python3 -c "from clawgate.storage.sqlite_store import SQLiteStore; SQLiteStore()"

echo ""
echo "================================================"
echo "✅ 初始化完成！"
echo "================================================"
echo ""
echo "📖 下一步:"
echo "  1. 下载模型: ./scripts/download_models.sh"
echo "  2. 启动服务: ./scripts/start.sh"
echo ""
