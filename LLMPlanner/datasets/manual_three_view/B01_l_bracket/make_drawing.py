"""
生成 B01 L 型支架的三视图 PNG（含尺寸标注）用于端到端验证。

零件规格（都用毫米）：
- 主视图：L 形，外接矩形 60 × 40mm，右上角切除 30 × 20mm 缺口
- 深度（Z 方向）：20mm（俯视图与侧视图上体现）
- 无孔无圆角，纯拉伸件

布局（第三角画法）：
- 主视图 front 在左上
- 俯视图 top   在左下
- 侧视图 right 在右上

用法：
    venv\\Scripts\\python.exe datasets\\manual_three_view\\B01_l_bracket\\make_drawing.py
"""
from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def load_font(size: int) -> ImageFont.ImageFont:
    for path in [
        "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
        "C:/Windows/Fonts/simsun.ttc",   # 宋体
        "C:/Windows/Fonts/arial.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def main() -> None:
    W, H = 1600, 1200
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)

    F_LABEL = load_font(20)
    F_DIM = load_font(18)
    F_TITLE = load_font(22)

    black = "black"
    # 每 mm 对应像素（图纸比例 6px/mm，能容纳 60mm 主视图约 360px + 边距）
    S = 6

    # ---- 主视图 front（左上），L 形 60×40，右上角切 30×20 ----
    fx, fy = 200, 200  # 左上角
    # L 形轮廓（顺时针）：左下→右下→右下的 30mm 高→缺口向左 30mm→上升 20mm→左侧 30mm→上升 20mm？重新推
    # 更精确：外接 60(宽)×40(高)，右上角切 30(宽)×20(高)
    # 顶点顺时针：左下(0,0) → 右下(60,0) → 右上前(60,20) → 缺口内角(30,20) → 缺口上(30,40) → 左上(0,40) → 回左下
    poly_front = [
        (fx + 0 * S, fy + 40 * S),  # 左下
        (fx + 60 * S, fy + 40 * S),  # 右下
        (fx + 60 * S, fy + 20 * S),  # 右上前（因 y 向下增，用 fy+20 表示"距底 20 距顶 20"）
        (fx + 30 * S, fy + 20 * S),  # 缺口内角
        (fx + 30 * S, fy + 0 * S),   # 缺口顶
        (fx + 0 * S, fy + 0 * S),    # 左上
    ]
    dr.line(poly_front + [poly_front[0]], fill=black, width=2)
    dr.text((fx, fy - 50), "主视图 (Front View)", fill=black, font=F_TITLE)

    # 尺寸标注
    # 总宽 60 mm（下方）
    dim_y = fy + 40 * S + 40
    dr.line([(fx, dim_y), (fx + 60 * S, dim_y)], fill=black, width=1)
    dr.line([(fx, dim_y - 6), (fx, dim_y + 6)], fill=black, width=1)
    dr.line([(fx + 60 * S, dim_y - 6), (fx + 60 * S, dim_y + 6)], fill=black, width=1)
    dr.text((fx + 30 * S - 20, dim_y + 8), "60 mm", fill=black, font=F_DIM)

    # 总高 40 mm（左侧）
    dim_x = fx - 50
    dr.line([(dim_x, fy), (dim_x, fy + 40 * S)], fill=black, width=1)
    dr.line([(dim_x - 6, fy), (dim_x + 6, fy)], fill=black, width=1)
    dr.line([(dim_x - 6, fy + 40 * S), (dim_x + 6, fy + 40 * S)], fill=black, width=1)
    dr.text((dim_x - 55, fy + 20 * S - 10), "40 mm", fill=black, font=F_DIM)

    # 缺口宽 30（上方）
    dim_y2 = fy - 40
    dr.line([(fx + 30 * S, dim_y2), (fx + 60 * S, dim_y2)], fill=black, width=1)
    dr.line([(fx + 30 * S, dim_y2 - 6), (fx + 30 * S, dim_y2 + 6)], fill=black, width=1)
    dr.line([(fx + 60 * S, dim_y2 - 6), (fx + 60 * S, dim_y2 + 6)], fill=black, width=1)
    dr.text((fx + 40 * S, dim_y2 - 22), "30 mm", fill=black, font=F_DIM)

    # 缺口高 20（右侧）
    dim_x2 = fx + 60 * S + 40
    dr.line([(dim_x2, fy), (dim_x2, fy + 20 * S)], fill=black, width=1)
    dr.line([(dim_x2 - 6, fy), (dim_x2 + 6, fy)], fill=black, width=1)
    dr.line([(dim_x2 - 6, fy + 20 * S), (dim_x2 + 6, fy + 20 * S)], fill=black, width=1)
    dr.text((dim_x2 + 8, fy + 10 * S - 10), "20 mm", fill=black, font=F_DIM)

    # ---- 俯视图 top（左下），60×20 矩形（宽 60，深 20） ----
    tx, ty = 200, 700
    dr.rectangle([tx, ty, tx + 60 * S, ty + 20 * S], outline=black, width=2)
    dr.text((tx, ty - 30), "俯视图 (Top View)", fill=black, font=F_TITLE)
    # 深度标注 20 mm（右侧）
    tdim_x = tx + 60 * S + 40
    dr.line([(tdim_x, ty), (tdim_x, ty + 20 * S)], fill=black, width=1)
    dr.line([(tdim_x - 6, ty), (tdim_x + 6, ty)], fill=black, width=1)
    dr.line([(tdim_x - 6, ty + 20 * S), (tdim_x + 6, ty + 20 * S)], fill=black, width=1)
    dr.text((tdim_x + 8, ty + 10 * S - 10), "20 mm", fill=black, font=F_DIM)

    # ---- 侧视图 right（右上），L 形 20 宽×40 高，右上缺 20×20 ----
    rx, ry = 900, 200
    # 侧视图对应从右往左看，形状是把主视图沿 Y 轴投影：也是 L 形
    # 20 宽 × 40 高，右上角切 20 × 20
    poly_right = [
        (rx + 0 * S, ry + 40 * S),
        (rx + 20 * S, ry + 40 * S),
        (rx + 20 * S, ry + 20 * S),
        (rx + 0 * S, ry + 20 * S),  # 不对，侧视图应该是纯矩形；L 型的凹陷在长边
    ]
    # 修正：侧视图应显示 Z(深度)=20 × Y(高)=40 的完整矩形（因为 L 型的缺口是在 X 方向去掉的）
    dr.rectangle([rx, ry, rx + 20 * S, ry + 40 * S], outline=black, width=2)
    dr.text((rx, ry - 50), "侧视图 (Right View)", fill=black, font=F_TITLE)
    # 深度标注 20 mm（下方）
    rdim_y = ry + 40 * S + 40
    dr.line([(rx, rdim_y), (rx + 20 * S, rdim_y)], fill=black, width=1)
    dr.line([(rx, rdim_y - 6), (rx, rdim_y + 6)], fill=black, width=1)
    dr.line([(rx + 20 * S, rdim_y - 6), (rx + 20 * S, rdim_y + 6)], fill=black, width=1)
    dr.text((rx + 10 * S - 20, rdim_y + 8), "20 mm", fill=black, font=F_DIM)

    # 顶部标题
    dr.text((W // 2 - 120, 40), "B01 L 型支架 三视图 (第三角画法)", fill=black, font=F_TITLE)

    out = Path(__file__).parent / "drawing.png"
    img.save(out)
    print(f"Saved: {out}")

    # Groundtruth
    import json
    gt = {
        "part_id": "B01",
        "name": "L bracket",
        "part_type": "l_bracket",
        "expected_bbox_mm": {"length": 60, "height": 40, "depth": 20},
        "expected_volume_mm3": 60 * 40 * 20 - 30 * 20 * 20,  # 48000 - 12000 = 36000
        "key_dimensions_mm": {
            "outer_length": 60,
            "outer_height": 40,
            "cutout_length": 30,
            "cutout_height": 20,
            "depth": 20,
        },
        "notes": "外接 60×40mm 矩形右上角切除 30×20mm 缺口形成 L 形，沿 Z 方向拉伸 20mm。",
    }
    (Path(__file__).parent / "groundtruth.json").write_text(
        json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Saved: {(Path(__file__).parent / 'groundtruth.json')}")


if __name__ == "__main__":
    main()
