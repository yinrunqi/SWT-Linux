"""手动脚本：用原子操作创建 100×50×30mm 长方体。

只在直接运行时执行，pytest 收集时不会向 SolidWorks 发请求。
"""

import os

import requests

BASE = os.environ.get("SW_API_URL", "http://localhost:5100")


def main() -> None:
    requests.post(f"{BASE}/api/new-part")
    requests.post(f"{BASE}/api/select-plane", json={"index": 0})
    requests.post(f"{BASE}/api/sketch/rectangle", json={"x1": 0, "y1": 0, "x2": 0.1, "y2": 0.05})
    requests.post(f"{BASE}/api/feature/extrusion", json={"depth": 0.03})
    print("创建长方体完成")


if __name__ == "__main__":
    main()
