"""列出网关当前提供的全部模型。用法: python 05_list_models.py"""

from common import client

ids = [m.id for m in client.models.list()]
print(f"共 {len(ids)} 个模型:")
for name in ids:
    print(" ", name)
