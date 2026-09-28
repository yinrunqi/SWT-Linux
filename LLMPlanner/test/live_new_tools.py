"""真机检查：轮廓圆弧、偏置基准面、镜像（不经过 LLM，直接调用 L1 工具）。

pytest 不收集本文件；需要 C# 服务在线：
    venv\\Scripts\\python.exe test\\live_new_tools.py
每个场景新建零件，结束后关闭全部文档。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.workflow.tools import WorkflowSession  # noqa: E402

BASE = "http://localhost:5100"
failures: list[str] = []


def check(label: str, actual: float, expected: float, tol: float) -> None:
    ok = abs(actual - expected) <= tol
    print(f"  [{'OK' if ok else 'FAIL'}] {label}: {actual:.3f} (expected {expected:.3f} ±{tol})")
    if not ok:
        failures.append(label)


def step(session: WorkflowSession, tool: str, args: dict) -> dict:
    result = session.call(tool, args)
    if not result.get("ok"):
        print(f"  [FAIL] {tool}: {result.get('error')}")
        failures.append(f"{tool} {args}")
    return result


def state(session: WorkflowSession) -> tuple[float, dict]:
    summary = session.state_summary()
    return summary["volume_mm3"], summary["bbox_mm"]


def close_all() -> None:
    requests.post(f"{BASE}/api/close-all-documents", json={}, timeout=60)


def scenario(name):
    def wrap(fn):
        def run():
            print(f"== {name}")
            session = WorkflowSession(base_url=BASE)
            session.start_part()
            try:
                fn(session)
            except Exception as exc:  # 真机脚本：记录后继续下一个场景
                print(f"  [FAIL] exception: {exc!r}")
                failures.append(f"{name}: {exc!r}")
            finally:
                close_all()
        return run
    return wrap


@scenario("rounded rect + slot + sector cut")
def profiles(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 10,
        "loops": [{"type": "rect", "center": [0, 0], "size": [80, 60], "corner_radius": 8}],
    })
    plate = (80 * 60 - 4 * (64 - math.pi * 16)) * 10
    volume, _ = state(session)
    check("rounded plate volume", volume, plate, 1.0)
    step(session, "cut", {
        "plane": "top", "through_all": True,
        "loops": [{"type": "slot", "start": [-25, -15], "end": [-5, -15], "width": 8}],
    })
    slot = (20 * 8 + math.pi * 16) * 10
    volume, _ = state(session)
    check("after slot", volume, plate - slot, 1.0)
    step(session, "cut", {
        "plane": "top", "through_all": True,
        "loops": [{"type": "annular_sector", "center": [15, 5], "r_inner": 5, "r_outer": 20,
                   "start_deg": 0, "end_deg": 90}],
    })
    sector = math.pi * (400 - 25) / 4 * 10
    volume, _ = state(session)
    check("after sector", volume, plate - slot - sector, 1.5)


@scenario("path with tangent arc on front_plane")
def tangent_path(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "front_plane", "depth": 5,
        "loops": [{"type": "path", "start": [0, 0], "segments": [
            {"to": [30, 0]}, {"to": [30, 20], "tangent": True}, {"to": [0, 20]},
        ]}],
    })
    volume, box = state(session)
    check("tangent D-shape volume", volume, (600 + math.pi * 100 / 2) * 5, 1.0)
    check("bulge reaches x=40", box["X"][1], 40.0, 0.01)


@scenario("offset ref planes: base plane / face / negative")
def ref_planes(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 10,
        "loops": [{"type": "rect", "center": [0, 0], "size": [40, 40]}],
    })
    made = step(session, "ref_plane", {"reference": "top_plane", "offset": 30})
    print("  ref_plane:", made)
    plane = made.get("name")
    step(session, "extrude", {
        "plane": plane, "depth": 20, "direction": "-Y",
        "loops": [{"type": "circle", "center": [0, 0], "diameter": 20}],
    })
    volume, box = state(session)
    check("boss from Y=30 down to the plate", volume, 16000 + math.pi * 100 * 20, 1.0)
    check("top at Y=30", box["Y"][1], 30.0, 0.01)
    below = step(session, "ref_plane", {"reference": "bottom", "offset": 15})
    step(session, "extrude", {
        "plane": below.get("name"), "depth": 15, "direction": "+Y",
        "loops": [{"type": "rect", "center": [10, 0], "size": [10, 10]}],
    })
    volume, box = state(session)
    check("plane 15 below bottom face (outward = -Y)", box["Y"][0], -15.0, 0.01)
    check("block grown back up to the plate", volume, 16000 + math.pi * 100 * 20 + 1500, 1.0)
    mid = step(session, "ref_plane", {"reference": "right_plane", "offset": -12})
    step(session, "cut", {
        "plane": mid.get("name") or mid.get("plane"), "through_all": True, "direction": "both",
        "loops": [{"type": "circle", "center": [0, 5], "diameter": 4}],
    })
    volume_after, _ = state(session)
    check("through-both cut from X=-12 removes Ø4 x 40", volume - volume_after, math.pi * 4 * 40, 1.0)


@scenario("mirror feature about right_plane, then mirror body")
def mirrors(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 10,
        "loops": [{"type": "rect", "center": [20, 0], "size": [40, 30]}],
    })
    step(session, "extrude", {
        "plane": "top", "depth": 15,
        "loops": [{"type": "circle", "center": [30, 8], "diameter": 10}],
    })
    features = [f["name"] for f in session.state_summary()["features"]]
    boss = features[-1]
    one_boss = math.pi * 25 * 15
    step(session, "mirror", {"plane": "front_plane", "features": [boss]})
    volume, _ = state(session)
    check("feature mirrored across front_plane", volume, 12000 + 2 * one_boss, 1.0)
    step(session, "mirror", {"plane": "right_plane", "body": True})
    volume, box = state(session)
    check("body mirror doubles volume", volume, 2 * (12000 + 2 * one_boss), 2.0)
    check("symmetric X min", box["X"][0], -40.0, 0.01)


if __name__ == "__main__":
    close_all()
    for run in (profiles, tangent_path, ref_planes, mirrors):
        run()
    print("FAILURES:", failures if failures else "none")
    sys.exit(1 if failures else 0)
