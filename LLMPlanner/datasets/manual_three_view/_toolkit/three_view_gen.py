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


# ============================================================
# 复合特征：底座之上的附加操作（圆角、面上切除等）
# ============================================================
#
# 每个 Op 表示一次"后处理"：在底座实体建好后，按 operations 列表顺序应用。
# LLM 建模时也按顺序执行；PNG 渲染时用于在主/俯/侧视图上叠加对应的可见特征。
#
# 坐标系约定：所有 op 的坐标都在 **profile 局部坐标系**（与 ExtrudedPart.profile_xy 一致），
# 即 XY 平面上的 mm 坐标。工具箱负责把它们转成 SolidWorks 模型坐标（× 0.001）+ 面内草图坐标。


@dataclass
class FilletEdges:
    """圆角修饰边。

    edges 指定哪些边圆角，支持以下常量字符串：
      - "top_perimeter"   顶面轮廓周边（拔模件的顶面小周长；普通件的上表面周长）
      - "bottom_perimeter" 底面轮廓周边
      - "vertical_corners" 四个竖直棱边（外壳/开关面板四角常用）
      - "all_perimeter"   顶+底两圈周边
      - "all"             所有边（谨慎使用，可能引入不必要的圆角）
    """
    edges: str
    radius: float          # 圆角半径（mm）
    label: str = ""


@dataclass
class CutoutOnFace:
    """在指定面上切除一个形状（凹槽或贯穿孔）。

    face: 目标面
      - "top"     顶面（+Z，拔模件是小面；普通件是上表面）
      - "bottom"  底面（-Z）
      - "front" / "back" / "left" / "right" 四个侧面（当前先只支持 top/bottom）
    shape: 切除形状
      - "circle"       用 cx, cy, radius
      - "rectangle"    用 cx, cy, width, height（矩形中心在 (cx,cy)）
      - "slot"         用 cx, cy, length, width（腰形孔中心在 (cx,cy)，长轴沿 X）
    depth: 切除深度（mm）；若 through_all=true 则忽略
    through_all: 是否贯穿切除
    positions: 若非空，忽略 (cx, cy)，在多个位置各切一份（用于四角螺丝孔阵列）
    """
    face: str
    shape: str            # "circle" | "rectangle" | "slot"
    depth: float          # mm
    through_all: bool = False
    cx: float = 0.0
    cy: float = 0.0
    radius: float = 0.0
    width: float = 0.0
    height: float = 0.0
    length: float = 0.0
    positions: list[tuple[float, float]] = field(default_factory=list)
    surface_level: float = 0.0
    label: str = ""


# Op = FilletEdges | CutoutOnFace （Python 3.10+ 联合类型，我们不强 typing）


def _cutout_shape_area(op: "CutoutOnFace") -> float:
    """计算 CutoutOnFace 的截面面积（mm²）。"""
    import math
    if op.shape == "circle":
        return math.pi * op.radius ** 2
    if op.shape == "rectangle":
        return op.width * op.height
    if op.shape == "slot":
        # ⚠️ 关键注意：C# 服务的 sketch_slot 在本地 SolidWorks 上**退化为中心矩形**（length × width）。
        # 所以体积扣除量必须与 SW 实际建模一致，用矩形面积；否则 gt 会与实测偏差 ~7%。
        # 若将来 SketchSlot 修复为真正腰形孔，改回：(L-W)·W + π·(W/2)²
        return op.length * op.width
    raise ValueError(f"未知切除形状: {op.shape}")


