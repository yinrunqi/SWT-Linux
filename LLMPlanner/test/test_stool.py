"""
test_stool.py — 四脚板凳建模测试

测试阶段一能力：拉伸方向翻转（flipDirection）。
通过 HTTP API 直接调用 SolidWorks 服务，验证能否做出一个四脚板凳。

运行方式：
    cd C:\\codes\\sw\\SolidWorksTutorial\\LLMPlanner
    venv\\Scripts\\activate.bat
    python test/test_stool.py              # 运行所有测试
    python test/test_stool.py flip         # 仅测试反向拉伸
    python test/test_stool.py stool        # 仅测试四脚板凳
    python test/test_stool.py stool_fillet # 带圆角的板凳
"""

import sys
import math
import requests

BASE_URL = "http://localhost:5100"


def api_get(endpoint: str, timeout: int = 60) -> dict:
    """GET 请求"""
    resp = requests.get(f"{BASE_URL}{endpoint}", timeout=timeout)
    data = resp.json()
    return data


def api_post(endpoint: str, body: dict = None, timeout: int = 60) -> dict:
    """POST 请求"""
    resp = requests.post(f"{BASE_URL}{endpoint}", json=body, timeout=timeout)
    data = resp.json()
    return data


def assert_success(result: dict, step_name: str):
    """断言 API 调用成功"""
    if not result.get("success"):
        print(f"  ❌ {step_name} 失败: {result.get('message', '未知错误')}")
        sys.exit(1)
    print(f"  ✅ {step_name}")


def assert_approx(actual: float, expected: float, tolerance: float, desc: str):
    """断言数值近似相等"""
    if abs(actual - expected) > tolerance:
        print(f"  ❌ {desc}: 期望 {expected:.4f}, 实际 {actual:.4f}, 超出容差 {tolerance:.4f}")
        sys.exit(1)
    print(f"  ✅ {desc}: {actual:.4f} ≈ {expected:.4f} (容差 {tolerance:.4f})")


# ============================================================
# 测试 1：反向拉伸（验证 flipDirection 参数）
# ============================================================

def test_flip_direction():
    """
    测试 flipDirection=true 的效果。
    在上视面画一个 50mm×50mm 的矩形，用 flipDirection=true 向下拉伸 30mm。
    预期：包围盒 Y 方向在 -0.03 ~ 0（负方向拉伸）。
    """
    print("\n" + "=" * 60)
    print("测试 1：反向拉伸 (flipDirection=true)")
    print("=" * 60)

    # 新建零件
    assert_success(api_post("/api/new-part"), "新建零件")

    # 选上视面 (index=1) 并进入草图
    assert_success(api_post("/api/select-plane", {"index": 1}), "选上视面")

    # 画矩形 50mm × 50mm
    assert_success(
        api_post("/api/sketch/rectangle", {"x1": 0, "y1": 0, "x2": 0.05, "y2": 0.05}),
        "画矩形 50×50mm"
    )

    # 反向拉伸 30mm (flipDirection=true)
    assert_success(
        api_post("/api/feature/extrusion", {"depth": 0.03, "flipDirection": True}),
        "反向拉伸 30mm"
    )

    # 验证包围盒
    bbox_result = api_get("/api/body-bounding-box")
    assert_success(bbox_result, "获取包围盒")
    bbox = bbox_result["boundingBox"]

    print(f"\n  包围盒: X[{bbox['minX']:.4f}, {bbox['maxX']:.4f}], "
          f"Y[{bbox['minY']:.4f}, {bbox['maxY']:.4f}], "
          f"Z[{bbox['minZ']:.4f}, {bbox['maxZ']:.4f}]")

    # 上视面在 Y=0，flipDirection=true 应该向 Y 负方向拉伸
    # 所以 minY 应该 ≈ -0.03, maxY 应该 ≈ 0
    assert_approx(bbox["minY"], -0.03, 0.001, "minY (反向拉伸到 -30mm)")
    assert_approx(bbox["maxY"], 0.0, 0.001, "maxY (上视面在 Y=0)")
    assert_approx(bbox["sizeY"], 0.03, 0.001, "sizeY = 30mm")

    print("\n  🎉 测试 1 通过！反向拉伸工作正常。")


# ============================================================
# 测试 2：四脚板凳（完整建模流程）
# ============================================================

