"""OpenCASCADE 后端：只读最终 B-rep，提供 rubric 判定所需的几何查询。

所有长度单位为 mm。同一解析曲面被导出器拆成的多片面（如 SolidWorks 把圆柱拆成两半）
在这里合并成一个 ``Surf``，使不同 CAD 系统导出的 STEP 得到相同的计数。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import (
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.IntCurvesFace import IntCurvesFace_ShapeIntersector
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import (
    TopAbs_EDGE,
    TopAbs_FACE,
    TopAbs_IN,
    TopAbs_SHELL,
    TopAbs_SOLID,
    TopAbs_VERTEX,
    TopAbs_WIRE,
)
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as ShapeMap
from OCP.TopoDS import TopoDS, TopoDS_Shape
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Dir, gp_Lin, gp_Pnt, gp_Trsf, gp_Vec

MESH_DEFLECTION = 0.01
_ANG_EPS = 1e-4  # rad, 判定平行/同轴的方向容差
_POS_EPS = 1e-3  # mm, 合并同一曲面的位置容差


def unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def canon_dir(d) -> np.ndarray:
    """方向去符号：第一个非零分量取正。"""
    d = unit(d)
    for c in d:
        if abs(c) > 1e-9:
            return d if c > 0 else -d
    return d


def line_distance(o1, d1, o2) -> float:
    """点 o2 到直线 (o1, d1) 的距离。"""
    w = np.asarray(o2) - np.asarray(o1)
    return float(np.linalg.norm(w - np.dot(w, d1) * d1))


def parallel(d1, d2, eps: float = _ANG_EPS) -> bool:
    return abs(abs(float(np.dot(unit(d1), unit(d2)))) - 1.0) < eps


def _np(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()], dtype=float)


def _pnt(v) -> gp_Pnt:
    return gp_Pnt(float(v[0]), float(v[1]), float(v[2]))


def _dir(v) -> gp_Dir:
    v = unit(v)
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


def _volume(shape: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return float(props.Mass())


def _area(face) -> tuple[float, np.ndarray]:
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return float(props.Mass()), _np(props.CentreOfMass())


def _uv_bounds(face) -> tuple[float, float, float, float]:
    try:
        return tuple(BRepTools.UVBounds_s(face))  # type: ignore[return-value]
    except TypeError:
        return tuple(BRepTools.UVBounds_s(face, 0.0, 0.0, 0.0, 0.0))  # type: ignore[return-value]


def _face_normal(face) -> tuple[np.ndarray, np.ndarray]:
    """UV 中点处的点与朝实体外侧的法向（已计入面方向）。"""
    u0, u1, v0, v1 = _uv_bounds(face)
    p, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal((u0 + u1) / 2, (v0 + v1) / 2, p, n)
    return _np(p), unit([n.X(), n.Y(), n.Z()])


def _face_points(face) -> np.ndarray:
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None:
        return np.zeros((0, 3))
    trsf = loc.Transformation()
    return np.array(
        [_np(tri.Node(i).Transformed(trsf)) for i in range(1, tri.NbNodes() + 1)]
    )


@dataclass(eq=False)
class Surf:
    """合并后的一张解析曲面。

    kind: plane / cylinder / torus / cone / sphere / other
    side: 圆柱/圆锥/球/环面上，法向指向轴（或管心圆）为 ``inner``（孔、凹圆角），否则 ``outer``。
    """

    kind: str
    side: str = ""
    radius: float = 0.0  # 圆柱/球半径；环面为管半径（minor）
    major: float = 0.0  # 环面中心圆半径
    semi_angle: float = 0.0  # 圆锥半角（度）
    axis_o: np.ndarray = field(default_factory=lambda: np.zeros(3))  # 轴上一点（环面/球为中心）
    axis_d: np.ndarray = field(default_factory=lambda: np.zeros(3))  # 轴向（平面为外法向）
    offset: float = 0.0  # 平面：n·x = offset
    area: float = 0.0
    points: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))
    n_faces: int = 0

    def extent_along(self, d) -> tuple[float, float]:
        s = self.points @ unit(d)
        return float(s.min()), float(s.max())

    def angular_coverage(self) -> tuple[float, float, float]:
        """绕轴的角度覆盖：(起始角, 终止角, 跨度)，单位度，基于参考方向 ``ref_dir``。"""
        e1, e2 = self.ref_axes()
        rel = self.points - self.axis_o
        ang = np.sort(np.degrees(np.arctan2(rel @ e2, rel @ e1)) % 360.0)
        if len(ang) == 0:
            return 0.0, 0.0, 0.0
        gaps = np.diff(np.concatenate([ang, [ang[0] + 360.0]]))
        k = int(np.argmax(gaps))
        start = float(ang[(k + 1) % len(ang)])
        span = 360.0 - float(gaps[k])
        return start, (start + span) % 360.0, span

    def ref_axes(self) -> tuple[np.ndarray, np.ndarray]:
        d = self.axis_d
        seed = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        e1 = unit(seed - np.dot(seed, d) * d)
        return e1, np.cross(d, e1)


def _classify_face(face) -> Surf:
    ad = BRepAdaptor_Surface(face)
    kind = ad.GetType()
    p, n = _face_normal(face)
    area, _ = _area(face)
    pts = _face_points(face)
    if kind == GeomAbs_Plane:
        return Surf("plane", axis_o=p, axis_d=n, offset=float(np.dot(n, p)), area=area, points=pts)
    if kind == GeomAbs_Cylinder:
        cyl = ad.Cylinder()
        o, d = _np(cyl.Axis().Location()), canon_dir(_np(cyl.Axis().Direction()))
        radial = (p - o) - np.dot(p - o, d) * d
        side = "inner" if np.dot(n, radial) < 0 else "outer"
        o = o - np.dot(o, d) * d  # 轴线上离原点最近的点，便于合并
        return Surf("cylinder", side, cyl.Radius(), axis_o=o, axis_d=d, area=area, points=pts)
    if kind == GeomAbs_Torus:
        tor = ad.Torus()
        o, d = _np(tor.Axis().Location()), canon_dir(_np(tor.Axis().Direction()))
        radial = unit((p - o) - np.dot(p - o, d) * d)
        tube_center = o + tor.MajorRadius() * radial
        side = "outer" if np.dot(n, p - tube_center) > 0 else "inner"
        return Surf(
            "torus", side, tor.MinorRadius(), tor.MajorRadius(),
            axis_o=o, axis_d=d, area=area, points=pts,
        )
    if kind == GeomAbs_Cone:
        cone = ad.Cone()
        apex = _np(cone.Apex())
        d = canon_dir(_np(cone.Axis().Direction()))
        radial = (p - apex) - np.dot(p - apex, d) * d
        side = "inner" if np.dot(n, radial) < 0 else "outer"
        return Surf(
            "cone", side, semi_angle=abs(math.degrees(cone.SemiAngle())),
            axis_o=apex, axis_d=d, area=area, points=pts,
        )
    if kind == GeomAbs_Sphere:
        sph = ad.Sphere()
        c = _np(sph.Location())
        side = "outer" if np.dot(n, p - c) > 0 else "inner"
        return Surf("sphere", side, sph.Radius(), axis_o=c, area=area, points=pts)
    return Surf("other", area=area, points=pts)


def _same_surface(a: Surf, b: Surf) -> bool:
    # 平面不合并：共面但分离的面（如两个凹槽底面）要分别计数
    if a.kind != b.kind or a.side != b.side or a.kind in ("other", "plane"):
        return False
    if abs(a.radius - b.radius) > _POS_EPS or abs(a.major - b.major) > _POS_EPS:
        return False
    if a.kind == "sphere":
        return np.linalg.norm(a.axis_o - b.axis_o) < _POS_EPS
    if not parallel(a.axis_d, b.axis_d):
        return False
    if a.kind == "cylinder":
        if line_distance(a.axis_o, a.axis_d, b.axis_o) >= _POS_EPS:
            return False
        # 同轴同径但轴向不相连的两段（如两端各一个盲孔）分开计数
        ta, tb = a.points @ a.axis_d, b.points @ a.axis_d
        return max(ta.min(), tb.min()) <= min(ta.max(), tb.max()) + _POS_EPS
    if a.kind == "cone":
        return abs(a.semi_angle - b.semi_angle) < 1e-3 and np.linalg.norm(a.axis_o - b.axis_o) < _POS_EPS
    return np.linalg.norm(a.axis_o - b.axis_o) < _POS_EPS  # torus


class Part:
    def __init__(self, shape: TopoDS_Shape):
        self.shape = shape
        BRepMesh_IncrementalMesh(shape, MESH_DEFLECTION, False, 0.1, True)
        self.solids = self._subshapes(TopAbs_SOLID)
        self.surfs: list[Surf] = []
        for face in self._subshapes(TopAbs_FACE):
            surf = _classify_face(TopoDS.Face(face))
            surf.n_faces = 1
            for group in self.surfs:
                if _same_surface(group, surf):
                    group.area += surf.area
                    group.points = np.vstack([group.points, surf.points])
                    group.n_faces += 1
                    break
            else:
                self.surfs.append(surf)
        # 贪心合并与面的顺序有关（A、C 不相连，B 把二者接起来）：反复合并直到不再变化
        merged = True
        while merged:
            merged = False
            for i in range(len(self.surfs)):
                for j in range(i + 1, len(self.surfs)):
                    a, b = self.surfs[i], self.surfs[j]
                    if _same_surface(a, b):
                        a.area += b.area
                        a.points = np.vstack([a.points, b.points])
                        a.n_faces += b.n_faces
                        del self.surfs[j]
                        merged = True
                        break
                if merged:
                    break
        pts = [s.points for s in self.surfs if len(s.points)]
        self.points = np.vstack(pts) if pts else np.zeros((0, 3))
        self._classifiers = [BRepClass3d_SolidClassifier(s) for s in self.solids]
        self._intersector = IntCurvesFace_ShapeIntersector()
        self._intersector.Load(shape, 1e-7)
        self._volume: float | None = None

    @classmethod
    def from_step(cls, path: str | Path) -> "Part":
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(path))
        if "RetDone" not in str(status):
            raise ValueError(f"STEP 读取失败: {path} ({status})")
        reader.TransferRoots()
        return cls(reader.OneShape())

    def _subshapes(self, kind) -> list:
        found = ShapeMap()
        TopExp.MapShapes_s(self.shape, kind, found)
        return [found.FindKey(i) for i in range(1, found.Extent() + 1)]

    # ---- 拓扑 / 全局量 ----
    @property
    def n_solids(self) -> int:
        return len(self.solids)

    @property
    def n_shells(self) -> int:
        return len(self._subshapes(TopAbs_SHELL))

    def is_valid(self) -> bool:
        return bool(BRepCheck_Analyzer(self.shape).IsValid())

    def volume(self) -> float:
        if self._volume is None:
            self._volume = _volume(self.shape)
        return self._volume

    def genus(self) -> int:
        """Euler–Poincaré: V − E + F − (L − F) = 2(S − G)，退化边不计。"""
        v = len(self._subshapes(TopAbs_VERTEX))
        e = sum(
            1 for edge in self._subshapes(TopAbs_EDGE)
            if not BRep_Tool.Degenerated_s(TopoDS.Edge(edge))
        )
        f = len(self._subshapes(TopAbs_FACE))
        loops = len(self._subshapes(TopAbs_WIRE))
        s = self.n_shells
        return int(round(s - (v - e + 2 * f - loops) / 2))

    def extents(self, axes: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """在正交坐标系 ``axes``（行向量，可为左手系）下的精确包围盒（世界坐标投影）。"""
        axes = np.asarray(axes, float)
        flip = np.linalg.det(axes) < 0
        rh = axes.copy()
        if flip:
            rh[2] = -rh[2]
        ax3 = gp_Ax3(gp_Pnt(0, 0, 0), _dir(rh[2]), _dir(rh[0]))
        trsf = gp_Trsf()
        trsf.SetTransformation(ax3)
        moved = BRepBuilderAPI_Transform(self.shape, trsf, True).Shape()
        box = Bnd_Box()
        BRepBndLib.AddOptimal_s(moved, box, False, False)
        lo, hi = _np(box.CornerMin()), _np(box.CornerMax())
        if flip:
            lo[2], hi[2] = -hi[2], -lo[2]
        return lo, hi

    # ---- 点 / 线 / 区域查询 ----
    def is_inside(self, p) -> bool:
        pnt = _pnt(p)
        for classifier in self._classifiers:
            classifier.Perform(pnt, 1e-7)
            if classifier.State() == TopAbs_IN:
                return True
        return False

    def line_intervals(self, origin, direction, t0: float, t1: float) -> list[tuple[float, float]]:
        """直线 origin + t·direction 在 [t0, t1] 内穿过材料的区间。"""
        o, d = np.asarray(origin, float), unit(direction)
        self._intersector.Perform(gp_Lin(_pnt(o), _dir(d)), t0, t1)
        ts = sorted(
            {t0, t1}
            | {self._intersector.WParameter(i) for i in range(1, self._intersector.NbPnt() + 1)}
        )
        out: list[tuple[float, float]] = []
        for a, b in zip(ts, ts[1:]):
            if b - a < 1e-9 or not self.is_inside(o + (a + b) / 2 * d):
                continue
            if out and abs(out[-1][1] - a) < 1e-9:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
        return out

    def common_volume(self, tool: TopoDS_Shape | None) -> float:
        if tool is None:
            return 0.0
        return _volume(BRepAlgoAPI_Common(self.shape, tool).Shape())

    def mirror_xor_volume(self, point, normal) -> float:
        trsf = gp_Trsf()
        trsf.SetMirror(gp_Ax2(_pnt(point), _dir(normal)))
        return self._xor_with(trsf)

    def rotate_xor_volume(self, point, direction, angle_deg: float) -> float:
        trsf = gp_Trsf()
        trsf.SetRotation(gp_Ax1(_pnt(point), _dir(direction)), math.radians(angle_deg))
        return self._xor_with(trsf)

    def _xor_with(self, trsf: gp_Trsf) -> float:
        moved = BRepBuilderAPI_Transform(self.shape, trsf, True).Shape()
        both = _volume(BRepAlgoAPI_Common(self.shape, moved).Shape())
        return 2 * self.volume() - 2 * both


def box_tool(origin, axes: np.ndarray, lo, hi) -> TopoDS_Shape | None:
    """坐标系 (origin, axes) 中 [lo, hi] 的长方体。axes 为正交行向量（可为左手系）。

    任一边长不为正时返回 None（空盒，common_volume 视为 0）。
    """
    axes = np.asarray(axes, float)
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    if np.any(hi - lo <= 1e-9):
        return None
    if np.linalg.det(axes) < 0:
        axes = np.array([axes[0], -axes[1], axes[2]])
        lo, hi = np.array([lo[0], -hi[1], lo[2]]), np.array([hi[0], -lo[1], hi[2]])
    corner = np.asarray(origin, float) + lo @ axes
    size = hi - lo
    return BRepPrimAPI_MakeBox(gp_Ax2(_pnt(corner), _dir(axes[2]), _dir(axes[0])), *map(float, size)).Shape()


def cylinder_tool(origin, direction, radius: float, height: float) -> TopoDS_Shape:
    return BRepPrimAPI_MakeCylinder(gp_Ax2(_pnt(origin), _dir(direction)), float(radius), float(height)).Shape()


def min_area_rect(points2d: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """二维点集的最小面积外接矩形：返回 (长边方向, 短边方向, [长, 短])。"""
    pts = np.unique(np.round(points2d, 6), axis=0)
    pts = pts[np.lexsort((pts[:, 1], pts[:, 0]))]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list = []
    for p in pts[::-1]:
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = np.array(lower[:-1] + upper[:-1])
    best = None
    for i in range(len(hull)):
        e = unit(hull[(i + 1) % len(hull)] - hull[i])
        if not np.any(e):
            continue
        n = np.array([-e[1], e[0]])
        a, b = hull @ e, hull @ n
        size = np.array([a.max() - a.min(), b.max() - b.min()])
        if best is None or size[0] * size[1] < best[0] - 1e-9:
            best = (size[0] * size[1], e, n, size)
    _, e, n, size = best
    if size[1] > size[0]:
        e, n, size = n, e, size[::-1]
    return e, n, size
