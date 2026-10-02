"""由参考实体生成三视图图纸（第三角画法）：HLR 投影出可见边（粗实线）与隐藏边（虚线），
再按 rubric 中的 ``drawing`` 清单画尺寸、直径/半径引线、中心线和注释。

视图与世界坐标（与 refs_three_view 的约定一致）：
- 主视图：从 +Z 看，图上 u = X、v = Y；
- 俯视图：从 +Y 看，u = X、v = −Z，放在主视图上方；
- 右视图：从 +X 看，u = −Z、v = Y，放在主视图右方。

标注清单（YAML）::

    drawing:
      dims:        # 线性尺寸；side 为尺寸线放在视图外框的哪一侧，tier 为第几层
        - {view: front, a: [0, 0, 0], b: [60, 0, 0], side: bottom, tier: 1, text: "60"}
      callouts:    # 直径/半径引线；at 为圆心（diameter）或圆弧上一点（radius）
        - {view: front, at: [20, 20, 10], r: 5, text: "2×Ø10", angle: 45}
      centers:     # 圆心十字中心线
        - {view: front, at: [20, 20, 10], r: 5}
      axes:        # 点画线（轴线、对称线）
        - {view: top, a: [0, 0, -60], b: [0, 0, 60]}
      circles:     # 点画线圆（如孔中心圆）
        - {view: front, at: [0, 0, 10], r: 24}
      notes: ["未注圆角 R3"]
"""

from __future__ import annotations

import math
from pathlib import Path

from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

FONT = r"C:\Windows\Fonts\msyh.ttc"
SS = 2  # 超采样倍数，最后缩小以抗锯齿

VIEWS = {
    "front": ((0, 0, 1), (1, 0, 0)),
    "top": ((0, 1, 0), (1, 0, 0)),
    "right": ((1, 0, 0), (0, 0, -1)),
}
TITLES = {"front": "主视图", "top": "俯视图", "right": "右视图"}


def to_uv(view: str, p) -> tuple[float, float]:
    x, y, z = p
    return {"front": (x, y), "top": (x, -z), "right": (-z, y)}[view]


def _polylines(compound, deflection: float) -> list[list[tuple[float, float]]]:
    out = []
    if compound is None or compound.IsNull():
        return out
    exp = TopExp_Explorer(compound, TopAbs_EDGE)
    while exp.More():
        curve = BRepAdaptor_Curve(TopoDS.Edge(exp.Current()))
        disc = GCPnts_QuasiUniformDeflection(curve, deflection)
        if disc.IsDone() and disc.NbPoints() >= 2:
            pts = [disc.Value(i) for i in range(1, disc.NbPoints() + 1)]
            out.append(_straighten([(p.X(), p.Y()) for p in pts]))
        exp.Next()
    return out


def _straighten(pl):
    """共线折线（如侧视看到的圆，投影成可能来回折返的线段）化简为两端点。"""
    (x0, y0), (x1, y1) = min(pl), max(pl)
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return pl
    if all(abs((x - x0) * dy - (y - y0) * dx) / n < 1e-6 for x, y in pl):
        t = [((x - x0) * dx + (y - y0) * dy) / n for x, y in pl]
        return [pl[t.index(min(t))], pl[t.index(max(t))]]
    return pl


def hlr_view(shape: TopoDS_Shape, view: str, deflection: float = 0.02):
    """返回 (可见折线, 光滑过渡边折线, 隐藏折线)，坐标为图面 (u, v)，单位 mm。"""
    d, x = VIEWS[view]
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*d), gp_Dir(*x))))
    algo.Update()
    algo.Hide()
    hlr = HLRBRep_HLRToShape(algo)
    visible = _polylines(hlr.VCompound(), deflection) + _polylines(hlr.OutLineVCompound(), deflection)
    smooth = _polylines(hlr.Rg1LineVCompound(), deflection)
    hidden = _polylines(hlr.HCompound(), deflection) + _polylines(hlr.OutLineHCompound(), deflection)
    return visible, smooth, hidden


def _bbox(polys):
    us = [p[0] for pl in polys for p in pl]
    vs = [p[1] for pl in polys for p in pl]
    return min(us), min(vs), max(us), max(vs)


