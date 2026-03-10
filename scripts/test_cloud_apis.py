#!/usr/bin/env python3
"""测试云端 API 连接"""

import requests
import os
import sys
import time

def test_api(name, model, test_prompt="你好"):
    """测试单个 API"""
    print(f"\n{'='*60}")
    print(f"测试 {name} - {model}")
    print(f"{'='*60}\n")

    try:
        start = time.time()
        response = requests.post(
            "http://localhost:8000/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": test_prompt}],
                "max_tokens": 30,
                "stream": False
            },
            timeout=30
        )
        latency = time.time() - start

        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            tokens = data["usage"]["total_tokens"]

            print(f"✅ 成功")
            print(f"  - 延迟: {latency:.2f}s")
            print(f"  - Tokens: {tokens}")
            print(f"  - 响应: {content[:60]}...")
            return True
        else:
            print(f"❌ 失败: HTTP {response.status_code}")
            print(f"  - {response.text[:100]}")
            return False

    except Exception as e:
        print(f"❌ 错误: {type(e).__name__}")
        print(f"  - {str(e)[:100]}")
        return False


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print(" " * 15 + "☁️  云端 API 测试")
    print("=" * 60)

    # 检查服务状态
    print("\n1. 检查服务状态...")
    try:
        health = requests.get("http://localhost:8000/health", timeout=5)
        data = health.json()

        print(f"  ✓ 服务版本: {data['version']}")
        print(f"  ✓ 本地模型: {', '.join(data['local_models'])}")
        print(f"  ✓ 云端后端: {', '.join(data['cloud_backends']) if data['cloud_backends'] else '无'}")

        if not data['cloud_backends']:
            print("\n⚠️  未检测到云端后端")
            print("   请先配置 API Key: ./scripts/configure_cloud.sh")
            sys.exit(1)

    except Exception as e:
        print(f"  ✗ 服务未运行: {e}")
        print("  请先启动服务: ./scripts/start_v2.sh")
        sys.exit(1)

    # 获取可用模型
    print("\n2. 获取可用模型...")
    models_resp = requests.get("http://localhost:8000/models")
    models_data = models_resp.json()

    cloud_models = models_data.get("cloud_models", [])
    if not cloud_models:
        print("  ⚠️  无云端模型可用")
        sys.exit(1)

    print(f"  ✓ 云端模型: {', '.join(cloud_models)}")

    # 测试云端模型
    print("\n3. 测试云端推理...")

    results = {}

    # 测试 GLM
    if any(m.startswith('glm') for m in cloud_models):
        glm_model = next(m for m in cloud_models if m.startswith('glm'))
        results['GLM'] = test_api("GLM (智谱)", glm_model)

    # 测试 OpenAI
    if any(m.startswith('gpt') for m in cloud_models):
        openai_model = next(m for m in cloud_models if m.startswith('gpt'))
        results['OpenAI'] = test_api("OpenAI", openai_model)

    # 测试 DeepSeek
    if any(m.startswith('deepseek') for m in cloud_models):
        deepseek_model = next(m for m in cloud_models if m.startswith('deepseek'))
        results['DeepSeek'] = test_api("DeepSeek", deepseek_model)

    # 总结
    print("\n" + "=" * 60)
    print("📊 测试总结")
    print("=" * 60 + "\n")

    success_count = sum(1 for v in results.values() if v)
    total_count = len(results)

    print(f"成功: {success_count}/{total_count}")

    for name, success in results.items():
        status = "✅" if success else "❌"
        print(f"  {status} {name}")

    if success_count == total_count:
        print("\n✅ 所有云端 API 测试通过！")
        print("\n💡 现在可以使用混合路由:")
        print("  - 本地模型: 低延迟，隐私保护")
        print("  - 云端模型: 高质量，复杂任务")
    else:
        print("\n⚠️  部分 API 测试失败")
        print("   请检查 API Key 配置")

    print("")


if __name__ == "__main__":
    main()
