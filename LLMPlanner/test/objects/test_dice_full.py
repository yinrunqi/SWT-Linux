"""
test_dice_full.py — 完整标准骰子（API 直调，含 21 个点数凹坑）

这是 test_dice.py（仅圆角立方体）的进阶版：在立方体 6 个面切出标准点数凹坑。
点数布局是确定性的几何计算（非自然语言可靠表达），用 API 直调方式生成，
确保几何 100% 正确，可作为"标准骰子配方"参考。

标准骰子规则：对面点数之和为 7（1↔6, 2↔5, 3↔4）。

⚠️ 关键坐标（实测确认）：上视面画 20mm 正方形并拉伸 20mm 后，立方体包围盒为：
  X ∈ [-0.01, 0.01]（中心 0）
  Y ∈ [0,     0.02]（中心 0.01）
  Z ∈ [-0.01, 0.01]（中心 0）   ← 上视面 y→模型-Z，矩形 y∈[-0.01,0.01] 映射到 Z∈[-0.01,0.01]
面分配：
  顶面 Y=0.02 → 1 点      底面 Y=0     → 6 点
  前面 Z=0.01 → 2 点      后面 Z=-0.01 → 5 点
  右面 X=0.01 → 3 点      左面 X=-0.01 → 4 点

用法：
  python test/objects/test_dice_full.py
"""

import sys

import requests

BASE = "http://localhost:5100"

# 几何参数（米）
HALF = 0.01          # 半边长（立方体边长 20mm）
PIP_R = 0.0015       # 凹点半径 1.5mm
PIP_DEPTH = 0.0015   # 凹点深度 1.5mm
OFF = 0.005          # 点位偏移量（距面中心 5mm）

# 各方向中心
CX = 0.0
CY = 0.01
CZ = 0.0


def api(path, body=None):
    """POST/GET 请求，返回 JSON。"""
    if body is None:
        r = requests.get(f"{BASE}{path}", timeout=60)
    else:
        r = requests.post(f"{BASE}{path}", json=body, timeout=60)
    return r.json()


def make_cube():
    """创建 20mm 立方体。"""
    api("/api/new-part")
    api("/api/select-plane", {"index": 1})  # 上视面
    api("/api/sketch/rectangle", {"x1": -HALF, "y1": -HALF, "x2": HALF, "y2": HALF})
    api("/api/feature/extrusion", {"depth": 0.02})


def cut_face_pips(name, select_ray, circles, flip=True):
    """
    在一个面上切出若干凹点。
    name:       面名（打印用）
    select_ray: select_face_and_sketch 的射线参数 dict
    circles:    圆心列表 [{cx,cy,cz}, ...]（模型 3D 坐标，米）
    flip:       切除方向是否反转（面法向量朝外，凹点需朝内切）
    """
    print(f"  [{name} {len(circles)} 点]")
    f = api("/api/select-face-and-sketch", select_ray)
    if not f.get("success"):
        print(f"    ❌ 选面失败: {f.get('message')}")
        return False
    for c in circles:
        api("/api/sketch/circle", {"cx": c["cx"], "cy": c["cy"], "cz": c["cz"], "radius": PIP_R})
    cut = api("/api/feature/cut-extrusion",
              {"depth": PIP_DEPTH, "endCondition": "blind", "flipDirection": flip})
    ok = cut.get("success", False)
    print(f"    {'✅' if ok else '❌'} 切除: {cut.get('message')}")
    return ok


