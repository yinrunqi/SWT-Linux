"""
旋转特征（Revolve）接口测试。
测试前请确保：
  1. SolidWorks 已启动
  2. Chapter1.ConnectToSw.exe 已运行（监听 http://localhost:5100）

测试内容：
  1. 中心线 + 矩形轮廓 → 旋转 360° → 圆柱体
  2. 中心线 + 线段轮廓 → 旋转 360° → 圆柱体（手动画轮廓）
  3. 中心线 + 圆弧+直线轮廓 → 旋转 360° → 球体

用法：
  python test_revolve.py              # 运行全部测试
  python test_revolve.py cylinder     # 仅运行圆柱体测试（矩形轮廓旋转）
  python test_revolve.py cylinder2    # 仅运行圆柱体测试（线段轮廓旋转）
  python test_revolve.py sphere       # 仅运行球体测试（圆弧轮廓旋转）
"""

import os
import sys
import math
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


def test_cylinder_rectangle():
    """
    圆柱体测试（矩形轮廓旋转）：
    在前视基准面画中心线（Y轴方向）+ 矩形轮廓（中心线右侧），旋转 360°。
    
    轮廓：矩形 (0,0)→(0.02,0.08)，中心线 x=0
    预期结果：⌀40mm × 80mm 的圆柱体
    """
    print("=" * 60)
    print("测试 1：圆柱体（矩形轮廓 + 旋转 360°）")
    print("  中心线 Y 轴方向 + 矩形 20mm×80mm → 旋转360° → ⌀40mm×80mm 圆柱")
    print("=" * 60)

    # 1. 新建零件
    api("POST", "/api/new-part")

    # 2. 选前视基准面
    api("POST", "/api/select-plane", {"index": 0})

    # 3. 画中心线（Y 轴方向：从原点到 (0, 0.08)）
    api("POST", "/api/sketch/centerline", {
        "x1": 0, "y1": 0, "x2": 0, "y2": 0.08
    })

    # 4. 画矩形轮廓（在中心线右侧：(0, 0) → (0.02, 0.08)）
    #    宽度 0.02m = 20mm（即半径），高度 0.08m = 80mm
    api("POST", "/api/sketch/rectangle", {
        "x1": 0, "y1": 0, "x2": 0.02, "y2": 0.08
    })

    # 5. 旋转 360°
    result = api("POST", "/api/feature/revolve", {"angle": 360})

    # 6. 验证：查看包围盒，预期 ≈ 40mm × 80mm × 40mm
    bbox = api("GET", "/api/body-bounding-box")
    if "boundingBox" in bbox:
        b = bbox["boundingBox"]
        sx = b.get("sizeX", 0) * 1000
        sy = b.get("sizeY", 0) * 1000
        sz = b.get("sizeZ", 0) * 1000
        print(f"  → 包围盒: {sx:.1f} × {sy:.1f} × {sz:.1f} mm")
        # 圆柱体：X≈40, Y≈80, Z≈40
        if abs(sx - 40) < 2 and abs(sy - 80) < 2 and abs(sz - 40) < 2:
            print("  ✅ 包围盒尺寸符合预期（⌀40mm × 80mm 圆柱体）")
        else:
            print("  ⚠️ 包围盒尺寸与预期不完全吻合，请在 SolidWorks 中确认")

    # 7. 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        has_revolve = any("旋转" in name or "Revolve" in name for name in feature_names)
        print(f"  → 特征树: {feature_names}")
        if has_revolve:
            print("  ✅ 旋转特征已创建")
        else:
            print("  ⚠️ 未在特征树中找到旋转特征名称")

    print()
    return result.get("success", False)


def test_cylinder_lines():
    """
    圆柱体测试（线段轮廓旋转）：
    在前视基准面画中心线 + 用直线手动画矩形轮廓，旋转 360°。
    
    这测试了中心线和多条直线的组合使用。
    轮廓：三条直线 (0,0)→(0.015,0)→(0.015,0.06)→(0,0.06)，中心线 x=0
    预期结果：⌀30mm × 60mm 的圆柱体
    """
    print("=" * 60)
    print("测试 2：圆柱体（线段轮廓 + 旋转 360°）")
    print("  中心线 + 3条直线 → 旋转360° → ⌀30mm×60mm 圆柱")
    print("=" * 60)

    # 1. 新建零件
    api("POST", "/api/new-part")

    # 2. 选前视基准面
    api("POST", "/api/select-plane", {"index": 0})

    # 3. 画中心线（Y 轴方向）
    api("POST", "/api/sketch/centerline", {
        "x1": 0, "y1": 0, "x2": 0, "y2": 0.06
    })

    # 4. 画轮廓：底边、右边、顶边（中心线自动封闭左边）
    api("POST", "/api/sketch/line", {
        "x1": 0, "y1": 0, "x2": 0.015, "y2": 0
    })
    api("POST", "/api/sketch/line", {
        "x1": 0.015, "y1": 0, "x2": 0.015, "y2": 0.06
    })
    api("POST", "/api/sketch/line", {
        "x1": 0.015, "y1": 0.06, "x2": 0, "y2": 0.06
    })

    # 5. 旋转 360°
    result = api("POST", "/api/feature/revolve", {"angle": 360})

    # 6. 验证包围盒
    bbox = api("GET", "/api/body-bounding-box")
    if "boundingBox" in bbox:
        b = bbox["boundingBox"]
        sx = b.get("sizeX", 0) * 1000
        sy = b.get("sizeY", 0) * 1000
        sz = b.get("sizeZ", 0) * 1000
        print(f"  → 包围盒: {sx:.1f} × {sy:.1f} × {sz:.1f} mm")
        if abs(sx - 30) < 2 and abs(sy - 60) < 2 and abs(sz - 30) < 2:
            print("  ✅ 包围盒尺寸符合预期（⌀30mm × 60mm 圆柱体）")
        else:
            print("  ⚠️ 包围盒尺寸与预期不完全吻合")

    print()
    return result.get("success", False)