def test_stool():
    """
    建模一个简单的四脚板凳。
    
    设计参数：
    - 凳面: 200mm × 200mm × 20mm (上视面画矩形, 向上拉伸)
    - 凳腿: 4 根圆柱，直径 20mm (半径 10mm)，长 150mm
    - 凳腿位置: 距边缘 30mm 的四个角

    策略：
    1. 上视面画凳面矩形 → 拉伸 20mm (向上)
    2. 选底面 → 画 4 个圆（腿截面）→ 反向拉伸 150mm (向下)
       由于当前 API 一次选面 → 画图 → 拉伸只能做一个特征，
       需要 4 次 "选底面 → 画圆 → 反向拉伸" 循环。

    预期结果：
    - 总高 = 凳面 20mm + 凳腿 150mm = 170mm
    - 包围盒: X=0~0.2, Y=-0.15~0.02, Z=-0.2~0
    - 凳面在 Y[0, 0.02]，腿在 Y[-0.15, 0]（底面法向量 -Y，默认拉伸方向即向下）
    """
    print("\n" + "=" * 60)
    print("测试 2：四脚板凳建模")
    print("=" * 60)

    # === Step 1: 创建凳面 ===
    print("\n--- Step 1: 创建凳面 200×200×20mm ---")

    assert_success(api_post("/api/new-part"), "新建零件")

    # 在上视面画凳面矩形
    # 上视面 (index=1): 草图 x→模型X, 草图 y→模型-Z, 拉伸→模型+Y
    assert_success(api_post("/api/select-plane", {"index": 1}), "选上视面")
    assert_success(
        api_post("/api/sketch/rectangle", {"x1": 0, "y1": 0, "x2": 0.2, "y2": 0.2}),
        "画凳面矩形 200×200mm"
    )
    # 向上拉伸 20mm (默认方向是 +Y)
    assert_success(
        api_post("/api/feature/extrusion", {"depth": 0.02}),
        "拉伸凳面 20mm"
    )

    # 验证凳面包围盒
    bbox = api_get("/api/body-bounding-box")["boundingBox"]
    print(f"  凳面包围盒: X[{bbox['minX']:.4f},{bbox['maxX']:.4f}] "
          f"Y[{bbox['minY']:.4f},{bbox['maxY']:.4f}] "
          f"Z[{bbox['minZ']:.4f},{bbox['maxZ']:.4f}]")

    # === Step 2: 创建 4 条腿 ===
    # 凳面底面在 Y=0（上视面拉伸前的位置）
    # 凳腿圆心位置：距边缘 30mm，即 X=0.03/0.17, Z=-0.03/-0.17
    # 射线选底面：从 Y 负方向向上射
    # 底面法向量朝 -Y，选底面射线：起点在 minY 下方，方向 (0, 1, 0)

    leg_positions = [
        (0.03, -0.03, "左前腿"),   # X=30mm, Z=-30mm (前视正方向)
        (0.17, -0.03, "右前腿"),   # X=170mm, Z=-30mm
        (0.03, -0.17, "左后腿"),   # X=30mm, Z=-170mm
        (0.17, -0.17, "右后腿"),   # X=170mm, Z=-170mm
    ]

    leg_radius = 0.01  # 10mm 半径 = 20mm 直径
    leg_length = 0.15  # 150mm 腿长

    for i, (lx, lz, name) in enumerate(leg_positions):
        print(f"\n--- Step 2.{i+1}: 创建{name} ---")

        # 选底面（Y=0 的面，从下方往上射）
        # 射线起点在底面下方，中心位置
        result = api_post("/api/select-face-and-sketch", {
            "rx": 0.1, "ry": -0.01, "rz": -0.1,
            "dx": 0, "dy": 1, "dz": 0
        })
        assert_success(result, f"选底面进入草图")

        # 在底面上画圆（腿截面）
        # 底面是 XZ 平面，但在草图中坐标已经被 ModelToSketchTransform 变换
        # 我们传入模型 3D 坐标 (lx, 0, lz)，C# 端会自动变换
        result = api_post("/api/sketch/circle", {
            "cx": lx, "cy": 0, "cz": lz, "radius": leg_radius
        })
        assert_success(result, f"画{name}截面圆 (r=10mm)")

        # 拉伸腿（底面法向量为 -Y，默认拉伸方向即为向下，不需要 flipDirection）
        result = api_post("/api/feature/extrusion", {
            "depth": leg_length
        })
        assert_success(result, f"{name}向下拉伸 150mm")

    # === 验证最终结果 ===
    print("\n--- 验证最终包围盒 ---")
    bbox = api_get("/api/body-bounding-box")["boundingBox"]
    print(f"  最终包围盒: X[{bbox['minX']:.4f},{bbox['maxX']:.4f}] "
          f"Y[{bbox['minY']:.4f},{bbox['maxY']:.4f}] "
          f"Z[{bbox['minZ']:.4f},{bbox['maxZ']:.4f}]")
    print(f"  尺寸: {bbox['sizeX']*1000:.1f} × {bbox['sizeY']*1000:.1f} × {bbox['sizeZ']*1000:.1f} mm")

    # 总高应该是 凳面 20mm + 腿 150mm = 170mm
    assert_approx(bbox["sizeY"], 0.17, 0.002, "总高度 170mm (凳面20+腿150)")
    # 宽度 200mm
    assert_approx(bbox["sizeX"], 0.2, 0.001, "宽度 200mm")

    # 验证特征树
    tree = api_get("/api/feature-tree")
    assert_success(tree, "获取特征树")
    features = tree["features"]
    # 统计拉伸特征（可能叫 "凸台-拉伸" 或 "Boss-Extrude"）
    extrude_features = [f for f in features if "Extrusion" in f.get("type", "") or "拉伸" in f.get("name", "")]
    print(f"  拉伸特征数量: {len(extrude_features)} (期望 5: 1凳面 + 4腿)")

    print("\n  🎉 测试 2 通过！四脚板凳建模成功！")