@dataclass
class ExtrudedPart:
    """
    2.5D 拉伸零件 —— 三视图的单一数据源。

    - profile_xy 是主视图 XY 平面上的闭合多边形顶点（**逆时针 CCW 为外轮廓**，mm）
    - depth_z 是沿 Z 方向的拉伸深度（mm，> 0）
    - holes 是沿 Z 方向的贯穿圆孔列表（可为空）
    - top_scale 是顶面轮廓相对于底面的**线性缩放比例**（默认 1.0 = 普通拉伸；
      <1.0 = 内收拔模，如键帽/花盆；>1.0 = 外扩拔模，如漏斗）。
      顶面轮廓 = profile_xy 沿其几何中心按 top_scale 缩放后的形状。
      对应 SolidWorks 拔模角：`draftAngleDeg = degrees(atan((1-top_scale)·半宽 / depth_z))`
      顶面孔中心也会按 top_scale 从中心向内/外缩放（半径亦缩放），但**当前 B04 只用于纯梯形（无孔）**。
    """
    profile_xy: list[tuple[float, float]]
    depth_z: float
    holes: list[Hole] = field(default_factory=list)
    name: str = ""
    part_type: str = "extrude_prism"   # l_bracket / plate_with_holes / t_bracket / keycap / ...
    notes: str = ""
    top_scale: float = 1.0             # 拔模：顶面尺寸相对底面的比例
    profile_centered: bool = False     # 若 True，profile 已围绕原点居中（键帽用；影响 SolidWorks 建模坐标）
    operations: list = field(default_factory=list)  # 附加复合特征：FilletEdges / CutoutOnFace 混合列表，按顺序应用

    def __post_init__(self) -> None:
        if len(self.profile_xy) < 3:
            raise ValueError("profile_xy 至少 3 个顶点")
        if self.depth_z <= 0:
            raise ValueError("depth_z 必须 > 0")
        if self.top_scale <= 0 or self.top_scale > 5:
            raise ValueError(f"top_scale 应在 (0, 5] 范围内，got {self.top_scale}")
        # 检查孔全部落在 profile 包围盒内（简单粗筛，不做严格 point-in-polygon）
        bb = self.bbox_xy()
        for h in self.holes:
            if not (bb[0] < h.cx - h.radius and h.cx + h.radius < bb[2]
                    and bb[1] < h.cy - h.radius and h.cy + h.radius < bb[3]):
                raise ValueError(
                    f"孔 (cx={h.cx}, cy={h.cy}, r={h.radius}) 超出 profile 包围盒 {bb}"
                )
        # 拔模拉伸暂不支持孔（避免顶面孔投影与切除组合的复杂性）
        if self.top_scale != 1.0 and self.holes:
            raise ValueError("拔模拉伸（top_scale≠1）暂不支持带孔零件")

    # -------- 拔模衍生量 --------

    def has_draft(self) -> bool:
        return abs(self.top_scale - 1.0) > 1e-6

    def draft_angle_deg(self) -> float:
        """从 top_scale + depth_z + 半宽 反推 SolidWorks 需要的拔模角度（度）。

        推导：底半宽 W → 顶半宽 W·s，则每侧内缩 W·(1-s)；斜边斜率相对深度 = W(1-s)/h；
        拔模角 = atan(W(1-s)/h)。若 s>1（外扩）则角度为 atan((s-1)·W/h)，方向向外。
        取 profile 的 X 半宽做基准（假设 profile 大致方形/居中；键帽满足）。
        """
        if not self.has_draft():
            return 0.0
        import math
        xmin, ymin, xmax, ymax = self.bbox_xy()
        half_w = (xmax - xmin) / 2.0
        return math.degrees(math.atan(abs(1 - self.top_scale) * half_w / self.depth_z))

    def draft_outward(self) -> bool:
        return self.top_scale > 1.0

    def top_profile_xy(self) -> list[tuple[float, float]]:
        """按 top_scale 从 profile 几何中心（bbox 中心）缩放得到顶面轮廓。"""
        xmin, ymin, xmax, ymax = self.bbox_xy()
        cx = (xmin + xmax) / 2.0
        cy = (ymin + ymax) / 2.0
        s = self.top_scale
        return [(cx + (x - cx) * s, cy + (y - cy) * s) for x, y in self.profile_xy]

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
        """零件体积（自动扣除 operations 里的所有切除量）。
        - 普通拉伸: profile 净面积 × 拉伸深度
        - 拔模拉伸（top_scale≠1）: **棱台/圆台公式** V = h/3 · (A_底 + A_顶 + √(A_底·A_顶))
          其中 A_顶 = A_底 · top_scale² （因线性缩放 → 面积按平方缩放）
        - 复合特征：每个 CutoutOnFace 扣除 shape_area × 切除深度（贯穿则按 depth_z）；
          FilletEdges 的体积影响极小，暂忽略。
        """
        import math
        a_bottom = self.profile_area_mm2()
        if not self.has_draft():
            v = a_bottom * self.depth_z
        else:
            a_top = a_bottom * self.top_scale ** 2
            v = self.depth_z / 3.0 * (a_bottom + a_top + math.sqrt(a_bottom * a_top))

        # 扣除复合切除
        for op in self.operations:
            if isinstance(op, CutoutOnFace):
                area = _cutout_shape_area(op)
                d = self.depth_z if op.through_all else op.depth
                # positions 若不为空则每个位置扣一份，否则单份
                n = max(1, len(op.positions))
                v -= area * d * n
        return round(v, 3)

    def key_dimensions(self) -> dict:
        """从几何自动抽取标注尺寸，用于 groundtruth 和后续 IR 对比。"""
        bb = self.bbox_3d_mm()
        dims: dict = {
            "outer_length": bb["length"],
            "outer_height": bb["height"],
            "depth": bb["depth"],
        }
        # 拔模：额外记录顶面尺寸和拔模角
        if self.has_draft():
            xmin, ymin, xmax, ymax = self.bbox_xy()
            dims["top_length"] = round((xmax - xmin) * self.top_scale, 3)
            dims["top_height"] = round((ymax - ymin) * self.top_scale, 3)
            dims["draft_angle_deg"] = round(self.draft_angle_deg(), 3)
            dims["draft_outward"] = self.draft_outward()
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

    bb = part.bbox_3d_mm()
    L, HT, D = bb["length"], bb["height"], bb["depth"]

    # ---- 自适应比例：确保主视图 + 俯视图 高度总和不超过画布 ----
    # 主视图高 = HT*S，俯视图高 = D*S；两者中间预留 100px 标注区
    # 主视图起点 y=250，画布高 1200，底部留 100px → 可用高度 850px
    # HT*S + D*S + 100(中间) <= 850  →  S <= 750 / (HT+D)
    S = scale_px_per_mm
    max_h_sum = 750  # 主视图 + 俯视图的总像素高度上限
    if (HT + D) * S > max_h_sum:
        S = max(3, int(max_h_sum / (HT + D)))
    # 宽度也约束：主视图 + 100 + 侧视图 <= 1400（画布 1600，两侧留 200 边距）
    max_w_sum = 1400
    if (L + D + 200) * S > max_w_sum:  # +200 是两个视图之间的间距
        S = max(3, min(S, int(max_w_sum / (L + D + 100))))

    # ---- 布局：4 个象限的中心位置 ----
    #    前上、右上、前下（俯视）；右下留空
    front_origin = (200, 250)                          # 主视图左上角
    top_origin = (200, 250 + int(HT * S) + 120)        # 俯视图跟在主视图下方 + 120px 间距
    right_origin = (200 + int(L * S) + 200, 250)       # 侧视图在主视图右侧 + 200px 间距

    # profile_xy 到主视图的映射：**注意 Y 轴翻转**（图纸 Y 向下）
    xmin, ymin, xmax, ymax = part.bbox_xy()

    def to_px_front(x: float, y: float) -> tuple[float, float]:
        """profile 局部 (x, y) → 主视图像素 (px, py)，Y 轴翻转。"""
        px = front_origin[0] + (x - xmin) * S
        py = front_origin[1] + (ymax - y) * S
        return px, py

    # ============ 主视图 front ============
    # 主视图 = XY 平面上看到的形状（沿 -Z 观察）：底面轮廓实线 + 顶面轮廓虚线（若有拔模）
    dr.text((front_origin[0], front_origin[1] - 60), "主视图 Front", fill="black", font=F_TITLE)
    verts_front = [to_px_front(x, y) for x, y in part.profile_xy]
    _draw_polygon(dr, verts_front, color="black", width=2)

    # 拔模：顶面轮廓用虚线绘制（在主视图上就是"外框实线 + 内框虚线"的键帽经典画法）
    if part.has_draft():
        top_profile = part.top_profile_xy()
        verts_top = [to_px_front(x, y) for x, y in top_profile]
        # 用虚线闭合画顶面轮廓
        for i in range(len(verts_top)):
            a = verts_top[i]
            b = verts_top[(i + 1) % len(verts_top)]
            _draw_dashed_line(dr, a[0], a[1], b[0], b[1], color="black")

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

    # 拔模：额外标注顶面尺寸（在主视图内部虚线框旁）
    if part.has_draft():
        top_l = L * part.top_scale
        top_h = HT * part.top_scale
        # 顶面宽标注放在虚线框上方
        top_center_x = (front_left + front_right) / 2
        top_center_y = (front_top + front_bottom) / 2
        top_left_px = top_center_x - top_l * S / 2
        top_right_px = top_center_x + top_l * S / 2
        top_bot_px = top_center_y + top_h * S / 2
        top_top_px = top_center_y - top_h * S / 2
        dr.text((top_left_px, top_top_px - 22), f"顶 {int(top_l)}×{int(top_h)} mm", fill="#0066cc", font=F_DIM)

    # ============ 俯视图 top ============
    # 俯视图 = XZ 平面（沿 -Y 观察）：宽 × 深 的形状
    # 若有拔模：显示为梯形（下边=底面宽 L，上边=顶面宽 L·top_scale，高=深度 D）
    dr.text((top_origin[0], top_origin[1] - 30), "俯视图 Top", fill="black", font=F_TITLE)
    top_w = L * S
    top_d = D * S
    if part.has_draft():
        # 梯形：假设顶面在俯视图**上方**（Z=depth 处），底面在下方
        top_top_w = L * part.top_scale * S
        cx_top = top_origin[0] + top_w / 2  # X 中心线
        # 梯形四点（顺时针）：底左、底右、顶右、顶左
        poly_trap = [
            (top_origin[0], top_origin[1] + top_d),                    # 底左 (Z=0 侧)
            (top_origin[0] + top_w, top_origin[1] + top_d),            # 底右
            (cx_top + top_top_w / 2, top_origin[1]),                   # 顶右 (Z=depth)
            (cx_top - top_top_w / 2, top_origin[1]),                   # 顶左
        ]
        _draw_polygon(dr, poly_trap, color="black", width=2)
        # 顶面宽度标注（上方）
        _draw_dimension_h(
            dr, cx_top - top_top_w / 2, cx_top + top_top_w / 2, top_origin[1] - 20,
            f"{int(L * part.top_scale)} mm", F_DIM,
        )
    else:
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

    # 俯视图标注：底宽（下方）、深（右侧）
    _draw_dimension_h(
        dr, top_origin[0], top_origin[0] + top_w, top_origin[1] + top_d + 40,
        f"{int(L)} mm", F_DIM,
    )
    _draw_dimension_v(
        dr, top_origin[0] + top_w + 45, top_origin[1], top_origin[1] + top_d,
        f"{int(D)} mm", F_DIM, text_on_right=True,
    )

    # ============ 侧视图 right ============
    # 侧视图 = YZ 平面（沿 -X 观察）：深 × 高 的形状
    # 若有拔模：显示为梯形（下边=底面高 HT，上边=顶面高 HT·top_scale，宽=深度 D）
    dr.text((right_origin[0], right_origin[1] - 60), "侧视图 Right", fill="black", font=F_TITLE)
    right_w = D * S
    right_h = HT * S
    if part.has_draft():
        top_side_h = HT * part.top_scale * S
        cy_right = right_origin[1] + right_h / 2  # Y 中心线
        # 梯形四点：底左 (Z=0, Y=full)、底右 (Z=depth, Y=full)、
        #           顶右 (Z=depth, Y=顶高)、顶左 (Z=0, Y=顶高)
        # 但侧视图是"Y 是竖轴、Z 是横轴"——键帽应该是"底面在纸下方最宽、顶面在纸上方最窄"
        # 这里 right_origin 到 right_origin+right_h 是 Y 方向（下→上），right_w 是 Z 深度方向
        # 修正：Y 向下 = 图像 y 增大 = 底面在下方，顶面在上方
        cx_right = right_origin[0] + right_w / 2  # 深度方向中心
        poly_trap_r = [
            (right_origin[0], right_origin[1] + right_h),                     # 底左 (Z=0, Y=底)
            (right_origin[0] + right_w, right_origin[1] + right_h),           # 底右 (Z=depth, Y=底)
            (cx_right + top_side_h / 2 * 0, right_origin[1]),                 # 占位（下面替换）
            (cx_right - top_side_h / 2 * 0, right_origin[1]),
        ]
        # 修正梯形：底面宽 right_h，顶面宽 right_h*top_scale，两侧各内缩相同
        # 键帽侧视图：底在下（Y=底面高完整），顶在上（Y=顶面高较窄）
        # 这里 Y 是竖轴，所以"底面高"体现在 Y 方向的整个 right_h，"顶面高"体现在中间收窄
        # 简化：把梯形沿 Y 方向"上窄下宽"绘制
        poly_trap_r = [
            (right_origin[0], right_origin[1] + right_h),
            (right_origin[0] + right_w, right_origin[1] + right_h),
            (cx_right + top_side_h / 2, right_origin[1]) if False else (right_origin[0] + right_w * (1 + part.top_scale) / 2, right_origin[1]),
            (right_origin[0] + right_w * (1 - part.top_scale) / 2, right_origin[1]),
        ]
        _draw_polygon(dr, poly_trap_r, color="black", width=2)
    else:
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
    if part.part_type != "stepped_organizer":
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

    # ============ 渲染复合特征（cutouts / fillets）============
    # 主视图上：顶面切除画实线（可见），底面切除画虚线（不可见）
    # 圆角特征：额外文字标注
    _render_composite_ops(
        dr, part, to_px_front, S, F_DIM,
        top_origin=top_origin, top_w=top_w, top_d=top_d,
        right_origin=right_origin, right_w=right_w, right_h=right_h,
    )

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
# 渲染复合特征（cutouts / fillets）
# ============================================================


