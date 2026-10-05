"""
test_ellipse.py — 椭圆草图功能测试

测试场景：
1. 基本椭圆绘制：在前视基准面画椭圆并拉伸 → 椭圆柱体
2. 椭圆切除：在长方体上表面画椭圆并切除 → 椭圆孔
3. 在实体面上画椭圆（坐标变换验证）

运行前确保：
- SolidWorks 已启动
- C# HTTP API 服务已运行在 localhost:5100
"""

import os

import pytest
import requests

BASE = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")
pytestmark = pytest.mark.solidworks


def test_ellipse_extrusion():
    """测试 1：画椭圆 + 拉伸 → 椭圆柱体"""
    print("\n" + "=" * 60)
    print("测试 1：椭圆拉伸 → 椭圆柱体")
    print("  椭圆: 中心(0,0), 长轴半径 50mm, 短轴半径 30mm")
    print("  拉伸深度: 40mm")
    print("=" * 60)

    # 新建零件
    r = requests.post(f"{BASE}/api/new-part")
    print(f"  new-part: {r.json()}")
    assert r.json()["success"]

    # 选择前视基准面
    r = requests.post(f"{BASE}/api/select-plane", json={"index": 0})
    print(f"  select-plane: {r.json()}")
    assert r.json()["success"]

    # 画椭圆：长轴半径 50mm=0.05m，短轴半径 30mm=0.03m
    r = requests.post(f"{BASE}/api/sketch/ellipse", json={
        "cx": 0, "cy": 0,
        "majorRadius": 0.05,
        "minorRadius": 0.03,
    })
    print(f"  sketch/ellipse: {r.json()}")
    assert r.json()["success"]

    # 拉伸 40mm = 0.04m
    r = requests.post(f"{BASE}/api/feature/extrusion", json={"depth": 0.04})
    print(f"  extrusion: {r.json()}")
    assert r.json()["success"]

    # 验证包围盒
    r = requests.get(f"{BASE}/api/body-bounding-box")
    data = r.json()
    print(f"  bounding-box: {data}")
    assert data["success"]
    box = data["boundingBox"]
    # 椭圆柱体：X 方向约 100mm (±50mm)，Y 方向约 60mm (±30mm)，Z 方向约 40mm
    print(f"  尺寸: {box['sizeX']*1000:.1f} x {box['sizeY']*1000:.1f} x {box['sizeZ']*1000:.1f} mm")

    print("✅ 测试 1 通过：椭圆柱体创建成功")


def test_ellipse_cut():
    """测试 2：在长方体上椭圆切除 → 椭圆孔"""
    print("\n" + "=" * 60)
    print("测试 2：椭圆切除 → 椭圆孔")
    print("  基础：100x80x30mm 长方体")
    print("  切除：中心处椭圆孔，长轴25mm，短轴15mm，贯穿")
    print("=" * 60)

    # 创建长方体
    r = requests.post(f"{BASE}/api/new-part")
    assert r.json()["success"]

    r = requests.post(f"{BASE}/api/select-plane", json={"index": 0})
    assert r.json()["success"]

    r = requests.post(f"{BASE}/api/sketch/rectangle", json={
        "x1": 0, "y1": 0, "x2": 0.1, "y2": 0.08
    })
    assert r.json()["success"]

    r = requests.post(f"{BASE}/api/feature/extrusion", json={"depth": 0.03})
    assert r.json()["success"]
    print("  基础长方体创建完成")

    # 在上表面画椭圆（射线从上方向下选面）
    r = requests.post(f"{BASE}/api/select-face-and-sketch", json={
        "rx": 0.05, "ry": 0.04, "rz": -0.01,
        "dx": 0, "dy": 0, "dz": 1,
    })
    print(f"  select-face: {r.json()}")
    assert r.json()["success"]

    # 在面上画椭圆（中心在长方体中心）
    r = requests.post(f"{BASE}/api/sketch/ellipse", json={
        "cx": 0.05, "cy": 0.04, "cz": 0,
        "majorRadius": 0.025,
        "minorRadius": 0.015,
    })
    print(f"  sketch/ellipse: {r.json()}")
    assert r.json()["success"]

    # 贯穿切除
    r = requests.post(f"{BASE}/api/feature/cut-extrusion", json={
        "depth": 0.03,
        "endCondition": "through_all",
    })
    print(f"  cut-extrusion: {r.json()}")
    assert r.json()["success"]

    print("✅ 测试 2 通过：椭圆孔切除成功")


def test_ellipse_on_top_face():
    """测试 3：在上视基准面上画椭圆 + 拉伸"""
    print("\n" + "=" * 60)
    print("测试 3：上视基准面椭圆拉伸")
    print("  椭圆: 中心(0.03, 0), 长轴半径 30mm, 短轴半径 20mm")
    print("  拉伸深度: 50mm")
    print("=" * 60)

    r = requests.post(f"{BASE}/api/new-part")
    assert r.json()["success"]

    # 选上视基准面
    r = requests.post(f"{BASE}/api/select-plane", json={"index": 1})
    print(f"  select-plane(上视): {r.json()}")
    assert r.json()["success"]

    # 画椭圆
    r = requests.post(f"{BASE}/api/sketch/ellipse", json={
        "cx": 0.03, "cy": 0,
        "majorRadius": 0.03,
        "minorRadius": 0.02,
    })
    print(f"  sketch/ellipse: {r.json()}")
    assert r.json()["success"]

    # 拉伸
    r = requests.post(f"{BASE}/api/feature/extrusion", json={"depth": 0.05})
    print(f"  extrusion: {r.json()}")
    assert r.json()["success"]

    print("✅ 测试 3 通过：上视基准面椭圆拉伸成功")


if __name__ == "__main__":
    # 先检查服务是否可用
    try:
        r = requests.get(f"{BASE}/api/ping", timeout=3)
        if not r.json().get("success"):
            print("❌ SolidWorks API 服务未就绪")
            exit(1)
    except Exception as e:
        print(f"❌ 无法连接到 SolidWorks API 服务: {e}")
        print("请确保 C# HTTP API 服务已在 localhost:5100 运行")
        exit(1)

    print("🔗 SolidWorks API 服务已连接")

    test_ellipse_extrusion()
    test_ellipse_cut()
    test_ellipse_on_top_face()

    print("\n" + "=" * 60)
    print("🎉 所有椭圆测试通过！")
    print("=" * 60)
