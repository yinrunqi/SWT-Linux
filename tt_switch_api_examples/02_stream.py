"""流式输出。用法: python 02_stream.py [模型名]"""

import sys

from common import client

model = sys.argv[1] if len(sys.argv) > 1 else "claude-sonnet-5"

stream = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "列出装配体建模的三个常见步骤。"}],
    max_tokens=1000,
    stream=True,
    stream_options={"include_usage": True},
)

usage = None
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
    if chunk.usage:
        usage = chunk.usage

print("\n\nusage:", usage)
