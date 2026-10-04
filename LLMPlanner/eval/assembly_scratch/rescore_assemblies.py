"""不重跑智能体，用当前的装配 rubric 与运动评测给已保存的装配体重新打分。

    python eval/assembly_scratch/rescore_assemblies.py --out eval/assembly_workflow_rescore.json \
        eval/assembly_workflow_compare.json=eval/artifacts/assembly_scratch ...
    python eval/assembly_scratch/rescore_assemblies.py --reference   # 两个参考装配体（阳性对照）

结果文件里的 aliases 用来把角色名映射到组件名；装配体按 <dir>/<workflow>/<case>/gold_r<repeat>/ 定位。
SolidWorks 偶尔在配合求解后长时间占满 CPU、服务无响应：此时重启 SolidWorks 和服务，该装配体重试一次。
--out 已有的行会跳过，可中断后续跑。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.assembly_scratch.build_standard_parts import call  # noqa: E402
from eval.assembly_scratch.sw_restart import SW, restart_solidworks  # noqa: E402,F401
from eval.eval_assembly_scratch import (  # noqa: E402
    CASE_ALIASES, CASES_DIR, _load_case, _motion, score_assembly_rubric, structure_ok,
)
from planner.assembly_workflow.session import AssemblySession  # noqa: E402

# case_id → 测例 yaml 文件名
CASE_FILES = dict(CASE_ALIASES)


class ServiceHung(RuntimeError):
    pass


def rescore(path: Path, case: dict, aliases: dict[str, str]) -> dict:
    try:
        call("/api/close-all-documents")
        session = AssemblySession(base_url=SW, timeout=300)
        call("/api/assembly/open", {"assemblyPath": str(path)})
        present = {c["name"] for c in session.asmwf("components").get("components") or []}
    except requests.RequestException as exc:
        raise ServiceHung(str(exc)) from exc
    session.aliases = {role: name for role, name in aliases.items() if name in present}
    started = time.perf_counter()
    inputs: dict = {}
    rubric = score_assembly_rubric(session, case, inputs)
    if inputs:
        path.with_name("rubric_inputs.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    spec = case["motion"]
    # 齿侧间隙要用零件 STEP，按 gold_roles（角色 → 零件）找，与 run_condition 相同：
    # gen 产物在装配体旁的 parts/<零件 id>，参考装配体用 gold_parts/<gold_dir>/ 下的标准零件
    gen_dir, gold = path.parent / "parts", CASES_DIR / "gold_parts" / case["gold_dir"]
    parts = {}
    for role, part_id in (case.get("gold_roles") or {}).items():
        candidates = [gen_dir / f"{part_id}.SLDPRT"] if gen_dir.is_dir() else \
            [gold / f"{role}.SLDPRT", gold / f"{part_id}.SLDPRT"]
        parts[role] = next((p for p in candidates if p.is_file()), candidates[-1])
    hung = None
    try:
        motion = _motion(session, spec, parts, SW)
    except requests.RequestException as exc:
        hung = exc
        motion = {"passed": False, "score": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        motion = {"passed": False, "score": 0.0, "score_v1": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    try:
        call("/api/close-all-documents")
    except requests.RequestException as exc:
        hung = hung or exc
    row = {
        "assembly": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "missing_roles": sorted(set(aliases) - set(session.aliases)),
        "assembly_score": (rubric or {}).get("score"),
        "motion_score": motion.get("score"),
        "assembly_score_v1": (rubric or {}).get("score_v1"),
        "motion_score_v1": motion.get("score_v1"),
        "structure_ok": structure_ok(rubric),
        "rubric": rubric,
        "motion": motion,
        "seconds": round(time.perf_counter() - started, 1),
    }
    if hung is not None:
        row["service_hung"] = str(hung)
    return row


def rescore_with_retry(path: Path, case: dict, aliases: dict[str, str]) -> dict:
    row = None
    for attempt in range(2):
        try:
            row = rescore(path, case, aliases)
        except ServiceHung as exc:
            row = {"assembly": str(path), "assembly_score": None, "motion_score": None, "service_hung": str(exc)}
        if "service_hung" not in row:
            return row
        restart_solidworks()
        row["attempts"] = attempt + 1
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pairs", nargs="*", help="结果 JSON=artifacts 目录")
    parser.add_argument("--reference", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--cases", default="", help="只跑这些 case_id 或文件名（逗号分隔），默认全部")
    args = parser.parse_args()
    wanted = {t.strip() for t in args.cases.split(",") if t.strip()}
    cases = {cid: _load_case(f) for cid, f in CASE_FILES.items()
             if not wanted or cid in wanted or f in wanted}
    out = Path(args.out).resolve()
    rows = json.loads(out.read_text(encoding="utf-8"))["rows"] if out.is_file() else []
    done = {(r["case_id"], r["workflow"], r.get("repeat"), r.get("source")) for r in rows}

    def write():
        out.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.reference:
        gold = CASES_DIR / "gold_parts"
        for cid in cases:
            path = gold / f"reference_{CASE_FILES[cid]}.SLDASM"
            if (cid, "reference", None, None) in done:
                continue
            if not path.is_file():
                print("skip", path)
                continue
            alias_file = path.with_suffix(".aliases.json")
            if alias_file.is_file():
                aliases = json.loads(alias_file.read_text(encoding="utf-8"))
            else:
                aliases = {role: f"{role}-1" for role in cases[cid]["gold_roles"]}
            row = {"case_id": cid, "workflow": "reference", **rescore_with_retry(path, cases[cid], aliases)}
            rows.append(row)
            print(cid, "reference", row["assembly_score"], row["motion_score"], flush=True)
            write()
    for pair in args.pairs:
        result_file, art_dir = pair.split("=", 1)
        data = json.loads(Path(result_file).read_text(encoding="utf-8"))
        for r in data["results"]:
            if not (r.get("assembly") or {}).get("aliases"):
                continue
            cid = r["case_id"]
            if cid not in cases or (cid, r["workflow"], r["repeat"], result_file) in done:
                continue
            condition = r.get("condition", "gold")
            path = (Path(art_dir) / r["workflow"] / cid / f"{condition}_r{r['repeat']}" / f"{cid}.SLDASM").resolve()
            if not path.is_file():
                print("missing", path)
                continue
            row = {
                "case_id": cid, "workflow": r["workflow"], "repeat": r["repeat"], "source": result_file,
                "old_assembly_score": r.get("assembly_score"), "old_motion_score": r.get("motion_score"),
                "old_motion_error": (r["assembly"].get("motion") or {}).get("error"),
                **rescore_with_retry(path, cases[cid], r["assembly"]["aliases"]),
            }
            rows.append(row)
            print(cid, r["workflow"], r["repeat"], row["assembly_score"], row["motion_score"], flush=True)
            write()
    write()


if __name__ == "__main__":
    main()
