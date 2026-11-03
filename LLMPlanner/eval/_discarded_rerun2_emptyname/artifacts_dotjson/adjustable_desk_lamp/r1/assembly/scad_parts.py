"""评测提供的零件加载工具，不要修改。"""
import json
from pathlib import Path

import simplecadapi as scad

_MANIFEST = json.loads((Path(__file__).parent / "parts" / "manifest.json").read_text(encoding="utf-8"))
PARTS = _MANIFEST["parts"]   # 零件 id -> {brep, sha256, step}
ROLES = _MANIFEST["roles"]   # 角色 -> 零件 id


def part_inputs(part_id: str):
    return (scad.file_input(PARTS[part_id]["brep"]),)


def load_part_solid(part_id: str) -> scad.Solid:
    """零件坐标系与上一阶段交付的 STEP 一致。"""
    item = PARTS[part_id]
    return scad.load_brep_region_rsolid(item["brep"], item["sha256"])
