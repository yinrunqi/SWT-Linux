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
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from eval.assembly_scratch.build_standard_parts import call  # noqa: E402
from eval.eval_assembly_scratch import CASES_DIR, score_assembly_rubric  # noqa: E402
from geomcheck.assembly_motion import analyze_frames, sample_motion  # noqa: E402
from planner.assembly_workflow.refs import resolve_on_faces  # noqa: E402
from planner.assembly_workflow.session import AssemblySession  # noqa: E402

CASE_FILES = {"two_stage_gearbox": "gearbox", "single_cylinder_engine": "engine"}
SW = "http://localhost:5100"
SW_EXE = os.environ.get(
    "SOLIDWORKS_EXE", r"C:\Program Files\softwares\solidworks2025\Corp\SOLIDWORKS\SLDWORKS.exe")
SERVICE_EXE = ROOT.parent / "Chapter1.ConnectToSw" / "bin" / "Debug" / "Chapter1.ConnectToSw.exe"


class ServiceHung(RuntimeError):
    pass


def restart_solidworks() -> None:
    print("  重启 SolidWorks 与服务", flush=True)
    for image in ("Chapter1.ConnectToSw.exe", "SLDWORKS.exe"):
        subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)
    time.sleep(3)
    subprocess.Popen([SW_EXE])
    time.sleep(90)
    log = open(Path(os.environ.get("TEMP", ".")) / "svc.log", "ab")
    subprocess.Popen([str(SERVICE_EXE)], cwd=str(SERVICE_EXE.parent), stdout=log, stderr=log,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(60):
        time.sleep(2)
        try:
            if requests.get(f"{SW}/api/ping", timeout=3).ok:
                return
        except requests.RequestException:
            pass
    raise RuntimeError("服务重启后仍无响应")


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
    rubric = score_assembly_rubric(session, case)
    spec = case["motion"]
    hung = None
    try:
        driver = session.aliases[spec["driver"]]
        ref = resolve_on_faces("cyl(i=0)", session.faces(driver))
        sampled = sample_motion(SW, spec, driver, ref)
        motion = analyze_frames(sampled["frames"], spec, session.aliases, sampled.get("interference_frames"))
    except requests.RequestException as exc:
        hung = exc
        motion = {"passed": False, "score": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:
        motion = {"passed": False, "score": 0.0, "error": f"{type(exc).__name__}: {exc}"}
    try:
        call("/api/close-all-documents")
    except requests.RequestException as exc:
        hung = hung or exc
    row = {
        "assembly": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
        "missing_roles": sorted(set(aliases) - set(session.aliases)),
        "assembly_score": (rubric or {}).get("score"),
        "motion_score": motion.get("score"),
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
    args = parser.parse_args()
    cases = {cid: yaml.safe_load((CASES_DIR / f"{f}.yaml").read_text(encoding="utf-8"))
             for cid, f in CASE_FILES.items()}
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
            aliases = {role: f"{role}-1" for role in cases[cid]["gold_roles"]}
            row = {"case_id": cid, "workflow": "reference", **rescore_with_retry(path, cases[cid], aliases)}
            rows.append(row)
            print(cid, "reference", row["assembly_score"], row["motion_score"], flush=True)
            write()
    for pair in args.pairs:
        result_file, art_dir = pair.split("=", 1)
        data = json.loads(Path(result_file).read_text(encoding="utf-8"))
        for r in data["results"]:
            if r.get("condition", "gold") != "gold" or not (r.get("assembly") or {}).get("aliases"):
                continue
            cid = r["case_id"]
            if (cid, r["workflow"], r["repeat"], result_file) in done:
                continue
            path = (Path(art_dir) / r["workflow"] / cid / f"gold_r{r['repeat']}" / f"{cid}.SLDASM").resolve()
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
