"""LLM 端到端建模测试：外齿轮（简化版）。

用「圆盘 + 顶面一次画 N 个外围矩形凹槽 + 一次 cut_extrusion」的做法
（不依赖 circular_pattern API，因为其在本地 SW 环境上几何未完全生效）。

考验点：
- LLM 需正确计算 N 个齿槽的位置（sin/cos 圆周分布）
- 一次 sketch 画多个封闭矩形轮廓（多轮廓完全分离，SW 允许）
- 一次 cut_extrusion 同时切除 N 个齿槽

预期形状：外径 60mm 圆盘 + 16 个齿槽（沿圆周均布，每齿槽 3mm×4mm）+ 中心 ⌀12mm 孔
包围盒：约 60×8×60 mm。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from test.objects._common import run_object_test


PROMPT = (
    "请建模一个简化的圆柱齿轮：\n"
    "1. 齿轮主体：⌀60mm 圆盘，厚 8mm，用上视基准面画外圆 (cx=0,cy=0,radius=0.03)，"
    "然后 feature_extrusion {depth: 0.008, midPlane: true} 居中拉伸；\n"
    "2. **齿槽（12 个，沿圆周均布）**：在圆盘顶面（Y=+0.004m）上用 select_face_and_sketch 选顶面，"
    "然后**在同一草图**内画 12 个小矩形（用 sketch_slot 画,不能相交,必须完全在圆盘内）。\n"
    "   计算：第 k 个齿槽（k=0..11）的中心角 θ_k = 2π·k/12（弧度），"
    "**中心距原点 25mm（圆盘半径 30mm 内缩 5mm）**，"
    "中心坐标 (cx_k, cz_k) = (0.025·cos(θ_k), 0.025·sin(θ_k))。\n"
    "   ⚠️ **用 sketch_slot 而不是 sketch_rectangle**（sketch_rectangle 的 y1/y2 在实体面上会被误解为模型 Y）。\n"
    "   每个齿槽：sketch_slot {cx: cx_k, cy: 0.004, cz: cz_k, length: 0.003, width: 0.002}\n"
    "   ⚠️ **必须自己算好 12 个齿槽的 cx/cz**（Python 中 cos(0)=1,sin(0)=0,"
    "cos(π/6)≈0.866,sin(π/6)=0.5 等等,把 0.025 乘上去），一次给出全部 12 个 sketch_slot 步骤。\n"
    "   ⚠️ **不要偷懒用文字说\"依此类推\"，必须真的列出全部 12 个 sketch_slot 步骤**。\n"
    "3. 画完 12 个齿槽后，一次 **feature_cut_extrusion {depth: 0.008, "
    "endCondition: 'through_all'}** 贯穿切除，形成 12 个齿槽；\n"
    "4. **中心孔**：再用 select_face_and_sketch 选顶面（射线 rx=0, ry=0.01, rz=0, dy=-1），"
    "然后 sketch_circle {cx: 0, cy: 0.004, cz: 0, radius: 0.006} 画中心 ⌀12mm 圆，"
    "feature_cut_extrusion {depth: 0.008, endCondition: 'through_all'} 贯穿。\n"
    "所有尺寸使用米制（1mm = 0.001m）。"
)


if __name__ == "__main__":
    ok = run_object_test(
        title="齿轮 (Gear)",
        prompt=PROMPT,
        required_ops=["new_part", "feature_extrusion", "feature_cut_extrusion", "sketch_slot"],
        expected_bbox={"sizeX": 60.0, "sizeY": 8.0, "sizeZ": 60.0},
        bbox_tolerance_mm=4.0,
    )
    sys.exit(0 if ok else 1)
