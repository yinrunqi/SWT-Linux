"""装配 Workflow 会话：HTTP、语义引用解析、宏工具、运动自检。"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import requests

from planner.workflow.errors import WorkflowError

from .refs import RefError, resolve_on_faces, split_ref

CONSTRAINED = {0: "unknown", 1: "under", 2: "fully", 3: "over"}


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
    def __init__(self, base_url: str = "http://localhost:5100", timeout: float = 180):
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()
        self.timeout = timeout
        self.aliases: dict[str, str] = {}
        self.mate_names: list[str] = []
        self.last_motion: dict | None = None
        self.saved = False
        self.has_assembly = False
        self.assembly_path = ""
        self.log: list[dict] = []
        self._handlers = {
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
        self.last_motion = None
        self.saved = False
        return data

    def call(self, name: str, args: dict | None) -> dict:
        args = args or {}
        entry = {"tool": name, "args": args}
        try:
            if name not in self._handlers:
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

    def resolve_ref(self, text: str) -> tuple[str, str]:
        component, selector = split_ref(text) if ":" in (text or "") else (text, "plane(front)")
        sw_name = self.resolve_name(component)
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
            cylinders, planes = [], []
            for face in faces:
                if face.get("surfaceType") == "cylinder" and face.get("radius") is not None:
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
            components.append({
                "alias": alias_of.get(name),
                "name": name,
                "fixed": item.get("isFixed"),
                "dof": CONSTRAINED.get(int(item.get("constrainedStatus") or 0), item.get("constrainedStatus")),
                "origin_mm": _round(translation_mm(item.get("transform") or [0] * 12)),
                "cylinders": cylinders[:8],
                "planes": planes[:6],
            })
        return {
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
        return self._mate(kind, str(args["a"]), str(args["b"]), **extra)

    def delete_mate(self, args: dict) -> dict:
        name = str(args.get("name") or "")
        self.asmwf("delete-mate", {"name": name})
        if name in self.mate_names:
            self.mate_names.remove(name)
        self.saved = False
        return {"deleted": name}

    # ------------------------------------------------------------ 宏工具

    def mount_on_shaft(self, args: dict) -> dict:
        part, shaft = str(args["part"]), str(args["shaft"])
        diameter = args.get("diameter")
        cyl_a = f"{part}:cyl(d={diameter})" if diameter else f"{part}:cyl(i=0)"
        cyl_b = f"{shaft}:cyl(d={diameter})" if diameter else f"{shaft}:cyl(i=0)"
        created = []
        try:
            created.append(self._mate("concentric", cyl_a, cyl_b, align="aligned"))
            offset = args.get("offset")
            if offset is None:
                created.append(self._mate("coincident", f"{part}:plane(front)", f"{shaft}:plane(front)", align="aligned"))
            else:
                created.append(self._mate(
                    "distance", f"{part}:plane(front)", f"{shaft}:plane(front)",
                    distance=float(offset) / 1000, align="aligned",
                ))
            if args.get("lock_rotation"):
                created.append(self._mate("gear", cyl_a, cyl_b, ratioNumerator=1, ratioDenominator=1, reverse=False))
        except Exception:
            self._rollback_mates([item["name"] for item in created])
            raise
        return {"mates": created}

    def revolute_joint(self, args: dict) -> dict:
        created = []
        try:
            created.append(self._mate("concentric", str(args["a"]), str(args["b"]), align="closest"))
            face_a = args.get("face_a") or f"{split_ref(args['a'])[0]}:plane(front)"
            face_b = args.get("face_b") or f"{split_ref(args['b'])[0]}:plane(front)"
            created.append(self._mate("coincident", face_a, face_b, align="aligned"))
        except Exception:
            self._rollback_mates([item["name"] for item in created])
            raise
        return {"mates": created}

    def slider_joint(self, args: dict) -> dict:
        created = []
        try:
            created.append(self._mate("concentric", str(args["a"]), str(args["b"]), align="closest"))
            created.append(self._mate("coincident", str(args["guide_a"]), str(args["guide_b"]), align="aligned"))
        except Exception:
            self._rollback_mates([item["name"] for item in created])
            raise
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
            self._rollback_mates([item["name"] for item in created])
            raise
        return {"mates": created}

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
