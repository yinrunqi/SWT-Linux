"""LLM 端到端建模测试：钥匙。

考验点：
- **新增 sketch_slot API**：钥匙圈孔用长圆孔轮廓（当前实现在本地 SW 上是矩形近似）
- 两次拉伸合并（钥匙圈头 + 钥匙杆），因圆和矩形不能画在同一草图里
- 顶面开孔坐标（cx=cz=0, cy=顶面Y坐标）

预期形状：
- 钥匙圈头：⌀20mm 圆盘，中心 10mm×5mm 长圆孔
- 钥匙杆：42mm×8mm 矩形梁，一端嵌入钥匙圈内 3mm
- 整体厚度 2mm，沿 Y 轴 midPlane 居中
- 总长约 X∈[-10, 49] = 59mm，宽 Z=20mm（=圆盘直径,杆比它窄），高 Y=2mm
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from test.objects._common import run_object_test


PROMPT = (
    "请建模一把小型钥匙，包含钥匙圈头（带圆孔）和钥匙杆两部分：\n"
    "1. 整体沿 Y 轴（厚度方向）以原点为中心居中，厚度 2mm；\n"
    "2. **钥匙圈头**：一个圆盘外径 20mm，圆心在原点。用上视基准面（select_plane {index:1}）画圆，"
    "然后 feature_extrusion {depth: 0.002, midPlane: true} 一次拉伸；\n"
    "3. **钥匙杆**：一个矩形梁，长 42mm（沿 +X 方向），宽 8mm（沿 Z 方向居中）。"
    "杆的末端**必须嵌入钥匙圈头内 3mm 以确保合并**成单一实体——"
    "即矩形沿 X 从 X=7mm（钥匙圈半径 10mm 减 3mm 嵌入）延伸到 X=49mm，"
    "沿 Z 从 Z=-4mm 到 Z=+4mm。**用第二次 select_plane {index:1}（上视面）"
    "+ sketch_rectangle {x1:0.007, y1:-0.004, x2:0.049, y2:0.004} + feature_extrusion "
    "{depth: 0.002, midPlane: true}**（不能和圆盘画在同一草图里）；\n"
    "4. **钥匙圈孔**：在钥匙圈头顶面用 select_face_and_sketch 选顶面，"
    "然后**用 sketch_slot 画一个长圆孔**：中心 cx=0、cy=顶面 Y 坐标(+0.001m)、cz=0，"
    "长 10mm（沿 X 方向）、宽 5mm，然后 feature_cut_extrusion 贯穿切除。\n"
    "所有尺寸使用米制（1mm = 0.001m）。"
)


if __name__ == "__main__":
    ok = run_object_test(
        title="钥匙 (Key)",
        prompt=PROMPT,
        required_ops=["new_part", "feature_extrusion", "sketch_slot", "feature_cut_extrusion"],
        expected_bbox={"sizeX": 59.0, "sizeY": 2.0, "sizeZ": 20.0},
        bbox_tolerance_mm=4.0,
        feature_checks=[("Boss-Extrude", 1), ("Cut-Extrude", 1)],
    )
    sys.exit(0 if ok else 1)
