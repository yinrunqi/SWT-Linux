"""
three_view_gen.py — 三视图合成生成器（单一数据源 → 三视图正交投影）

设计目标：**三视图之间 100% 一致，不可能矛盾**。
所有几何、尺寸、体积、包围盒、groundtruth 全部从同一个 `ExtrudedPart` 定义派生。
改任何一个参数，三张视图 + groundtruth 一起更新，人工层面无法引入不一致。

坐标系约定（右手系，与 SolidWorks 一致）：
    X — 向右（宽度方向，主视图水平）
    Y — 向上（高度方向，主视图垂直）
    Z — 向外（深度方向，垂直纸面）

零件定义域：**2.5D 拉伸件**
    profile_xy 是主视图（XY 平面）上的闭合轮廓；
    沿 Z 拉伸 depth_z 得到 3D 实体；
    holes 是**沿 Z 方向的贯穿圆孔**（位于 XY 平面上的圆心 + 半径）。

三视图约定（第三角画法，与 three_view.md skill 一致）：
    主视图 front (XY plane, 沿 -Z 方向观察)：显示 profile_xy 的全部轮廓 + 孔
    俯视图 top   (XZ plane, 沿 -Y 方向观察)：显示零件在 XZ 平面上的投影（宽 × 深的矩形，孔投影为矩形内 2 条竖线）
    侧视图 right (YZ plane, 沿 -X 方向观察)：显示零件在 YZ 平面上的投影（深 × 高的矩形，L 形缺口在此不显示）

用法：
    from _toolkit import ExtrudedPart, Hole, build_part

    part = ExtrudedPart(
        profile_xy=[(0,0),(60,0),(60,20),(30,20),(30,40),(0,40)],
        depth_z=20,
        holes=[],
        name="B01 L 型支架",
    )
    build_part(part, out_dir=Path("B01_l_bracket"), part_id="B01")
    # 自动生成 drawing.png + groundtruth.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont


# ============================================================
# 数据结构：单一数据源
# ============================================================


@dataclass
class Hole:
    """沿 Z 方向的贯穿圆孔（圆心位于 XY 平面）。"""
    cx: float          # 圆心 X 坐标（mm，profile 局部坐标系）
    cy: float          # 圆心 Y 坐标（mm）
    radius: float      # 半径（mm）
    label: str = ""    # 可选标注（如 "M5 通孔"）


@dataclass
class ExtrudedPart:
    """
    2.5D 拉伸零件 —— 三视图的单一数据源。

    - profile_xy 是主视图 XY 平面上的闭合多边形顶点（**逆时针 CCW 为外轮廓**，mm）
    - depth_z 是沿 Z 方向的拉伸深度（mm，> 0）
    - holes 是沿 Z 方向的贯穿圆孔列表（可为空）
    """
    profile_xy: list[tuple[float, float]]
    depth_z: float
    holes: list[Hole] = field(default_factory=list)
    name: str = ""
    part_type: str = "extrude_prism"   # l_bracket / plate_with_holes / t_bracket / ...
    notes: str = ""

    def __post_init__(self) -> None:
        if len(self.profile_xy) < 3:
            raise ValueError("profile_xy 至少 3 个顶点")
        if self.depth_z <= 0:
            raise ValueError("depth_z 必须 > 0")
        # 检查孔全部落在 profile 包围盒内（简单粗筛，不做严格 point-in-polygon）
        bb = self.bbox_xy()
        for h in self.holes:
            if not (bb[0] < h.cx - h.radius and h.cx + h.radius < bb[2]
                    and bb[1] < h.cy - h.radius and h.cy + h.radius < bb[3]):
                raise ValueError(
                    f"孔 (cx={h.cx}, cy={h.cy}, r={h.radius}) 超出 profile 包围盒 {bb}"
                )

    # -------- 派生量（三视图和 groundtruth 都从这些方法取值） --------

    def bbox_xy(self) -> tuple[float, float, float, float]:
        """profile 在 XY 平面的包围盒 (xmin, ymin, xmax, ymax)。"""
        xs = [p[0] for p in self.profile_xy]
        ys = [p[1] for p in self.profile_xy]
        return min(xs), min(ys), max(xs), max(ys)

    def bbox_3d_mm(self) -> dict:
        """3D 包围盒尺寸（mm），用于 groundtruth。"""
        xmin, ymin, xmax, ymax = self.bbox_xy()
        return {
            "length": round(xmax - xmin, 3),  # X 方向
            "height": round(ymax - ymin, 3),  # Y 方向
            "depth": round(self.depth_z, 3),  # Z 方向
        }

    def profile_area_mm2(self) -> float:
        """profile 面积（mm²），扣除孔面积。用鞋带公式。"""
        pts = self.profile_xy
        n = len(pts)
        s = 0.0
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            s += x1 * y2 - x2 * y1
        gross = abs(s) / 2.0
        holes_area = sum(3.141592653589793 * h.radius ** 2 for h in self.holes)
        return gross - holes_area

    def volume_mm3(self) -> float:
        """零件体积 = profile 净面积 × 拉伸深度。"""
        return round(self.profile_area_mm2() * self.depth_z, 3)

    def key_dimensions(self) -> dict:
        """从几何自动抽取标注尺寸，用于 groundtruth 和后续 IR 对比。"""
        bb = self.bbox_3d_mm()
        dims: dict = {
            "outer_length": bb["length"],
            "outer_height": bb["height"],
            "depth": bb["depth"],
        }
        for i, h in enumerate(self.holes, 1):
            dims[f"hole{i}_diameter"] = round(h.radius * 2, 3)
            dims[f"hole{i}_center_x"] = round(h.cx, 3)
            dims[f"hole{i}_center_y"] = round(h.cy, 3)
        return dims


# ============================================================
# 视图投影：XY 是唯一数据源，其余两视图从此派生
# ============================================================


def _project_top_view(part: ExtrudedPart) -> tuple[float, float]:
    """俯视图（XZ 平面）尺寸：宽 × 深 = length × depth_z。"""
    bb = part.bbox_3d_mm()
    return bb["length"], bb["depth"]


def _project_right_view(part: ExtrudedPart) -> tuple[float, float]:
    """侧视图（YZ 平面）尺寸：深 × 高 = depth_z × height。"""
    bb = part.bbox_3d_mm()
    return bb["depth"], bb["height"]


# ============================================================
# 渲染：Pillow
# ============================================================


def _load_font(size: int) -> ImageFont.ImageFont:
    for path in [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_dimension_h(
    dr: ImageDraw.ImageDraw,
    x1: float, x2: float, y: float,
    text: str,
    font: ImageFont.ImageFont,
    color: str = "black",
) -> None:
    """水平尺寸标注（下方或上方，一字线 + 两端箭头端刻 + 数字）。"""
    dr.line([(x1, y), (x2, y)], fill=color, width=1)
    dr.line([(x1, y - 6), (x1, y + 6)], fill=color, width=1)
    dr.line([(x2, y - 6), (x2, y + 6)], fill=color, width=1)
    mid = (x1 + x2) / 2
    bbox = dr.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    dr.text((mid - tw / 2, y + 4), text, fill=color, font=font)


def _draw_dimension_v(
    dr: ImageDraw.ImageDraw,
    x: float, y1: float, y2: float,
    text: str,
    font: ImageFont.ImageFont,
    color: str = "black",
    text_on_right: bool = False,
) -> None:
    """竖直尺寸标注。"""
    dr.line([(x, y1), (x, y2)], fill=color, width=1)
    dr.line([(x - 6, y1), (x + 6, y1)], fill=color, width=1)
    dr.line([(x - 6, y2), (x + 6, y2)], fill=color, width=1)
    mid = (y1 + y2) / 2
    bbox = dr.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = x + 8 if text_on_right else x - tw - 8
    dr.text((tx, mid - th / 2), text, fill=color, font=font)


def _draw_polygon(
    dr: ImageDraw.ImageDraw,
    verts_px: list[tuple[float, float]],
    color: str = "black",
    width: int = 2,
) -> None:
    """画闭合多边形轮廓（不填充）。"""
    for i in range(len(verts_px)):
        a = verts_px[i]
        b = verts_px[(i + 1) % len(verts_px)]
        dr.line([a, b], fill=color, width=width)


def _draw_circle(
    dr: ImageDraw.ImageDraw,
    cx: float, cy: float, r: float,
    color: str = "black",
    width: int = 2,
) -> None:
    dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=width)


def _draw_center_marker(
    dr: ImageDraw.ImageDraw,
    cx: float, cy: float, size: float = 8,
    color: str = "black",
) -> None:
    """画孔的十字中心线标记。"""
    dr.line([(cx - size, cy), (cx + size, cy)], fill=color, width=1)
    dr.line([(cx, cy - size), (cx, cy + size)], fill=color, width=1)


# ============================================================
# 主渲染函数
# ============================================================


def render_three_view_png(
    part: ExtrudedPart,
    out_path: Path,
    scale_px_per_mm: int = 6,
    canvas_size: tuple[int, int] = (1600, 1200),
) -> None:
    """
    把 ExtrudedPart 渲染为标准三视图 PNG（第三角画法）。

    布局：
        ┌────────────────────┬────────────────────┐
        │  主视图 front       │  侧视图 right       │
        │  (XY plane)         │  (YZ plane)         │
        │  显示 profile       │  显示 深×高 矩形     │
        ├────────────────────┼────────────────────┤
        │  俯视图 top         │                    │
        │  (XZ plane)         │                    │
        │  显示 宽×深 矩形     │                    │
        └────────────────────┴────────────────────┘

    所有视图使用同一 `scale_px_per_mm` 比例，孔位置从 profile_xy 坐标系精确投影。
    """
    W, H = canvas_size
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)

    F_TITLE = _load_font(22)
    F_LABEL = _load_font(20)
    F_DIM = _load_font(18)

    S = scale_px_per_mm
    bb = part.bbox_3d_mm()
    L, HT, D = bb["length"], bb["height"], bb["depth"]

    # ---- 布局：4 个象限的中心位置 ----
    #    前上、右上、前下（俯视）；右下留空
    #    每个视图分配 700×450 的显示区
    front_origin = (200, 250)   # 主视图左上角像素
    top_origin = (200, 700)     # 俯视图左上角像素
    right_origin = (900, 250)   # 侧视图左上角像素

    # profile_xy 到主视图的映射：**注意 Y 轴翻转**（图纸 Y 向下）
    xmin, ymin, xmax, ymax = part.bbox_xy()

    def to_px_front(x: float, y: float) -> tuple[float, float]:
        """profile 局部 (x, y) → 主视图像素 (px, py)，Y 轴翻转。"""
        px = front_origin[0] + (x - xmin) * S
        py = front_origin[1] + (ymax - y) * S
        return px, py

    # ============ 主视图 front ============
    dr.text((front_origin[0], front_origin[1] - 60), "主视图 Front", fill="black", font=F_TITLE)
    verts_front = [to_px_front(x, y) for x, y in part.profile_xy]
    _draw_polygon(dr, verts_front, color="black", width=2)

    # 画孔（真圆 + 十字中心）
    for h in part.holes:
        hx, hy = to_px_front(h.cx, h.cy)
        _draw_circle(dr, hx, hy, h.radius * S, color="black", width=2)
        _draw_center_marker(dr, hx, hy, size=h.radius * S + 6)

    # 主视图标注：总宽（下方）、总高（左侧）
    front_left = front_origin[0]
    front_right = front_origin[0] + L * S
    front_top = front_origin[1]
    front_bottom = front_origin[1] + HT * S
    _draw_dimension_h(dr, front_left, front_right, front_bottom + 40, f"{int(L)} mm", F_DIM)
    _draw_dimension_v(dr, front_left - 45, front_top, front_bottom, f"{int(HT)} mm", F_DIM, text_on_right=False)

    # ============ 俯视图 top ============
    dr.text((top_origin[0], top_origin[1] - 30), "俯视图 Top", fill="black", font=F_TITLE)
    top_w = L * S
    top_d = D * S
    dr.rectangle(
        [top_origin[0], top_origin[1], top_origin[0] + top_w, top_origin[1] + top_d],
        outline="black", width=2,
    )
    # 孔在俯视图中投影为 2 条虚线段（沿 Z 方向）；简化：画短线段代表
    for h in part.holes:
        # 孔在俯视图中的 X 位置来自 profile.cx
        hx = top_origin[0] + (h.cx - xmin) * S
        # 用虚线段表示孔在深度方向贯穿
        _draw_dashed_line(dr, hx - h.radius * S, top_origin[1], hx - h.radius * S, top_origin[1] + top_d)
        _draw_dashed_line(dr, hx + h.radius * S, top_origin[1], hx + h.radius * S, top_origin[1] + top_d)

    # 俯视图标注：宽（下方）、深（右侧）
    _draw_dimension_h(
        dr, top_origin[0], top_origin[0] + top_w, top_origin[1] + top_d + 40,
        f"{int(L)} mm", F_DIM,
    )
    _draw_dimension_v(
        dr, top_origin[0] + top_w + 45, top_origin[1], top_origin[1] + top_d,
        f"{int(D)} mm", F_DIM, text_on_right=True,
    )

    # ============ 侧视图 right ============
    dr.text((right_origin[0], right_origin[1] - 60), "侧视图 Right", fill="black", font=F_TITLE)
    right_w = D * S
    right_h = HT * S
    dr.rectangle(
        [right_origin[0], right_origin[1], right_origin[0] + right_w, right_origin[1] + right_h],
        outline="black", width=2,
    )
    # 孔在侧视图投影：孔的 Y 位置对应 profile.cy
    for h in part.holes:
        hy = right_origin[1] + (ymax - h.cy) * S
        _draw_dashed_line(dr, right_origin[0], hy - h.radius * S, right_origin[0] + right_w, hy - h.radius * S)
        _draw_dashed_line(dr, right_origin[0], hy + h.radius * S, right_origin[0] + right_w, hy + h.radius * S)

    # 侧视图标注：深（下方）、高（右侧）
    _draw_dimension_h(
        dr, right_origin[0], right_origin[0] + right_w, right_origin[1] + right_h + 40,
        f"{int(D)} mm", F_DIM,
    )
    _draw_dimension_v(
        dr, right_origin[0] + right_w + 45, right_origin[1], right_origin[1] + right_h,
        f"{int(HT)} mm", F_DIM, text_on_right=True,
    )

    # ============ 关键局部尺寸（L 型/T 型缺口等）============
    # 用户传的 profile 里已经把每个顶点坐标写死了。这里自动检测哪些"内角"是缺口的角，
    # 并标注该缺口相对包围盒的尺寸。
    _annotate_notches(dr, part, to_px_front, F_DIM)

    # ============ 附加标注：孔径 ============
    for h in part.holes:
        hx, hy = to_px_front(h.cx, h.cy)
        label = f"⌀{int(h.radius * 2)} mm"
        # 从孔中心引一条 45° 引线到右上
        lx = hx + h.radius * S + 20
        ly = hy - h.radius * S - 20
        dr.line([(hx, hy), (lx, ly)], fill="black", width=1)
        bbox = dr.textbbox((0, 0), label, font=F_DIM)
        tw = bbox[2] - bbox[0]
        dr.text((lx + 2, ly - 22), label, fill="black", font=F_DIM)

    # ============ 顶部标题 ============
    title = f"{part.name} · 三视图 (第三角画法)"
    dr.text((W // 2 - 150, 40), title, fill="black", font=F_TITLE)

    # ============ 底部：投影法说明（不暴露任何 groundtruth 数字，防作弊）============
    footer = "投影法：第三角画法（主视图=Front，俯视图=Top，侧视图=Right）  单位：mm"
    dr.text((200, H - 60), footer, fill="#909090", font=F_LABEL)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


def _draw_dashed_line(
    dr: ImageDraw.ImageDraw,
    x1: float, y1: float, x2: float, y2: float,
    color: str = "black",
    dash: int = 6, gap: int = 4,
) -> None:
    """画虚线（用于俯视图/侧视图中孔的投影）。"""
    import math
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    t = 0.0
    while t < length:
        a = (x1 + ux * t, y1 + uy * t)
        t_end = min(t + dash, length)
        b = (x1 + ux * t_end, y1 + uy * t_end)
        dr.line([a, b], fill=color, width=1)
        t = t_end + gap


def _annotate_notches(
    dr: ImageDraw.ImageDraw,
    part: ExtrudedPart,
    to_px_front,
    font: ImageFont.ImageFont,
) -> None:
    """
    自动识别 profile 上的"缺口"（凹角），并标注缺口的宽×高。

    规则：对每三个连续顶点 (a, b, c)，若外积 (b-a)×(c-b) 表示右转（在 CCW polygon 中为凹角），
    则 b 是一个凹角。取该凹角对应的两条边的长度作为缺口尺寸。
    """
    pts = part.profile_xy
    n = len(pts)

    # 找出所有凹角
    concave_indices: list[int] = []
    for i in range(n):
        a = pts[(i - 1) % n]
        b = pts[i]
        c = pts[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if cross < -0.01:
            concave_indices.append(i)

    # 每个凹角对应一个缺口尺寸。**关键去重规则**：
    # 一个矩形缺口在 CCW polygon 里会产生 2 个连续的凹角（一进一出），二者共享缺口宽度和高度，
    # 应只标注 1 次。用"已标注的凹角对"集合来跟踪。
    labeled: set[int] = set()
    for i in concave_indices:
        if i in labeled:
            continue
        b = pts[i]
        # 检查 i+1 是否也是凹角（矩形缺口的另一侧）
        next_i = (i + 1) % n
        if next_i in concave_indices:
            # 一对凹角对应一个矩形缺口，取两点距离为一维、垂直边为另一维
            b2 = pts[next_i]
            dx = abs(b2[0] - b[0])
            dy = abs(b2[1] - b[1])
            # b 和 b2 之间的边是缺口的"底边"：dx 或 dy 决定方向
            # 缺口宽 = max(dx, dy)（内边长度）；缺口高从 b 到相邻顶点的另一条边取
            a = pts[(i - 1) % n]
            edge_before = (abs(b[0] - a[0]), abs(b[1] - a[1]))
            c2 = pts[(next_i + 1) % n]
            edge_after = (abs(c2[0] - b2[0]), abs(c2[1] - b2[1]))
            perp = max(edge_before[0], edge_before[1], edge_after[0], edge_after[1])
            inner = max(dx, dy)
            if inner > 0.01 and perp > 0.01:
                bx, by = to_px_front(*b)
                label = f"缺口 {int(inner)}×{int(perp)} mm"
                dr.text((bx + 12, by + 4), label, fill="#0066cc", font=font)
                labeled.add(i)
                labeled.add(next_i)
        else:
            # 孤立凹角（L 型的情况）：a→b 和 b→c 两边就是缺口的两边
            a = pts[(i - 1) % n]
            c = pts[(i + 1) % n]
            edge_ab = (abs(b[0] - a[0]), abs(b[1] - a[1]))
            edge_bc = (abs(c[0] - b[0]), abs(c[1] - b[1]))
            notch_w = edge_ab[0] if edge_ab[0] > 0.01 else edge_bc[0]
            notch_h = edge_ab[1] if edge_ab[1] > 0.01 else edge_bc[1]
            if notch_w > 0.01 and notch_h > 0.01:
                bx, by = to_px_front(*b)
                label = f"缺口 {int(notch_w)}×{int(notch_h)} mm"
                dr.text((bx + 12, by + 4), label, fill="#0066cc", font=font)
                labeled.add(i)


# ============================================================
# groundtruth 导出
# ============================================================


def save_groundtruth(part: ExtrudedPart, out_path: Path, part_id: str) -> None:
    """把 ExtrudedPart 的派生量存为 groundtruth.json（用于测试自动校验）。"""
    gt = {
        "part_id": part_id,
        "name": part.name,
        "part_type": part.part_type,
        "profile_xy": part.profile_xy,
        "depth_z": part.depth_z,
        "holes": [
            {"cx": h.cx, "cy": h.cy, "radius": h.radius, "label": h.label}
            for h in part.holes
        ],
        "expected_bbox_mm": part.bbox_3d_mm(),
        "expected_volume_mm3": round(part.volume_mm3(), 3),
        "key_dimensions_mm": part.key_dimensions(),
        "notes": part.notes,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")


# ============================================================
# 一键 build：渲染 PNG + 导出 groundtruth
# ============================================================


def build_part(
    part: ExtrudedPart,
    out_dir: Path,
    part_id: str,
    scale_px_per_mm: int = 6,
) -> None:
    """一次生成 {part_id} 目录下的 drawing.png + groundtruth.json。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    render_three_view_png(part, out_dir / "drawing.png", scale_px_per_mm=scale_px_per_mm)
    save_groundtruth(part, out_dir / "groundtruth.json", part_id=part_id)
    print(f"[OK] {part_id} — {part.name}")
    print(f"     bbox: {part.bbox_3d_mm()} mm")
    print(f"     volume: {part.volume_mm3()} mm³")
    print(f"     files: {out_dir / 'drawing.png'}, {out_dir / 'groundtruth.json'}")
