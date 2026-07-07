"""LLM 端到端建模测试：钥匙（带齿槽）。

考验点：
- **新增 sketch_slot API**：钥匙圈孔用长圆孔轮廓 + 5 个钥匙齿槽复用同一 API
- 两次拉伸合并（钥匙圈头 + 钥匙杆），因圆和矩形不能画在同一草图里
- 顶面开孔坐标（cx=cz=顶面模型坐标, cy=顶面 Y 坐标）
- 在**钥匙杆顶面**用一草图内画 5 个独立齿槽轮廓 + 一次贯穿切除，形成锯齿边

预期形状：
- 钥匙圈头：⌀20mm 圆盘，中心 10mm×5mm 长圆孔
- 钥匙杆：42mm×8mm 矩形梁，一端嵌入钥匙圈内 3mm
- 钥匙齿：5 个 3mm×2mm 齿槽，沿 X 方向均布在钥匙杆的一条长边（Z=+4mm 侧），紧贴杆边
- 整体厚度 2mm，沿 Y 轴 midPlane 居中
- 总长约 X∈[-10, 49] = 59mm，宽 Z=20mm（=圆盘直径,杆比它窄），高 Y=2mm
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from test.objects._common import run_object_test


PROMPT = (
    "请建模一把小型钥匙，包含**钥匙圈头（带圆孔）、钥匙杆、钥匙齿**三部分。"
    "所有尺寸使用米制（1mm = 0.001m），整体沿 Y 轴（厚度方向）以原点为中心居中，厚度 2mm。\n"
    "\n"
    "1. **钥匙圈头**：一个圆盘外径 20mm，圆心在原点。用上视基准面（select_plane {index:1}）画圆，"
    "然后 feature_extrusion {depth: 0.002, midPlane: true} 一次拉伸；\n"
    "\n"
    "2. **钥匙杆**：一个矩形梁，长 42mm（沿 +X 方向），宽 8mm（沿 Z 方向居中）。"
    "杆的末端**必须嵌入钥匙圈头内 3mm 以确保合并**成单一实体——"
    "即矩形沿 X 从 X=7mm（钥匙圈半径 10mm 减 3mm 嵌入）延伸到 X=49mm，"
    "沿 Z 从 Z=-4mm 到 Z=+4mm。**用第二次 select_plane {index:1}（上视面）"
    "+ sketch_rectangle {x1:0.007, y1:-0.004, x2:0.049, y2:0.004} + feature_extrusion "
    "{depth: 0.002, midPlane: true}**（不能和圆盘画在同一草图里）；\n"
    "\n"
    "3. **钥匙圈孔**：在钥匙圈头顶面用 select_face_and_sketch 选顶面"
    "（射线 rx=0, ry=0.01, rz=0, dx=0, dy=-1, dz=0——从上方射向 -Y 命中顶面），"
    "然后**用 sketch_slot 画一个长圆孔**：cx=0, cy=0.001（顶面 Y 坐标）, cz=0，"
    "长 10mm（沿 X）、宽 5mm，然后 feature_cut_extrusion "
    "{depth: 0.002, endCondition: 'through_all'} 贯穿切除。\n"
    "\n"
    "4. **钥匙齿（关键！这是钥匙的标志性特征）**：在钥匙杆顶面开出 5 个锯齿状凹槽。"
    "**再一次** select_face_and_sketch 选钥匙杆的顶面"
    "（射线 rx=0.03, ry=0.01, rz=0, dx=0, dy=-1, dz=0——起点在杆顶面上方一点，射向下方命中杆顶面）。"
    "然后**在同一草图**内画 5 个 sketch_slot 齿槽（各自独立、不相交、必须完全在杆内）——"
    "每个齿槽 length=3mm（沿 X）、width=2mm（沿 Z），"
    "**齿槽的 Z 中心固定 cz=+0.003（贴近杆的一条长边 Z=+4mm，齿槽 Z 范围 [+0.002, +0.004] 正好和杆边缘齐平）**，"
    "5 个齿槽沿 +X 方向均布，中心 X 坐标依次为 0.017, 0.023, 0.029, 0.035, 0.041（每两个中心间距 6mm）。"
    "**必须一次列出全部 5 个 sketch_slot 步骤，禁止省略。每个 sketch_slot 参数完整为**："
    "sketch_slot {cx: <上面的X>, cy: 0.001, cz: 0.003, length: 0.003, width: 0.002}。"
    "画完 5 个齿槽后一次 feature_cut_extrusion {depth: 0.002, endCondition: 'through_all'} 贯穿切除，"
    "形成锯齿边。\n"
    "\n"
    "⚠️ 重要规则：\n"
    "- sketch_slot 的 length 必须严格大于 width（否则会报错），本例齿槽 3mm > 2mm 满足。\n"
    "- 在实体面上画 sketch_slot 时，cx/cy/cz 都是**模型 3D 坐标**（不是草图 2D）。cy 必须等于所在面的 Y 坐标。\n"
    "- 5 个齿槽必须画在同一草图内、一次切除；不要每个齿都单独 select_face_and_sketch + cut。"
)


if __name__ == "__main__":
    ok = run_object_test(
        title="钥匙 (Key with teeth)",
        prompt=PROMPT,
        required_ops=["new_part", "feature_extrusion", "sketch_slot", "feature_cut_extrusion"],
        expected_bbox={"sizeX": 59.0, "sizeY": 2.0, "sizeZ": 20.0},
        bbox_tolerance_mm=4.0,
        # 主体 2 次 Boss-Extrude（圆盘+杆），2 次 Cut-Extrude（圈孔+齿槽）
        feature_checks=[("Boss-Extrude", 2), ("Cut-Extrude", 2)],
    )
    sys.exit(0 if ok else 1)
