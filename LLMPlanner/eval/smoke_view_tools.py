"""真机检查 view_model / view_assembly 截图工具（不调用 LLM）。

建一个带孔的方块零件、一个由 crank_rocker 标准零件组成的小装配体，分别截图，拼图存到
eval/artifacts/smoke_view_tools/ 供人工查看；同时核对截图前后视角与显示模式是否恢复。

    venv\\Scripts\\python.exe -X utf8 eval\\smoke_view_tools.py
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from planner.assembly_workflow.session import AssemblySession  # noqa: E402
from planner.assembly_workflow.tools import tool_names  # noqa: E402
from planner.workflow.tools import IMAGE_KEY  # noqa: E402
from planner.workflow.tools_v2 import WorkflowSessionV2  # noqa: E402

GOLD = ROOT / "eval" / "assembly_scratch" / "gold_parts" / "crank_rocker"


def _save(result: dict, path: Path) -> dict:
    image = result.pop(IMAGE_KEY, None)
    if image:
        path.write_bytes(base64.b64decode(image))
        result["saved_to"] = str(path)
    return result


def _check(label: str, result: dict, expect_ok: bool = True) -> bool:
    ok = bool(result.get("ok")) == expect_ok
    print(f"[{'OK' if ok else 'FAIL'}] {label}: {json.dumps(result, ensure_ascii=False)[:400]}", flush=True)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sw", default="http://localhost:5100")
    args = parser.parse_args()
    out = ROOT / "eval" / "artifacts" / "smoke_view_tools"
    out.mkdir(parents=True, exist_ok=True)
    close = lambda: requests.post(f"{args.sw}/api/close-all-documents", json={}, timeout=60)  # noqa: E731
    passed = True

    close()
    try:
        part = WorkflowSessionV2(base_url=args.sw)
        part.start_part()
        passed &= _check("空零件截图被拒", part.call("view_model", {}), expect_ok=False)
        passed &= _check("拉伸方块", part.call("extrude", {
            "plane": "Z=0", "loops": [{"type": "rect", "center": [0, 0], "size": [60, 40]}], "depth": 20}))
        passed &= _check("打孔", part.call("cut", {
            "plane": "Z=20", "loops": [{"type": "circle", "center": [15, 0], "diameter": 12}], "through_all": True}))
        passed &= _check("零件四视图", _save(part.call("view_model", {}), out / "part_default.png"))
        passed &= _check("零件单视图 top", _save(part.call("view_model", {"views": ["top"]}), out / "part_top.png"))
        passed &= _check("未知视向被拒", part.call("view_model", {"views": ["side"]}), expect_ok=False)
    finally:
        close()

    try:
        asm = AssemblySession(base_url=args.sw, workflow="v3", allowed_tools=tool_names("v3"))
        asm.start_assembly(str(out / "smoke_view.SLDASM"))
        passed &= _check("插入 frame", asm.call("insert_component", {
            "part": str(GOLD / "cr_frame.SLDPRT"), "name": "frame"}))
        passed &= _check("插入 crank", asm.call("insert_component", {
            "part": str(GOLD / "cr_crank.SLDPRT"), "name": "crank", "at": [0, 0, 30]}))
        passed &= _check("插入 rocker", asm.call("insert_component", {
            "part": str(GOLD / "cr_rocker.SLDPRT"), "name": "rocker", "at": [120, 0, 30]}))
        passed &= _check("装配体四视图", _save(asm.call("view_assembly", {}), out / "assembly_default.png"))
        result = asm.call("view_assembly", {"views": ["isometric", "front"], "highlight": ["crank", "nope"]})
        passed &= _check("高亮 crank", _save(result, out / "assembly_highlight.png"))
        passed &= result.get("highlight_not_found") == ["nope"]
    finally:
        close()
    print(f"截图在 {out}；{'全部符合预期' if passed else '有检查未通过'}", flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
