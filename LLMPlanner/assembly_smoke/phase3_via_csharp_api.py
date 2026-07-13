"""Phase 3: 通过 C# HTTP API 完整跑装配体流程

这次不用 pywin32 直接控 COM, 而是走服务端 API。C# 强类型 COM 调用比 pywin32
late-binding 稳得多, 之前 SelectByID2/AddMate5 类型不匹配的坑在 C# 里没有。

流程:
  1. /api/assembly/new             - 建装配体 (打开空模板 + SaveAs)
  2. /api/assembly/add-component   x2 - 加入小/大齿轮
  3. /api/assembly/mate-coincident - 让两齿轮前视基准面重合 (同 Z 层)
  4. /api/assembly/mate-concentric x2 - 每个齿轮的圆柱面与装配体主平面上的 axis 同心
                                       (简化: 两齿轮的圆柱面之间放 Concentric ??? 不对)
     修正: 齿轮 mate 一般是:
       - Gear Mate (核心): 两齿轮的圆柱面直接 Gear Mate 定义传动比 + 联动
       - 装配体应有 3 个位置约束确保两齿轮都能各自绕 Z 轴转:
         a) 齿轮 1 圆柱面 与 装配体 Z 轴 (Right Plane 或 Assembly 自身的轴) Concentric
         b) 齿轮 2 圆柱面 与 装配体 Z 轴 Concentric  → 两个各自的旋转轴定位
         c) 齿轮 1 前视基准面 与 装配体 前视基准面 Coincident
         d) Gear Mate: 齿轮 1 圆柱面 ↔ 齿轮 2 圆柱面 (齿数比 20:10)
       但如果两个齿轮加进去的位置本身就在正确位置 (X=-35, X=+35, Z=0), 装配体
       Rigid 固定它们, 就不需要 Concentric 定位, 只加:
         - Gear Mate (齿数比) → 拖一个另一个联动
       但 AddComponent 后, 组件默认是"浮动"(不固定), 除非第一个组件. 拖动会散架.
       所以: 至少要 Concentric 各自的旋转轴 + Coincident Z 平面 才能约束齿轮到正确位置

  简化版 (本次冒烟目标):
    - 加齿轮到指定位置
    - 直接尝试 Gear Mate → 看是否能加成功 (需要圆柱面配对)
    - 拖动小齿轮看大齿轮是否联动

  齿轮圆柱面射线参数 (装配体坐标系, 圆盘 Z∈[0, 15mm]):
    - 小齿轮圆盘轴线在 (X=-35, Y=0), 直径 40mm → 从远处 X 射向轴
      起点 (-100, 0, 7.5), 方向 (+1, 0, 0)
    - 大齿轮圆盘轴线在 (X=+35, Y=0), 直径 80mm
      起点 (100, 0, 7.5), 方向 (-1, 0, 0)
"""
import os
import requests
import sys

BASE = "http://localhost:5100"
SMOKE_DIR = os.path.dirname(os.path.abspath(__file__))
SMALL_GEAR = os.path.join(SMOKE_DIR, "gear_small.SLDPRT")
BIG_GEAR = os.path.join(SMOKE_DIR, "gear_big.SLDPRT")
ASSEMBLY_PATH = os.path.join(SMOKE_DIR, "gear_pair_assembly_v2.SLDASM")


def call(path, body=None, timeout=90):
    r = requests.post(f"{BASE}{path}", json=body or {}, timeout=timeout)
    j = r.json()
    ok = "✅" if j.get("success") else "❌"
    print(f"{ok} POST {path} → {j.get('message', '')[:100]}")
    if not j.get("success"):
        print(f"    详情: {j}")
        return None
    return j


def main():
    print("=" * 60)
    print("Phase 3: 通过 C# HTTP API 完整装配体流程")
    print("=" * 60)

    # 前置检查
    assert os.path.exists(SMALL_GEAR), f"小齿轮不存在: {SMALL_GEAR}"
    assert os.path.exists(BIG_GEAR), f"大齿轮不存在: {BIG_GEAR}"

    # Step 1: 新建装配体
    print("\n--- Step 1: 新建装配体 ---")
    r = call("/api/assembly/new", {"savePath": ASSEMBLY_PATH})
    if r is None:
        print("❌ 新建装配体失败, 终止")
        sys.exit(1)

    # Step 2: 加入小齿轮
    print("\n--- Step 2: 加入小齿轮 @ X=-35mm ---")
    r = call("/api/assembly/add-component", {
        "partPath": SMALL_GEAR,
        "x": -0.035, "y": 0, "z": 0,
    })
    if r is None:
        sys.exit(1)
    small_name = r.get("componentName")
    print(f"    小齿轮组件名: {small_name}")

    # Step 3: 加入大齿轮
    print("\n--- Step 3: 加入大齿轮 @ X=+35mm ---")
    r = call("/api/assembly/add-component", {
        "partPath": BIG_GEAR,
        "x": 0.035, "y": 0, "z": 0,
    })
    if r is None:
        sys.exit(1)
    big_name = r.get("componentName")
    print(f"    大齿轮组件名: {big_name}")

    # Step 4: Coincident Mate - 让两齿轮的前视基准面重合 (对齐到 Z=0)
    print("\n--- Step 4: Coincident Mate 前视基准面 ---")
    r = call("/api/assembly/mate-coincident", {
        "component1": small_name, "plane1": "前视基准面",
        "component2": big_name,   "plane2": "前视基准面",
    })
    if r is None:
        print("⚠️ Coincident Mate 失败, 但继续尝试后续 Mate")

    # Step 5: Gear Mate - 齿数比 20:10 (大:小)
    print("\n--- Step 5: Gear Mate 齿数比 20:10 (大齿:小齿) ---")
    # 射线选圆柱面:
    #   - 小齿轮: 圆盘中心 (装配体) X=-35, 从 X=-100 射向 +X 方向命中圆柱面
    #   - 大齿轮: 圆盘中心 X=+35, 从 X=+100 射向 -X 方向命中圆柱面
    r = call("/api/assembly/mate-gear", {
        "component1": small_name,
        "rx1": -0.10, "ry1": 0, "rz1": 0.0075,
        "dx1": 1, "dy1": 0, "dz1": 0,
        "component2": big_name,
        "rx2": 0.10, "ry2": 0, "rz2": 0.0075,
        "dx2": -1, "dy2": 0, "dz2": 0,
        "gearRatioNumerator": 20,
        "gearRatioDenominator": 10,
        "reverse": False,
    }, timeout=120)
    if r is None:
        print("⚠️ Gear Mate 失败")

    # Step 6: 保存
    print("\n--- Step 6: 保存装配体 ---")
    call("/api/assembly/save")

    print("\n" + "=" * 60)
    print(f"✅ Phase 3 完成! 装配体: {ASSEMBLY_PATH}")
    print("    请在 SolidWorks 里手动:")
    print("    1. 打开 Motion 或 Move Component (Assembly 工具栏)")
    print("    2. 拖动小齿轮, 观察大齿轮是否按 2:1 反向联动旋转")
    print("=" * 60)


if __name__ == "__main__":
    main()
