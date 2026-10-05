"""
切除拉伸（FeatureCut）接口测试。
测试前请确保：
  1. SolidWorks 已启动
  2. Chapter1.ConnectToSw.exe 已运行（监听 http://localhost:5100）

用法：
  python test_cut_extrusion.py          # 运行全部测试
  python test_cut_extrusion.py blind    # 仅运行盲孔测试
  python test_cut_extrusion.py through  # 仅运行贯穿孔测试
"""

import os
import sys
import pytest
import requests
import time

BASE = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")
pytestmark = pytest.mark.solidworks


def api(method, path, json=None):
    """发送请求并打印结果，返回响应 JSON。"""
    url = f"{BASE}{path}"
    if method == "GET":
        resp = requests.get(url, timeout=10)
    else:
        resp = requests.post(url, json=json, timeout=30)
    data = resp.json()
    status = "✅" if data.get("success") else "❌"
    print(f"  {status} {method} {path} → {data.get('message', '')}")
    return data


def check_server():
    """检查 HTTP API 服务是否可用。"""
    print("=" * 60)
    print("检查 API 服务连通性...")
    print("=" * 60)
    try:
        resp = requests.get(f"{BASE}/api/ping", timeout=5)
        if resp.json().get("success"):
            print("  ✅ API 服务正常\n")
            return True
    except Exception as e:
        print(f"  ❌ 无法连接 API 服务: {e}")
        print("  请确保 Chapter1.ConnectToSw.exe 已运行\n")
        return False


def test_blind_hole():
    """
    盲孔测试：
    创建 100×50×30mm 长方体 → 选上表面 → 画半径 10mm 圆 → 切除深度 20mm
    预期：长方体上表面出现一个深 20mm 的圆孔。
    """
    print("=" * 60)
    print("测试 1：盲孔切除 (blind)")
    print("  长方体 100×50×30mm，上表面画 R10mm 圆，切除深度 20mm")
    print("=" * 60)

    # 1. 新建零件
    api("POST", "/api/new-part")

    # 2. 选前视基准面，画矩形，拉伸出长方体
    api("POST", "/api/select-plane", {"index": 0})  # 前视面
    api("POST", "/api/sketch/rectangle", {
        "x1": -0.05, "y1": 0, "x2": 0.05, "y2": 0.05  # 100mm宽 × 50mm高
    })
    api("POST", "/api/feature/extrusion", {"depth": 0.03})  # 拉伸 30mm
    print("  → 长方体创建完成")

    # 3. 查看当前包围盒，确认长方体尺寸
    bbox = api("GET", "/api/body-bounding-box")
    if "boundingBox" in bbox:
        b = bbox["boundingBox"]
        print(f"  → 包围盒: {b.get('sizeX', 0)*1000:.1f} × {b.get('sizeY', 0)*1000:.1f} × {b.get('sizeZ', 0)*1000:.1f} mm")

    # 4. 选上表面进入草图（射线从上方向下打）
    #    长方体中心大约在 (0, 0.025, 0.015)，上表面 Y=0.05
    #    从 (0, 0.1, 0.015) 向 (0, -1, 0) 方向射线拾取上表面
    api("POST", "/api/select-face-and-sketch", {
        "rx": 0, "ry": 0.1, "rz": 0.015,
        "dx": 0, "dy": -1, "dz": 0
    })

    # 5. 在上表面画圆（使用模型 3D 坐标，C# 端会自动转换为草图坐标）
    #    上表面中心点的模型坐标: X=0, Y=0.025, Z=0.015
    #    （包围盒 X[-0.05,0.05], Y[0,0.05], Z[0,0.03] → 面中心在 (0, 0.05, 0.015)）
    #    注意：cy 是面上的 Y 坐标（Y=0.05），cz 是 Z 方向中点（Z=0.015）
    api("POST", "/api/sketch/circle", {
        "cx": 0, "cy": 0.05, "cz": 0.015, "radius": 0.01
    })

    # 6. 执行盲孔切除，深度 20mm
    result = api("POST", "/api/feature/cut-extrusion", {"depth": 0.02})

    # 7. 查看特征树，确认切除特征已创建
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        has_cut = any("切除" in name or "Cut" in name for name in feature_names)
        print(f"  → 特征树: {feature_names}")
        if has_cut:
            print("  ✅ 盲孔切除特征已创建")
        else:
            print("  ⚠️ 未在特征树中找到切除特征名称（可能名称不同，请在 SolidWorks 中确认）")

    print()
    return result.get("success", False)