class _Canvas:
    def __init__(self, width, height, scale):
        from PIL import Image, ImageDraw, ImageFont

        self.img = Image.new("RGB", (width * SS, height * SS), "white")
        self.draw = ImageDraw.Draw(self.img)
        self.font = ImageFont.truetype(FONT, 17 * SS)
        self.title_font = ImageFont.truetype(FONT, 20 * SS)
        self.scale = scale * SS
        self.origin = {}

    def xy(self, view, u, v):
        ox, oy = self.origin[view]
        return ox + u * self.scale, oy - v * self.scale

    def line(self, pts, width=1, color="black", dash=None):
        pts = [tuple(p) for p in pts]
        w = max(1, int(round(width * SS)))
        if not dash:
            self.draw.line(pts, fill=color, width=w, joint="curve")
            return
        pattern = [d * SS for d in dash]
        k, left, on = 0, pattern[0], True
        if len(pts) == 2:
            # 直线的虚线相位按绝对位置取，HLR 输出的重叠共线段画出来完全重合
            (x0, y0), (x1, y1) = pts
            dx, dy = x1 - x0, y1 - y0
            if (dx if abs(dx) >= abs(dy) else dy) < 0:
                pts = [(x1, y1), (x0, y0)]
                (x0, y0), (x1, y1) = pts
            n = math.hypot(x1 - x0, y1 - y0) or 1.0
            s = ((x0 * (x1 - x0) + y0 * (y1 - y0)) / n) % sum(pattern)
            while s >= pattern[k]:
                s -= pattern[k]
                k = (k + 1) % len(pattern)
                on = not on
            left = pattern[k] - s
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            seg = math.hypot(x1 - x0, y1 - y0)
            t = 0.0
            while t < seg - 1e-9:
                step = min(left, seg - t)
                if on:
                    a, b = t / seg, (t + step) / seg
                    self.draw.line([(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                                    (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b)], fill=color, width=w)
                t += step
                left -= step
                if left <= 1e-9:
                    k = (k + 1) % len(pattern)
                    left, on = pattern[k], not on
        return

    def text(self, xy, s, anchor="mm", font=None, angle=0):
        from PIL import Image, ImageDraw

        font = font or self.font
        if not angle:
            self.draw.text(xy, s, fill="black", font=font, anchor=anchor)
            return
        l, t, r, b = self.draw.textbbox((0, 0), s, font=font, anchor="lt")
        tile = Image.new("RGBA", (r - l + 4, b - t + 4), (255, 255, 255, 0))
        ImageDraw.Draw(tile).text((2 - l, 2 - t), s, fill="black", font=font, anchor="lt")
        tile = tile.rotate(angle, expand=True)
        self.img.paste(tile, (int(xy[0] - tile.width / 2), int(xy[1] - tile.height / 2)), tile)

    def arrow(self, tip, frm, size=7):
        (x, y), (fx, fy) = tip, frm
        ang = math.atan2(y - fy, x - fx)
        s = size * SS
        p1 = (x - s * math.cos(ang - 0.3), y - s * math.sin(ang - 0.3))
        p2 = (x - s * math.cos(ang + 0.3), y - s * math.sin(ang + 0.3))
        self.draw.polygon([tip, p1, p2], fill="black")


def render(shape: TopoDS_Shape, spec: dict, title: str, path: str | Path, max_w=1500, max_h=1050):
    spec = spec or {}
    geo = {v: hlr_view(shape, v) for v in VIEWS}
    boxes = {v: _bbox(vis + sm + hid) for v, (vis, sm, hid) in geo.items()}
    w = {v: b[2] - b[0] for v, b in boxes.items()}
    h = {v: b[3] - b[1] for v, b in boxes.items()}

    def tiers(view, side):
        return max([d.get("tier", 1) for d in spec.get("dims", []) if d["view"] == view and d["side"] == side] or [0])

    pad = 70  # 视图之间、四周留给尺寸的像素
    tier_px = 34
    margin = {v: {s: 30 + tier_px * tiers(v, s) for s in ("top", "bottom", "left", "right")} for v in VIEWS}
    left_px = max(margin["front"]["left"], margin["top"]["left"]) + pad
    mid_px = max(margin["front"]["right"], margin["top"]["right"]) + margin["right"]["left"] + pad
    right_px = margin["right"]["right"] + pad
    top_px = margin["top"]["top"] + pad
    midv_px = margin["top"]["bottom"] + max(margin["front"]["top"], 0) + pad
    from PIL import ImageFont

    notes = spec.get("notes", [])
    bottom_px = max(margin["front"]["bottom"], margin["right"]["bottom"]) + pad + 60 + 26 * len(notes)
    avail_w = max_w - left_px - mid_px - right_px
    avail_h = max_h - top_px - midv_px - bottom_px
    scale = min(avail_w / (w["front"] + w["right"]), avail_h / (h["top"] + max(h["front"], h["right"])))
    width = int(left_px + mid_px + right_px + (w["front"] + w["right"]) * scale)
    note_font = ImageFont.truetype(FONT, 17)
    note_lines = [(k, line) for note in notes for k, line in enumerate(_wrap(note_font, note, width - 84))]
    bottom_px += 26 * (len(note_lines) - len(notes))
    height = int(top_px + midv_px + bottom_px + (h["top"] + max(h["front"], h["right"])) * scale)
    cv = _Canvas(width, height, scale)

    fx0 = left_px * SS
    fy_top = (top_px + h["top"] * scale + midv_px) * SS
    cv.origin["front"] = (fx0 - boxes["front"][0] * cv.scale, fy_top + boxes["front"][3] * cv.scale)
    cv.origin["top"] = (fx0 - boxes["top"][0] * cv.scale, top_px * SS + boxes["top"][3] * cv.scale)
    rx0 = fx0 + w["front"] * cv.scale + mid_px * SS
    cv.origin["right"] = (rx0 - boxes["right"][0] * cv.scale, fy_top + boxes["right"][3] * cv.scale)

    for v, (vis, sm, hid) in geo.items():
        for pl in hid:
            cv.line([cv.xy(v, *p) for p in pl], width=1.3, dash=(7, 4))
        for pl in sm:
            cv.line([cv.xy(v, *p) for p in pl], width=1.0, color=(90, 90, 90))
        for pl in vis:
            cv.line([cv.xy(v, *p) for p in pl], width=2.4)
        u0, v0, u1, v1 = boxes[v]
        x, y = cv.xy(v, (u0 + u1) / 2, v0)
        cv.text((x, y + (margin[v]["bottom"] + 14) * SS), TITLES[v], font=cv.title_font)

    for a in spec.get("axes", []):
        cv.line([cv.xy(a["view"], *to_uv(a["view"], a["a"])), cv.xy(a["view"], *to_uv(a["view"], a["b"]))],
                width=0.9, color=(40, 40, 160), dash=(16, 3, 2, 3))
    for c in spec.get("circles", []):
        u, v = to_uv(c["view"], c["at"])
        pts = [(u + c["r"] * math.cos(t), v + c["r"] * math.sin(t)) for t in [2 * math.pi * k / 180 for k in range(181)]]
        cv.line([cv.xy(c["view"], *p) for p in pts], width=0.9, color=(40, 40, 160), dash=(16, 3, 2, 3))
    for c in spec.get("centers", []):
        u, v = to_uv(c["view"], c["at"])
        ext = c["r"] + min(max(2.0, 0.25 * c["r"]), 4.0)
        cv.line([cv.xy(c["view"], u - ext, v), cv.xy(c["view"], u + ext, v)], width=0.9, color=(40, 40, 160), dash=(10, 2, 2, 2))
        cv.line([cv.xy(c["view"], u, v - ext), cv.xy(c["view"], u, v + ext)], width=0.9, color=(40, 40, 160), dash=(10, 2, 2, 2))
    for d in spec.get("dims", []):
        _dim(cv, d, boxes[d["view"]], tier_px)
    for c in spec.get("callouts", []):
        _callout(cv, c)

    x0 = 30 * SS
    y = height * SS - (40 + 26 * len(note_lines)) * SS
    for k, line in note_lines:
        cv.text((x0 + (24 * SS if k else 0), y), line, anchor="lm")
        y += 26 * SS
    cv.text((x0, y), f"{title}    第三角画法    单位：mm", anchor="lm", font=cv.title_font)

    from PIL import Image

    out = cv.img.resize((width, height), Image.LANCZOS)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    out.save(path)
    return out


def _wrap(font, text: str, max_w: float) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        if font.getlength(cur + ch) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    return lines + [cur]


def _dim(cv: _Canvas, d: dict, box, tier_px):
    view = d["view"]
    ua, va = to_uv(view, d["a"])
    ub, vb = to_uv(view, d["b"])
    u0, v0, u1, v1 = box
    off = (16 + tier_px * (d.get("tier", 1) - 1) + 14) / (cv.scale / SS)
    side = d["side"]
    pa, pb = cv.xy(view, ua, va), cv.xy(view, ub, vb)
    if side in ("top", "bottom"):
        level = v1 + off if side == "top" else v0 - off
        qa, qb = cv.xy(view, ua, level), cv.xy(view, ub, level)
    else:
        level = u1 + off if side == "right" else u0 - off
        qa, qb = cv.xy(view, level, va), cv.xy(view, level, vb)
    gap = 3 * SS
    for p, q in ((pa, qa), (pb, qb)):
        dx, dy = q[0] - p[0], q[1] - p[1]
        n = math.hypot(dx, dy) or 1
        cv.line([(p[0] + dx / n * gap, p[1] + dy / n * gap), (q[0] + dx / n * 5 * SS, q[1] + dy / n * 5 * SS)], width=0.8)
    cv.line([qa, qb], width=0.8)
    cv.arrow(qa, qb)
    cv.arrow(qb, qa)
    mx, my = (qa[0] + qb[0]) / 2, (qa[1] + qb[1]) / 2
    if side in ("top", "bottom"):
        cv.text((mx, my - 12 * SS), d["text"])
    else:
        cv.text((mx - 12 * SS, my), d["text"], angle=90)


def _callout(cv: _Canvas, c: dict):
    view = c["view"]
    u, v = to_uv(view, c["at"])
    ang = math.radians(c.get("angle", 45))
    r = c.get("r", 0.0)
    tip = cv.xy(view, u + r * math.cos(ang), v + r * math.sin(ang))
    length = c.get("leader", 40) * SS
    elbow = (tip[0] + length * math.cos(ang), tip[1] - length * math.sin(ang))
    right = math.cos(ang) >= 0
    tail = (elbow[0] + (60 if right else -60) * SS, elbow[1])
    cv.line([tip, elbow, tail], width=0.8)
    cv.arrow(tip, elbow)
    cv.text((elbow[0] + (4 if right else -4) * SS, elbow[1] - 12 * SS), c["text"], anchor="lm" if right else "rm")
