"""从参考装配体导出 rubric 关节的轴线锚点与螺旋导程，写入 assembly_rubrics/joint_anchors.json。

    python eval/assembly_scratch/derive_joint_anchors.py [--cases a,b]

锚点是转轴在两侧刚体组首个角色零件局部坐标下的位置与方向，只取决于零件几何与关节落在哪个孔上，
与零件怎样摆放、机构转到什么位形无关。导出后用合并了锚点的 rubric 重新给参考装配体打分，必须仍是 100。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.assembly_scratch.build_standard_parts import call  # noqa: E402
from eval.assembly_scratch.sw_restart import SW, restart_solidworks  # noqa: E402
from eval.eval_assembly_scratch import (  # noqa: E402
    ASSEMBLY_RUBRIC_DIR, CASE_ALIASES, CASES_DIR, JOINT_ANCHORS, _load_case,
)
from geomcheck.assembly_rubric import derive_anchors, evaluate  # noqa: E402
from planner.assembly_workflow.session import AssemblySession  # noqa: E402


def read_assembly(path: Path) -> tuple[list[dict], list[dict]]:
    """SolidWorks 偶尔卡死：重启后重试一次。"""
    for attempt in range(2):
        try:
            call("/api/close-all-documents")
            call("/api/assembly/open", {"assemblyPath": str(path)})
            session = AssemblySession(base_url=SW, timeout=300)
            components = session.asmwf("components").get("components") or []
            mates = session.asmwf("mates").get("mates") or []
            call("/api/close-all-documents")
            return components, mates
        except requests.RequestException:
            if attempt:
                raise
            restart_solidworks()
    raise AssertionError


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="")
    parser.add_argument("--force", action="store_true", help="已导出的测例也重新导出")
    args = parser.parse_args()
    wanted = {t.strip() for t in args.cases.split(",") if t.strip()}
    table = json.loads(JOINT_ANCHORS.read_text(encoding="utf-8")) if JOINT_ANCHORS.is_file() else {}
    gold = CASES_DIR / "gold_parts"
    for cid, fname in CASE_ALIASES.items():
        if wanted and cid not in wanted and fname not in wanted:
            continue
        if cid in table and not args.force:
            continue
        path = gold / f"reference_{fname}.SLDASM"
        rubric_path = ASSEMBLY_RUBRIC_DIR / f"{cid}.yaml"
        if not path.is_file() or not rubric_path.is_file():
            print("skip", cid)
            continue
        case = _load_case(fname)
        alias_file = path.with_suffix(".aliases.json")
        aliases = (json.loads(alias_file.read_text(encoding="utf-8")) if alias_file.is_file()
                   else {role: f"{role}-1" for role in case["gold_roles"]})
        components, mates = read_assembly(path)
        present = {c["name"] for c in components}
        aliases = {r: n for r, n in aliases.items() if n in present}
        rubric = yaml.safe_load(rubric_path.read_text(encoding="utf-8"))
        derived = derive_anchors(components, mates, aliases, rubric)
        merged = json.loads(json.dumps(rubric))
        for index, item in derived.items():
            merged["joints"][int(index)].update(item)
        check = evaluate(components, mates, aliases, merged)
        bad = [j for j in check["joints"] if not j["ok"]]
        print(f"{cid}: {len(derived)} 个关节有锚点, 参考分 {check['score']}", flush=True)
        if check["score"] != 100:
            raise SystemExit(f"{cid} 合并锚点后参考装配体不是 100：{bad}")
        table[cid] = derived
        JOINT_ANCHORS.write_text(json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
