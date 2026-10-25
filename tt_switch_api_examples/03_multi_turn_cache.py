"""多轮对话 + prompt 缓存。

同一轮对话的所有请求必须带同一个 X-Session-Id 请求头，否则缓存完全不命中。
用法: python 03_multi_turn_cache.py [模型名]
"""

import sys
import uuid

from common import client

model = sys.argv[1] if len(sys.argv) > 1 else "gpt-5.5"
session_id = str(uuid.uuid4())

messages = [{"role": "system", "content": "你是 CAD 建模助手，回答控制在两句话内。"}]

for question in ["什么是草图约束？", "那它和配合有什么区别？"]:
    messages.append({"role": "user", "content": question})
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=1000,
        extra_headers={"X-Session-Id": session_id},
    )
    answer = resp.choices[0].message.content
    messages.append({"role": "assistant", "content": answer})
    print(f"Q: {question}\nA: {answer}\nusage: {resp.usage}\n")
