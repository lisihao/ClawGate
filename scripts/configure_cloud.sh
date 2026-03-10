#!/bin/bash
# 云端 API 配置向导

set -e

echo "================================================"
echo "☁️  OpenClaw Gateway 云端 API 配置"
echo "================================================"

echo ""
echo "本脚本将帮助您配置云端 LLM API Keys"
echo ""

# 检查 .env 文件
if [ ! -f ".env" ]; then
    echo "创建 .env 文件..."
    cat > .env << 'EOF'
# OpenClaw Gateway 云端 API Keys

# GLM (智谱 AI) - https://open.bigmodel.cn
GLM_API_KEY=

# OpenAI - https://platform.openai.com
OPENAI_API_KEY=

# DeepSeek - https://platform.deepseek.com
DEEPSEEK_API_KEY=
EOF
fi

# 配置 GLM
echo "================================================"
echo "1. GLM (智谱 AI)"
echo "================================================"
echo ""
echo "获取 API Key: https://open.bigmodel.cn"
echo "推荐模型: glm-4-flash (快速), glm-5 (高质量)"
echo ""
read -p "请输入 GLM API Key (留空跳过): " glm_key

if [ -n "$glm_key" ]; then
    # 更新 .env
    if grep -q "^GLM_API_KEY=" .env; then
        sed -i.bak "s/^GLM_API_KEY=.*/GLM_API_KEY=$glm_key/" .env
    else
        echo "GLM_API_KEY=$glm_key" >> .env
    fi
    echo "✅ GLM API Key 已保存"

    # 测试连接
    echo "测试 GLM 连接..."
    export GLM_API_KEY="$glm_key"

    test_result=$(curl -s -X POST https://open.bigmodel.cn/api/paas/v4/chat/completions \
      -H "Authorization: Bearer $glm_key" \
      -H "Content-Type: application/json" \
      -d '{
        "model": "glm-4-flash",
        "messages": [{"role": "user", "content": "测试"}],
        "max_tokens": 5
      }' | grep -o '"content"' || echo "")

    if [ -n "$test_result" ]; then
        echo "✅ GLM API 连接成功！"
    else
        echo "⚠️  GLM API 测试失败，请检查 API Key"
    fi
else
    echo "⏭️  跳过 GLM 配置"
fi

# 配置 OpenAI
echo ""
echo "================================================"
echo "2. OpenAI"
echo "================================================"
echo ""
echo "获取 API Key: https://platform.openai.com/api-keys"
echo "推荐模型: gpt-4o-mini (经济), gpt-4o (高质量)"
echo ""
read -p "请输入 OpenAI API Key (留空跳过): " openai_key

if [ -n "$openai_key" ]; then
    if grep -q "^OPENAI_API_KEY=" .env; then
        sed -i.bak "s/^OPENAI_API_KEY=.*/OPENAI_API_KEY=$openai_key/" .env
    else
        echo "OPENAI_API_KEY=$openai_key" >> .env
    fi
    echo "✅ OpenAI API Key 已保存"

    # 简单测试（避免收费，只检查 Key 格式）
    if [[ "$openai_key" =~ ^sk-[a-zA-Z0-9]{32,}$ ]]; then
        echo "✅ API Key 格式正确"
    else
        echo "⚠️  API Key 格式可能不正确"
    fi
else
    echo "⏭️  跳过 OpenAI 配置"
fi

# 配置 DeepSeek
echo ""
echo "================================================"
echo "3. DeepSeek"
echo "================================================"
echo ""
echo "获取 API Key: https://platform.deepseek.com"
echo "推荐模型: deepseek-r1 (推理), deepseek-v3 (通用)"
echo ""
read -p "请输入 DeepSeek API Key (留空跳过): " deepseek_key

if [ -n "$deepseek_key" ]; then
    if grep -q "^DEEPSEEK_API_KEY=" .env; then
        sed -i.bak "s/^DEEPSEEK_API_KEY=.*/DEEPSEEK_API_KEY=$deepseek_key/" .env
    else
        echo "DEEPSEEK_API_KEY=$deepseek_key" >> .env
    fi
    echo "✅ DeepSeek API Key 已保存"
else
    echo "⏭️  跳过 DeepSeek 配置"
fi

# 清理备份
rm -f .env.bak

# 总结
echo ""
echo "================================================"
echo "✅ 配置完成！"
echo "================================================"
echo ""
echo "已配置的 API:"

configured_count=0
if grep -q "^GLM_API_KEY=..*" .env; then
    echo "  ✓ GLM (智谱 AI)"
    configured_count=$((configured_count + 1))
fi

if grep -q "^OPENAI_API_KEY=..*" .env; then
    echo "  ✓ OpenAI"
    configured_count=$((configured_count + 1))
fi

if grep -q "^DEEPSEEK_API_KEY=..*" .env; then
    echo "  ✓ DeepSeek"
    configured_count=$((configured_count + 1))
fi

if [ $configured_count -eq 0 ]; then
    echo "  (无)"
    echo ""
    echo "💡 提示: 至少配置一个云端 API 以启用混合路由"
else
    echo ""
    echo "📖 下一步:"
    echo "  1. 重启服务: ./scripts/start_v2.sh"
    echo "  2. 测试云端: python3 scripts/test_cloud_apis.py"
    echo "  3. 查看可用模型: curl http://localhost:8000/models"
fi

echo ""