# ============================================================
# 测试 3：带圆角的板凳
# ============================================================

def test_stool_with_fillet():
    """
    建模一个带圆角的四脚板凳。
    策略：先完成所有结构性建模（凳面+4腿），最后做修饰特征（圆角）。
    这是 CAD 建模最佳实践——圆角会改变面拓扑，放在最后不影响其他操作。
    """
    print("\n" + "=" * 60)
    print("测试 3：带圆角的四脚板凳")
    print("=" * 60)

    # === Step 1: 创建凳面 ===
    print("\n--- Step 1: 创建凳面 200×200×20mm ---")
    assert_success(api_post("/api/new-part"), "新建零件")
    assert_success(api_post("/api/select-plane", {"index": 1}), "选上视面")
    assert_success(
        api_post("/api/sketch/rectangle", {"x1": 0, "y1": 0, "x2": 0.2, "y2": 0.2}),
        "画凳面矩形"
    )
    assert_success(api_post("/api/feature/extrusion", {"depth": 0.02}), "拉伸凳面")

    # === Step 2: 创建 4 条腿 ===
    leg_positions = [
        (0.03, -0.03, "左前腿"),
        (0.17, -0.03, "右前腿"),
        (0.03, -0.17, "左后腿"),
        (0.17, -0.17, "右后腿"),
    ]

    for i, (lx, lz, name) in enumerate(leg_positions):
        print(f"\n--- Step 2.{i+1}: 创建{name} ---")
        result = api_post("/api/select-face-and-sketch", {
            "rx": 0.1, "ry": -0.01, "rz": -0.1,
            "dx": 0, "dy": 1, "dz": 0
        })
        assert_success(result, f"选底面")
        result = api_post("/api/sketch/circle", {
            "cx": lx, "cy": 0, "cz": lz, "radius": 0.01
        })
        assert_success(result, f"画{name}截面圆")
        # 底面法向量为 -Y，默认拉伸方向即为向下，不需要 flipDirection
        result = api_post("/api/feature/extrusion", {
            "depth": 0.15
        })
        assert_success(result, f"{name}向下拉伸")

    # === Step 3: 对凳面顶部边做圆角（最后做修饰特征）===
    print("\n--- Step 3: 凳面顶部边 R5mm 圆角 ---")
    edges_to_fillet = ["top_front", "top_back", "top_left", "top_right"]
    for edge_name in edges_to_fillet:
        result = api_post("/api/feature/fillet", {
            "radius": 0.005,
            "edgeSelection": edge_name
        })
        assert_success(result, f"圆角 {edge_name}")

    # === 验证 ===
    print("\n--- 验证 ---")
    bbox = api_get("/api/body-bounding-box")["boundingBox"]
    print(f"  最终包围盒尺寸: {bbox['sizeX']*1000:.1f} × {bbox['sizeY']*1000:.1f} × {bbox['sizeZ']*1000:.1f} mm")
    assert_approx(bbox["sizeY"], 0.17, 0.002, "总高度 170mm")

    tree = api_get("/api/feature-tree")
    features = tree["features"]
    fillet_features = [f for f in features if "Fillet" in f.get("type", "") or "圆角" in f.get("name", "")]
    print(f"  圆角特征数量: {len(fillet_features)} (期望 4)")

    print("\n  🎉 测试 3 通过！带圆角的板凳建模成功！")


# ============================================================
# 主入口
# ============================================================

def main():
    # 检查服务可用性
    print("检查 SolidWorks HTTP API 服务...")
    try:
        result = api_get("/api/ping")
        if not result.get("success"):
            print("❌ 服务响应异常")
            sys.exit(1)
        print("✅ 服务正常运行\n")
    except requests.exceptions.ConnectionError:
        print("❌ 无法连接到 SolidWorks 服务 (http://localhost:5100)")
        print("   请先启动 Chapter1.ConnectToSw.exe")
        sys.exit(1)

    # 选择测试
    test_name = sys.argv[1] if len(sys.argv) > 1 else "all"

    if test_name == "flip":
        test_flip_direction()
    elif test_name == "stool":
        test_stool()
    elif test_name == "stool_fillet":
        test_stool_with_fillet()
    elif test_name == "all":
        test_flip_direction()
        test_stool()
        test_stool_with_fillet()
    else:
        print(f"未知测试: {test_name}")
        print("可用测试: flip, stool, stool_fillet, all")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("🎊 所有测试通过！")
    print("=" * 60)


if __name__ == "__main__":
    main()
