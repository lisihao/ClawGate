#!/bin/bash
# Context Shift 双模型摘要服务启动脚本（ClawGate 专用）
# 阶段1 (抽取): Qwen3-0.6B @ port 18083
# 阶段2 (压缩): Qwen3-1.7B @ port 18084

set -euo pipefail

THUNDER_ROOT="$HOME/ThunderLLAMA"
MODEL_0_6B="$HOME/models/qwen3-0.6b-gguf/Qwen3-0.6B-Q5_K_M.gguf"  # 使用 Q5_K_M
MODEL_1_7B="$HOME/models/qwen3-1.7b-gguf/Qwen3-1.7B-Q8_0.gguf"
PORT_0_6B=18083
PORT_1_7B=18084
LOG_DIR="$HOME/ClawGate/logs/context-shift"
LOG_0_6B="$LOG_DIR/stage1_0.6b.log"
LOG_1_7B="$LOG_DIR/stage2_1.7b.log"

# 创建日志目录
mkdir -p "$LOG_DIR"
mkdir -p "$LOG_DIR/.pids"

# 检查模型是否存在
if [ ! -f "$MODEL_0_6B" ]; then
    echo "❌ 模型不存在: $MODEL_0_6B"
    echo "请检查模型路径或下载模型"
    exit 1
fi

if [ ! -f "$MODEL_1_7B" ]; then
    echo "❌ 模型不存在: $MODEL_1_7B"
    echo "请检查模型路径或下载模型"
    exit 1
fi

# 检查 llama-server 是否存在
if [ ! -f "$THUNDER_ROOT/build/bin/llama-server" ]; then
    echo "❌ llama-server 不存在: $THUNDER_ROOT/build/bin/llama-server"
    echo "请先编译 ThunderLLAMA"
    exit 1
fi

# 检查端口是否被占用，如果占用则终止
for PORT in $PORT_0_6B $PORT_1_7B; do
    if lsof -Pi :$PORT -sTCP:LISTEN -t >/dev/null 2>&1 ; then
        echo "⚠️  端口 $PORT 已被占用，尝试终止..."
        lsof -ti :$PORT | xargs kill -9 2>/dev/null || true
        sleep 2
    fi
done

echo "========================================"
echo "启动 Context Shift 双模型服务"
echo "========================================"
echo ""

# 启动 0.6B 模型 (阶段1：抽取)
echo "▶ 启动 Stage 1 模型 (0.6B - 抽取)"
echo "  模型: $(basename $MODEL_0_6B)"
echo "  端口: $PORT_0_6B"
echo "  日志: $LOG_0_6B"

"$THUNDER_ROOT/build/bin/llama-server" \
  -m "$MODEL_0_6B" \
  --port $PORT_0_6B \
  --ctx-size 8192 \
  --threads 4 \
  --n-gpu-layers 99 \
  > "$LOG_0_6B" 2>&1 &

PID_0_6B=$!
echo "$PID_0_6B" > "$LOG_DIR/.pids/stage1.pid"
echo "  PID: $PID_0_6B"
echo ""

# 启动 1.7B 模型 (阶段2：压缩)
echo "▶ 启动 Stage 2 模型 (1.7B - 压缩)"
echo "  模型: $(basename $MODEL_1_7B)"
echo "  端口: $PORT_1_7B"
echo "  日志: $LOG_1_7B"

"$THUNDER_ROOT/build/bin/llama-server" \
  -m "$MODEL_1_7B" \
  --port $PORT_1_7B \
  --ctx-size 8192 \
  --threads 4 \
  --n-gpu-layers 99 \
  > "$LOG_1_7B" 2>&1 &

PID_1_7B=$!
echo "$PID_1_7B" > "$LOG_DIR/.pids/stage2.pid"
echo "  PID: $PID_1_7B"
echo ""

# 等待两个服务都就绪（最多 60 秒）
echo "等待服务启动..."
for i in {1..60}; do
    if lsof -Pi :$PORT_0_6B -sTCP:LISTEN -t >/dev/null 2>&1 && \
       lsof -Pi :$PORT_1_7B -sTCP:LISTEN -t >/dev/null 2>&1; then
        echo "✅ 两个服务端口都已监听"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "❌ 服务启动超时"
        echo "查看日志:"
        echo "  tail $LOG_0_6B"
        echo "  tail $LOG_1_7B"
        kill $PID_0_6B $PID_1_7B 2>/dev/null || true
        exit 1
    fi
    printf "."
    sleep 1
done
echo ""

# 等待模型加载完成（测试请求）
echo ""
echo "等待模型加载完成..."
for i in {1..90}; do
    # 测试 0.6B
    if curl -X POST http://127.0.0.1:$PORT_0_6B/completion \
        -H "Content-Type: application/json" \
        -d '{"prompt":"test","n_predict":1}' \
        --silent --max-time 3 >/dev/null 2>&1; then
        echo "✅ Stage 1 (0.6B) 模型就绪"
        # 测试 1.7B
        if curl -X POST http://127.0.0.1:$PORT_1_7B/completion \
            -H "Content-Type: application/json" \
            -d '{"prompt":"test","n_predict":1}' \
            --silent --max-time 3 >/dev/null 2>&1; then
            echo "✅ Stage 2 (1.7B) 模型就绪"
            break
        fi
    fi
    if [ $i -eq 90 ]; then
        echo "❌ 模型加载超时（90秒）"
        echo "检查日志:"
        echo "  tail -50 $LOG_0_6B"
        echo "  tail -50 $LOG_1_7B"
        kill $PID_0_6B $PID_1_7B 2>/dev/null || true
        exit 1
    fi
    printf "."
    sleep 1
done
echo ""

echo ""
echo "========================================"
echo "✅ Context Shift 服务运行中"
echo "========================================"
echo "Stage 1 (0.6B 抽取): http://127.0.0.1:$PORT_0_6B"
echo "Stage 2 (1.7B 压缩): http://127.0.0.1:$PORT_1_7B"
echo ""
echo "PID:"
echo "  Stage 1: $PID_0_6B"
echo "  Stage 2: $PID_1_7B"
echo ""
echo "日志:"
echo "  Stage 1: $LOG_0_6B"
echo "  Stage 2: $LOG_1_7B"
echo ""
echo "停止服务:"
echo "  kill $PID_0_6B $PID_1_7B"
echo "  或使用: bash $(dirname $0)/stop_context_shift_services.sh"
echo ""
