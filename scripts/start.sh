#!/bin/bash
# OpenClaw Gateway 启动脚本

set -e

echo "🚀 启动 OpenClaw Gateway..."

# 激活虚拟环境
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "❌ 虚拟环境不存在！请先运行 ./scripts/setup.sh"
    exit 1
fi

# 启动服务
python3 -m uvicorn clawgate.api.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --reload \
    --log-level info
