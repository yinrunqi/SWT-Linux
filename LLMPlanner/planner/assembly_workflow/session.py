"""装配 Workflow 会话：HTTP、语义引用解析、宏工具、运动自检。"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import requests

from planner.workflow.errors import WorkflowError

from .refs import RefError, needs_edges, resolve_on_edges, resolve_on_faces, split_ref

# swConstrainedStatus_e
CONSTRAINED = {
    1: "unknown", 2: "under", 3: "fully", 4: "over", 5: "no_solution", 6: "invalid", 7: "autosolve_off",
}

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


# v3：这些工具成功后附带一份精简的装配增量（活动度、冗余、过定义、悬空件），不用再调 get_assembly_state
MUTATING_TOOLS = {
    "insert_component", "set_component_pose", "ground_component", "unfix_component", "mate", "delete_mate",
    "joint", "close_loop", "mount_on_shaft", "revolute_joint", "slider_joint", "mesh_gears",
    "crank_slider", "rack_pinion", "screw_pair", "linear_coupler",
}


def _parse_json_string(value: Any) -> Any:
    """模型常把数组 / 对象参数写成 JSON 字符串（如 poses="[{...}]"）；解析不出数组或对象时原样保留。"""
    if not isinstance(value, str) or value.strip()[:1] not in ("[", "{"):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    return parsed if isinstance(parsed, (list, dict)) else value


def _plain(exc: WorkflowError) -> str:
    """去掉 str(WorkflowError) 开头的 [CODE]，重新包装报错时不重复错误码。"""
    text = str(exc)
    return text.split("] ", 1)[1] if text.startswith("[") and "] " in text else text


def angle_about_z(matrix: list[float]) -> float:
    r = np.array(matrix[:9], float).reshape(3, 3)
    return math.degrees(math.atan2(r[1, 0], r[0, 0]))


def translation_mm(matrix: list[float]) -> list[float]:
    return [float(matrix[9]) * 1000, float(matrix[10]) * 1000, float(matrix[11]) * 1000]


class AssemblySession:
    def __init__(
        self,
        base_url: str = "http://localhost:5100",
        timeout: float = 900,
        *,
        workflow: str = "v1",
        allowed_tools: set[str] | None = None,
        speed_optimizations: bool = False,
    ):
        if workflow not in ("v1", "v2", "v3"):
            raise ValueError(f"未知 workflow {workflow}")
        self.base_url = base_url.rstrip("/")
        self.http = requests.Session()
        self.timeout = timeout
        self.workflow = workflow
        # v3 = v2 的配合与宏工具 + 增量状态、秩法自由度、自动闭环、位姿自检等（见 docs）
        self.v2plus = workflow in ("v2", "v3")
        self.v3 = workflow == "v3"
        self.speed_optimizations = bool(speed_optimizations and self.v3)
        self.allowed_tools = allowed_tools
        self.aliases: dict[str, str] = {}
        self.mate_names: list[str] = []
        self.joints: list[dict] = []
        self.last_motion: dict | None = None
        self.last_pose: dict | None = None
        self.last_dof: dict | None = None
        self._face_cache: dict[tuple, list[dict]] = {}
        self._pair_deletes: dict[frozenset, int] = {}
        self._structure_version = 0
        self._full_state_version = -1
        self._full_state_cache: dict | None = None
        self._known_components: dict[str, dict] = {}
        self._known_mates: dict[str, dict] = {}
        self._known_fixed: set[str] = set()
        self._last_full_delta: dict = {}
        self.state_cache_hits = 0
        self.state_cache_misses = 0
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
            "set_component_pose": self.set_component_pose,
            "ground_component": self.ground_component,
            "unfix_component": self.unfix_component,
            "mate": self.mate,
            "delete_mate": self.delete_mate,
            "mount_on_shaft": self.mount_on_shaft,
            "revolute_joint": self.revolute_joint,
            "slider_joint": self.slider_joint,
            "mesh_gears": self.mesh_gears,
            "crank_slider": self.crank_slider,
            "check_interference": self.check_interference,
            "check_motion": self.check_motion,
            "check_pose": self.check_pose,
            "save_assembly": self.save_assembly,
            "view_assembly": self.view_assembly,
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
            message = str(data.get("message") or hint or "SolidWorks 调用失败")
            if any(marker in message for marker in (
                "RPC 服务器不可用", "RPC server is unavailable", "0x800706BA",
                "已与其客户端断开连接", "has disconnected from its clients",
            )):
                raise WorkflowError("SW_UNAVAILABLE", message)
            raise WorkflowError("FEATURE_FAILED", message)
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
        self.last_motion = self.last_pose = self.last_dof = None
        self._face_cache.clear()
        self._pair_deletes.clear()
        self._reset_state_tracking()
        self.saved = False
        return data

    def _reset_state_tracking(self) -> None:
        self._structure_version = 0
        self._full_state_version = -1
        self._full_state_cache = None
        self._known_components.clear()
        self._known_mates.clear()
        self._known_fixed.clear()
        self._last_full_delta = {}
        self.state_cache_hits = 0
        self.state_cache_misses = 0

    def state_bundle(self, *, force: bool = False) -> dict:
        """返回同一时刻的 components + mates；speed 模式按结构版本缓存。

        旧模式保持两次历史 API 调用，避免无 flag 时改变请求序列。speed 模式使用
        /api/asmwf/state，把两次 COM/HTTP 边界合并为一次。
        """
        if (
            self.speed_optimizations
            and not force
            and self._full_state_cache is not None
            and self._full_state_version == self._structure_version
        ):
            self.state_cache_hits += 1
            return self._full_state_cache
        if self.speed_optimizations:
            bundle = self.asmwf("state")
            self.state_cache_misses += 1
        else:
            bundle = {
                "components": self.asmwf("components").get("components") or [],
                "mates": self.asmwf("mates").get("mates") or [],
            }
        normalized = {
            "components": list(bundle.get("components") or []),
            "mates": list(bundle.get("mates") or []),
        }
        if self.speed_optimizations:
            self._full_state_cache = normalized
            self._full_state_version = self._structure_version
            self._sync_incremental_state(normalized["components"], normalized["mates"])
            modes, components, mates = self._modes(
                normalized["components"], normalized["mates"],
            )
            self._last_full_delta = self._delta_from(modes, components, mates)
        return normalized

    def _sync_incremental_state(self, components: list[dict], mates: list[dict]) -> None:
        self._known_components = {
            str(item.get("name")): {
                "component": str(item.get("name")),
                "fixed": bool(item.get("isFixed")),
            }
            for item in components
            if item.get("name")
        }
        self._known_fixed = {
            str(item.get("name")) for item in components if item.get("name") and item.get("isFixed")
        }
        self._known_mates = {}
        for item in mates:
            name = str(item.get("name") or "")
            if not name:
                continue
            self._known_mates[name] = {
                "name": name,
                "type": item.get("type") or item.get("mateType"),
                "mateType": item.get("mateType"),
                "components": [str(c) for c in item.get("components") or [] if c],
                "errorCode": int(item.get("errorCode") or 0),
                "suppressed": bool(item.get("suppressed")),
            }

    @staticmethod
    def _component_from_ref(value: Any) -> str | None:
        if not isinstance(value, str) or not value:
            return None
        return value.split(":", 1)[0]

    def _remember_mates(self, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._remember_mates(item)
            return
        if not isinstance(value, dict):
            return
        name = str(value.get("name") or "")
        a = self._component_from_ref(value.get("a"))
        b = self._component_from_ref(value.get("b"))
        components = [str(c) for c in value.get("components") or [] if c]
        if not components:
            components = [c for c in (a, b) if c]
        if name and components:
            self._known_mates[name] = {
                "name": name,
                "type": value.get("type") or value.get("joint"),
                "components": components,
                "errorCode": 0,
                "suppressed": False,
            }
        for key in ("mates", "created", "mate"):
            if key in value:
                self._remember_mates(value[key])

    def _record_incremental_mutation(self, name: str, args: dict, result: dict) -> None:
        if name == "insert_component":
            component = str(result.get("component") or "")
            alias = str(result.get("alias") or args.get("name") or "")
            if component:
                self._known_components[component] = {
                    "component": component,
                    "alias": alias or self._alias(component),
                    "fixed": False,
                }
        elif name == "ground_component":
            component = str(result.get("component") or "")
            if component:
                self._known_fixed.add(component)
                self._known_components.setdefault(component, {"component": component})["fixed"] = True
        elif name == "unfix_component":
            component = str(result.get("component") or "")
            self._known_fixed.discard(component)
            if component:
                self._known_components.setdefault(component, {"component": component})["fixed"] = False
        elif name == "delete_mate":
            deleted = str(result.get("deleted") or args.get("name") or "")
            self._known_mates.pop(deleted, None)
        else:
            self._remember_mates(result)

    def _incremental_floating(self) -> list[str]:
        components = set(self._known_components) | set(self.aliases.values())
        reached = set(self._known_fixed)
        changed = True
        while changed:
            changed = False
            for mate in self._known_mates.values():
                if mate.get("errorCode") or mate.get("suppressed"):
                    continue
                pair = {str(c) for c in mate.get("components") or [] if c}
                if len(pair) < 2:
                    continue
                if pair & reached and not pair <= reached:
                    reached |= pair
                    changed = True
        return sorted(self._alias(name) for name in components - reached)

    def compact_state_digest(self) -> dict:
        """不访问 SolidWorks 的有界当前状态，供每轮 compact prompt 与早停使用。"""
        mates = [
            {
                "name": item["name"],
                "type": item.get("type"),
                "components": [self._alias(c) for c in item.get("components") or []],
            }
            for item in self._known_mates.values()
        ]
        mates.sort(key=lambda item: item["name"])
        validation = {
            "motion": None if not self.last_motion else bool(self.last_motion.get("passed")),
            "pose": None if not self.last_pose else bool(self.last_pose.get("passed")),
            "dof": None if not self.last_dof else bool(self.last_dof.get("matches_expected")),
        }
        digest = {
            "version": self._structure_version,
            "components": [
                {"alias": alias, "component": component, "fixed": component in self._known_fixed}
                for alias, component in sorted(self.aliases.items())
            ],
            "mates": mates[-160:],
            "floating": self._incremental_floating(),
            "saved": self.saved,
            "validation": validation,
            "full_state_age_mutations": (
                None if self._full_state_version < 0
                else self._structure_version - self._full_state_version
            ),
        }
        for key in ("mobility", "redundant", "over_defined", "broken_mates"):
            if key in self._last_full_delta:
                digest[key] = self._last_full_delta[key]
        return digest

    def progress_snapshot(self) -> dict:
        digest = self.compact_state_digest()
        return {
            "components": len(digest["components"]),
            "mates": len(digest["mates"]),
            "fixed": sum(bool(item.get("fixed")) for item in digest["components"]),
            "floating": len(digest["floating"]),
            "broken": len(digest.get("broken_mates") or []),
            "over_defined": len(digest.get("over_defined") or []),
            "mobility": digest.get("mobility"),
            "saved": bool(digest["saved"]),
            "motion_passed": digest["validation"]["motion"] is True,
            "pose_passed": digest["validation"]["pose"] is True,
            "dof_passed": digest["validation"]["dof"] is True,
            "version": digest["version"],
        }

    def save_best_snapshot(self, path: str | Path) -> str:
        """保存当前装配并复制一个不会改变活动文档路径的最佳状态副本。"""
        was_saved = self.saved
        try:
            self.save_assembly({})
            source = Path(self.assembly_path)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        finally:
            self.saved = was_saved
        return str(target)

    def restore_best_snapshot(self, path: str | Path, final_path: str | Path) -> None:
        """关闭当前文档，把最佳副本恢复为正式结果，再重新打开。"""
        best = Path(path)
        if not best.is_file():
            raise WorkflowError("FEATURE_FAILED", f"最佳状态文件不存在: {best}")
        final = Path(final_path)
        self._ok(self._request("POST", "/api/close-all-documents", {}))
        shutil.copy2(best, final)
        self._ok(self._request("POST", "/api/assembly/open", {"assemblyPath": str(final)}))
        self.has_assembly = True
        self.assembly_path = str(final)
        self._face_cache.clear()
        self._reset_state_tracking()
        self.saved = True
        # 组件名通常保持不变；刷新一次用于校验并重建增量摘要。
        self.state_bundle(force=True)

    def call(self, name: str, args: dict | None) -> dict:
        args = {key: _parse_json_string(value) for key, value in (args or {}).items()}
        entry = {"tool": name, "args": args}
        cache_hits_before = self.state_cache_hits
        try:
            if name not in self._handlers or (self.allowed_tools is not None and name not in self.allowed_tools):
                raise WorkflowError("TOOL_ARGS", f"未知工具 {name}")
            if name != "finish" and not self.has_assembly:
                raise WorkflowError("NO_PART", "还没有装配体，先新建")
            result = {"ok": True, **self._handlers[name](args)}
            if self.speed_optimizations and name == "get_assembly_state":
                result["state_cache_hit"] = self.state_cache_hits > cache_hits_before
            if self.v3 and name in MUTATING_TOOLS:
                # 改了结构，之前的运动 / 位姿 / 自由度检查都不再代表当前装配体
                self.last_motion = self.last_pose = self.last_dof = None
                if self.speed_optimizations:
                    self._structure_version += 1
                    self._record_incremental_mutation(name, args, result)
                    self._full_state_cache = None
                    result["after"] = self.compact_state_digest()
                else:
                    result["after"] = self._delta()
        except RefError as exc:
            result = {"ok": False, "error": {"code": "REF", "message": str(exc)}}
        except WorkflowError as exc:
            result = {"ok": False, "error": exc.to_dict()}
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            message = f"参数错误: {exc}"
            for key, value in args.items():
                if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
                    try:
                        json.loads(value)
                    except ValueError as bad:
                        message += f"；参数 {key} 应是数组或对象，收到的是不合法的 JSON 字符串（{bad}）"
            result = {"ok": False, "error": {"code": "TOOL_ARGS", "message": message}}
        entry["result"] = result
        self.log.append(entry)
        return result

    # ------------------------------------------------------------ 名称与引用

    def resolve_name(self, name: str) -> str:
        if name in self.aliases:
            return self.aliases[name]
        if self.speed_optimizations and name in self._known_components:
            return name
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
        if not self.v3:
            return self.asmwf("component-faces", body).get("faces") or []
        # 面几何是零件局部坐标，装配里只变位姿，不变面；齿轮有上千个面，每次重查很慢
        key = (body["component"], radius)
        if key not in self._face_cache:
            self._face_cache[key] = self.asmwf("component-faces", body).get("faces") or []
        return self._face_cache[key]

    def _face_summary(self, name: str) -> dict:
        cylinders, planes, spheres = [], [], []
        for face in self.faces(name):
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
        out = {"cylinders": cylinders[:8], "planes": planes[:6]}
        if spheres:
            out["spheres"] = spheres[:4]
        return out

    def _alias(self, sw_name: str) -> str:
        return next((a for a, s in self.aliases.items() if s == sw_name), sw_name)

    def _modes(self, components: list[dict] | None = None, mates: list[dict] | None = None) -> tuple:
        from geomcheck.assembly_rubric import mobility_modes

        if components is None or mates is None:
            if self.speed_optimizations:
                bundle = self.state_bundle()
                components = bundle["components"]
                mates = bundle["mates"]
            else:
                components = self.asmwf("components").get("components") or []
                mates = self.asmwf("mates").get("mates") or []
        good = [m for m in mates if not m.get("suppressed") and int(m.get("errorCode") or 0) == 0]
        names = [c["name"] for c in components]
        fixed = {c["name"] for c in components if c.get("isFixed")}
        return mobility_modes(names, fixed, good), components, mates

    def _delta(self) -> dict:
        try:
            modes, components, mates = self._modes()
        except WorkflowError as exc:
            return {"error": exc.to_dict().get("message")}
        return self._delta_from(modes, components, mates)

    def _delta_from(self, modes: dict, components: list[dict], mates: list[dict]) -> dict:
        reached = self._grounded_by_mates(components, mates)
        return {
            "mobility": modes["mobility"],
            "redundant": modes["redundant"],
            "over_defined": sorted(self._alias(c["name"]) for c in components
                                   if int(c.get("constrainedStatus") or 0) in (4, 5, 6)),
            "broken_mates": [m.get("name") for m in mates if int(m.get("errorCode") or 0) != 0],
            "floating": sorted(self._alias(c["name"]) for c in components if c["name"] not in reached),
        }

    @staticmethod
    def _grounded_by_mates(components: list[dict], mates: list[dict]) -> set[str]:
        from geomcheck.assembly_rubric import COUPLINGS

        reached = {c["name"] for c in components if c.get("isFixed")}
        changed = True
        while changed:
            changed = False
            for m in mates:
                comps = [c for c in (m.get("components") or []) if c]
                if len(comps) < 2 or m.get("mateType") in COUPLINGS or int(m.get("errorCode") or 0):
                    continue
                if set(comps) & reached and not set(comps) <= reached:
                    reached |= set(comps)
                    changed = True
        return reached

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
        try:
            return sw_name, resolve_on_faces(selector, self.faces(sw_name, parsed_radius))
        except RefError as exc:
            if not self.v3:
                raise
            faces = self._face_summary(sw_name)
            cyl = [f"{c['ref']} d={c['diameter_mm']} at={c['axis_origin_mm'][:2]}" for c in faces["cylinders"]]
            pln = [f"{p['ref']} n={p['normal']}" for p in faces["planes"]]
            raise RefError(f"{exc}；{component} 的圆柱面：{cyl or '无'}；平面：{pln or '无'}"
                           "（at 为零件局部 XY，mm，可写 cyl(d=..., near=[x,y])）") from None

    def _mate(self, kind: str, a: str, b: str, **kw) -> dict:
        ca, ra = self.resolve_ref(a)
        cb, rb = self.resolve_ref(b)
        end_a = {"component": ca, "ref": ra}
        end_b = {"component": cb, "ref": rb}
        reference_a = kw.pop("referenceComponentA", None)
        reference_b = kw.pop("referenceComponentB", None)
        if reference_a:
            end_a["referenceComponent"] = self.resolve_name(str(reference_a))
        if reference_b:
            end_b["referenceComponent"] = self.resolve_name(str(reference_b))
        body = {"type": kind, "a": end_a, "b": end_b, **kw}
        result = self.asmwf("mate", body)
        self.mate_names.append(result["name"])
        self.saved = False
        return {"name": result["name"], "type": kind, "a": f"{ca}:{ra}", "b": f"{cb}:{rb}"}

    def _mate_side(self, name: str) -> float | None:
        """(a 面上一点 − b 面上一点)·b 面法向，单位 m。"""
        for mate in self.asmwf("mates").get("mates") or []:
            if mate.get("name") != name:
                continue
            ents = [e.get("params") for e in mate.get("entities") or []]
            if len(ents) < 2 or any(p is None or len(p) < 6 for p in ents):
                return None
            pa, pb, nb = np.array(ents[0][:3]), np.array(ents[1][:3]), np.array(ents[1][3:6])
            return float(np.dot(pa - pb, nb / (np.linalg.norm(nb) or 1)))
        return None

    def _offset_mate(self, face_a: str, face_b: str, offset_mm: float) -> dict:
        """a 面落在 b 面法向正侧 offset mm 处（负值落在反侧）。
        距离配合本身不分正负，SolidWorks 按求解结果随便选一侧，所以建好后量一下，反了就翻转重建。"""
        want = float(offset_mm) / 1000
        mate = self._mate("distance", face_a, face_b, distance=abs(want), align="aligned")
        if abs(want) < 1e-9:
            return mate
        side = self._mate_side(mate["name"])
        if side is not None and side * want < 0:
            self._rollback_mates([mate["name"]])
            mate = self._mate("distance", face_a, face_b, distance=abs(want), align="aligned", flip=True)
            side = self._mate_side(mate["name"])
            if side is not None and side * want < 0:
                self._rollback_mates([mate["name"]])
                raise WorkflowError("FEATURE_NO_EFFECT", f"{face_a} 无法放到 {face_b} 的 {offset_mm} mm 一侧")
        return mate

    def _rollback_mates(self, names: list[str]) -> None:
        for name in reversed(names):
            try:
                self.asmwf("delete-mate", {"name": name})
            except WorkflowError:
                pass
            if name in self.mate_names:
                self.mate_names.remove(name)

    # ------------------------------------------------------------ 观察

    def view_assembly(self, args: dict) -> dict:
        from planner.sw_views import capture_grid
        from planner.workflow.tools import IMAGE_KEY

        if not self.aliases and not (self.asmwf("components").get("components") or []):
            raise WorkflowError("TOOL_ARGS", "装配体里还没有组件，截图看不到东西")
        highlight = args.get("highlight") or []
        if isinstance(highlight, str):
            highlight = [highlight]
        names, unknown = [], []
        for item in highlight:
            try:
                names.append(self.resolve_name(str(item)))
            except WorkflowError:
                unknown.append(str(item))
        image, meta = capture_grid(self._request, "assembly", args.get("views"), highlight=names)
        result = {**meta, "note": "截图附在下一条消息里"}
        if names:
            result["highlighted"] = names
        missing = unknown + list(meta.get("missing_highlight") or [])
        result.pop("missing_highlight", None)
        if missing:
            result["highlight_not_found"] = missing
        result[IMAGE_KEY] = image
        return result

    def get_assembly_state(self, args: dict) -> dict:
        if self.v3:
            return self._state_v3(args)
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
            if self.v2plus and spheres:
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
        if self.v2plus:
            alias = {sw: al for al, sw in self.aliases.items()}
            state["joints"] = [
                {"kind": e["kind"], "a": alias.get(e["a"], e["a"]), "b": alias.get(e["b"], e["b"]),
                 "mates": e["mates"], **({"loop": True} if e.get("loop") else {})}
                for e in self.joints
            ]
        return state

    def _state_v3(self, args: dict) -> dict:
        """默认只给组件状态与配合；面列表在 insert_component 时已经返回过，要再看时用 faces / components。"""
        modes, raw, mates = self._modes()
        if self.speed_optimizations:
            self._last_full_delta = self._delta_from(modes, raw, mates)
        wanted = {self.resolve_name(n) for n in args.get("components") or []}
        with_faces = bool(args.get("faces"))
        components = []
        for item in raw:
            name = item["name"]
            summary = {
                "alias": self._alias(name) if name in self.aliases.values() else None,
                "name": name,
                "fixed": item.get("isFixed"),
                "dof": CONSTRAINED.get(int(item.get("constrainedStatus") or 0), item.get("constrainedStatus")),
                "origin_mm": _round(translation_mm(item.get("transform") or [0] * 12)),
                "rot_z_deg": _round(angle_about_z(item.get("transform") or [1, 0, 0, 0, 1, 0, 0, 0, 1]), 2),
            }
            free = modes["free"].get(name)
            if free:
                summary["free"] = free
            if with_faces or name in wanted:
                summary.update(self._face_summary(name))
            components.append(summary)
        return {
            "components": components,
            "mates": [
                {
                    "name": m.get("name"), "type": m.get("type"),
                    "components": [self._alias(c) for c in m.get("components") or [] if c],
                    **({"error": m.get("errorCode")} if int(m.get("errorCode") or 0) else {}),
                    **({"gear": [m.get("gearRatioNumerator"), m.get("gearRatioDenominator"), m.get("reverse")]}
                       if m.get("gearRatioNumerator") is not None else {}),
                }
                for m in mates
            ],
            "mobility": modes["mobility"],
            "redundant": modes["redundant"],
            "saved": self.saved,
            "last_motion": None if not self.last_motion else {"passed": self.last_motion.get("passed")},
            "last_pose": None if not self.last_pose else {"passed": self.last_pose.get("passed")},
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
            # set-component-pose 的物理转向与 angleDeg 相反（行向量约定，见 mesh_gears）；rotate_deg 按绕 +Z 逆时针
            self._ok(self._request("POST", "/api/assembly/set-component-pose", {
                "componentName": sw_name,
                "x": xyz[0], "y": xyz[1], "z": xyz[2],
                "axisX": 0, "axisY": 0, "axisZ": 1, "angleDeg": -angle,
            }))
        self.aliases[alias] = sw_name
        self.saved = False
        out = {"alias": alias, "component": sw_name, "at_mm": list(at), "rotate_deg": angle}
        if self.v3:
            out["faces"] = self._face_summary(sw_name)
        return out

    def set_component_pose(self, args: dict) -> dict:
        name = self.resolve_name(str(args.get("component") or ""))
        at = args.get("at")
        axis = args.get("axis") or [0, 0, 1]
        if not isinstance(at, list) or len(at) != 3:
            raise WorkflowError("TOOL_ARGS", "at 必须是 [x,y,z] mm")
        if not isinstance(axis, list) or len(axis) != 3:
            raise WorkflowError("TOOL_ARGS", "axis 必须是 [x,y,z]")
        data = self._ok(self._request("POST", "/api/assembly/set-component-pose", {
            "componentName": name,
            "x": float(at[0]) / 1000,
            "y": float(at[1]) / 1000,
            "z": float(at[2]) / 1000,
            "axisX": float(axis[0]),
            "axisY": float(axis[1]),
            "axisZ": float(axis[2]),
            # C# 使用行向量约定，物理旋转方向与 API angleDeg 相反。
            "angleDeg": -float(args.get("angle_deg") or 0),
        }))
        self.saved = False
        self._face_cache.clear()
        return {
            "component": name,
            "at_mm": [float(v) for v in at],
            "axis": [float(v) for v in axis],
            "angle_deg": float(args.get("angle_deg") or 0),
            **({} if not isinstance(data, dict) else data),
        }

    def ground_component(self, args: dict) -> dict:
        name = self.resolve_name(str(args.get("component") or ""))
        self._ok(self._request("POST", "/api/assembly/fix-component", {"componentName": name}))
        self.saved = False
        return {"component": name, "fixed": True}

    def unfix_component(self, args: dict) -> dict:
        name = self.resolve_name(str(args.get("component") or ""))
        self._ok(self._request("POST", "/api/assembly/unfix-component", {"componentName": name}))
        self.saved = False
        return {"component": name, "fixed": False}

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
        if args.get("flip") is not None:
            extra["flip"] = bool(args["flip"])
        if args.get("reference_a"):
            extra["referenceComponentA"] = args["reference_a"]
        if args.get("reference_b"):
            extra["referenceComponentB"] = args["reference_b"]
        a, b = str(args["a"]), str(args["b"])
        if self.v2plus:
            kind = kind.replace("-", "_")
            extra.update(self._mechanical_args(kind, args))
            if kind == "lock":
                a = a if ":" in a else f"{a}:component"
                b = b if ":" in b else f"{b}:component"
        try:
            result = self._mate(kind, a, b, **extra)
        except WorkflowError as exc:
            if kind == "distance" and "errStatus=5" in str(exc):
                raise WorkflowError(exc.code, f"{_plain(exc)}。距离配合不分正负，SolidWorks 可能把 a 放到了 b 的另一侧；"
                                              f"{'去掉' if extra.get('flip') else '加上'} flip=true 换到另一侧再试。") from None
            raise
        if kind == "distance" and abs(extra.get("distance") or 0) > 1e-9:
            side = self._mate_side(result["name"])
            if side is not None:
                result["side_mm"] = round(side * 1000, 3)
        if self.v2plus:
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
        pair = None
        if self.v3:
            if self.speed_optimizations:
                mate = self._known_mates.get(name)
                if mate is None:
                    mate = next((m for m in self.state_bundle()["mates"] if m.get("name") == name), None)
            else:
                mate = next((m for m in self.asmwf("mates").get("mates") or [] if m.get("name") == name), None)
            comps = [c for c in (mate or {}).get("components") or [] if c]
            pair = frozenset(comps) if len(comps) >= 2 else None
        self.asmwf("delete-mate", {"name": name})
        if name in self.mate_names:
            self.mate_names.remove(name)
        self._forget_mates([name])
        self.saved = False
        out = {"deleted": name}
        if pair:
            self._pair_deletes[pair] = self._pair_deletes.get(pair, 0) + 1
            if self._pair_deletes[pair] >= 3:
                out["warning"] = (f"{'/'.join(sorted(self._alias(c) for c in pair))} 之间的配合已经删了 "
                                  f"{self._pair_deletes[pair]} 次。反复加删说明思路不对：先 check_dof 看哪个零件"
                                  "多了或少了自由度，再决定要哪种关节，不要换个面再试同一种配合。")
        return out

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
            if keyed and self.v2plus:
                created.append(self._mate("concentric", cyl_a, cyl_b, align="aligned", lockRotation=True))
            else:
                created.append(self._mate("concentric", cyl_a, cyl_b, align="aligned"))
            offset = args.get("offset")
            if offset is None:
                created.append(self._mate("coincident", f"{part}:plane(front)", f"{shaft}:plane(front)", align="aligned"))
            else:
                created.append(self._offset_mate(f"{part}:plane(front)", f"{shaft}:plane(front)", offset))
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

    def _gear_tip_radius(self, component: str) -> tuple[str, float, list[float], list[float]]:
        sw = self.resolve_name(component)
        info = self.asmwf("component-faces", {"component": sw, "maxFaces": 400})
        faces = info.get("faces") or []
        cyls = [f for f in faces if f.get("surfaceType") == "cylinder" and f.get("radius")]
        if not cyls:
            raise WorkflowError("TOOL_ARGS", f"{component} 没有圆柱面，无法当作齿轮")
        tip = max(cyls, key=lambda f: float(f["radius"]))
        tf = info.get("transform") or [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
        origin = [float(tf[9]), float(tf[10]), float(tf[11])]
        return sw, float(tip["radius"]), origin, tf

    def _mesh_internal(self, args: dict) -> dict:
        a, b = str(args["a"]), str(args["b"])
        if args.get("teeth_a") is None or args.get("teeth_b") is None or args.get("module") is None:
            raise WorkflowError("TOOL_ARGS", "内啮合必须给 teeth_a、teeth_b、module")
        za, zb, module_m = int(args["teeth_a"]), int(args["teeth_b"]), float(args["module"]) / 1000
        if zb <= za:
            raise WorkflowError("TOOL_ARGS", "内啮合时 b 是内齿圈，teeth_b 应大于 teeth_a")
        sw_a, _, o_a, _ = self._gear_tip_radius(a)
        sw_b, _, o_b, tf_b = self._gear_tip_radius(b)
        cd = math.hypot(o_a[0] - o_b[0], o_a[1] - o_b[1])
        expected = module_m * (zb - za) / 2
        if abs(cd - expected) > 0.0015:
            raise WorkflowError(
                "FEATURE_NO_EFFECT",
                f"中心距 {cd*1000:.2f} mm 与 m(z2−z1)/2={expected*1000:.2f} mm 差 {abs(cd-expected)*1000:.2f} mm，"
                "先把齿轮装到正确的轴上再啮合",
                center_distance_mm=round(cd * 1000, 3),
                expected_mm=round(expected * 1000, 3),
            )
        # 齿圈局部 0° 方向是轮齿。找离中心连线最近的齿圈齿槽，按分度圆弧长相等换算到外齿轮上，
        # 转外齿轮 a 让它的齿落进该齿槽（齿圈不动）。角度约定同外啮合分支。
        pitch_a, pitch_b = 2 * math.pi / za, 2 * math.pi / zb
        theta = math.atan2(o_a[1] - o_b[1], o_a[0] - o_b[0])
        alpha_b = -math.radians(angle_about_z(tf_b))
        frac = ((alpha_b + pitch_b / 2 - theta) / pitch_b) % 1.0
        alpha_a = theta + frac * pitch_a
        phase_applied = self._ok(self._request("POST", "/api/assembly/set-component-pose", {
            "componentName": sw_a,
            "x": o_a[0], "y": o_a[1], "z": o_a[2],
            "axisX": 0, "axisY": 0, "axisZ": 1,
            "angleDeg": -math.degrees(alpha_a),
        }))
        reverse = False if args.get("reverse") is None else bool(args["reverse"])
        mate = self._mate(
            "gear", f"{a}:cyl(i=0)", f"{b}:cyl(i=0)",
            ratioNumerator=za, ratioDenominator=zb, reverse=reverse,
        )
        self._record_joint("coupling:gear", a, b, [mate])
        return {
            "mate": mate,
            "internal": True,
            "teeth": [za, zb],
            "module_mm": _round(module_m * 1000),
            "center_distance_mm": _round(cd * 1000),
            "reverse": reverse,
            "phase_applied": phase_applied,
        }

    def mesh_gears(self, args: dict) -> dict:
        if args.get("internal"):
            return self._mesh_internal(args)
        a, b = str(args["a"]), str(args["b"])
        sw_a, r_a, o_a, tf_a = self._gear_tip_radius(a)
        sw_b, r_b, o_b, _ = self._gear_tip_radius(b)
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
        # 齿轮零件在局部 0° 方向上是齿。按中心连线方向和 a 当前转角摆 b，
        # 让 a 的齿正对 b 的齿槽；中心连线不沿 X 时只错半个齿距不够。
        # set-component-pose 的角度与 Transform 读出的角度同号，物理转向与之相反（行向量约定）。
        pitch_a, pitch_b = 2 * math.pi / int(teeth_a), 2 * math.pi / int(teeth_b)
        theta = math.atan2(o_b[1] - o_a[1], o_b[0] - o_a[0])
        alpha_a = -math.radians(angle_about_z(tf_a))
        phase_a = ((theta - alpha_a) / pitch_a) % 1.0
        alpha_b = theta + math.pi - pitch_b * (0.5 - phase_a)
        phase_applied = self._ok(self._request("POST", "/api/assembly/set-component-pose", {
            "componentName": sw_b,
            "x": o_b[0], "y": o_b[1], "z": o_b[2],
            "axisX": 0, "axisY": 0, "axisZ": 1,
            "angleDeg": -math.degrees(alpha_b),
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
            created.append(self._offset_mate(f"{rod}:plane(front)", f"{frame}:plane(front)", 8))
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
                        created.append(self._offset_mate(face_a, face_b, args["offset"]))
                else:
                    created.append(self._mate("lock", f"{a}:component", f"{b}:component"))
            elif kind in ("revolute", "cylindrical"):
                created.append(self._mate("concentric", a, b, align="closest"))
                if kind == "revolute" and not minimal:
                    if args.get("offset") is None:
                        created.append(self._mate("coincident", face_a, face_b, align="aligned"))
                    else:
                        created.append(self._offset_mate(face_a, face_b, args["offset"]))
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
        if self.v3:
            return self._joint_v3(kind, args)
        created = self._joint_mates(kind, args)
        entry = self._record_joint(kind, str(args["a"]), str(args["b"]), created)
        return {"joint": kind, "components": [entry["a"], entry["b"]], "mates": created}

    # ------------------------------------------------------------ v3：关节的轴向 / 止转约束按需添加

    def _world_axis(self, ref: str) -> tuple[np.ndarray, np.ndarray] | None:
        """圆柱引用在装配坐标下的轴线 (点 m, 方向)。"""
        comp, sel = self.resolve_ref(ref)
        if not str(sel).startswith("face:"):
            return None
        index = int(str(sel).split(":")[1])
        face = next((f for f in self.faces(comp) if f.get("index") == index), None)
        if not face or face.get("surfaceType") != "cylinder":
            return None
        tf = next((c.get("transform") for c in self.asmwf("components").get("components") or []
                   if c["name"] == comp), None)
        if tf is None:
            return None
        axes = np.array(tf[:9], float).reshape(3, 3)
        origin = np.array(tf[9:12], float) + np.array(face["axisOrigin"], float) @ axes
        return origin, np.array(face["axis"], float) @ axes

    def _axis_gap_hint(self, a: str, b: str) -> str:
        try:
            la, lb = self._world_axis(a), self._world_axis(b)
        except (WorkflowError, RefError):
            return ""
        if not la or not lb:
            return ""
        u = la[1] / (np.linalg.norm(la[1]) or 1)
        gap = float(np.linalg.norm(np.cross(lb[0] - la[0], u))) * 1000
        return (f"两条轴线当前相距 {gap:.2f} mm。若这是闭环的最后一个铰，说明孔位或杆长与已装好的部分对不上："
                "检查是否选错了孔（用 near=[x,y] 指定）或前面的关节装错了孔。")

    def _offset_from_z(self, args: dict, at_z: float) -> float:
        if args.get("face_a") or args.get("face_b"):
            raise WorkflowError("TOOL_ARGS", "at_z 只用于默认的前视基准面；已给 face_a/face_b 时请用 offset")
        comp_b = self._component_of(str(args["b"]))
        tf = next((c.get("transform") for c in self.asmwf("components").get("components") or []
                   if c["name"] == comp_b), None)
        if tf is None:
            raise WorkflowError("TOOL_ARGS", f"找不到组件 {comp_b}")
        bz = float(tf[8])
        if abs(abs(bz) - 1) > 1e-3:
            raise WorkflowError("TOOL_ARGS", f"{args['b']} 的零件 Z 轴不沿装配 Z，at_z 无法换算，请用 offset")
        return (at_z - float(tf[11]) * 1000) * (1 if bz > 0 else -1)

    def _joint_extra(self, kind: str, args: dict) -> list[dict]:
        """完整关节比最小关节多出的那条约束：转动副 / 销槽的轴向贴合，圆柱导向滑块的止转面。"""
        a, b = str(args["a"]), str(args["b"])
        face_a = args.get("face_a") or f"{split_ref(a)[0] if ':' in a else a}:plane(front)"
        face_b = args.get("face_b") or f"{split_ref(b)[0] if ':' in b else b}:plane(front)"
        if kind in ("revolute", "pin_slot"):
            if args.get("offset") is not None:
                return [self._offset_mate(face_a, face_b, args["offset"])]
            return [self._mate("coincident", face_a, face_b, align="aligned")]
        if not args.get("guide_a") or not args.get("guide_b"):
            raise WorkflowError("TOOL_ARGS", "slider 需要 guide_a/guide_b（再约束一个面，只留沿一条轴的平移）")
        return [self._mate("coincident", str(args["guide_a"]), str(args["guide_b"]), align="aligned")]

    def _extra_needed(self, kind: str, primary: list[dict]) -> bool:
        """最小关节建好后，多出的那条约束限制的运动是否还自由（闭环里常已被别的铰定住）。"""
        from geomcheck.assembly_rubric import AXIAL, _rot_row, axial_row, relative_motion_free

        modes, _, mates = self._modes()
        mate = next((m for m in mates if m.get("name") == primary[0]["name"]), None)
        if not mate:
            return True
        ents = [e for e in mate.get("entities") or [] if e.get("params") and int(e.get("refType", -1)) in AXIAL]
        comps = [c for c in mate.get("components") or [] if c]
        if not ents or len(comps) < 2:
            return True
        p, u = np.array(ents[0]["params"][:3], float), np.array(ents[0]["params"][3:6], float)
        row = _rot_row(u / np.linalg.norm(u)) if kind == "slider" else axial_row(p, u)
        return relative_motion_free(modes, comps[0], comps[1], row)

    def _joint_v3(self, kind: str, args: dict) -> dict:
        args = dict(args)
        if args.get("at_z") is not None and args.get("offset") is None:
            args["offset"] = self._offset_from_z(args, float(args["at_z"]))
        splittable = kind in ("revolute", "pin_slot") or (
            kind == "slider" and self._is_cylinder_ref(str(args["a"])) and self._is_cylinder_ref(str(args["b"])))
        note = None
        if not splittable:
            created = self._joint_mates(kind, args)
        else:
            try:
                created = self._joint_mates(kind, args, minimal=True)
            except WorkflowError as exc:
                if "errStatus=5" in str(exc) and kind != "pin_slot":
                    raise WorkflowError(exc.code, f"{_plain(exc)}。"
                                        + self._axis_gap_hint(str(args["a"]), str(args["b"]))) from None
                raise
            try:
                needed = self._extra_needed(kind, created)
                if needed:
                    created += self._joint_extra(kind, args)
            except WorkflowError as exc:
                self._rollback(created)
                message = _plain(exc)
                if "errStatus=5" in message:
                    message += ("。轴向（或止转）约束与已有约束冲突：两个基准面的位置要求与已装好的部分不一致。"
                                "用 at_z / offset 写明 a 的层位，或检查前面关节的层位。")
                raise WorkflowError(exc.code, message) from None
            except Exception:
                self._rollback(created)
                raise
            if not needed:
                what = "止转" if kind == "slider" else "轴向位置"
                note = f"{what}已被其他关节定住（闭环），只加了主约束，避免冗余过定义"
        entry = self._record_joint(kind, str(args["a"]), str(args["b"]), created,
                                   **({"loop": True} if note else {}))
        out = {"joint": kind, "components": [entry["a"], entry["b"]], "mates": created}
        if note:
            out["note"] = note
        return out

    def close_loop(self, args: dict) -> dict:
        kind = str(args.get("type") or "revolute").lower()
        if self.v3:
            return self._joint_v3(kind, args)
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
        out = {"mate": mate, "lead_mm": lead}
        if self.v3:
            out.update(self._verify_screw(mate["name"], lead))
        return out

    def _verify_screw(self, name: str, lead: float) -> dict:
        """按当前约束的运动空间核对螺母相对螺杆的实际导程（mm/转）；对不上通常是选错了圆柱面。"""
        modes, _, mates = self._modes()
        mate = next((m for m in mates if m.get("name") == name), None)
        ents = [e for e in (mate or {}).get("entities") or [] if e.get("params")]
        comps = [c for c in (mate or {}).get("components") or [] if c]
        null, cols = modes["null"], modes["columns"]
        if not ents or len(comps) < 2 or not null.size:
            return {}
        p = np.array(ents[0]["params"][:3], float)
        u = np.array(ents[0]["params"][3:6], float)
        u = u / np.linalg.norm(u)

        def block(n):
            return null[6 * cols[n]:6 * cols[n] + 6] if n in cols else np.zeros((6, null.shape[1]))
        rel = block(comps[1]) - block(comps[0])
        spin = u @ rel[3:]
        if np.max(np.abs(spin)) < 1e-9:
            return {"verify": "螺杆与螺母之间不能相对转动：检查螺杆是否被锁死，或螺母是否同时被别的约束定住"}
        travel = u @ (rel[:3] + np.cross(rel[3:].T, p).T)
        if np.linalg.matrix_rank(np.vstack([spin, travel]), tol=1e-9) > 1:
            return {"verify": "螺母沿螺杆的移动还没有和转动耦合（另有独立的滑动自由度），检查螺母或滑台的约束"}
        k = int(np.argmax(np.abs(spin)))
        measured = abs(2 * math.pi * float(travel[k]) / spin[k]) * 1000
        report = {"verified_lead_mm": round(measured, 4)}
        if abs(measured - lead) > 0.05 * lead:
            report["verify"] = (f"按当前约束算出的导程是 {measured:.4f} mm/转，与要求的 {lead} 不符："
                                "可能选错了圆柱面（用 screw_ref / nut_ref 显式指定），或螺母另有约束")
        return report

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

    def _check_dof_v3(self, args: dict) -> dict:
        """按 SolidWorks 里的真实配合求约束秩：直接加的 mate 也算在内，闭环冗余单独报出。"""
        modes, components, mates = self._modes()
        reached = self._grounded_by_mates(components, mates)
        report = {
            "mobility": modes["mobility"],
            "redundant": modes["redundant"],
            "free": {self._alias(n): v for n, v in modes["free"].items()},
            "floating": sorted(self._alias(c["name"]) for c in components if c["name"] not in reached),
            "over_defined": sorted(self._alias(c["name"]) for c in components
                                   if int(c.get("constrainedStatus") or 0) in (4, 5, 6)),
            "broken_mates": [m.get("name") for m in mates if int(m.get("errorCode") or 0) != 0],
            "not_counted": modes["skipped"],
            "hint": "free 是每个零件相对机架还能动的自由度（dof=转动+平移）；"
                    "活动度偏大时找 dof 比预期多的零件补约束，偏小时找冗余或装错的关节。",
        }
        expected = args.get("expected_mobility")
        if expected is not None:
            report["expected_mobility"] = int(expected)
            report["matches_expected"] = modes["mobility"] == int(expected) and not report["floating"]
        self.last_dof = report
        return report

    def check_dof(self, args: dict) -> dict:
        if self.v3:
            return self._check_dof_v3(args)
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
        primary_error = None
        try:
            return self._check_motion(args)
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                self.asmwf("motion-delete")
            except Exception:
                if primary_error is None:
                    raise

    def _check_motion(self, args: dict) -> dict:
        driver_alias = str(args["driver"])
        driver = self.resolve_name(driver_alias)
        if args.get("driver_ref"):
            _, ref = self.resolve_ref(str(args["driver_ref"]))
        else:
            ref = "bore:auto"
        sweep = float(args.get("sweep_deg") or 360)
        quick_profile = self.speed_optimizations
        requested_frames = int(args.get("frames") or (7 if quick_profile else 13))
        frames = max(3, min(requested_frames, 7)) if quick_profile else max(3, requested_frames)
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
        components = (
            self.state_bundle()["components"]
            if self.speed_optimizations
            else (self.asmwf("components").get("components") or [])
        )
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
        # Animation 的马达实际转速比 rpm 快好几倍，稀疏帧的角度会混叠；转角比用密采样逐帧展开后再算
        dense_frame_count = 31 if quick_profile else 121
        dense = self.asmwf("motion-sample", {
            "times": [duration * i / (dense_frame_count - 1) for i in range(dense_frame_count)],
            "interference": False,
        }).get("frames") or raw_frames
        sampling: dict = {}
        if self.v3:
            from geomcheck.assembly_motion import refine_aliased
            dense, sampling = refine_aliased(
                lambda times: self.asmwf("motion-sample", {"times": times, "interference": False}).get("frames") or [],
                driver, duration, dense,
                max_dense_frames=61 if quick_profile else 481,
            )

        def turned(name: str) -> float:
            angles = [angle_about_z(frame["transforms"][name]) for frame in dense]
            return sum((b - a + 180) % 360 - 180 for a, b in zip(angles, angles[1:]))

        d_in = turned(driver) or 1e-9
        kinematics = {"driver_turned_deg": _round(d_in, 1)}
        if expect_moving:
            alias = {sw: role for role, sw in self.aliases.items()}
            kinematics["sample_ratio"] = {
                alias.get(name, name): _round(turned(name) / d_in, 4) for name in expect_moving
            }
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
        if quick_profile:
            report.update({"sampling_profile": "quick", "dense_frames": len(dense)})
        if self.v3:
            report["interference_pairs"] = self._pair_summary(raw_frames)
            if sampling.get("driver_jump_deg"):
                sampling["hint"] = "驱动件在相邻帧间跳转，多半是机构卡死或闭环装不上，求解器在两个位形之间跳"
            if sampling:
                report["sampling"] = sampling
            for key in ("moved", "stuck", "drifted_fixed"):
                report[key] = [self._alias(n) for n in report[key]]
        self.last_motion = report
        return report

    def _pair_summary(self, frames: list[dict]) -> list[dict]:
        """逐帧干涉按零件对汇总：首次出现的帧、出现帧数、最大干涉体积。静态就有的多半是层位 / 孔位错，
        转到某个角度才出现的多半是相位或杆件摆动范围问题。"""
        pairs: dict[tuple, dict] = {}
        for index, frame in enumerate(frames):
            for item in (frame.get("interference") or {}).get("interferences") or []:
                key = tuple(sorted(self._alias(c) for c in item.get("components") or []))
                row = pairs.setdefault(key, {"pair": list(key), "first_frame": index, "frames": 0, "max_mm3": 0.0})
                row["frames"] += 1
                row["max_mm3"] = max(row["max_mm3"], _round(float(item.get("volume") or 0) * 1e9, 3))
        return sorted(pairs.values(), key=lambda r: -r["max_mm3"])

    def check_pose(self, args: dict) -> dict:
        """有限行程 / 多自由度机构的自检：按 set 依次加临时角度 / 距离配合把机构摆到一个位形，
        核对关键点、做静态干涉与固定件漂移检查，然后删掉临时配合。与评测的位姿模式是同一套机制。"""
        from geomcheck.assembly_motion import run_poses

        poses = args.get("poses") or [{"name": args.get("name") or "pose", "set": args.get("set") or [],
                                       "points": args.get("points") or []}]
        for pose in poses:
            for item in pose.get("set") or []:
                if item.get("type") not in ("angle", "offset", "distance"):
                    raise WorkflowError("TOOL_ARGS", "set 里每项 type 必须是 angle / offset / distance")
        spec = {"poses": poses, "expect_fixed": args.get("expect_fixed") or [],
                "frame": args.get("frame") or next((a for a, s in self.aliases.items()
                                                    if s in self._fixed_names()), None),
                "tolerance_mm": float(args.get("tolerance_mm") or 1.5), "max_interference": 0}
        if not spec["frame"]:
            raise WorkflowError("TOOL_ARGS", "还没有固定件，先 ground_component")
        result = run_poses(self, spec)
        for row in result.get("poses") or []:
            for item in row.get("interference_pairs") or []:
                item["components"] = [self._alias(c) for c in item.get("components") or []]
            row.pop("checks", None)
        report = {"passed": result["passed"], "poses": result["poses"]}
        self.last_pose = report
        self.saved = False
        return report

    def _fixed_names(self) -> set[str]:
        if self.speed_optimizations:
            return set(self._known_fixed)
        return {c["name"] for c in self.asmwf("components").get("components") or [] if c.get("isFixed")}

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
        if self.v3:
            motion_ok = bool(self.last_motion and self.last_motion.get("passed"))
            pose_ok = bool(self.last_pose and self.last_pose.get("passed")
                           and self.last_dof and self.last_dof.get("matches_expected"))
            if motion_ok or pose_ok:
                return {"ok": True, "summary": args.get("summary", ""),
                        "verified_by": "check_motion" if motion_ok else "check_pose + check_dof"}
            return {"ok": False, "error": {
                "code": "MOTION_FAIL",
                "message": "finish 前需要：一次通过的 check_motion（整周回转机构）；或一次通过的 check_pose，"
                           "且 check_dof(expected_mobility=设计活动度) 吻合（有限行程 / 多自由度机构）",
                "last_motion": None if not self.last_motion else {"passed": self.last_motion.get("passed")},
                "last_pose": None if not self.last_pose else {"passed": self.last_pose.get("passed")},
                "last_dof_matches": None if not self.last_dof else self.last_dof.get("matches_expected"),
            }}
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
