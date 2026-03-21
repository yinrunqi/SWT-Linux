import requests

# 组合操作：一键创建长方体
# resp = requests.post("http://localhost:5100/api/create-box", json={
#     "length": 0.1, "width": 0.05, "height": 0.03
# })
# print(resp.json())

# 或者用原子操作逐步控制：
requests.post("http://localhost:5100/api/new-part")
requests.post("http://localhost:5100/api/select-plane", json={"index": 0})
requests.post("http://localhost:5100/api/sketch/rectangle", json={"x1": 0, "y1": 0, "x2": 0.1, "y2": 0.05})
requests.post("http://localhost:5100/api/feature/extrusion", json={"depth": 0.03})


print("创建长方体完成")
