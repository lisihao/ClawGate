#!/usr/bin/env python3
"""直接使用 requests 测试"""

import requests
import json
import time

print("\n🧪 直接API测试\n")

url = "http://localhost:8000/v1/chat/completions"

payload = {
    "model": "qwen-1.7b",
    "messages": [{"role": "user", "content": "你好"}],
    "max_tokens": 20
}

print(f"📡 发送请求: {url}")
print(f"📦 Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}\n")

try:
    start_time = time.time()
    response = requests.post(
        url,
        json=payload,
        timeout=60
    )
    latency = time.time() - start_time

    print(f"✓ 状态码: {response.status_code}")
    print(f"✓ 延迟: {latency:.3f}s")

    if response.status_code == 200:
        data = response.json()
        print(f"✓ 响应: {json.dumps(data, indent=2, ensure_ascii=False)[:500]}")
    else:
        print(f"❌ 错误: {response.text}")

except Exception as e:
    print(f"❌ 异常: {type(e).__name__}: {e}")
