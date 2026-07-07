"""
build_all.py — 一键（重）生成 manual_three_view 目录下所有样例三视图。

用法（从 LLMPlanner 目录运行）：
    venv\\Scripts\\python.exe -X utf8 datasets\\manual_three_view\\build_all.py

每个样例都由**同一个 ExtrudedPart 定义派生**三视图 + groundtruth，
保证视图之间 100% 一致，人工无法引入矛盾。

添加新样例：在下方 `SAMPLES` 列表里追加即可。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让脚本可以直接从 LLMPlanner 或 datasets/manual_three_view 目录运行
_THIS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_THIS_DIR))

from _toolkit import ExtrudedPart, Hole, build_part  # noqa: E402


# ============================================================
# 样例定义 —— 每个 ExtrudedPart 就是三视图的唯一数据源
# ============================================================


SAMPLES: list[tuple[str, str, ExtrudedPart]] = [
    # -------- B01 L 型支架 --------
    (
        "B01",
        "B01_l_bracket",
        ExtrudedPart(
            name="B01 L 型支架",
            part_type="l_bracket",
            profile_xy=[
                (0, 0),    # 左下
                (60, 0),   # 右下
                (60, 20),  # 右上（缺口下边）
                (30, 20),  # 缺口内角
                (30, 40),  # 缺口上边
                (0, 40),   # 左上
            ],
            depth_z=20,
            holes=[],
            notes="外接 60×40mm 矩形右上角切除 30×20mm 缺口形成 L 形，沿 Z 拉伸 20mm。",
        ),
    ),

    # -------- B02 带 2 孔平板 --------
    # 简单矩形板 + 2 个对称通孔，验证 Vision 能否识别孔位与孔径
    (
        "B02",
        "B02_plate_with_holes",
        ExtrudedPart(
            name="B02 带 2 孔平板",
            part_type="plate_with_holes",
            profile_xy=[
                (0, 0), (80, 0), (80, 40), (0, 40),   # 80×40 矩形
            ],
            depth_z=10,
            holes=[
                Hole(cx=20, cy=20, radius=5, label="左孔 ⌀10"),
                Hole(cx=60, cy=20, radius=5, label="右孔 ⌀10"),
            ],
            notes="80×40×10mm 平板，2 个 ⌀10mm 通孔，孔中心距左/右边缘各 20mm、距上下边缘 20mm。",
        ),
    ),

    # -------- B03 T 型支架 --------
    # 主视图为 T 形（顶部横杆 + 中间竖杆），验证 Vision 处理带双凹角的复杂轮廓
    (
        "B03",
        "B03_t_bracket",
        ExtrudedPart(
            name="B03 T 型支架",
            part_type="t_bracket",
            profile_xy=[
                # T 形轮廓（逆时针）：底部竖杆宽 20mm 位于水平方向中间，
                # 顶部横杆宽 60mm 高 15mm。总外接 60×45mm。
                (20, 0),   # 竖杆左下
                (40, 0),   # 竖杆右下
                (40, 30),  # 竖杆右上（横杆底右侧）
                (60, 30),  # 横杆右下
                (60, 45),  # 横杆右上
                (0, 45),   # 横杆左上
                (0, 30),   # 横杆左下
                (20, 30),  # 竖杆左上（横杆底左侧）
            ],
            depth_z=15,
            holes=[],
            notes="T 形轮廓：外接 60×45mm，顶部横杆 60×15mm、下方竖杆 20×30mm 居中，沿 Z 拉伸 15mm。",
        ),
    ),
]


def main() -> None:
    # 每个样例输出到 manual_three_view/<subdir>/
    base = _THIS_DIR

    print("=" * 60)
    print(f"批量生成三视图样例（共 {len(SAMPLES)} 个）")
    print("=" * 60)

    for part_id, subdir, part in SAMPLES:
        out_dir = base / subdir
        build_part(part, out_dir=out_dir, part_id=part_id)
        print()

    print("=" * 60)
    print("全部完成。图纸与 groundtruth 均从 ExtrudedPart 派生，保证一致。")
    print("=" * 60)


if __name__ == "__main__":
    main()
