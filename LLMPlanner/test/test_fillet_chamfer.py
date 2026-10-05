"""
圆角（Fillet）和倒角（Chamfer）接口测试。
测试前请确保：
  1. SolidWorks 已启动
  2. Chapter1.ConnectToSw.exe 已运行（监听 http://localhost:5100）

用法：
  python test_fillet_chamfer.py              # 运行全部测试
  python test_fillet_chamfer.py fillet       # 仅运行圆角测试
  python test_fillet_chamfer.py chamfer      # 仅运行倒角测试
  python test_fillet_chamfer.py fillet_ray   # 仅运行射线选边圆角测试
  python test_fillet_chamfer.py chamfer_dd   # 仅运行不等距倒角测试
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


def create_box(length_mm, width_mm, height_mm):
    """
    辅助函数：创建一个长方体（前视基准面矩形 + 拉伸）。
    参数单位为 mm，内部转换为 m。
    返回包围盒数据。
    """
    l = length_mm / 1000
    w = width_mm / 1000
    h = height_mm / 1000

    api("POST", "/api/new-part")
    api("POST", "/api/select-plane", {"index": 0})  # 前视面
    api("POST", "/api/sketch/rectangle", {
        "x1": -l / 2, "y1": 0, "x2": l / 2, "y2": w
    })
    api("POST", "/api/feature/extrusion", {"depth": h})
    print(f"  → 长方体 {length_mm}×{width_mm}×{height_mm}mm 创建完成")

    bbox = api("GET", "/api/body-bounding-box")
    if "boundingBox" in bbox:
        b = bbox["boundingBox"]
        print(f"  → 包围盒: {b.get('sizeX', 0)*1000:.1f} × {b.get('sizeY', 0)*1000:.1f} × {b.get('sizeZ', 0)*1000:.1f} mm")
    return bbox


def test_fillet_all_edges():
    """
    圆角测试 1：所有边圆角
    创建 100mm 立方体 → 对所有边做 5mm 圆角
    预期：特征树中出现 Fillet 特征
    """
    print("=" * 60)
    print("测试 1：所有边圆角 (fillet, edgeSelection=all)")
    print("  100mm 立方体，所有边 R5mm 圆角")
    print("=" * 60)

    create_box(100, 100, 100)

    # 对所有边做圆角
    result = api("POST", "/api/feature/fillet", {
        "radius": 0.005,           # 5mm
        "edgeSelection": "all"
    })

    # 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        feature_types = [f["type"] for f in tree["features"]]
        has_fillet = any("圆角" in name or "Fillet" in name or "Fillet" in t
                        for name, t in zip(feature_names, feature_types))
        print(f"  → 特征树: {feature_names}")
        if has_fillet:
            print("  ✅ 圆角特征已创建")
        else:
            print("  ⚠️ 未在特征树中找到圆角特征")

    print()
    return result.get("success", False)


def test_fillet_ray_edge():
    """
    圆角测试 2：射线选边圆角
    创建 80×60×40mm 长方体 → 通过射线选中一条顶边 → 做 3mm 圆角
    预期：仅选中的一条边有圆角
    """
    print("=" * 60)
    print("测试 2：射线选边圆角 (fillet, edgeSelection=ray)")
    print("  80×60×40mm 长方体，通过射线选中顶部前侧边做 R3mm 圆角")
    print("=" * 60)

    create_box(80, 60, 40)

    # 长方体：X[-0.04, 0.04], Y[0, 0.06], Z[0, 0.04]
    # 选中顶部前侧边（Y=0.06, Z=0 位置的边）
    # 射线从 (0, 0.07, -0.01) 指向 (0, -1, 1) 方向，应命中该边
    result = api("POST", "/api/feature/fillet", {
        "radius": 0.003,           # 3mm
        "edgeSelection": "ray",
        "rx": 0, "ry": 0.07, "rz": -0.01,
        "dx": 0, "dy": -1, "dz": 1
    })

    # 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        has_fillet = any("圆角" in name or "Fillet" in name for name in feature_names)
        print(f"  → 特征树: {feature_names}")
        if has_fillet:
            print("  ✅ 射线选边圆角特征已创建")
        else:
            print("  ⚠️ 未在特征树中找到圆角特征")

    print()
    return result.get("success", False)


def test_chamfer_equal_distance():
    """
    倒角测试 1：等距倒角
    创建 80×60×40mm 长方体 → 对所有边做 3mm 等距倒角
    预期：特征树中出现 Chamfer 特征
    """
    print("=" * 60)
    print("测试 3：等距倒角 (chamfer, type=equal_distance, all edges)")
    print("  80×60×40mm 长方体，所有边 C3mm 等距倒角")
    print("=" * 60)

    create_box(80, 60, 40)

    result = api("POST", "/api/feature/chamfer", {
        "type": "equal_distance",
        "distance1": 0.003,        # 3mm
        "edgeSelection": "all"
    })

    # 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        feature_types = [f["type"] for f in tree["features"]]
        has_chamfer = any("倒角" in name or "Chamfer" in name or "Chamfer" in t
                         for name, t in zip(feature_names, feature_types))
        print(f"  → 特征树: {feature_names}")
        if has_chamfer:
            print("  ✅ 等距倒角特征已创建")
        else:
            print("  ⚠️ 未在特征树中找到倒角特征")

    print()
    return result.get("success", False)


def test_chamfer_distance_distance():
    """
    倒角测试 2：不等距倒角
    创建 60×60×30mm 长方体 → 射线选顶边 → 做 2mm × 4mm 不等距倒角
    """
    print("=" * 60)
    print("测试 4：不等距倒角 (chamfer, type=distance_distance, ray)")
    print("  60×60×30mm 长方体，射线选顶部前边做 2mm × 4mm 不等距倒角")
    print("=" * 60)

    create_box(60, 60, 30)

    # 长方体：X[-0.03, 0.03], Y[0, 0.06], Z[0, 0.03]
    # 射线选顶部前侧边 (Y=0.06, Z=0)
    result = api("POST", "/api/feature/chamfer", {
        "type": "distance_distance",
        "distance1": 0.002,        # 2mm
        "distance2": 0.004,        # 4mm
        "edgeSelection": "ray",
        "rx": 0, "ry": 0.07, "rz": -0.01,
        "dx": 0, "dy": -1, "dz": 1
    })

    # 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        has_chamfer = any("倒角" in name or "Chamfer" in name for name in feature_names)
        print(f"  → 特征树: {feature_names}")
        if has_chamfer:
            print("  ✅ 不等距倒角特征已创建")
        else:
            print("  ⚠️ 未在特征树中找到倒角特征")

    print()
    return result.get("success", False)


def test_fillet_then_chamfer():
    """
    组合测试：先圆角后倒角
    创建 100×80×50mm 长方体 → 射线选一条底边做圆角 → 射线选另一条边做倒角
    验证两个特征可以共存
    """
    print("=" * 60)
    print("测试 5：组合测试 — 圆角 + 倒角共存")
    print("  100×80×50mm 长方体，底部前边 R5mm 圆角，顶部前边 C3mm 倒角")
    print("=" * 60)

    create_box(100, 80, 50)

    # 长方体：X[-0.05, 0.05], Y[0, 0.08], Z[0, 0.05]

    # 圆角：选底部前边 (Y=0, Z=0)
    # 从 (0, -0.01, -0.01) 向 (0, 1, 1) 射线
    r1 = api("POST", "/api/feature/fillet", {
        "radius": 0.005,
        "edgeSelection": "ray",
        "rx": 0, "ry": -0.01, "rz": -0.01,
        "dx": 0, "dy": 1, "dz": 1
    })

    # 倒角：选顶部前边 (Y=0.08, Z=0)
    # 从 (0, 0.09, -0.01) 向 (0, -1, 1) 射线
    r2 = api("POST", "/api/feature/chamfer", {
        "type": "equal_distance",
        "distance1": 0.003,
        "edgeSelection": "ray",
        "rx": 0, "ry": 0.09, "rz": -0.01,
        "dx": 0, "dy": -1, "dz": 1
    })

    # 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        has_fillet = any("圆角" in name or "Fillet" in name for name in feature_names)
        has_chamfer = any("倒角" in name or "Chamfer" in name for name in feature_names)
        print(f"  → 特征树: {feature_names}")
        if has_fillet and has_chamfer:
            print("  ✅ 圆角和倒角特征均已创建")
        elif has_fillet:
            print("  ⚠️ 仅圆角成功，倒角未找到")
        elif has_chamfer:
            print("  ⚠️ 仅倒角成功，圆角未找到")
        else:
            print("  ❌ 圆角和倒角特征均未找到")

    print()
    both_ok = r1.get("success", False) and r2.get("success", False)
    return both_ok


if __name__ == "__main__":
    if not check_server():
        sys.exit(1)

    # 解析命令行参数，选择运行哪些测试
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    results = {}

    test_map = {
        "fillet": ("圆角(所有边)", test_fillet_all_edges),
        "fillet_ray": ("圆角(射线选边)", test_fillet_ray_edge),
        "chamfer": ("等距倒角(所有边)", test_chamfer_equal_distance),
        "chamfer_dd": ("不等距倒角(射线选边)", test_chamfer_distance_distance),
        "combo": ("圆角+倒角组合", test_fillet_then_chamfer),
    }

    if arg == "all":
        for key, (name, func) in test_map.items():
            results[name] = func()
            time.sleep(1)
    elif arg in test_map:
        name, func = test_map[arg]
        results[name] = func()
    else:
        print(f"未知测试名: {arg}")
        print(f"可用: {', '.join(test_map.keys())}, all")
        sys.exit(1)

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