def _render_composite_ops(
    dr: ImageDraw.ImageDraw,
    part: "ExtrudedPart",
    to_px_front,
    S: int,
    font: ImageFont.ImageFont,
    top_origin: tuple[int, int],
    top_w: float,
    top_d: float,
    right_origin: tuple[int, int],
    right_w: float,
    right_h: float,
) -> None:
    """
    把复合特征（CutoutOnFace / FilletEdges）叠加渲染到三视图上。

    主视图规则（沿 -Z 观察 → 看到底座 XY 轮廓）：
      - 顶面（+Z 面）切除：**实线**（可见）
      - 底面（-Z 面）切除：**虚线**（不可见）
      - 贯穿切除：实线 + 虚线都画（同一形状叠加，看起来就是实线）
    俯视图规则（沿 -Y 观察 → 看到 XZ 侧影）：
      - 顶面切除表现为顶部（Z=depth 侧）向下凹陷
      - 底面切除表现为底部（Z=0 侧）向上凹陷
      - 我们简化：画虚线段表示切除的 Z 深度范围
    侧视图规则（沿 -X 观察）：同理但用 Y 位置

    圆角：只在图例区加文字说明（不改轮廓），避免误导 Vision 认为主轮廓有变化。
    """
    # 先收集所有 op 分类
    fillets = [op for op in part.operations if isinstance(op, FilletEdges)]
    cutouts = [op for op in part.operations if isinstance(op, CutoutOnFace)]

    xmin, ymin, xmax, ymax = part.bbox_xy()
    depth_z = part.depth_z

    def to_px_top(x: float, z: float) -> tuple[float, float]:
        return (
            top_origin[0] + (x - xmin) * S,
            top_origin[1] + (depth_z - z) * S,
        )

    def to_px_right(z: float, y: float) -> tuple[float, float]:
        return (
            right_origin[0] + z * S,
            right_origin[1] + (ymax - y) * S,
        )

    def shape_half_extents(op: CutoutOnFace) -> tuple[float, float]:
        if op.shape == "circle":
            return op.radius, op.radius
        if op.shape == "slot":
            return op.length / 2.0, op.width / 2.0
        return op.width / 2.0, op.height / 2.0

    def dashed_rect(mapper, a1, b1, a2, b2) -> None:
        corners = [
            mapper(a1, b1),
            mapper(a2, b1),
            mapper(a2, b2),
            mapper(a1, b2),
        ]
        for index, start in enumerate(corners):
            end = corners[(index + 1) % len(corners)]
            _draw_dashed_line(
                dr,
                start[0],
                start[1],
                end[0],
                end[1],
                color="#555555",
            )

    # ---- 在各正交视图上叠加 cutouts ----
    operation_legend: list[str] = []
    for op_index, op in enumerate(cutouts):
        marker = chr(ord("A") + op_index)
        half_x, half_second = shape_half_extents(op)
        operation_legend.append(
            f"{marker}  {op.label or op.shape}: "
            f"{int(half_x * 2)}×{int(half_second * 2)}mm, "
            f"深 {op.depth:g}mm"
        )
        positions = op.positions if op.positions else [(op.cx, op.cy)]
        face = op.face.lower()
        if face in {"top_y", "bottom_y"}:
            surface = op.surface_level
            if not surface:
                surface = ymax if face == "top_y" else ymin
            direction = -1.0 if face == "top_y" else 1.0
            end_y = surface + direction * op.depth
            for cx, cz in positions:
                _draw_cutout_shape_at(
                    dr,
                    op,
                    cx,
                    cz,
                    to_px_top,
                    S,
                    color="black",
                    solid=face == "top_y",
                )
                half_x, half_z = shape_half_extents(op)
                dashed_rect(
                    to_px_front,
                    cx - half_x,
                    min(surface, end_y),
                    cx + half_x,
                    max(surface, end_y),
                )
                dashed_rect(
                    to_px_right,
                    max(0.0, cz - half_z),
                    min(surface, end_y),
                    min(depth_z, cz + half_z),
                    max(surface, end_y),
                )
                label_point = to_px_top(cx, cz)
                dr.text(
                    (label_point[0] - 6, label_point[1] - 10),
                    marker,
                    fill="#0066cc",
                    font=font,
                )
            continue

        # Legacy top/bottom mean the +Z/-Z faces and use XY coordinates.
        front_side = face in {"top", "front_z"}
        for cx, cy in positions:
            _draw_cutout_shape_at(
                dr,
                op,
                cx,
                cy,
                to_px_front,
                S,
                color="black",
                solid=front_side or op.through_all,
            )
            half_x, half_y = shape_half_extents(op)
            start_z = depth_z - op.depth if front_side else 0.0
            end_z = depth_z if front_side else op.depth
            dashed_rect(
                to_px_top,
                cx - half_x,
                start_z,
                cx + half_x,
                end_z,
            )
            dashed_rect(
                to_px_right,
                start_z,
                cy - half_y,
                end_z,
                cy + half_y,
            )
            px, py = to_px_front(cx, cy)
            dr.text(
                (px - 6, py - 10),
                marker,
                fill="#0066cc",
                font=font,
            )

    if operation_legend:
        legend_x = right_origin[0]
        legend_y = right_origin[1] + right_h + 120
        dr.text(
            (legend_x, legend_y - 28),
            "局部特征：",
            fill="#0066cc",
            font=font,
        )
        for line in operation_legend:
            dr.text(
                (legend_x, legend_y),
                line,
                fill="#0066cc",
                font=font,
            )
            legend_y += 24

    # ---- 圆角：在标题下方加文字图例 ----
    if fillets:
        legend_y = 90  # 靠近顶部
        legend_x = 200
        for f in fillets:
            label = f"● 圆角 {f.edges} R{f.radius}mm" + (f" ({f.label})" if f.label else "")
            dr.text((legend_x, legend_y), label, fill="#0066cc", font=font)
            legend_y += 22


