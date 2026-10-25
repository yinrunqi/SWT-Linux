"""最简单的单轮对话。用法: python 01_basic_chat.py [模型名]"""

import sys

from common import client

model = sys.argv[1] if len(sys.argv) > 1 else "gpt-5.5"

resp = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": "你是一个简洁的助手。"},
        {"role": "user", "content": "用一句话解释什么是 SolidWorks 的配合（Mate）。"},
    ],
    max_tokens=1000,
)

print(resp.choices[0].message.content)
print("\nusage:", resp.usage)
