"""装配体语义引用：comp:cyl(d=10)、comp:face(+Y, largest)、comp:plane(front)。"""

from __future__ import annotations

import math
import re
from typing import Any


class RefError(ValueError):
    pass


_PLANE_ALIAS = {"front": "front", "top": "top", "right": "right"}


def split_ref(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    if ":" not in raw:
        raise RefError("引用必须写成 组件:选择器，例如 shaft1:cyl(d=10)")
    component, selector = raw.split(":", 1)
    if not component or not selector:
        raise RefError(f"无效引用 {text}")
    return component.strip(), selector.strip()


def _num(token: str) -> float:
    return float(token.strip())


def _parse_args(blob: str) -> dict[str, Any]:
    """解析 cyl(d=10, near=[0,0]) 或 face(+Y, largest)。"""
    blob = blob.strip()
    if not blob:
        return {}
    out: dict[str, Any] = {}
    positional: list[str] = []
    depth = 0
    current = []
    for ch in blob:
        if ch == "[":
            depth += 1
            current.append(ch)
        elif ch == "]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            positional.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        positional.append("".join(current).strip())
    for item in positional:
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
            key, value = key.strip(), value.strip()
            if value.startswith("[") and value.endswith("]"):
                out[key] = [float(p) for p in value[1:-1].split(",") if p.strip()]
            elif value.lower() in ("true", "false"):
                out[key] = value.lower() == "true"
            else:
                try:
                    out[key] = float(value)
                except ValueError:
                    out[key] = value
        else:
            out.setdefault("_pos", []).append(item)
    return out


def parse_selector(selector: str) -> dict[str, Any]:
    text = selector.strip()
    if text.startswith("face:") or text.startswith("plane:") or text.startswith("bore:"):
        return {"kind": "raw", "ref": text}
    if text.lower() in ("bore", "bore:auto"):
        return {"kind": "raw", "ref": "bore:auto"}
    if text.startswith("face#"):
        return {"kind": "raw", "ref": f"face:{int(text[5:])}"}
    match = re.fullmatch(r"(cyl|cylinder|face|plane)\((.*)\)", text, re.I)
    if match:
        kind = match.group(1).lower()
        args = _parse_args(match.group(2))
        if kind in ("cyl", "cylinder"):
            return {"kind": "cyl", **args}
        if kind == "plane":
            name = (args.get("_pos") or ["front"])[0].lower()
            if name not in _PLANE_ALIAS:
                raise RefError(f"未知基准面 {name}")
            return {"kind": "raw", "ref": f"plane:{_PLANE_ALIAS[name]}"}
        return {"kind": "face", **args}
    if text.lower() in _PLANE_ALIAS:
        return {"kind": "raw", "ref": f"plane:{text.lower()}"}
    raise RefError(f"无法解析选择器 {selector}")


def _axis_token(token: str) -> tuple[str, int]:
    token = token.strip()
    sign = -1 if token.startswith("-") else 1
    axis = token.lstrip("+-").upper()
    if axis not in ("X", "Y", "Z"):
        raise RefError(f"未知轴向 {token}")
    return axis, sign


def pick_cylinder(faces: list[dict], *, diameter_mm: float | None, near=None, index: int | None = None) -> dict:
    items = [f for f in faces if f.get("surfaceType") == "cylinder" and f.get("radius") is not None]
    if not items:
        raise RefError("没有圆柱面")
    if index is not None:
        items = sorted(items, key=lambda f: f["index"])
        if index < 0 or index >= len(items):
            raise RefError(f"圆柱面序号 {index} 超出范围")
        return items[index]
    if diameter_mm is not None:
        target = diameter_mm / 2000.0
        items = sorted(items, key=lambda f: abs(float(f["radius"]) - target))
        if abs(float(items[0]["radius"]) - target) > 2e-4:
            raise RefError(f"没有直径接近 {diameter_mm} mm 的圆柱面")
    if near is not None:
        nx, ny = float(near[0]) / 1000.0, float(near[1]) / 1000.0
        items = sorted(
            items,
            key=lambda f: math.hypot(float(f["axisOrigin"][0]) - nx, float(f["axisOrigin"][1]) - ny),
        )
    return items[0]


def pick_plane(faces: list[dict], args: dict) -> dict:
    items = [f for f in faces if f.get("surfaceType") == "plane"]
    if not items:
        raise RefError("没有平面")
    pos = args.get("_pos") or []
    rank = "largest"
    axis, sign = None, None
    for token in pos:
        low = token.lower()
        if low in ("largest", "smallest", "outermost", "innermost"):
            rank = low
        elif re.fullmatch(r"[+-]?[xyzXYZ]", token):
            axis, sign = _axis_token(token)
    if axis:
        scored = []
        for face in items:
            normal = face.get("normal") or [0, 0, 0]
            mapping = {"X": 0, "Y": 1, "Z": 2}
            component = float(normal[mapping[axis]])
            if component * sign <= 0.3:
                continue
            point = face.get("point") or [0, 0, 0]
            scored.append((face, float(point[mapping[axis]]) * sign, float(face.get("area") or 0)))
        if not scored:
            raise RefError(f"没有法向接近 {sign:+d}{axis} 的平面")
        if rank in ("outermost",):
            scored.sort(key=lambda item: (-item[1], -item[2]))
        elif rank == "innermost":
            scored.sort(key=lambda item: (item[1], -item[2]))
        elif rank == "smallest":
            scored.sort(key=lambda item: (item[2], -item[1]))
        else:
            scored.sort(key=lambda item: (-item[2], -item[1]))
        return scored[0][0]
    items = sorted(items, key=lambda f: float(f.get("area") or 0), reverse=(rank != "smallest"))
    return items[0]


def resolve_on_faces(selector: str, faces: list[dict]) -> str:
    parsed = parse_selector(selector)
    if parsed["kind"] == "raw":
        return parsed["ref"]
    if parsed["kind"] == "cyl":
        diameter = parsed.get("d") or parsed.get("diameter")
        face = pick_cylinder(
            faces,
            diameter_mm=None if diameter is None else float(diameter),
            near=parsed.get("near"),
            index=None if parsed.get("i") is None else int(parsed["i"]),
        )
        return f"face:{face['index']}"
    face = pick_plane(faces, parsed)
    return f"face:{face['index']}"
