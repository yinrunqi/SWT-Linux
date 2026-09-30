"""装配 Workflow 会话：HTTP、语义引用解析、宏工具、运动自检。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import requests

from planner.workflow.errors import WorkflowError

from .refs import RefError, needs_edges, resolve_on_edges, resolve_on_faces, split_ref

CONSTRAINED = {0: "unknown", 1: "under", 2: "fully", 3: "over"}

# 关节保留的自由度：(空间, 平面)；平面列为 None 表示不是平面关节
JOINT_DOF = {
    "revolute": (1, 1),
    "slider": (1, 1),
    "cylindrical": (2, 1),
    "pin_slot": (2, 2),
    "planar": (3, 3),
    "ball": (3, None),
    "screw": (1, None),
}


def LINEAR_COUPLER_RATIO(ratio: float) -> tuple[float, float]:
    """b 的位移 / a 的位移 = ratio 时 SolidWorks 线性耦合的 (分子, 分母)。
    实测（smoke_mechanical_mates.py）：分子 1、分母 2 时 b 的位移是 a 的 2 倍。"""
    return 1.0, float(ratio)


def _round(value, digits=3):
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return [_round(item, digits) for item in value]
    return round(float(value), digits)


def angle_about_z(matrix: list[float]) -> float:
    r = np.array(matrix[:9], float).reshape(3, 3)
    return math.degrees(math.atan2(r[1, 0], r[0, 0]))


def translation_mm(matrix: list[float]) -> list[float]:
    return [float(matrix[9]) * 1000, float(matrix[10]) * 1000, float(matrix[11]) * 1000]


class AssemblySession:
    def __init__(
        self,
        base_url: str = "http://localhost:5100",
        timeout: float = 180,
        *,
        workflow: str = "v1",
        allowed_tools: set[str] | None = None,
    ):
        if workflow not in ("v1", "v2"):
            raise ValueError(f"未知 workflow {workflow}")
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()
        self.timeout = timeout
        self.workflow = workflow
        self.allowed_tools = allowed_tools
        self.aliases: dict[str, str] = {}
        self.mate_names: list[str] = []
        self.joints: list[dict] = []
        self.last_motion: dict | None = None
        self.saved = False
        self.has_assembly = False
        self.assembly_path = ""
        self.log: list[dict] = []
        self._handlers = {
            "joint": self.joint,
            "rack_pinion": self.rack_pinion,
            "screw_pair": self.screw_pair,
            "linear_coupler": self.linear_coupler,
            "close_loop": self.close_loop,
            "check_dof": self.check_dof,
            "get_assembly_state": self.get_assembly_state,
            "insert_component": self.insert_component,
            "ground_component": self.ground_component,
            "mate": self.mate,
            "delete_mate": self.delete_mate,
            "mount_on_shaft": self.mount_on_shaft,
            "revolute_joint": self.revolute_joint,
            "slider_joint": self.slider_joint,
            "mesh_gears": self.mesh_gears,
            "crank_slider": self.crank_slider,
            "check_interference": self.check_interference,
            "check_motion": self.check_motion,
            "save_assembly": self.save_assembly,
            "finish": self.finish,
        }

    # ------------------------------------------------------------ HTTP

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            if method == "GET":
                response = self.http.get(f"{self.base_url}{path}", timeout=self.timeout)
            else:
                response = self.http.post(f"{self.base_url}{path}", json=body or {}, timeout=self.timeout)
        except requests.RequestException as exc:
            raise WorkflowError("SW_UNAVAILABLE", str(exc)) from None
        try:
            return response.json()
        except ValueError:
            raise WorkflowError("SW_UNAVAILABLE", f"HTTP {response.status_code}: {response.text[:200]}") from None

    def _ok(self, data: dict, hint: str = "") -> dict:
        if not data.get("success"):
            raise WorkflowError("FEATURE_FAILED", str(data.get("message") or hint or "SolidWorks 调用失败"))
        return data.get("result", data)

    def asmwf(self, op: str, body: dict | None = None) -> dict:
        return self._ok(self._request("POST", f"/api/asmwf/{op}", body), op)

    def start_assembly(self, path: str) -> dict:
        data = self._ok(self._request("POST", "/api/assembly/new", {"savePath": path}))
        self.has_assembly = True
        self.assembly_path = path
        self.aliases.clear()
        self.mate_names.clear()
        self.joints.clear()
        self.last_motion = None
        self.saved = False
        return data

    def call(self, name: str, args: dict | None) -> dict:
        args = args or {}
        entry = {"tool": name, "args": args}
        try:
            if name not in self._handlers or (self.allowed_tools is not None and name not in self.allowed_tools):
                raise WorkflowError("TOOL_ARGS", f"未知工具 {name}")
            if name != "finish" and not self.has_assembly:
                raise WorkflowError("NO_PART", "还没有装配体，先新建")
            result = {"ok": True, **self._handlers[name](args)}
        except RefError as exc:
            result = {"ok": False, "error": {"code": "REF", "message": str(exc)}}
        except WorkflowError as exc:
            result = {"ok": False, "error": exc.to_dict()}
        except (KeyError, TypeError, ValueError) as exc:
            result = {"ok": False, "error": {"code": "TOOL_ARGS", "message": f"参数错误: {exc}"}}
        entry["result"] = result
        self.log.append(entry)
        return result

    # ------------------------------------------------------------ 名称与引用

    def resolve_name(self, name: str) -> str:
        if name in self.aliases:
            return self.aliases[name]
        components = self.asmwf("components").get("components") or []
        exact = [c["name"] for c in components if c["name"] == name]
        if exact:
            return exact[0]
        contains = [c["name"] for c in components if name.lower() in c["name"].lower()]
        if len(contains) == 1:
            return contains[0]
        raise WorkflowError("TOOL_ARGS", f"找不到组件 {name}")

    def faces(self, component: str, radius: float | None = None) -> list[dict]:
        body = {"component": self.resolve_name(component), "maxFaces": 80}
        if radius is not None:
            body["radius"] = radius
        return self.asmwf("component-faces", body).get("faces") or []

    def edges(self, component: str, kind: str = "all") -> list[dict]:
        body = {"component": self.resolve_name(component), "kind": kind, "maxEdges": 400}
        return self.asmwf("component-edges", body).get("edges") or []

    def resolve_ref(self, text: str) -> tuple[str, str]:
        component, selector = split_ref(text) if ":" in (text or "") else (text, "plane(front)")
        sw_name = self.resolve_name(component)
        if needs_edges(selector):
            return sw_name, resolve_on_edges(selector, self.edges(sw_name))
        parsed_radius = None
        try:
            from .refs import parse_selector
            sel = parse_selector(selector)
            if sel.get("kind") == "cyl" and (sel.get("d") or sel.get("diameter")):
                parsed_radius = float(sel.get("d") or sel["diameter"]) / 2000.0
        except Exception:
            parsed_radius = None
        return sw_name, resolve_on_faces(selector, self.faces(sw_name, parsed_radius))

    def _mate(self, kind: str, a: str, b: str, **kw) -> dict:
        ca, ra = self.resolve_ref(a)
        cb, rb = self.resolve_ref(b)
        body = {"type": kind, "a": {"component": ca, "ref": ra}, "b": {"component": cb, "ref": rb}, **kw}
        result = self.asmwf("mate", body)
        self.mate_names.append(result["name"])
        self.saved = False
        return {"name": result["name"], "type": kind, "a": f"{ca}:{ra}", "b": f"{cb}:{rb}"}

    def _rollback_mates(self, names: list[str]) -> None:
        for name in reversed(names):
            try:
                self.asmwf("delete-mate", {"name": name})
            except WorkflowError:
                pass
            if name in self.mate_names:
                self.mate_names.remove(name)

    # ------------------------------------------------------------ 观察

    def get_assembly_state(self, args: dict) -> dict:
        raw = self.asmwf("components").get("components") or []
        mates = self.asmwf("mates").get("mates") or []
        alias_of = {sw: alias for alias, sw in self.aliases.items()}
        components = []
        for item in raw:
            name = item["name"]
            faces = self.asmwf("component-faces", {"component": name, "maxFaces": 80}).get("faces") or []
            cylinders, planes, spheres = [], [], []
            for face in faces:
                if face.get("surfaceType") == "sphere" and face.get("radius") is not None:
                    spheres.append({
                        "ref": f"face:{face['index']}",
                        "diameter_mm": _round(float(face["radius"]) * 2000),
                        "center_mm": _round([v * 1000 for v in face.get("center") or [0, 0, 0]]),
                    })
                elif face.get("surfaceType") == "cylinder" and face.get("radius") is not None:
                    cylinders.append({
                        "ref": f"face:{face['index']}",
                        "diameter_mm": _round(float(face["radius"]) * 2000),
                        "axis": _round(face.get("axis")),
                        "axis_origin_mm": _round([v * 1000 for v in face.get("axisOrigin") or [0, 0, 0]]),
                    })
                elif face.get("surfaceType") == "plane":
                    planes.append({
                        "ref": f"face:{face['index']}",
                        "area_mm2": _round(float(face.get("area") or 0) * 1e6, 1),
                        "normal": _round(face.get("normal")),
                    })
            cylinders.sort(key=lambda c: c["diameter_mm"] or 0)
            planes.sort(key=lambda p: -(p["area_mm2"] or 0))
            summary = {
                "alias": alias_of.get(name),
                "name": name,
                "fixed": item.get("isFixed"),
                "dof": CONSTRAINED.get(int(item.get("constrainedStatus") or 0), item.get("constrainedStatus")),
                "origin_mm": _round(translation_mm(item.get("transform") or [0] * 12)),
                "cylinders": cylinders[:8],
                "planes": planes[:6],
            }
            if self.workflow == "v2" and spheres:
                summary["spheres"] = spheres[:4]
            components.append(summary)
        state = {
            "components": components,
            "mates": [
                {
                    "name": m.get("name"),
                    "type": m.get("type"),
                    "components": m.get("components"),
                    "gear": (
                        [m.get("gearRatioNumerator"), m.get("gearRatioDenominator"), m.get("reverse")]
                        if m.get("gearRatioNumerator") is not None else None
                    ),
                }
                for m in mates
            ],
            "saved": self.saved,
            "last_motion": None if not self.last_motion else {
                "passed": self.last_motion.get("passed"),
                "interference_frames": self.last_motion.get("interference_frames"),
                "stuck": self.last_motion.get("stuck"),
            },
        }
        if self.workflow == "v2":
            alias = {sw: al for al, sw in self.aliases.items()}
            state["joints"] = [
                {"kind": e["kind"], "a": alias.get(e["a"], e["a"]), "b": alias.get(e["b"], e["b"]),
                 "mates": e["mates"], **({"loop": True} if e.get("loop") else {})}
                for e in self.joints
            ]
        return state

    # ------------------------------------------------------------ 放置 / 配合

    def insert_component(self, args: dict) -> dict:
        part = str(args.get("part") or "")
        alias = str(args.get("name") or "").strip()
        if not part or not alias:
            raise WorkflowError("TOOL_ARGS", "需要 part 和 name")
        at = args.get("at") or [0, 0, 0]
        if len(at) != 3:
            raise WorkflowError("TOOL_ARGS", "at 必须是 [x,y,z] mm")
        xyz = [float(v) / 1000 for v in at]
        data = self._ok(self._request("POST", "/api/assembly/add-component", {
            "partPath": part, "x": xyz[0], "y": xyz[1], "z": xyz[2],
        }))
        sw_name = data.get("componentName") or data.get("result")
        if isinstance(sw_name, dict):
            sw_name = sw_name.get("componentName")
        if not sw_name:
            raise WorkflowError("FEATURE_FAILED", "插入组件后没有返回名称")
        angle = float(args.get("rotate_deg") or 0)
        if angle:
            self._ok(self._request("POST", "/api/assembly/set-component-pose", {
                "componentName": sw_name,
                "x": xyz[0], "y": xyz[1], "z": xyz[2],
                "axisX": 0, "axisY": 0, "axisZ": 1, "angleDeg": angle,
            }))
        self.aliases[alias] = sw_name
        self.saved = False
        return {"alias": alias, "component": sw_name, "at_mm": list(at), "rotate_deg": angle}

    def ground_component(self, args: dict) -> dict:
        name = self.resolve_name(str(args.get("component") or ""))
        self._ok(self._request("POST", "/api/assembly/fix-component", {"componentName": name}))
        self.saved = False
        return {"component": name, "fixed": True}

    def mate(self, args: dict) -> dict:
        kind = str(args.get("type") or "").lower()
        extra = {}
        if args.get("align"):
            extra["align"] = args["align"]
        if args.get("distance") is not None:
            extra["distance"] = float(args["distance"]) / 1000
        if args.get("angle_deg") is not None:
            extra["angleDeg"] = float(args["angle_deg"])
        if args.get("ratio_numerator") is not None:
            extra["ratioNumerator"] = float(args["ratio_numerator"])
            extra["ratioDenominator"] = float(args.get("ratio_denominator") or 1)
        if args.get("reverse") is not None:
            extra["reverse"] = bool(args["reverse"])
        a, b = str(args["a"]), str(args["b"])
        if self.workflow == "v2":
            kind = kind.replace("-", "_")
            extra.update(self._mechanical_args(kind, args))
            if kind == "lock":
                a = a if ":" in a else f"{a}:component"
                b = b if ":" in b else f"{b}:component"
        result = self._mate(kind, a, b, **extra)
        if self.workflow == "v2":
            self._record_joint(f"mate:{kind}", a, b, [result])
        return result

    @staticmethod
    def _mechanical_args(kind: str, args: dict) -> dict:
        extra: dict[str, Any] = {}
        if kind == "concentric" and args.get("lock_rotation"):
            extra["lockRotation"] = True
        if kind == "distance" and (args.get("distance_min") is not None or args.get("distance_max") is not None):
            low = float(args.get("distance_min") if args.get("distance_min") is not None else 0)
            high = float(args.get("distance_max") if args.get("distance_max") is not None else low)
            extra["distanceMin"] = low / 1000
            extra["distanceMax"] = high / 1000
            if args.get("distance") is None:
                extra["distance"] = low / 1000
        if kind == "rack_pinion":
            if args.get("pitch_diameter") is not None:
                extra["pitchDiameter"] = float(args["pitch_diameter"]) / 1000
            elif args.get("travel_per_rev") is not None:
                extra["travelPerRevolution"] = float(args["travel_per_rev"]) / 1000
        if kind == "screw" and args.get("lead") is not None:
            extra["lead"] = float(args["lead"]) / 1000
        if kind == "slot" and args.get("slot_constraint"):
            extra["slotConstraint"] = str(args["slot_constraint"])
        return extra

    def delete_mate(self, args: dict) -> dict:
        name = str(args.get("name") or "")
        self.asmwf("delete-mate", {"name": name})
        if name in self.mate_names:
            self.mate_names.remove(name)
        self._forget_mates([name])
        self.saved = False
        return {"deleted": name}

    # ------------------------------------------------------------ 关节登记

    def _component_of(self, ref: str) -> str:
        return self.resolve_name(split_ref(ref)[0] if ":" in ref else ref)

    def _record_joint(self, kind: str, a: str, b: str, mates: list[dict], **extra) -> dict:
        entry = {
            "kind": kind,
            "a": self._component_of(a),
            "b": self._component_of(b),
            "mates": [m["name"] for m in mates],
            **extra,
        }
        self.joints.append(entry)
        return entry

    def _forget_mates(self, names: list[str]) -> None:
        gone = set(names)
        kept = []
        for entry in self.joints:
            entry["mates"] = [m for m in entry["mates"] if m not in gone]
            if entry["mates"]:
                kept.append(entry)
        self.joints[:] = kept

    def _rollback(self, created: list[dict]) -> None:
        names = [item["name"] for item in created]
        self._rollback_mates(names)
        self._forget_mates(names)

    # ------------------------------------------------------------ 宏工具

    def mount_on_shaft(self, args: dict) -> dict:
        part, shaft = str(args["part"]), str(args["shaft"])
        diameter = args.get("diameter")
        cyl_a = f"{part}:cyl(d={diameter})" if diameter else f"{part}:cyl(i=0)"
        cyl_b = f"{shaft}:cyl(d={diameter})" if diameter else f"{shaft}:cyl(i=0)"
        keyed = bool(args.get("lock_rotation"))
        created = []
        try:
            if keyed and self.workflow == "v2":
                created.append(self._mate("concentric", cyl_a, cyl_b, align="aligned", lockRotation=True))
            else:
                created.append(self._mate("concentric", cyl_a, cyl_b, align="aligned"))
            offset = args.get("offset")
            if offset is None:
                created.append(self._mate("coincident", f"{part}:plane(front)", f"{shaft}:plane(front)", align="aligned"))
            else:
                created.append(self._mate(
                    "distance", f"{part}:plane(front)", f"{shaft}:plane(front)",
                    distance=float(offset) / 1000, align="aligned",
                ))
            if keyed and self.workflow == "v1":
                created.append(self._mate("gear", cyl_a, cyl_b, ratioNumerator=1, ratioDenominator=1, reverse=False))
        except Exception:
            self._rollback(created)
            raise
        self._record_joint("fixed" if keyed else "revolute", part, shaft, created)
        return {"mates": created}

    def revolute_joint(self, args: dict) -> dict:
        created = []
        try:
            created.append(self._mate("concentric", str(args["a"]), str(args["b"]), align="closest"))
            face_a = args.get("face_a") or f"{split_ref(args['a'])[0]}:plane(front)"
            face_b = args.get("face_b") or f"{split_ref(args['b'])[0]}:plane(front)"
            created.append(self._mate("coincident", face_a, face_b, align="aligned"))
        except Exception:
            self._rollback(created)
            raise
        self._record_joint("revolute", str(args["a"]), str(args["b"]), created)
        return {"mates": created}

    def slider_joint(self, args: dict) -> dict:
        created = []
        try:
            created.append(self._mate("concentric", str(args["a"]), str(args["b"]), align="closest"))
            created.append(self._mate("coincident", str(args["guide_a"]), str(args["guide_b"]), align="aligned"))
        except Exception:
            self._rollback(created)
            raise
        self._record_joint("slider", str(args["a"]), str(args["b"]), created)
        return {"mates": created}

    def _gear_tip_radius(self, component: str) -> tuple[str, float, list[float]]:
        sw = self.resolve_name(component)
        info = self.asmwf("component-faces", {"component": sw, "maxFaces": 400})
        faces = info.get("faces") or []
        cyls = [f for f in faces if f.get("surfaceType") == "cylinder" and f.get("radius")]
        if not cyls:
            raise WorkflowError("TOOL_ARGS", f"{component} 没有圆柱面，无法当作齿轮")
        tip = max(cyls, key=lambda f: float(f["radius"]))
        tf = info.get("transform") or [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
        origin = [float(tf[9]), float(tf[10]), float(tf[11])]
        return sw, float(tip["radius"]), origin

    def mesh_gears(self, args: dict) -> dict:
        a, b = str(args["a"]), str(args["b"])
        sw_a, r_a, o_a = self._gear_tip_radius(a)
        sw_b, r_b, o_b = self._gear_tip_radius(b)
        cd = math.hypot(o_a[0] - o_b[0], o_a[1] - o_b[1])
        teeth_a = args.get("teeth_a")
        teeth_b = args.get("teeth_b")
        module = args.get("module")
        if module is not None:
            module_m = float(module) / 1000
        else:
            module_m = max(1e-6, (r_a + r_b - cd) / 2)
        if teeth_a is None:
            teeth_a = max(10, int(round(2 * (r_a / module_m - 1))))
        if teeth_b is None:
            teeth_b = max(10, int(round(2 * (r_b / module_m - 1))))
        expected = module_m * (int(teeth_a) + int(teeth_b)) / 2
        if abs(cd - expected) > 0.0015:
            raise WorkflowError(
                "FEATURE_NO_EFFECT",
                f"中心距 {cd*1000:.2f} mm 与 m(z1+z2)/2={expected*1000:.2f} mm 差 {abs(cd-expected)*1000:.2f} mm，"
                "先把齿轮装到正确的轴上再啮合",
                center_distance_mm=round(cd * 1000, 3),
                expected_mm=round(expected * 1000, 3),
            )
        # 偶数齿从动轮错开半个齿距，避免齿顶对齿顶
        phase_applied = None
        if int(teeth_b) % 2 == 0:
            phase_applied = self._ok(self._request("POST", "/api/assembly/set-component-pose", {
                "componentName": sw_b,
                "x": o_b[0], "y": o_b[1], "z": o_b[2],
                "axisX": 0, "axisY": 0, "axisZ": 1,
                "angleDeg": 180.0 / int(teeth_b),
            }))
        reverse = True if args.get("reverse") is None else bool(args["reverse"])
        mate = self._mate(
            "gear", f"{a}:cyl(i=0)", f"{b}:cyl(i=0)",
            ratioNumerator=int(teeth_a), ratioDenominator=int(teeth_b), reverse=reverse,
        )
        self._record_joint("coupling:gear", a, b, [mate])
        return {
            "mate": mate,
            "teeth": [int(teeth_a), int(teeth_b)],
            "module_mm": _round(module_m * 1000),
            "center_distance_mm": _round(cd * 1000),
            "reverse": reverse,
            "phase_applied": phase_applied,
        }

    def crank_slider(self, args: dict) -> dict:
        frame, crank, rod, slider = args["frame"], args["crank"], args["rod"], args["slider"]
        fb = args.get("frame_bore") or args.get("crank_bore") or 10
        cb = args.get("crank_bore") or fb
        pin = args.get("crank_pin") or cb
        big = args.get("rod_big") or pin
        small = args.get("rod_small") or pin
        created = []
        try:
            created.append(self._mate(
                "concentric", f"{crank}:cyl(d={cb}, near=[0,0])", f"{frame}:cyl(d={fb})", align="closest",
            ))
            created.append(self._mate("coincident", f"{crank}:plane(front)", f"{frame}:plane(front)", align="aligned"))
            created.append(self._mate(
                "concentric", f"{rod}:cyl(d={big}, near=[0,0])", f"{crank}:cyl(d={pin}, near=[{args.get('throw', 25)},0])",
                align="closest",
            ))
            created.append(self._mate("distance", f"{rod}:plane(front)", f"{frame}:plane(front)",
                                     distance=0.008, align="aligned"))
            created.append(self._mate(
                "concentric", f"{rod}:cyl(d={small})", f"{slider}:cyl(d={args.get('slider_pin') or small})",
                align="closest",
            ))
            created.append(self._mate("coincident", f"{slider}:plane(right)", f"{frame}:plane(right)", align="aligned"))
        except Exception:
            self._rollback(created)
            raise
        self._record_joint("revolute", crank, frame, created[0:2])
        self._record_joint("revolute", rod, crank, created[2:4])
        self._record_joint("revolute", rod, slider, created[4:5])
        self._record_joint("slider", slider, frame, created[5:6])
        return {"mates": created}

    # ------------------------------------------------------------ v2：关节映射与耦合

    @staticmethod
    def _is_cylinder_ref(ref: str) -> bool:
        selector = split_ref(ref)[1].lower() if ":" in ref else ""
        return selector.startswith(("cyl", "bore"))

    def _statuses(self) -> dict[str, str]:
        return {
            c["name"]: CONSTRAINED.get(int(c.get("constrainedStatus") or 0), "unknown")
            for c in self.asmwf("components").get("components") or []
        }

    def _joint_mates(self, kind: str, args: dict, *, minimal: bool = False) -> list[dict]:
        a, b = str(args["a"]), str(args["b"])
        comp_a = split_ref(a)[0] if ":" in a else a
        comp_b = split_ref(b)[0] if ":" in b else b
        face_a = args.get("face_a") or f"{comp_a}:plane(front)"
        face_b = args.get("face_b") or f"{comp_b}:plane(front)"
        created: list[dict] = []
        try:
            if kind == "fixed":
                if ":" in a and ":" in b:
                    created.append(self._mate("concentric", a, b, align="closest", lockRotation=True))
                    if args.get("offset") is None:
                        created.append(self._mate("coincident", face_a, face_b, align="aligned"))
                    else:
                        created.append(self._mate("distance", face_a, face_b,
                                                  distance=float(args["offset"]) / 1000, align="aligned"))
                else:
                    created.append(self._mate("lock", f"{a}:component", f"{b}:component"))
            elif kind in ("revolute", "cylindrical"):
                created.append(self._mate("concentric", a, b, align="closest"))
                if kind == "revolute" and not minimal:
                    if args.get("offset") is None:
                        created.append(self._mate("coincident", face_a, face_b, align="aligned"))
                    else:
                        created.append(self._mate("distance", face_a, face_b,
                                                  distance=float(args["offset"]) / 1000, align="aligned"))
            elif kind == "slider":
                primary = "concentric" if self._is_cylinder_ref(a) and self._is_cylinder_ref(b) else "coincident"
                created.append(self._mate(primary, a, b, align="closest" if primary == "concentric" else "aligned"))
                if not minimal or primary == "coincident":
                    if not args.get("guide_a") or not args.get("guide_b"):
                        raise WorkflowError("TOOL_ARGS", "slider 需要 guide_a/guide_b（再约束一个面，只留沿一条轴的平移）")
                    created.append(self._mate("coincident", str(args["guide_a"]), str(args["guide_b"]), align="aligned"))
                if args.get("limit_min") is not None or args.get("limit_max") is not None:
                    if not args.get("travel_a") or not args.get("travel_b"):
                        raise WorkflowError("TOOL_ARGS", "行程限位需要 travel_a/travel_b（两个垂直于运动方向的面）")
                    low = float(args.get("limit_min") or 0) / 1000
                    high = float(args["limit_max"]) / 1000 if args.get("limit_max") is not None else low
                    created.append(self._mate("distance", str(args["travel_a"]), str(args["travel_b"]),
                                              distance=low, distanceMin=low, distanceMax=high))
            elif kind == "planar":
                created.append(self._mate("coincident", a, b, align="aligned"))
            elif kind == "ball":
                sa = a if ":" in a else f"{a}:sphere"
                sb = b if ":" in b else f"{b}:sphere"
                created.append(self._mate("concentric", sa, sb, align="closest"))
            elif kind == "pin_slot":
                created.append(self._mate("slot", a, b, slotConstraint=str(args.get("slot_constraint") or "free")))
                if not minimal:
                    created.append(self._mate("coincident", face_a, face_b, align="aligned"))
            else:
                raise WorkflowError("TOOL_ARGS", f"未知关节类型 {kind}")
        except Exception:
            self._rollback(created)
            raise
        return created

    def joint(self, args: dict) -> dict:
        kind = str(args.get("type") or "").lower()
        created = self._joint_mates(kind, args)
        entry = self._record_joint(kind, str(args["a"]), str(args["b"]), created)
        return {"joint": kind, "components": [entry["a"], entry["b"]], "mates": created}

    def close_loop(self, args: dict) -> dict:
        kind = str(args.get("type") or "revolute").lower()
        a_name = self._component_of(str(args["a"]))
        b_name = self._component_of(str(args["b"]))
        connected = self._connected_to_ground()
        warning = None
        if not (a_name in connected and b_name in connected):
            warning = "两端并非都已经连到固定件，这不是闭环关节；普通连接请用 joint"
        attempts = []
        for minimal in (bool(args.get("minimal", True)), False):
            try:
                created = self._joint_mates(kind, args, minimal=minimal)
            except WorkflowError as exc:
                attempts.append({"minimal": minimal, "error": exc.to_dict().get("message")})
                if minimal is False or not args.get("minimal", True):
                    raise
                continue
            over = [n for n, s in self._statuses().items() if s == "over"]
            if over:
                self._rollback(created)
                attempts.append({"minimal": minimal, "over_defined": over})
                continue
            entry = self._record_joint(kind, str(args["a"]), str(args["b"]), created, loop=True, minimal=minimal)
            return {
                "joint": kind,
                "minimal": minimal,
                "components": [entry["a"], entry["b"]],
                "mates": created,
                "attempts": attempts,
                **({"warning": warning} if warning else {}),
            }
        raise WorkflowError("FEATURE_FAILED", f"闭环关节无法建立: {attempts}")

    def _connected_to_ground(self) -> set[str]:
        components = self.asmwf("components").get("components") or []
        reached = {c["name"] for c in components if c.get("isFixed")}
        changed = True
        while changed:
            changed = False
            for entry in self.joints:
                pair = {entry["a"], entry["b"]}
                if pair & reached and not pair <= reached:
                    reached |= pair
                    changed = True
        return reached

    def _coupling_precheck(self, *names: str) -> list[str]:
        statuses = self._statuses()
        return [n for n in names if statuses.get(n) == "fully"]

    def rack_pinion(self, args: dict) -> dict:
        rack, pinion = str(args["rack"]), str(args["pinion"])
        if args.get("pitch_diameter") is not None:
            pitch = float(args["pitch_diameter"])
        elif args.get("module") is not None and args.get("teeth") is not None:
            pitch = float(args["module"]) * int(args["teeth"])
        else:
            raise WorkflowError("TOOL_ARGS", "rack_pinion 需要 pitch_diameter（mm）或 module + teeth")
        rack_ref = str(args.get("rack_edge") or f"{rack}:edge(line, longest)")
        pinion_ref = str(args.get("pinion_ref") or f"{pinion}:cyl(i=0)")
        locked = self._coupling_precheck(self.resolve_name(rack), self.resolve_name(pinion))
        if locked:
            raise WorkflowError("FEATURE_NO_EFFECT", f"{locked} 已完全约束；耦合要加在已有的滑动/转动关节上")
        mate = self._mate("rackpinion", rack_ref, pinion_ref, pitchDiameter=pitch / 1000,
                          reverse=bool(args.get("reverse", False)))
        self._record_joint("coupling:rack_pinion", rack_ref, pinion_ref, [mate])
        return {"mate": mate, "pitch_diameter_mm": pitch, "travel_per_rev_mm": _round(math.pi * pitch)}

    def _matching_cylinders(self, a: str, b: str) -> tuple[str, str]:
        ca = [f for f in self.faces(a) if f.get("surfaceType") == "cylinder" and f.get("radius")]
        cb = [f for f in self.faces(b) if f.get("surfaceType") == "cylinder" and f.get("radius")]
        pairs = [
            (abs(float(x["radius"]) - float(y["radius"])), float(x["radius"]), x, y)
            for x in ca for y in cb if abs(float(x["radius"]) - float(y["radius"])) < 3e-4
        ]
        if not pairs:
            raise WorkflowError("TOOL_ARGS", f"{a} 与 {b} 没有直径相同的圆柱面，请显式给引用")
        _, _, x, y = min(pairs, key=lambda item: (item[0], item[1]))
        return f"{a}:face:{x['index']}", f"{b}:face:{y['index']}"

    def screw_pair(self, args: dict) -> dict:
        screw, nut = str(args["screw"]), str(args["nut"])
        lead = float(args["lead"])
        if lead <= 0:
            raise WorkflowError("TOOL_ARGS", "lead（导程 mm/转）必须为正")
        if args.get("screw_ref") and args.get("nut_ref"):
            ref_a, ref_b = str(args["screw_ref"]), str(args["nut_ref"])
        else:
            ref_a, ref_b = self._matching_cylinders(screw, nut)
        mate = self._mate("screw", ref_a, ref_b, lead=lead / 1000, reverse=bool(args.get("reverse", False)))
        self._record_joint("screw", ref_a, ref_b, [mate])
        return {"mate": mate, "lead_mm": lead}

    def linear_coupler(self, args: dict) -> dict:
        a, b = str(args["a"]), str(args["b"])
        ratio = float(args.get("ratio") or 1.0)
        if ratio <= 0:
            raise WorkflowError("TOOL_ARGS", "ratio 必须为正（b 的位移 / a 的位移）")
        edge_a = str(args.get("edge_a") or f"{a}:edge(line, longest)")
        edge_b = str(args.get("edge_b") or f"{b}:edge(line, longest)")
        locked = self._coupling_precheck(self._component_of(edge_a), self._component_of(edge_b))
        if locked:
            raise WorkflowError("FEATURE_NO_EFFECT", f"{locked} 已完全约束；耦合要加在已有的滑动关节上")
        ca, ra = self.resolve_ref(edge_a)
        cb, rb = self.resolve_ref(edge_b)
        end_a = {"component": ca, "ref": ra}
        end_b = {"component": cb, "ref": rb}
        if args.get("reference"):
            end_a["referenceComponent"] = end_b["referenceComponent"] = self.resolve_name(str(args["reference"]))
        numerator, denominator = LINEAR_COUPLER_RATIO(ratio)
        result = self.asmwf("mate", {
            "type": "linearcoupler", "a": end_a, "b": end_b,
            "ratioNumerator": numerator, "ratioDenominator": denominator,
            "reverse": bool(args.get("reverse", False)),
        })
        self.mate_names.append(result["name"])
        self.saved = False
        mate = {"name": result["name"], "type": "linearcoupler", "a": f"{ca}:{ra}", "b": f"{cb}:{rb}"}
        self._record_joint("coupling:linear", edge_a, edge_b, [mate])
        return {"mate": mate, "ratio": ratio, "reference": result.get("referenceComponents")}

    def check_dof(self, args: dict) -> dict:
        components = self.asmwf("components").get("components") or []
        statuses = {c["name"]: CONSTRAINED.get(int(c.get("constrainedStatus") or 0), "unknown") for c in components}
        fixed = {c["name"] for c in components if c.get("isFixed")}
        # 固定件与 fixed 关节连成的刚体组算作一个构件
        parent = {c["name"]: c["name"] for c in components}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            if x in parent and y in parent:
                parent[find(x)] = find(y)

        ground = next(iter(fixed), None)
        for name in fixed:
            union(name, ground)
        for entry in self.joints:
            if entry["kind"] == "fixed":
                union(entry["a"], entry["b"])
        bodies = {find(n) for n in parent}
        joints, couplings, unknown = [], [], []
        for entry in self.joints:
            kind = entry["kind"]
            if kind == "fixed":
                continue
            if kind.startswith("coupling:"):
                couplings.append(kind.split(":", 1)[1])
            elif kind in JOINT_DOF:
                if find(entry["a"]) != find(entry["b"]):
                    joints.append(kind)
            else:
                unknown.append(kind)
        n = len(bodies)
        spatial = 6 * (n - 1) - sum(6 - JOINT_DOF[k][0] for k in joints) - len(couplings)
        planar_ok = all(JOINT_DOF[k][1] is not None for k in joints)
        planar = (3 * (n - 1) - sum(3 - JOINT_DOF[k][1] for k in joints) - len(couplings)) if planar_ok else None
        linked = self._connected_to_ground()
        floating = sorted(
            name for name in parent
            if name not in linked and not any(name in (e["a"], e["b"]) for e in self.joints)
        )
        report = {
            "bodies": n,
            "joints": joints,
            "couplings": couplings,
            "mobility_spatial": spatial,
            "mobility_planar": planar,
            "unknown_mates": unknown,
            "floating": floating,
            "over_defined": sorted(n for n, s in statuses.items() if s == "over"),
            "not_grounded": sorted(set(parent) - linked),
        }
        expected = args.get("expected_mobility")
        if expected is not None:
            actual = planar if planar is not None else spatial
            report["expected_mobility"] = int(expected)
            report["matches_expected"] = actual == int(expected)
        return report

    # ------------------------------------------------------------ 检查 / 收尾

    def check_interference(self, args: dict) -> dict:
        data = self._ok(self._request("POST", "/api/assembly/check-interference", {}))
        return {
            "interferenceCount": data.get("interferenceCount", 0),
            "totalVolume_mm3": _round(float(data.get("totalVolume") or 0) * 1e9, 4),
            "interferences": data.get("interferences") or [],
        }

    def check_motion(self, args: dict) -> dict:
        driver_alias = str(args["driver"])
        driver = self.resolve_name(driver_alias)
        if args.get("driver_ref"):
            _, ref = self.resolve_ref(str(args["driver_ref"]))
        else:
            ref = "bore:auto"
        sweep = float(args.get("sweep_deg") or 360)
        frames = max(3, int(args.get("frames") or 13))
        rpm = float(args.get("rpm") or 10)
        duration = abs(sweep) / (rpm * 6)  # deg / (rpm * 360/60)
        if duration <= 0:
            duration = 6
        self.asmwf("motion-drive", {
            "component": driver, "ref": ref, "rpm": rpm, "durationSeconds": duration,
        })
        times = [duration * i / (frames - 1) for i in range(frames)]
        sampled = self.asmwf("motion-sample", {"times": times, "interference": True})
        raw_frames = sampled.get("frames") or []
        components = self.asmwf("components").get("components") or []
        names = [c["name"] for c in components]
        t0 = {n: raw_frames[0]["transforms"][n] for n in names if n in raw_frames[0]["transforms"]}
        moved, stuck_expected = [], []
        expect_moving = [self.resolve_name(n) for n in (args.get("expect_moving") or [])]
        expect_fixed = [self.resolve_name(n) for n in (args.get("expect_fixed") or [])]
        mid = raw_frames[len(raw_frames) // 2]["transforms"]
        for name in names:
            first = t0[name]
            delta = max(
                sum(abs(float(a) - float(b)) for a, b in zip(first, other))
                for other in (mid[name], raw_frames[-1]["transforms"][name])
            )
            if delta > 1e-5:
                moved.append(name)
            elif name in expect_moving:
                stuck_expected.append(name)
        drifted = []
        for name in expect_fixed:
            first = t0[name]
            if any(
                sum(abs(float(a) - float(b)) for a, b in zip(first, frame["transforms"][name])) > 1e-4
                for frame in raw_frames
            ):
                drifted.append(name)
        inter_frames = []
        for index, frame in enumerate(raw_frames):
            inter = frame.get("interference") or {}
            count = int(inter.get("interferenceCount") or 0)
            if count:
                inter_frames.append({
                    "frame": index,
                    "time": frame.get("time"),
                    "count": count,
                    "volume_mm3": _round(float(inter.get("totalVolume") or 0) * 1e9, 4),
                    "pairs": [
                        item.get("components") for item in (inter.get("interferences") or [])
                    ],
                })
        driver_angles = [angle_about_z(frame["transforms"][driver]) for frame in raw_frames]
        kinematics = {"driver_angles_deg": [_round(v, 2) for v in driver_angles]}
        if expect_moving:
            other = expect_moving[0]
            other_angles = [angle_about_z(frame["transforms"][other]) for frame in raw_frames]
            d_in = (driver_angles[-1] - driver_angles[0]) or 1e-9
            kinematics["sample_ratio"] = _round((other_angles[-1] - other_angles[0]) / d_in, 4)
        passed = not inter_frames and not stuck_expected and not drifted
        report = {
            "passed": passed,
            "frames": len(raw_frames),
            "duration": duration,
            "interference_frames": inter_frames,
            "moved": moved,
            "stuck": stuck_expected,
            "drifted_fixed": drifted,
            "kinematics": kinematics,
        }
        self.last_motion = report
        return report

    def save_assembly(self, args: dict) -> dict:
        path = args.get("path") or self.assembly_path
        if path and path != self.assembly_path:
            data = self._ok(self._request("POST", "/api/assembly/save", {"savePath": path}))
        else:
            data = self._ok(self._request("POST", "/api/assembly/save", {}))
        self.saved = True
        self.assembly_path = path or self.assembly_path
        return {"path": self.assembly_path, "saved": True, **({} if not isinstance(data, dict) else {})}

    def finish(self, args: dict) -> dict:
        if not self.saved:
            return {"ok": False, "error": {"code": "NOT_SAVED", "message": "请先 save_assembly"}}
        if not self.last_motion or not self.last_motion.get("passed"):
            return {
                "ok": False,
                "error": {
                    "code": "MOTION_FAIL",
                    "message": "finish 前必须有一次通过的 check_motion（无穿模、预期运动件确实在动）",
                    "last_motion": self.last_motion,
                },
            }
        return {"ok": True, "summary": args.get("summary", ""), "last_motion": self.last_motion}
