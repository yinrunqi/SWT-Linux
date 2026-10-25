"""工具调用（function calling）。用法: python 04_tool_call.py [模型名]"""

import json
import sys

from common import client

model = sys.argv[1] if len(sys.argv) > 1 else "gpt-5.5"

tools = [{
    "type": "function",
    "function": {
        "name": "get_part_mass",
        "description": "查询零件的质量（千克）",
        "parameters": {
            "type": "object",
            "properties": {"part_name": {"type": "string", "description": "零件名称"}},
            "required": ["part_name"],
        },
    },
}]


def get_part_mass(part_name):
    return {"part_name": part_name, "mass_kg": 1.25}


messages = [{"role": "user", "content": "零件 bracket_01 有多重？"}]
resp = client.chat.completions.create(model=model, messages=messages, tools=tools, max_tokens=1000)
msg = resp.choices[0].message

if not msg.tool_calls:
    print("模型没有调用工具:", msg.content)
    sys.exit()

messages.append(msg)
for call in msg.tool_calls:
    args = json.loads(call.function.arguments)
    print(f"模型调用 {call.function.name}({args})")
    messages.append({
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps(get_part_mass(**args), ensure_ascii=False),
    })

final = client.chat.completions.create(model=model, messages=messages, tools=tools, max_tokens=1000)
print("最终回答:", final.choices[0].message.content)
