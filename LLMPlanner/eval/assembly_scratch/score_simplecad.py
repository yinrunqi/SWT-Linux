"""给 SimpleCADAPI 装配基线（eval/eval_assembly_simplecad.py 的产物）打分：在 SolidWorks 里重建装配体，再用装配 rubric 与运动评测打分。

每个测例运行目录（<artifacts>/<case_id>/r<k>/）里只读 parts/<part_id>.step 和 assembly_export.json，不重跑 LLM。
重建（桥接）：
    1. 零件 STEP → SLDPRT（/api/import-step），按求解位姿插入组件，ground 的组件固定；
    2. 约束 → 配合，按连接器坐标系在零件上找几何：
       fixed → 锁定；revolute → 同轴圆柱面同心 + 垂直轴线的一对平面（重合或距离）；
       prismatic → 两对与滑动方向平行、彼此不平行的平面；gear → 两个同轴圆柱面上的齿轮配合（反向，比为节圆半径比）；
       belt → 同向齿轮配合；rack_pinion 不支持，记在 unsupported。
       每加一个配合都量一次各组件位姿，偏离求解位姿就换对齐 / 翻转方式重试，取偏离最小的一种；
    3. 存为 sw/<case_id>.SLDASM 与角色 → 组件名映射。
打分沿用 rescore_assemblies.rescore（装配 rubric + 运动/多位姿检查）。

    venv\\Scripts\\python.exe -X utf8 eval\\assembly_scratch\\score_simplecad.py --result eval\\assembly_simplecad_sonnet5.json `
        --out eval\\assembly_simplecad_sonnet5_scored.json
--rebuild 重新桥接（默认复用已存的 SLDASM），--rescore 忽略 --out 里已有的行重新打分。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import eval.assembly_scratch.rescore_assemblies as rescorer  # noqa: E402
from eval.eval_assembly_scratch import CASE_ALIASES, _load_case  # noqa: E402
from planner.assembly_workflow.session import AssemblySession  # noqa: E402
from planner.workflow.errors import WorkflowError  # noqa: E402

SW = "http://localhost:5100"
SW_EXE = r"D:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"
SERVICE_BAT = Path.home() / "start_sw_service.bat"
AXIS_TOL = math.sin(math.radians(1.0))
LINE_TOL_MM = 0.15
DRIFT_MM, DRIFT_DEG = 0.05, 0.1
DEFAULT_PLANES = {"right": 0, "top": 1, "front": 2}  # 基准面 → 法向所在的零件局部轴


# ---------------------------------------------------------------- SolidWorks 进程（须在桌面会话里启动）

def _schtask(name: str, command: str) -> None:
    subprocess.run(["schtasks", "/create", "/tn", name, "/tr", command, "/sc", "once", "/st", "23:59", "/it", "/f"],
                   capture_output=True)
    subprocess.run(["schtasks", "/run", "/tn", name], capture_output=True)


def restart_solidworks() -> None:
    print("  重启 SolidWorks 与服务", flush=True)
    for image in ("Chapter1.ConnectToSw.exe", "SLDWORKS.exe"):
        subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)
    time.sleep(5)
    _schtask("SwApp", f'"{SW_EXE}"')
    time.sleep(90)
    _schtask("SwService", str(SERVICE_BAT))
    for _ in range(60):
        time.sleep(2)
        try:
            if requests.get(f"{SW}/api/ping", timeout=3).ok:
                return
        except requests.RequestException:
            pass
    raise RuntimeError("服务重启后仍无响应")


rescorer.restart_solidworks = restart_solidworks


def post(path: str, body: dict, timeout: float = 300) -> dict:
    r = requests.post(f"{SW}{path}", json=body, timeout=timeout)
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"{path}: {data.get('message')}")
    return data


# ---------------------------------------------------------------- 几何

def frame(f: dict) -> tuple[np.ndarray, np.ndarray]:
    """(R, t)：R 的列是局部 x/y/z 轴在父坐标系中的方向，t 为原点（mm）。"""
    R = np.column_stack([np.array(f[k], float) for k in ("x_axis", "y_axis", "z_axis")])
    return R, np.array(f["origin"], float)


def axis_angle(R: np.ndarray) -> tuple[np.ndarray, float]:
    """旋转矩阵 → (单位轴, 角度°)，用四元数避免 180° 附近的奇异。"""
    m = R
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
    v = np.array([x, y, z])
    n = np.linalg.norm(v)
    if n < 1e-12:
        return np.array([0.0, 0.0, 1.0]), 0.0
    return v / n, math.degrees(2 * math.atan2(n, w))


def sw_pose(transform: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """SolidWorks Transform2.ArrayData：前 9 个按行是局部 x/y/z 轴的世界方向，9–11 为原点（m）。"""
    a = np.array(transform[:12], float)
    return a[:9].reshape(3, 3).T, a[9:12] * 1000


def rot_err_deg(Ra: np.ndarray, Rb: np.ndarray) -> float:
    c = (np.trace(Ra.T @ Rb) - 1) / 2
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def parallel(u: np.ndarray, v: np.ndarray) -> bool:
    return np.linalg.norm(np.cross(u, v)) < AXIS_TOL


def line_dist(p: np.ndarray, origin: np.ndarray, d: np.ndarray) -> float:
    w = p - origin
    return float(np.linalg.norm(w - np.dot(w, d) * d))


# ---------------------------------------------------------------- 桥接

class Bridge:
    def __init__(self, run_dir: Path, case: dict, export: dict, export_sha256: str):
        self.run_dir = run_dir
        self.case = case
        self.export = export
        self.export_sha256 = export_sha256
        self.sw_dir = run_dir / "sw"
        self.session = AssemblySession(base_url=SW, timeout=300)
        self.names: dict[str, str] = {}          # 角色 → SolidWorks 组件名
        self.pose: dict[str, tuple] = {}          # 角色 → 求解位姿 (R, t)
        self.faces: dict[str, dict] = {}
        self.connectors = export.get("connectors") or {}
        self.log: list[dict] = []

    # ---- 组件

    def import_parts(self) -> dict[str, Path]:
        out = {}
        (self.sw_dir / "parts").mkdir(parents=True, exist_ok=True)
        for comp in self.export["components"]:
            pid = comp.get("source_part_id") or comp["part_id"]
            if pid in out:
                continue
            step = self.run_dir / "parts" / f"{pid}.step"
            sld = self.sw_dir / "parts" / f"{pid}.SLDPRT"
            if not step.is_file():
                continue
            if not sld.is_file():
                post("/api/import-step", {"stepPath": str(step.resolve()), "savePath": str(sld.resolve())})
            out[pid] = sld
        return out

    def insert(self, sld: dict[str, Path]) -> None:
        grounded = set(self.export.get("grounded") or [])
        comps = sorted(self.export["components"], key=lambda c: c["role"] not in grounded)
        for comp in comps:
            role, pid = comp["role"], comp.get("source_part_id") or comp["part_id"]
            if pid not in sld:
                self.log.append({"component": role, "error": f"零件 {pid} 没有 STEP"})
                continue
            R, t = frame(comp["placement"])
            data = post("/api/assembly/add-component", {"partPath": str(sld[pid].resolve()), "x": 0, "y": 0, "z": 0})
            name = data.get("componentName") or (data.get("result") or {}).get("componentName")
            axis, angle = axis_angle(R)
            post("/api/assembly/set-component-pose", {
                "componentName": name, "x": t[0] / 1000, "y": t[1] / 1000, "z": t[2] / 1000,
                "axisX": axis[0], "axisY": axis[1], "axisZ": axis[2], "angleDeg": -angle,
            })
            self.names[role] = name
            self.pose[role] = (R, t)
        listing = {c["name"]: c for c in self.session.asmwf("components").get("components") or []}
        for role, name in self.names.items():
            fixed = bool(listing.get(name, {}).get("isFixed"))
            if role in grounded and not fixed:
                post("/api/assembly/fix-component", {"componentName": name})
            elif role not in grounded and fixed:
                post("/api/assembly/unfix-component", {"componentName": name})
        self.session.aliases = dict(self.names)
        drift = self.drift()
        self.log.append({"step": "insert", "max_drift": drift})

    def drift(self) -> dict:
        worst = {"mm": 0.0, "deg": 0.0, "role": None}
        by_name = {v: k for k, v in self.names.items()}
        for c in self.session.asmwf("components").get("components") or []:
            role = by_name.get(c["name"])
            if role is None or not c.get("transform"):
                continue
            R, t = sw_pose(c["transform"])
            R0, t0 = self.pose[role]
            mm, deg = float(np.linalg.norm(t - t0)), rot_err_deg(R, R0)
            if mm > worst["mm"] or deg > worst["deg"]:
                worst = {"mm": max(mm, worst["mm"]), "deg": max(deg, worst["deg"]), "role": role}
        worst["mm"], worst["deg"] = round(worst["mm"], 4), round(worst["deg"], 4)
        return worst

    # ---- 几何查询（零件局部坐标）

    def geom(self, role: str) -> dict:
        if role not in self.faces:
            data = self.session.asmwf("component-faces", {"component": self.names[role], "maxFaces": 400})
            cyl, pla = [], []
            for f in data.get("faces") or []:
                if f.get("surfaceType") == "cylinder" and f.get("axis"):
                    cyl.append({"ref": f"face:{f['index']}", "p": np.array(f["axisOrigin"]) * 1000,
                                "d": np.array(f["axis"]) / np.linalg.norm(f["axis"]), "r": f["radius"] * 1000,
                                "area": f.get("area") or 0})
                elif f.get("surfaceType") == "plane" and f.get("normal") and f.get("point"):
                    pla.append({"ref": f"face:{f['index']}", "q": np.array(f["point"]) * 1000,
                                "n": np.array(f["normal"]) / np.linalg.norm(f["normal"]), "area": f.get("area") or 0})
            self.faces[role] = {"cyl": cyl, "plane": pla}
        return self.faces[role]

    def connector(self, end: dict) -> tuple[str, np.ndarray, np.ndarray, np.ndarray]:
        role = end["component_id"]
        f = self.connectors[role][end["connector_id"]]["local_frame"]
        R, o = frame(f)
        return role, o, R[:, 2], R

    def coaxial_cylinder(self, role: str, origin: np.ndarray, d: np.ndarray, *, loose: bool = False) -> dict | None:
        best = None
        for c in self.geom(role)["cyl"]:
            if not parallel(c["d"], d):
                continue
            dist = line_dist(c["p"], origin, d)
            if dist > LINE_TOL_MM and not loose:
                continue
            key = (round(dist, 3), -c["area"])
            if best is None or key < best[0]:
                best = (key, c)
        return None if best is None else best[1]

    def planes(self, role: str) -> list[dict]:
        """候选平面：三个基准面（过零件原点）+ 零件的平面，局部坐标。"""
        out = [{"ref": f"plane:{name}", "q": np.zeros(3), "n": np.eye(3)[:, k], "default": True, "area": 0}
               for name, k in DEFAULT_PLANES.items()]
        out += [{**p, "default": False} for p in self.geom(role)["plane"]]
        return out

    def world(self, role: str, p: np.ndarray, *, vector: bool = False) -> np.ndarray:
        R, t = self.pose[role]
        return R @ p if vector else R @ p + t

    # ---- 配合

    def add_mate(self, kind: str, role_a: str, ref_a: str, role_b: str, ref_b: str, variants: list[dict]) -> dict:
        """依次试 variants，保留使位姿偏离最小的一种。"""
        tried, best = [], None
        for extra in variants:
            body = {"type": kind, "a": {"component": self.names[role_a], "ref": ref_a},
                    "b": {"component": self.names[role_b], "ref": ref_b}, **extra}
            try:
                name = self.session.asmwf("mate", body)["name"]
            except WorkflowError as exc:
                tried.append({**extra, "error": str(exc)[:200]})
                continue
            d = self.drift()
            tried.append({**extra, "drift": d})
            if d["mm"] <= DRIFT_MM and d["deg"] <= DRIFT_DEG:
                return {"mate": name, "type": kind, "a": f"{role_a}:{ref_a}", "b": f"{role_b}:{ref_b}", **extra,
                        "drift": d}
            score = d["mm"] + d["deg"]
            if best is None or score < best[0]:
                if best is not None:
                    self.session.asmwf("delete-mate", {"name": best[1]})
                best = (score, name, extra, d)
            else:
                self.session.asmwf("delete-mate", {"name": name})
            self._restore()
        if best is None:
            raise RuntimeError(f"{kind} 配合全部失败：{tried}")
        return {"mate": best[1], "type": kind, "a": f"{role_a}:{ref_a}", "b": f"{role_b}:{ref_b}", **best[2],
                "drift": best[3], "warning": "偏离求解位姿", "tried": tried}

    def _restore(self) -> None:
        """删掉失败的配合后，把被带动的组件放回求解位姿。"""
        for role, name in self.names.items():
            R, t = self.pose[role]
            axis, angle = axis_angle(R)
            try:
                post("/api/assembly/set-component-pose", {
                    "componentName": name, "x": t[0] / 1000, "y": t[1] / 1000, "z": t[2] / 1000,
                    "axisX": axis[0], "axisY": axis[1], "axisZ": axis[2], "angleDeg": -angle,
                })
            except Exception:  # noqa: BLE001
                pass
        grounded = set(self.export.get("grounded") or [])
        for role in grounded & set(self.names):
            try:
                post("/api/assembly/fix-component", {"componentName": self.names[role]})
            except Exception:  # noqa: BLE001
                pass

    def plane_mate(self, ra: str, pa: dict, rb: str, pb: dict) -> dict:
        """两平面平行：当前相距为 0 时重合，否则按当前距离做距离配合。"""
        na, nb = self.world(ra, pa["n"], vector=True), self.world(rb, pb["n"], vector=True)
        gap = float(np.dot(self.world(ra, pa["q"]) - self.world(rb, pb["q"]), nb))
        same = float(np.dot(na, nb)) > 0
        aligns = ["aligned", "anti"] if same else ["anti", "aligned"]
        if abs(gap) < 1e-3:
            return self.add_mate("coincident", ra, pa["ref"], rb, pb["ref"], [{"align": a} for a in aligns])
        variants = [{"align": a, "distance": abs(gap) / 1000, "flip": f} for a in aligns for f in (False, True)]
        return self.add_mate("distance", ra, pa["ref"], rb, pb["ref"], variants)

    def plane_pairs(self, ra: str, rb: str, ok_normal) -> list[tuple[float, dict, dict]]:
        pairs = []
        for pa in self.planes(ra):
            na = self.world(ra, pa["n"], vector=True)
            if not ok_normal(na):
                continue
            for pb in self.planes(rb):
                nb = self.world(rb, pb["n"], vector=True)
                if not parallel(na, nb):
                    continue
                gap = abs(float(np.dot(self.world(ra, pa["q"]) - self.world(rb, pb["q"]), nb)))
                # 优先基准面（不受面片划分影响），其次相距最近、面积大的实体面
                rank = (0 if pa["default"] and pb["default"] else 1 if pa["default"] or pb["default"] else 2,
                        round(gap, 3), -(pa["area"] + pb["area"]))
                pairs.append((rank, pa, pb))
        pairs.sort(key=lambda x: x[0])
        return pairs

    def revolute(self, c: dict) -> list[dict]:
        ra, oa, za, _ = self.connector(c["a"])
        rb, ob, zb, _ = self.connector(c["b"])
        mates = []
        cyl_a = self.coaxial_cylinder(ra, oa, za)
        cyl_b = self.coaxial_cylinder(rb, ob, zb)
        if cyl_a is None or cyl_b is None:
            raise RuntimeError(f"找不到与连接器轴线同轴的圆柱面：{ra if cyl_a is None else rb}")
        mates.append(self.add_mate("concentric", ra, cyl_a["ref"], rb, cyl_b["ref"], [{"align": "closest"}]))
        axis = self.world(ra, za, vector=True)
        pairs = self.plane_pairs(ra, rb, lambda n: parallel(n, axis))
        if not pairs:
            raise RuntimeError("找不到垂直于转轴的一对平面来定轴向位置")
        mates.append(self.plane_mate(ra, pairs[0][1], rb, pairs[0][2]))
        return mates

    def prismatic(self, c: dict) -> list[dict]:
        ra, _, za, _ = self.connector(c["a"])
        rb = c["b"]["component_id"]
        axis = self.world(ra, za, vector=True)
        pairs = self.plane_pairs(ra, rb, lambda n: abs(float(np.dot(n, axis))) < AXIS_TOL)
        if not pairs:
            raise RuntimeError("找不到与滑动方向平行的平面")
        first = pairs[0]
        n1 = self.world(ra, first[1]["n"], vector=True)
        second = next((p for p in pairs if not parallel(self.world(ra, p[1]["n"], vector=True), n1)), None)
        mates = [self.plane_mate(ra, first[1], rb, first[2])]
        if second is None:
            raise RuntimeError("只找到一对与滑动方向平行的平面，滑块只约束成平面副")
        mates.append(self.plane_mate(ra, second[1], rb, second[2]))
        return mates

    def fixed(self, c: dict) -> list[dict]:
        ra, rb = c["a"]["component_id"], c["b"]["component_id"]
        return [self.add_mate("lock", ra, "component", rb, "component", [{}])]

    def coupling(self, c: dict, ratio: tuple[float, float], reverse: bool) -> list[dict]:
        ra, oa, za, _ = self.connector(c["a"])
        rb, ob, zb, _ = self.connector(c["b"])
        cyl_a = self.coaxial_cylinder(ra, oa, za) or self.coaxial_cylinder(ra, oa, za, loose=True)
        cyl_b = self.coaxial_cylinder(rb, ob, zb) or self.coaxial_cylinder(rb, ob, zb, loose=True)
        if cyl_a is None or cyl_b is None:
            raise RuntimeError(f"齿轮配合找不到与轴线平行的圆柱面：{ra if cyl_a is None else rb}")
        return [self.add_mate("gear", ra, cyl_a["ref"], rb, cyl_b["ref"],
                              [{"ratioNumerator": ratio[0], "ratioDenominator": ratio[1], "reverse": reverse}])]

    def ordered_constraints(self) -> list[dict]:
        """fixed 先加；运动副按从机架出发的生成树顺序加，闭环的那条放最后（它的轴向面配合多半冗余）。"""
        allc = list(self.export.get("constraints") or [])
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.setdefault(x, x) != x:
                x = parent[x]
            return x

        fixed = [c for c in allc if c["kind"] == "fixed"]
        for c in fixed:
            parent[find(c["a"]["component_id"])] = find(c["b"]["component_id"])
        joints = [c for c in allc if c["kind"] in ("revolute", "prismatic")]
        reached = {find(g) for g in self.export.get("grounded") or []}
        tree, pending = [], list(joints)
        while True:
            frontier = set(reached)
            layer = []
            for c in pending:
                ca, cb = find(c["a"]["component_id"]), find(c["b"]["component_id"])
                if (ca in frontier) != (cb in frontier) and not (ca in reached and cb in reached):
                    reached |= {ca, cb}
                    layer.append(c)
            if not layer:
                break
            tree += layer
            pending = [c for c in pending if c not in layer]
        rest = [c for c in allc if c["kind"] not in ("fixed", "revolute", "prismatic")]
        return fixed + tree + pending + rest

    def constraints(self) -> None:
        for c in self.ordered_constraints():
            entry = {"constraint": c["id"], "kind": c["kind"], "a": c["a"]["component_id"], "b": c["b"]["component_id"]}
            try:
                if c["a"]["component_id"] not in self.names or c["b"]["component_id"] not in self.names:
                    raise RuntimeError("约束两端有组件没插入")
                if c["kind"] == "fixed":
                    entry["mates"] = self.fixed(c)
                elif c["kind"] == "revolute":
                    entry["mates"] = self.revolute(c)
                elif c["kind"] == "prismatic":
                    entry["mates"] = self.prismatic(c)
                elif c["kind"] == "gear":
                    entry["mates"] = self.coupling(c, (c["pitch_radius_a"], c["pitch_radius_b"]), True)
                elif c["kind"] == "belt":
                    entry["mates"] = self.coupling(c, (c["pulley_radius_a"], c["pulley_radius_b"]), False)
                else:
                    entry["unsupported"] = True
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"{type(exc).__name__}: {exc}"[:400]
            self.log.append(entry)

    def build(self) -> dict:
        self.sw_dir.mkdir(parents=True, exist_ok=True)
        post("/api/close-all-documents", {})
        sld = self.import_parts()
        path = self.sw_dir / f"{self.case['case_id']}.SLDASM"
        self.session.start_assembly(str(path.resolve()))
        self.insert(sld)
        self.constraints()
        self.session.asmwf("mates")
        final = self.drift()
        self.session.save_assembly({})
        post("/api/close-all-documents", {})
        n = [e for e in self.log if "constraint" in e]
        summary = {
            "assembly": str(path),
            "aliases": self.names,
            "constraints": len(n),
            "translated": sum(1 for e in n if e.get("mates")),
            "failed": [f"{e['constraint']}({e['kind']}): {e['error']}" for e in n if e.get("error")],
            "unsupported": [f"{e['constraint']}({e['kind']})" for e in n if e.get("unsupported")],
            "off_pose_mates": sum(1 for e in n for m in e.get("mates") or [] if m.get("warning")),
            "final_drift": final,
        }
        (self.sw_dir / "bridge.json").write_text(
            json.dumps({"export_sha256": self.export_sha256, "summary": summary, "log": self.log},
                       ensure_ascii=False, indent=1, default=str),
            encoding="utf-8")
        (self.sw_dir / f"{self.case['case_id']}.aliases.json").write_text(json.dumps(self.names), encoding="utf-8")
        return summary


# ---------------------------------------------------------------- 主流程

def export_sha(run_dir: Path) -> str | None:
    path = run_dir / "assembly_export.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def score_run(run: dict, art: Path, rebuild: bool) -> dict:
    cid, k = run["case_id"], run["repeat"]
    run_dir = art / cid / f"r{k}"
    case = _load_case(CASE_ALIASES[cid])
    parts = run.get("parts") or []
    part_rate = run.get("part_pass_rate", 0.0)
    asm = run.get("assembly") or {}
    row = {
        "case_id": cid, "condition": "gen", "repeat": k, "workflow": "simplecadapi", "model": run.get("model"),
        "part_pass_rate": part_rate,
        "parts": [{key: p.get(key) for key in ("part_id", "status", "accepted", "score", "llm_calls", "token_usage",
                                               "elapsed_s")} for p in parts],
        "agent": {key: asm.get(key) for key in ("status", "error", "llm_calls", "tool_counts", "token_usage",
                                                "elapsed_s", "delivered", "export_ok", "export_detail")},
    }
    if asm.get("status") == "error":
        return {**row, "assembly_score": None, "motion_score": None, "e2e": False,
                "infra_error": f"装配智能体中断：{asm.get('error')}"}
    export_path = run_dir / "assembly_export.json"
    sha = export_sha(run_dir)
    row["export_sha256"] = sha
    if sha is None or not asm.get("export"):
        return {**row, "assembly_score": 0.0, "motion_score": 0.0, "e2e": False, "note": "没有可读的装配包"}
    if asm.get("export_sha256") and asm["export_sha256"] != sha:
        return {**row, "assembly_score": None, "motion_score": None, "e2e": False,
                "infra_error": "assembly_export.json 与生成时记录的哈希不一致（可能是旧产物）"}
    export = json.loads(export_path.read_text(encoding="utf-8"))
    if export.get("problems"):
        return {**row, "assembly_score": 0.0, "motion_score": 0.0, "e2e": False,
                "note": "装配包未通过完整性检查：" + "；".join(export["problems"])}
    bridge_file = run_dir / "sw" / "bridge.json"
    sldasm = run_dir / "sw" / f"{cid}.SLDASM"
    cached = json.loads(bridge_file.read_text(encoding="utf-8")) if bridge_file.is_file() else {}
    if rebuild or not sldasm.is_file() or cached.get("export_sha256") != sha:
        for attempt in range(2):
            try:
                summary = Bridge(run_dir, case, export, sha).build()
                break
            except requests.RequestException as exc:
                if attempt:
                    return {**row, "assembly_score": None, "motion_score": None, "e2e": False,
                            "infra_error": f"bridge: {exc}"}
                restart_solidworks()
    else:
        summary = cached["summary"]
    scored = rescorer.rescore_with_retry(sldasm.resolve(), case, summary["aliases"])
    motion = scored.get("motion") or {}
    row.update({
        "bridge": summary,
        "rubric": scored.get("rubric"),
        "motion": motion,
        "missing_roles": scored.get("missing_roles"),
        "assembly_score": scored.get("assembly_score"),
        "motion_score": scored.get("motion_score"),
        "e2e": part_rate == 1.0 and bool(motion.get("passed")),
    })
    if scored.get("service_hung"):
        row["infra_error"] = scored["service_hung"]
    return row


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r.get("infra_error")]
    if not ok:
        return {}
    return {
        "n": len(ok),
        "part_pass_rate": round(sum(r["part_pass_rate"] for r in ok) / len(ok), 3),
        "assembly_score": round(sum(r["assembly_score"] or 0 for r in ok) / len(ok), 2),
        "motion_score": round(sum(r["motion_score"] or 0 for r in ok) / len(ok), 2),
        "e2e": sum(1 for r in ok if r["e2e"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--result", required=True, help="eval_assembly_simplecad.py 的汇总 JSON")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cases", default="", help="只打这些 case_id（逗号分隔）")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--rescore", action="store_true")
    args = ap.parse_args()
    data = json.loads(Path(args.result).read_text(encoding="utf-8"))
    art = ROOT / data["artifacts"]
    wanted = {t.strip() for t in args.cases.split(",") if t.strip()}
    out = Path(args.out)
    rows = [] if args.rescore or not out.is_file() else json.loads(out.read_text(encoding="utf-8"))["rows"]
    current = {(r["case_id"], r["repeat"]): (r.get("assembly") or {}).get("status") != "error"
               and export_sha(art / r["case_id"] / f"r{r['repeat']}") for r in data["runs"] if "case_id" in r}
    # 测例重跑后装配包变了（或从有变无），旧分数作废
    done = {(r["case_id"], r["repeat"]) for r in rows
            if not r.get("infra_error") and "export_sha256" in r
            and r["export_sha256"] == current.get((r["case_id"], r["repeat"]))}
    rows = [r for r in rows if (r["case_id"], r["repeat"]) in done]

    def write():
        out.write_text(json.dumps({"model": data.get("model"), "summary": summarize(rows), "rows": rows},
                                  ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    for run in data["runs"]:
        if "case_id" not in run or (wanted and run["case_id"] not in wanted):
            continue
        if (run["case_id"], run["repeat"]) in done:
            continue
        print(f"=== {run['case_id']} r{run['repeat']}", flush=True)
        row = score_run(run, art, args.rebuild)
        rows = [r for r in rows if (r["case_id"], r["repeat"]) != (row["case_id"], row["repeat"])] + [row]
        b = row.get("bridge") or {}
        print(f"  parts={row['part_pass_rate']:.2f} rubric={row['assembly_score']} motion={row['motion_score']} "
              f"e2e={row['e2e']} mates={b.get('translated')}/{b.get('constraints')} failed={len(b.get('failed') or [])}",
              flush=True)
        write()
    write()
    print(json.dumps(summarize(rows), ensure_ascii=False))


if __name__ == "__main__":
    main()