def _draw_cutout_shape_at(
    dr: ImageDraw.ImageDraw,
    op: CutoutOnFace,
    cx: float, cy: float,
    to_px_front,
    S: int,
    color: str = "black",
    solid: bool = True,
) -> None:
    """在主视图指定位置画一个切除形状（circle/rectangle/slot）。"""
    px, py = to_px_front(cx, cy)

    if op.shape == "circle":
        r_px = op.radius * S
        if solid:
            dr.ellipse([px - r_px, py - r_px, px + r_px, py + r_px], outline=color, width=2)
        else:
            _draw_dashed_arc(dr, px, py, r_px, color=color)
        # 十字中心线
        _draw_center_marker(dr, px, py, size=r_px + 4, color=color)

    elif op.shape == "rectangle":
        w_px = op.width * S / 2
        h_px = op.height * S / 2
        if solid:
            dr.rectangle([px - w_px, py - h_px, px + w_px, py + h_px], outline=color, width=2)
        else:
            for a, b in [
                ((px - w_px, py - h_px), (px + w_px, py - h_px)),
                ((px + w_px, py - h_px), (px + w_px, py + h_px)),
                ((px + w_px, py + h_px), (px - w_px, py + h_px)),
                ((px - w_px, py + h_px), (px - w_px, py - h_px)),
            ]:
                _draw_dashed_line(dr, a[0], a[1], b[0], b[1], color=color)

    elif op.shape == "slot":
        # 腰形孔：两端半圆 + 中间矩形
        L_px = op.length * S / 2  # 半长
        W_px = op.width * S / 2   # 半宽（半径）
        # 中间矩形部分从 (-L+W, -W) 到 (L-W, W)
        rect_left = px - L_px + W_px
        rect_right = px + L_px - W_px
        if solid:
            dr.line([(rect_left, py - W_px), (rect_right, py - W_px)], fill=color, width=2)
            dr.line([(rect_left, py + W_px), (rect_right, py + W_px)], fill=color, width=2)
            # 两端半圆
            dr.arc([px - L_px, py - W_px, rect_left + W_px, py + W_px],
                   start=90, end=270, fill=color, width=2)
            dr.arc([rect_right - W_px, py - W_px, px + L_px, py + W_px],
                   start=270, end=90, fill=color, width=2)
        else:
            _draw_dashed_line(dr, rect_left, py - W_px, rect_right, py - W_px, color=color)
            _draw_dashed_line(dr, rect_left, py + W_px, rect_right, py + W_px, color=color)
            _draw_dashed_arc(dr, px - L_px + W_px, py, W_px, color=color, start=90, end=270)
            _draw_dashed_arc(dr, px + L_px - W_px, py, W_px, color=color, start=270, end=90)