def build_standard_dice() -> bool:
    print("=" * 60)
    print("生成标准骰子（20mm 立方体 + 21 个点数凹坑）")
    print("=" * 60)

    try:
        if not api("/api/ping").get("success"):
            print("  ❌ 服务异常"); return False
    except Exception as e:
        print(f"  ❌ 无法连接服务: {e}"); return False
    print("  ✅ 服务正常\n")

    make_cube()
    print("  ✅ 立方体创建完成\n")

    results = []

    # 顶面 Y=0.02 (1 点)：中心；面内 = X, Z
    results.append(cut_face_pips(
        "顶面",
        {"rx": 0, "ry": 0.03, "rz": 0, "dx": 0, "dy": -1, "dz": 0},
        [{"cx": CX, "cy": 0.02, "cz": CZ}],
    ))

    # 前面 Z=0.01 (2 点)：对角；面内 = X, Y
    results.append(cut_face_pips(
        "前面",
        {"rx": 0, "ry": CY, "rz": 0.03, "dx": 0, "dy": 0, "dz": -1},
        [{"cx": CX - OFF, "cy": CY - OFF, "cz": 0.01},
         {"cx": CX + OFF, "cy": CY + OFF, "cz": 0.01}],
    ))

    # 右面 X=0.01 (3 点)：对角线；面内 = Y, Z
    results.append(cut_face_pips(
        "右面",
        {"rx": 0.03, "ry": CY, "rz": 0, "dx": -1, "dy": 0, "dz": 0},
        [{"cx": 0.01, "cy": CY - OFF, "cz": CZ - OFF},
         {"cx": 0.01, "cy": CY, "cz": CZ},
         {"cx": 0.01, "cy": CY + OFF, "cz": CZ + OFF}],
    ))

    # 左面 X=-0.01 (4 点)：四角；面内 = Y, Z
    results.append(cut_face_pips(
        "左面",
        {"rx": -0.03, "ry": CY, "rz": 0, "dx": 1, "dy": 0, "dz": 0},
        [{"cx": -0.01, "cy": CY - OFF, "cz": CZ - OFF},
         {"cx": -0.01, "cy": CY - OFF, "cz": CZ + OFF},
         {"cx": -0.01, "cy": CY + OFF, "cz": CZ - OFF},
         {"cx": -0.01, "cy": CY + OFF, "cz": CZ + OFF}],
    ))

    # 后面 Z=-0.01 (5 点)：四角 + 中心；面内 = X, Y
    results.append(cut_face_pips(
        "后面",
        {"rx": 0, "ry": CY, "rz": -0.03, "dx": 0, "dy": 0, "dz": 1},
        [{"cx": CX - OFF, "cy": CY - OFF, "cz": -0.01},
         {"cx": CX + OFF, "cy": CY - OFF, "cz": -0.01},
         {"cx": CX, "cy": CY, "cz": -0.01},
         {"cx": CX - OFF, "cy": CY + OFF, "cz": -0.01},
         {"cx": CX + OFF, "cy": CY + OFF, "cz": -0.01}],
    ))

    # 底面 Y=0 (6 点)：两列三排；面内 = X, Z
    results.append(cut_face_pips(
        "底面",
        {"rx": 0, "ry": -0.01, "rz": 0, "dx": 0, "dy": 1, "dz": 0},
        [{"cx": CX - OFF, "cy": 0, "cz": CZ - OFF},
         {"cx": CX + OFF, "cy": 0, "cz": CZ - OFF},
         {"cx": CX - OFF, "cy": 0, "cz": CZ},
         {"cx": CX + OFF, "cy": 0, "cz": CZ},
         {"cx": CX - OFF, "cy": 0, "cz": CZ + OFF},
         {"cx": CX + OFF, "cy": 0, "cz": CZ + OFF}],
    ))

    ok_count = sum(1 for r in results if r)
    print(f"\n  6 个面中 {ok_count}/6 个成功切出点数")

    bbox = api("/api/body-bounding-box").get("boundingBox", {})
    sx, sy, sz = bbox.get("sizeX", 0) * 1000, bbox.get("sizeY", 0) * 1000, bbox.get("sizeZ", 0) * 1000
    print(f"  包围盒: {sx:.1f} × {sy:.1f} × {sz:.1f} mm")

    passed = ok_count == 6 and abs(sx - 20) < 2 and abs(sy - 20) < 2 and abs(sz - 20) < 2
    print(f"\n  {'🎉 标准骰子生成成功！' if passed else '⚠️ 部分面切除失败，请在 SolidWorks 中检查'}")
    return passed


if __name__ == "__main__":
    passed = build_standard_dice()
    print("\n" + "=" * 60)
    print(f"  完整骰子: {'✅ 通过' if passed else '❌ 失败'}")
    sys.exit(0 if passed else 1)
