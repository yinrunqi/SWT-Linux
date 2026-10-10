"""草图预检：在发给 SolidWorks 之前用 shapely 发现不闭合、自交、越界、壁太薄等问题。

SolidWorks 对这类草图往往只返回“特征失败”，LLM 很难据此修复；
预检给出标准化错误码和具体位置，修复成本低得多。坐标单位为 mm（草图平面二维坐标）。

轮廓写法：
    {"type": "rect", "x1": .., "y1": .., "x2": .., "y2": ..}
    {"type": "circle", "cx": .., "cy": .., "r": ..}
    {"type": "polygon", "points": [[x, y], ...]}          # 自动闭合
    {"type": "polyline", "points": [[x, y], ...], "closed": false}
    {"type": "regular_polygon", "cx": .., "cy": .., "r": .., "sides": 6}  # r 为外接圆半径
    {"type": "slot", "x1": .., "y1": .., "x2": .., "y2": .., "width": ..}
    {"type": "path", "segments": [...]}                    # 直线 + 圆弧，见 curves.py
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon
from shapely.validation import explain_validity

from .curves import discretize
from .errors import ERROR_CODES

MIN_AREA_MM2 = 1e-6
CIRCLE_RESOLUTION = 64


@dataclass
class Issue:
    code: str
    message: str
    loops: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "loops": self.loops}


@dataclass
class SketchCheckResult:
    issues: list[Issue] = field(default_factory=list)
    outer_loops: list[int] = field(default_factory=list)
    inner_loops: dict[int, int] = field(default_factory=dict)  # 内环 -> 所属外环
    region_area_mm2: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def codes(self) -> list[str]:
        return [issue.code for issue in self.issues]

    def add(self, code: str, message: str, *loops: int) -> None:
        assert code in ERROR_CODES, code
        self.issues.append(Issue(code, message, list(loops)))

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "issues": [issue.to_dict() for issue in self.issues],
            "outer_loops": self.outer_loops,
            "inner_loops": self.inner_loops,
            "region_area_mm2": round(self.region_area_mm2, 6),
        }


def _points(raw) -> list[tuple[float, float]]:
    return [(float(p[0]), float(p[1])) for p in raw or []]


def to_geometry(loop: dict):
    """把一个轮廓描述转成 shapely 几何；开放折线返回 LineString。"""
    kind = str(loop.get("type", "")).lower()
    if kind == "rect":
        x1, y1, x2, y2 = (float(loop[key]) for key in ("x1", "y1", "x2", "y2"))
        return Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])
    if kind == "circle":
        radius = float(loop["r"])
        if radius <= 0:
            return None
        return Point(float(loop["cx"]), float(loop["cy"])).buffer(radius, quad_segs=CIRCLE_RESOLUTION // 4)
    if kind == "regular_polygon":
        sides = int(loop.get("sides", 6))
        radius = float(loop["r"])
        if sides < 3 or radius <= 0:
            return None
        start = math.radians(float(loop.get("angle_deg", 0)))
        return Polygon([
            (
                float(loop["cx"]) + radius * math.cos(start + 2 * math.pi * i / sides),
                float(loop["cy"]) + radius * math.sin(start + 2 * math.pi * i / sides),
            )
            for i in range(sides)
        ])
    if kind == "slot":
        width = float(loop["width"])
        if width <= 0:
            return None
        line = LineString([(float(loop["x1"]), float(loop["y1"])), (float(loop["x2"]), float(loop["y2"]))])
        return line.buffer(width / 2, quad_segs=CIRCLE_RESOLUTION // 4)
    if kind == "path":
        points = discretize(loop["segments"])
        return Polygon(points) if len(points) >= 3 else None
    if kind in ("polygon", "polyline"):
        points = _points(loop.get("points"))
        closed = kind == "polygon" or bool(loop.get("closed", False))
        if len(points) >= 2 and points[0] == points[-1]:
            points = points[:-1]
            closed = True
        if not closed:
            return LineString(points) if len(points) >= 2 else None
        if len(points) < 3:
            return None
        return Polygon(points)
    raise ValueError(f"未知轮廓类型: {kind!r}")


def check_profile(loops: list[dict], *, min_wall_mm: float | None = None) -> SketchCheckResult:
    """检查一个草图的全部轮廓，并推断外环/内环（孔）的嵌套关系。"""
    result = SketchCheckResult()
    if not loops:
        result.add("SKETCH_EMPTY", "草图没有轮廓")
        return result

    geoms: dict[int, Polygon] = {}
    for index, loop in enumerate(loops):
        try:
            geom = to_geometry(loop)
        except (KeyError, TypeError, ValueError) as exc:
            result.add("SKETCH_DEGENERATE", f"轮廓 {index} 参数不完整: {exc}", index)
            continue
        if geom is None:
            result.add("SKETCH_DEGENERATE", f"轮廓 {index} 点数不足或尺寸非正", index)
            continue
        if isinstance(geom, LineString):
            gap = Point(geom.coords[0]).distance(Point(geom.coords[-1]))
            result.add("SKETCH_OPEN_LOOP", f"轮廓 {index} 未闭合，首尾相距 {gap:.3f}mm", index)
            continue
        if not geom.is_valid:
            result.add("SKETCH_SELF_INTERSECT", f"轮廓 {index} 自相交: {explain_validity(geom)}", index)
            continue
        if geom.area <= MIN_AREA_MM2:
            result.add("SKETCH_DEGENERATE", f"轮廓 {index} 面积为零", index)
            continue
        geoms[index] = geom

    # 重合的轮廓互相包含，会让父环关系成环
    for index in sorted(geoms):
        for other in sorted(geoms):
            if other < index and other in geoms and index in geoms and geoms[other].equals(geoms[index]):
                result.add("SKETCH_LOOPS_OVERLAP", f"轮廓 {other} 与 {index} 完全重合", other, index)
                del geoms[index]
                break

    # 嵌套关系：被某个轮廓完全包含的是内环，取包含它的最小轮廓为父环。
    for index, geom in geoms.items():
        parents = [
            other for other, other_geom in geoms.items()
            if other != index and other_geom.contains(geom) and not geom.contains(other_geom)
        ]
        if parents:
            result.inner_loops[index] = min(parents, key=lambda item: geoms[item].area)
        else:
            result.outer_loops.append(index)

    indices = sorted(geoms)
    for pos, a in enumerate(indices):
        for b in indices[pos + 1:]:
            ga, gb = geoms[a], geoms[b]
            if ga.contains(gb) or gb.contains(ga):
                continue
            if ga.intersection(gb).area > MIN_AREA_MM2 or ga.crosses(gb) or ga.overlaps(gb):
                result.add("SKETCH_LOOPS_OVERLAP", f"轮廓 {a} 与 {b} 相交但互不包含", a, b)

    if min_wall_mm is not None:
        for inner, outer in result.inner_loops.items():
            wall = geoms[inner].exterior.distance(geoms[outer].exterior)
            if wall < min_wall_mm:
                result.add(
                    "SKETCH_THIN_WALL",
                    f"轮廓 {inner} 到外环 {outer} 的最小壁厚 {wall:.3f}mm < {min_wall_mm}mm",
                    inner, outer,
                )
        inners = sorted(result.inner_loops)
        for pos, a in enumerate(inners):
            for b in inners[pos + 1:]:
                if result.inner_loops[a] != result.inner_loops[b]:
                    continue
                gap = geoms[a].distance(geoms[b])
                if gap < min_wall_mm:
                    result.add("SKETCH_THIN_WALL", f"轮廓 {a} 与 {b} 间距 {gap:.3f}mm < {min_wall_mm}mm", a, b)

    area = 0.0
    for index, geom in geoms.items():
        depth, cursor = 0, index
        while cursor in result.inner_loops and depth <= len(geoms):
            depth += 1
            cursor = result.inner_loops[cursor]
        area += geom.area if depth % 2 == 0 else -geom.area
    result.region_area_mm2 = max(0.0, area)
    return result


def check_within(
    boundary: dict,
    loops: list[dict],
    *,
    min_margin_mm: float = 0.0,
) -> SketchCheckResult:
    """在已有面上切孔/加凸台时，检查每个轮廓都落在面的边界内并保留边距。"""
    result = check_profile(loops)
    try:
        outline = to_geometry(boundary)
    except (KeyError, TypeError, ValueError) as exc:
        result.add("SKETCH_DEGENERATE", f"面边界参数不完整: {exc}")
        return result
    if outline is None or isinstance(outline, LineString):
        result.add("SKETCH_DEGENERATE", "面边界不是闭合区域")
        return result
    for index, loop in enumerate(loops):
        try:
            geom = to_geometry(loop)
        except (KeyError, TypeError, ValueError):
            continue
        if geom is None or isinstance(geom, LineString) or not geom.is_valid:
            continue
        if not outline.contains(geom):
            result.add("SKETCH_OUTSIDE_BOUNDARY", f"轮廓 {index} 超出面的边界", index)
            continue
        margin = geom.exterior.distance(outline.exterior)
        if margin < min_margin_mm:
            result.add(
                "SKETCH_THIN_WALL", f"轮廓 {index} 到面边界 {margin:.3f}mm < {min_margin_mm}mm", index
            )
    return result