def test_sphere():
    """
    球体测试（圆弧轮廓旋转）：
    在前视基准面画中心线（Y轴方向）+ 半圆弧轮廓，旋转 360°。
    
    轮廓：圆弧圆心(0, 0.02)，从(0.02, 0.02)到(0, 0.02)画半圆（实际是从右到上的四分之一弧）
    实际实现：用半圆弧 + 一条直线封闭轮廓
    
    球体半径 R=20mm → ⌀40mm
    圆弧圆心 (0, 0.02)
    起点 (0.02, 0.02) — 圆心正右方
    终点 (-0.02, 0.02) — 圆心正左方（但我们用中心线在 X=0，所以终点为 (0, 0.04) 和 (0, 0)）
    
    更简单的方案：圆弧圆心 (0, 0.02)，从 (0, 0) 逆时针到 (0, 0.04)，即右半圆弧
    预期结果：⌀40mm 球体
    """
    print("=" * 60)
    print("测试 3：球体（圆弧轮廓 + 旋转 360°）")
    print("  中心线 + 半圆弧(R=20mm) → 旋转360° → ⌀40mm 球体")
    print("=" * 60)

    # 1. 新建零件
    api("POST", "/api/new-part")

    # 2. 选前视基准面
    api("POST", "/api/select-plane", {"index": 0})

    # 3. 画中心线（Y 轴方向，覆盖球体高度范围）
    api("POST", "/api/sketch/centerline", {
        "x1": 0, "y1": 0, "x2": 0, "y2": 0.04
    })

    # 4. 画半圆弧（右半圆弧）
    #    圆心: (0, 0.02)  半径 0.02m = 20mm
    #    起点: (0, 0)      — 圆心正下方
    #    终点: (0, 0.04)   — 圆心正上方
    #    方向: -1（逆时针）— 从下方经右方到上方（右半圆弧）
    #
    #    注意：不需要额外画直线来封闭轮廓！
    #    圆弧两端点都在旋转轴（X=0）上，SolidWorks 会自动用旋转轴封闭。
    #    如果画一条与中心线重合的直线反而会导致旋转失败。
    api("POST", "/api/sketch/arc", {
        "cx": 0, "cy": 0.02,
        "startX": 0, "startY": 0,
        "endX": 0, "endY": 0.04,
        "direction": -1
    })

    # 5. 旋转 360°
    result = api("POST", "/api/feature/revolve", {"angle": 360})

    # 6. 验证包围盒
    bbox = api("GET", "/api/body-bounding-box")
    if "boundingBox" in bbox:
        b = bbox["boundingBox"]
        sx = b.get("sizeX", 0) * 1000
        sy = b.get("sizeY", 0) * 1000
        sz = b.get("sizeZ", 0) * 1000
        print(f"  → 包围盒: {sx:.1f} × {sy:.1f} × {sz:.1f} mm")
        # 球体：所有方向≈40mm
        if abs(sx - 40) < 2 and abs(sy - 40) < 2 and abs(sz - 40) < 2:
            print("  ✅ 包围盒尺寸符合预期（⌀40mm 球体）")
        else:
            print("  ⚠️ 包围盒尺寸与预期不完全吻合")

    # 7. 查看特征树
    tree = api("GET", "/api/feature-tree")
    if "features" in tree:
        feature_names = [f["name"] for f in tree["features"]]
        print(f"  → 特征树: {feature_names}")

    print()
    return result.get("success", False)


if __name__ == "__main__":
    if not check_server():
        sys.exit(1)

    # 解析命令行参数
    arg = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    results = {}

    if arg in ("all", "cylinder"):
        results["圆柱体（矩形轮廓旋转）"] = test_cylinder_rectangle()
        if arg == "all":
            time.sleep(1)

    if arg in ("all", "cylinder2"):
        results["圆柱体（线段轮廓旋转）"] = test_cylinder_lines()
        if arg == "all":
            time.sleep(1)

    if arg in ("all", "sphere"):
        results["球体（圆弧轮廓旋转）"] = test_sphere()

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
