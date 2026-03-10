#!/usr/bin/env python3
"""测试 GLM Coding Plan URL"""

import requests
import json

API_URL = "http://localhost:8000/v1/chat/completions"

print("\n🧪 测试 GLM 模型（Coding Plan）\n")

# 测试 GLM-5
print("=" * 60)
print("测试 1: GLM-5")
print("=" * 60)

try:
    response = requests.post(
        API_URL,
        json={
            "model": "glm-5",
            "messages": [{"role": "user", "content": "写一个Python函数计算斐波那契数列"}],
            "max_tokens": 100
        },
        timeout=30
    )

    print(f"状态码: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"✅ 成功！")
        print(f"响应: {data['choices'][0]['message']['content'][:200]}...")
    else:
        print(f"❌ 失败: {response.text}")
except Exception as e:
    print(f"❌ 错误: {e}")

# 测试 GLM-4-Flash
print("\n" + "=" * 60)
print("测试 2: GLM-4-Flash")
print("=" * 60)

try:
    response = requests.post(
        API_URL,
        json={
            "model": "glm-4-flash",
            "messages": [{"role": "user", "content": "你好"}],
            "max_tokens": 50
        },
        timeout=30
    )

    print(f"状态码: {response.status_code}")

    if response.status_code == 200:
        data = response.json()
        print(f"✅ 成功！")
        print(f"响应: {data['choices'][0]['message']['content'][:200]}...")
    else:
        print(f"❌ 失败: {response.text}")
except Exception as e:
    print(f"❌ 错误: {e}")

print("\n" + "=" * 60)