def test_through_all_hole():
    """
    贯穿孔测试：
    创建 80×60×10mm 薄板 → 选上表面 → 画半径 3mm 圆 → 贯穿切除
    预期：薄板上出现一个完全贯穿的直径 6mm 圆孔。
    """
    print("=" * 60)
    print("测试 2：贯穿孔切除 (through_all)")
    print("  薄板 80×60×10mm，上表面画 R3mm 圆，贯穿切除")
    print("=" * 60)

    # 1. 新建零件
    api("POST", "/api/new-part")

    # 2. 选前视基准面，画矩形，拉伸出薄板
    api("POST", "/api/select-plane", {"index": 0})  # 前视面
    api("POST", "/api/sketch/rectangle", {
        "x1": -0.04, "y1": 0, "x2": 0.04, "y2": 0.06  # 80mm宽 × 60mm高
    })
    api("POST", "/api/feature/extrusion", {"depth": 0.01})  # 拉伸 10mm（薄板）
    print("  → 薄板创建完成")

    # 3. 查看包围盒
    bbox = api("GET", "/api/body-bounding-box")
    if "boundingBox" in bbox:
        b = bbox["boundingBox"]
        print(f"  → 包围盒: {b.get('sizeX', 0)*1000:.1f} × {b.get('sizeY', 0)*1000:.1f} × {b.get('sizeZ', 0)*1000:.1f} mm")

    # 4. 选上表面进入草图
    #    薄板：X[-0.04, 0.04], Y[0, 0.06], Z[0, 0.01]
    #    上表面 Y=0.06，从 (0, 0.1, 0.005) 向下射线
    api("POST", "/api/select-face-and-sketch", {
        "rx": 0, "ry": 0.1, "rz": 0.005,
        "dx": 0, "dy": -1, "dz": 0
    })

    # 5. 画圆（使用模型 3D 坐标，半径 3mm）
    #    上表面 Y=0.06，中心 X=0, Z=0.005
    api("POST", "/api/sketch/circle", {
        "cx": 0, "cy": 0.06, "cz": 0.005, "radius": 0.003
    })

    # 6. 贯穿切除
    result = api("POST", "/api/feature/cut-extrusion", {
        "depth": 0.01,  # 贯穿模式下 depth 不影响实际深度，但参数必须给
        "endCondition": "through_all"
    })

    # 7. 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        has_cut = any("切除" in name or "Cut" in name for name in feature_names)
        print(f"  → 特征树: {feature_names}")
        if has_cut:
            print("  ✅ 贯穿孔切除特征已创建")
        else:
            print("  ⚠️ 未在特征树中找到切除特征名称（可能名称不同，请在 SolidWorks 中确认）")

    print()
    return result.get("success", False)


if __name__ == "__main__":
    if not check_server():
        sys.exit(1)

    # 解析命令行参数，选择运行哪些测试
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    results = {}

    if arg in ("all", "blind"):
        results["盲孔切除"] = test_blind_hole()
        if arg == "all":
            time.sleep(1)  # 两个测试之间稍作间隔

    if arg in ("all", "through"):
        results["贯穿孔切除"] = test_through_all_hole()

    # 汇总
    print("=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    for name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
    print()

    all_passed = all(results.values())
    if all_passed:
        print("🎉 所有测试通过！")
    else:
        print("⚠️ 部分测试失败，请检查 SolidWorks 中的实际效果")
    sys.exit(0 if all_passed else 1)
