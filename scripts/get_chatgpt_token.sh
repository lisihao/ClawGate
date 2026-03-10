#!/bin/bash
# ChatGPT Session Token 获取指导

set -e

echo "================================================"
echo "🔑 ChatGPT Session Token 获取指导"
echo "================================================"
echo ""

echo "📋 获取步骤："
echo ""
echo "1️⃣  打开浏览器，访问: https://chatgpt.com"
echo "    使用您的 ChatGPT Plus/Pro 账户登录"
echo ""
echo "2️⃣  打开开发者工具："
echo "    - Windows/Linux: 按 F12"
echo "    - Mac: 按 Cmd+Option+I"
echo ""
echo "3️⃣  切换到 Application 标签"
echo "    （如果是 Firefox，则是 Storage 标签）"
echo ""
echo "4️⃣  左侧菜单："
echo "    Storage → Cookies → https://chatgpt.com"
echo ""
echo "5️⃣  找到 Cookie："
echo "    名称: __Secure-next-auth.session-token"
echo "    复制其 Value 值（很长的字符串）"
echo ""

echo "================================================"
echo ""

read -p "是否已获取到 Token？(y/n): " got_token

if [ "$got_token" != "y" ]; then
    echo ""
    echo "💡 提示："
    echo "   如果找不到 __Secure-next-auth.session-token，"
    echo "   请确保已登录 ChatGPT Plus/Pro 账户。"
    echo ""
    echo "   如需帮助，查看: CHATGPT_SUBSCRIPTION_SETUP.md"
    exit 0
fi

echo ""
read -p "请粘贴您的 Token: " token

if [ -z "$token" ]; then
    echo "❌ Token 不能为空"
    exit 1
fi

# 保存到 .env
if [ ! -f ".env" ]; then
    echo "创建 .env 文件..."
    touch .env
fi

# 检查是否已存在 CHATGPT_ACCESS_TOKEN
if grep -q "^CHATGPT_ACCESS_TOKEN=" .env; then
    # 更新现有的
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        sed -i '' "s|^CHATGPT_ACCESS_TOKEN=.*|CHATGPT_ACCESS_TOKEN=$token|" .env
    else
        # Linux
        sed -i "s|^CHATGPT_ACCESS_TOKEN=.*|CHATGPT_ACCESS_TOKEN=$token|" .env
    fi
    echo "✅ Token 已更新到 .env"
else
    # 新增
    echo "" >> .env
    echo "# ChatGPT 订阅账户（使用 chatgpt.com/backend-api）" >> .env
    echo "CHATGPT_ACCESS_TOKEN=$token" >> .env
    echo "✅ Token 已保存到 .env"
fi

echo ""
echo "================================================"
echo "✅ 配置完成！"
echo "================================================"
echo ""
echo "📖 下一步："
echo "  1. 重启服务: ./scripts/start_v2.sh"
echo "  2. 测试 GPT-5.2:"
echo ""
echo "     curl -X POST http://localhost:8000/v1/chat/completions \\"
echo "       -H 'Content-Type: application/json' \\"
echo "       -d '{"
echo "         \"model\": \"gpt-5.2\","
echo "         \"messages\": [{\"role\": \"user\", \"content\": \"你好\"}],"
echo "         \"max_tokens\": 50"
echo "       }'"
echo ""
echo "  3. 查看可用模型: curl http://localhost:8000/models"
echo ""
