"""
最简单的 LLM API 连通性测试。
用于排查 OPENAI_BASE_URL / OPENAI_API_KEY 配置问题。
"""

import os
import sys

# 1. 检查环境变量
api_key = os.getenv("OPENAI_API_KEY")
base_url = os.getenv("OPENAI_BASE_URL")

print("=" * 50)
print("环境变量检查")
print("=" * 50)
print(f"OPENAI_API_KEY: {'已设置 (' + api_key[:8] + '...)' if api_key else '❌ 未设置'}")
print(f"OPENAI_BASE_URL: {base_url if base_url else '未设置（将使用默认地址）'}")
print()

if not api_key:
    print("请先设置 OPENAI_API_KEY 环境变量")
    sys.exit(1)

# 2. 用 requests 直接测试连通性
import requests

test_url = (base_url.rstrip("/") if base_url else "https://api.openai.com/v1") 
# 去掉可能多余的路径部分，只保留 base
if test_url.endswith("/chat/completions"):
    test_url = test_url.replace("/chat/completions", "")
models_url = f"{test_url}/models"

print("=" * 50)
print("网络连通性测试 (requests)")
print("=" * 50)
print(f"测试地址: {models_url}")
try:
    resp = requests.get(models_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
    print(f"状态码: {resp.status_code}")
    print(f"响应 (前500字): {resp.text[:500]}")
except Exception as e:
    print(f"❌ 连接失败: {e}")
print()

# 3. 用 openai SDK 测试
from openai import OpenAI

print("=" * 50)
print("OpenAI SDK 测试")
print("=" * 50)

client_kwargs = {"api_key": api_key}
if base_url:
    # 注意：OpenAI SDK 的 base_url 应该是不含 /chat/completions 的
    clean_url = base_url.rstrip("/")
    if clean_url.endswith("/chat/completions"):
        clean_url = clean_url.replace("/chat/completions", "")
    client_kwargs["base_url"] = clean_url
    print(f"使用 base_url: {clean_url}")

client = OpenAI(**client_kwargs)

try:
    print("发送简单请求...")
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "说'你好'两个字"}],
        max_tokens=10,
    )
    print(f"✅ 成功！模型回复: {completion.choices[0].message.content}")
except Exception as e:
    print(f"❌ 调用失败: {type(e).__name__}: {e}")
