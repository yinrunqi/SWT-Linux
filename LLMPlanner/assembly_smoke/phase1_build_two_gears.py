"""
装配体冒烟 Phase 1: 用现有 HTTP API 建两个独立齿轮 .sldprt 文件

这里完全不动 C# 代码, 只是通过现有的零件建模 API 建齿轮, 然后手动 SaveAs 保存.
后续 phase2 用 pywin32 打开这两个 .sldprt 建装配体.

小齿轮: ⌀40mm 分度圆 + 10 齿, 厚 15mm, 独立文件 gear_small.sldprt
大齿轮: ⌀80mm 分度圆 + 20 齿, 厚 15mm, 齿形错相 9°, 独立文件 gear_big_phased.sldprt

每个齿轮独立居中在原点 (X=0, Y=0), 便于装配时用 Concentric mate 对准.
"""
import math
import os

import requests

BASE = "http://localhost:5100"
SMOKE_DIR = os.path.abspath(os.path.dirname(__file__))

def call(path, body=None):
    r = requests.post(f"{BASE}{path}", json=body or {}, timeout=90)
    d = r.json()
    if not d.get("success"):
        raise RuntimeError(f"{path} failed: {d.get('message')}")
    return d


def build_gear(
    radius_m: float,
    tooth_count: int,
    tooth_h_m: float,
    tooth_w_m: float,
    thickness_m: float,
    phase_angle_deg: float = 0,
    tooth_root_overlap_m: float = 0.0005,
):
    """建一个独立居中在原点的齿轮零件, 步骤和 C3 v7 完全一致."""
    # 1. new_part → 前视基准面 → 分度圆 → 拉伸厚度
    call("/api/new-part")
    call("/api/select-plane", {"index": 0})
    call("/api/sketch/circle", {"cx": 0, "cy": 0, "radius": radius_m})
    call("/api/feature/extrusion", {"depth": thickness_m})

    # 2. 种子齿：先构造 +X 方向矩形，再按 phase_angle_deg 绕原点旋转。
    # 用四条线而不是 sketch/rectangle，避免依赖失效的装配体 Transform 来错相。
    call("/api/select-plane", {"index": 0})
    angle = math.radians(phase_angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    unrotated_corners = [
        (radius_m - tooth_root_overlap_m, -tooth_w_m / 2),
        (radius_m + tooth_h_m, -tooth_w_m / 2),
        (radius_m + tooth_h_m, tooth_w_m / 2),
        (radius_m - tooth_root_overlap_m, tooth_w_m / 2),
    ]
    corners = [
        (x * cos_a - y * sin_a, x * sin_a + y * cos_a)
        for x, y in unrotated_corners
    ]
    for start, end in zip(corners, corners[1:] + corners[:1]):
        call("/api/sketch/line", {
            "x1": start[0], "y1": start[1],
            "x2": end[0], "y2": end[1],
        })
    call("/api/feature/extrusion", {"depth": thickness_m})

    # 3. 圆周阵列出全部齿
    call("/api/feature/circular-pattern", {"count": tooth_count, "axis": "z"})


if __name__ == "__main__":
    # 先确认服务活着
    r = requests.get(f"{BASE}/api/ping", timeout=5).json()
    print(f"C# 服务: {r['message']}\n")

    # ===== 建小齿轮 (⌀40mm 10 齿) =====
    print("=" * 60)
    print("Step 1: 建小齿轮 (分度圆 ⌀40mm, 10 齿, 厚 15mm)")
    print("=" * 60)
    build_gear(radius_m=0.020, tooth_count=10, tooth_h_m=0.004, tooth_w_m=0.005, thickness_m=0.015)
    # 检查包围盒
    bb = requests.get(f"{BASE}/api/body-bounding-box", timeout=15).json()["boundingBox"]
    print(f"小齿轮包围盒: sizeX={bb['sizeX']*1000:.1f}mm, sizeY={bb['sizeY']*1000:.1f}mm, sizeZ={bb['sizeZ']*1000:.1f}mm")
    print(f"    (期望: 48×48×15mm, 齿顶圆 ⌀48)")

    small_path = os.path.join(SMOKE_DIR, "gear_small.sldprt")
    print(f"\n[⚠️ 手动步骤] 请在 SolidWorks 里 File → Save As → 保存为:")
    print(f"    {small_path}")
    print(f"    保存后按回车继续...")
    input()

    # ===== 建大齿轮 (⌀80mm 20 齿) =====
    print("\n" + "=" * 60)
    print("Step 2: 建大齿轮 (分度圆 ⌀80mm, 20 齿, 厚 15mm, 相位 +9°)")
    print("=" * 60)
    build_gear(
        radius_m=0.040,
        tooth_count=20,
        tooth_h_m=0.004,
        tooth_w_m=0.005,
        thickness_m=0.015,
        phase_angle_deg=9.0,
    )
    bb = requests.get(f"{BASE}/api/body-bounding-box", timeout=15).json()["boundingBox"]
    print(f"大齿轮包围盒: sizeX={bb['sizeX']*1000:.1f}mm, sizeY={bb['sizeY']*1000:.1f}mm, sizeZ={bb['sizeZ']*1000:.1f}mm")
    print(f"    (期望: 88×88×15mm, 齿顶圆 ⌀88)")

    big_path = os.path.join(SMOKE_DIR, "gear_big_phased.sldprt")
    print(f"\n[⚠️ 手动步骤] 请在 SolidWorks 里 File → Save As → 保存为:")
    print(f"    {big_path}")
    print(f"    保存后按回车继续...")
    input()

    # ===== 验证 =====
    print("\n" + "=" * 60)
    print("Phase 1 完成! 两个独立齿轮 .sldprt 已保存")
    print("=" * 60)
    print(f"  小齿轮: {small_path} — 存在: {os.path.exists(small_path)}")
    print(f"  大齿轮: {big_path}   — 存在: {os.path.exists(big_path)}")
    print(f"\n下一步: 运行 phase2_assembly_via_com.py 用 pywin32 建装配体并加齿轮联动配合")
