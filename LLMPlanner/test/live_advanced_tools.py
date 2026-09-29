"""真机检查：到面、筋、沉头孔、扫描、放样、抽壳、拔模、钣金、编辑特征。

    venv\\Scripts\\python.exe test\\live_advanced_tools.py
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
            except Exception as exc:
                print(f"  [FAIL] exception: {exc!r}")
                failures.append(f"{name}: {exc!r}")
            finally:
                close_all()
        return run
    return wrap


@scenario("edit feature depth")
def edit_depth(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 10,
        "loops": [{"type": "rect", "center": [0, 0], "size": [40, 30]}],
    })
    name = next(
        item["name"] for item in session.state_summary()["features"]
        if item.get("type") not in ("ProfileFeature", "3DProfileFeature")
    )
    step(session, "edit_feature", {"name": name, "value": 20})
    volume = session.state_summary()["volume_mm3"]
    check("edited block 40×30×20", volume, 24000, 2.0)


@scenario("up_to side pad")
def up_to_pad(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 30,
        "loops": [{"type": "rect", "center": [0, 0], "size": [50, 40]}],
    })
    plane = step(session, "ref_plane", {"reference": "right", "offset": 20})
    step(session, "extrude", {
        "plane": plane["name"],
        "loops": [{"type": "circle", "center": [0, 15], "diameter": 16}],
        "up_to": "right", "direction": "-X",
    })
    state = session.state_summary()
    check("block + Ø16×20 pad", state["volume_mm3"], 60000 + math.pi * 64 * 20, 3.0)
    check("X size 70", state["bbox_mm"]["size"][0], 70.0, 0.05)


@scenario("rib on L bracket")
def rib_l(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 6,
        "loops": [{"type": "rect", "center": [0, 0], "size": [60, 40]}],
    })
    step(session, "extrude", {
        "plane": "right", "depth": 6, "direction": "-X",
        "loops": [{"type": "rect", "center": [0, 21], "size": [40, 30]}],
    })
    step(session, "rib", {
        "plane": "front_plane", "start": [0, 6], "end": [19, 24], "thickness": 4,
    })
    volume = session.state_summary()["volume_mm3"]
    check("L + triangular gusset", volume, 22284, 20)


@scenario("counterbore four holes")
def cbores(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 10,
        "loops": [{"type": "rect", "center": [0, 0], "size": [80, 60]}],
    })
    step(session, "counterbore", {
        "plane": "top", "centers": [[26, 16], [26, -16], [-26, 16], [-26, -16]],
        "through_diameter": 6, "head_diameter": 11, "head_depth": 3.5, "through_all": True,
    })
    one = math.pi * 9 * 6.5 + math.pi * 30.25 * 3.5
    check("plate minus 4 counterbores", session.state_summary()["volume_mm3"], 48000 - 4 * one, 4.0)


@scenario("sweep quarter torus")
def sweep_arc(session: WorkflowSession) -> None:
    step(session, "sweep", {
        "path_plane": "front_plane",
        "path_start": [40, 0],
        "path_segments": [{"to": [0, 40], "center": [0, 0], "ccw": True}],
        "profile_plane": "top_plane",
        "loops": [{"type": "circle", "center": [40, 0], "diameter": 16}],
    })
    volume = session.state_summary()["volume_mm3"]
    check("quarter torus Ø16 R40", volume, math.pi * 64 * (40 * math.pi / 2), 80.0)


@scenario("loft square to circle")
def loft_adapter(session: WorkflowSession) -> None:
    mid = step(session, "ref_plane", {"reference": "top_plane", "offset": 40})
    step(session, "loft", {"sections": [
        {"plane": "top_plane", "loops": [{"type": "rect", "center": [0, 0], "size": [30, 30]}]},
        {"plane": mid["name"], "loops": [{"type": "circle", "center": [0, 0], "diameter": 24}]},
    ]})
    volume = session.state_summary()["volume_mm3"]
    expected = 0.5 * (900 + math.pi * 144) * 40
    check("square-to-round loft", volume, expected, 4000)


@scenario("shell open box")
def shell_box(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 40,
        "loops": [{"type": "rect", "center": [0, 0], "size": [80, 50]}],
    })
    step(session, "shell", {"thickness": 4, "open_faces": ["top"]})
    check("open box 4mm wall", session.state_summary()["volume_mm3"], 80 * 50 * 40 - 72 * 42 * 36, 8.0)


@scenario("draft sides of a block")
def draft_block(session: WorkflowSession) -> None:
    step(session, "extrude", {
        "plane": "top_plane", "depth": 30,
        "loops": [{"type": "rect", "center": [0, 0], "size": [40, 40]}],
    })
    state = session.state_summary()
    sides = [face["ref"] for face in state["planar_faces"] if face["normal"] in ("+X", "-X", "+Z", "-Z")]
    step(session, "draft", {"neutral": "bottom", "faces": sides[:4], "angle_deg": 5})
    after = session.state_summary()
    check("draft changes volume", after["volume_mm3"], state["volume_mm3"], 1e9)
    if abs(after["volume_mm3"] - state["volume_mm3"]) <= 1e-3:
        failures.append("draft no volume change")
        print("  [FAIL] draft no volume change")
    else:
        print(f"  [OK] draft volume {state['volume_mm3']} -> {after['volume_mm3']}")


@scenario("sheet metal rectangle")
def sheet_flat(session: WorkflowSession) -> None:
    step(session, "sheet_metal", {
        "plane": "top_plane", "thickness": 1.5,
        "loops": [{"type": "rect", "center": [0, 0], "size": [70, 40]}],
    })
    check("sheet 70×40×1.5", session.state_summary()["volume_mm3"], 4200, 5.0)


if __name__ == "__main__":
    close_all()
    for run in (edit_depth, up_to_pad, rib_l, cbores, sweep_arc, loft_adapter, shell_box, draft_block, sheet_flat):
        run()
    print("FAILURES:", failures if failures else "none")
    sys.exit(1 if failures else 0)