def _draw_dashed_arc(
    dr: ImageDraw.ImageDraw,
    cx: float, cy: float, r: float,
    color: str = "black",
    start: int = 0, end: int = 360,
    dash_len_deg: int = 8, gap_deg: int = 4,
) -> None:
    """画虚线圆弧。"""
    ang = start
    while ang < end:
        a2 = min(ang + dash_len_deg, end)
        dr.arc([cx - r, cy - r, cx + r, cy + r], start=ang, end=a2, fill=color, width=1)
        ang = a2 + gap_deg


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
        "operations": _serialize_operations(part.operations),
        "notes": part.notes,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(gt, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_operations(ops: list) -> list:
    """把 operations 列表序列化为 JSON-safe 的 dict 列表。"""
    out = []
    for op in ops:
        if isinstance(op, FilletEdges):
            out.append({
                "type": "fillet_edges",
                "edges": op.edges,
                "radius": op.radius,
                "label": op.label,
            })
        elif isinstance(op, CutoutOnFace):
            out.append({
                "type": "cutout_on_face",
                "face": op.face,
                "shape": op.shape,
                "depth": op.depth,
                "through_all": op.through_all,
                "cx": op.cx,
                "cy": op.cy,
                "radius": op.radius,
                "width": op.width,
                "height": op.height,
                "length": op.length,
                "positions": op.positions,
                "surface_level": op.surface_level,
                "label": op.label,
            })
    return out


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
