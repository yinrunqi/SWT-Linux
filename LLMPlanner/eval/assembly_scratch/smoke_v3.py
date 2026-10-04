"""workflow v3 工具的冒烟测试：用 v3 会话按参考装配体的搭法重搭一遍，评测器打分仍须 100，
智能体侧的 check_dof / check_motion / check_pose 与 finish 门槛也要放行。不跑 LLM。

    python eval/assembly_scratch/smoke_v3.py [--cases gearbox,crank_rocker_linkage] --out eval/assembly_smoke_v3.json

装配体存到 gold_parts/smoke_v3/，不覆盖参考装配体；搭建脚本顺带写的 aliases 文件跑完后还原。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.assembly_scratch import build_reference_assemblies as bra  # noqa: E402
from eval.assembly_scratch.build_standard_parts import call  # noqa: E402
from eval.assembly_scratch.rescore_assemblies import rescore_with_retry  # noqa: E402
from eval.assembly_scratch.sw_restart import SW  # noqa: E402
from eval.eval_assembly_scratch import ASSEMBLY_RUBRIC_DIR, CASE_ALIASES, _load_case  # noqa: E402
from planner.assembly_workflow.session import AssemblySession  # noqa: E402
from planner.assembly_workflow.tools import tool_names  # noqa: E402

SMOKE = bra.GOLD / "smoke_v3"


class SmokeSession(AssemblySession):
    """v3 会话；新建装配体时改存到 smoke 目录，并记下每次工具调用的结果。"""

    def __init__(self, **kw):
        super().__init__(workflow="v3", allowed_tools=tool_names("v3"), **kw)
        self.log: list[dict] = []

    def start_assembly(self, path: str) -> dict:
        return super().start_assembly(str(SMOKE / Path(path).name))

    def call(self, name: str, args: dict | None) -> dict:
        out = super().call(name, args)
        self.log.append({"tool": name, "ok": bool(out.get("ok")), "note": out.get("note"),
                         "verify": out.get("verify"), "verified_lead_mm": out.get("verified_lead_mm"),
                         "error": (out.get("error") or {}).get("message")})
        return out


def agent_checks(path: Path, aliases: dict[str, str], case: dict, mobility: int) -> dict:
    """打开 smoke 装配体，按智能体的用法做自检，最后看 finish 门槛（不保存修改）。"""
    call("/api/close-all-documents")
    call("/api/assembly/open", {"assemblyPath": str(path)})
    s = SmokeSession(base_url=SW, timeout=900)
    s.has_assembly, s.assembly_path, s.aliases = True, str(path), dict(aliases)
    spec = case["motion"]
    out: dict = {}
    if spec.get("mode") == "poses":
        pose = s.call("check_pose", {"poses": spec["poses"], "expect_fixed": spec.get("expect_fixed") or [],
                                     "frame": spec.get("frame"), "tolerance_mm": spec.get("tolerance_mm")})
        out["check_pose"] = pose.get("passed") if pose.get("ok") else pose.get("error")
    else:
        motion = s.call("check_motion", {"driver": spec["driver"], "expect_moving": spec.get("expect_moving") or [],
                                         "expect_fixed": spec.get("expect_fixed") or [],
                                         "sweep_deg": spec.get("sweep_deg") or 360})
        out["check_motion"] = motion.get("passed") if motion.get("ok") else motion.get("error")
        out["interference_pairs"] = motion.get("interference_pairs")
    dof = s.call("check_dof", {"expected_mobility": mobility})
    out["check_dof"] = {k: dof.get(k) for k in ("mobility", "redundant", "floating", "over_defined",
                                                "matches_expected", "not_counted")}
    s.saved = True  # 只检查门槛逻辑，不把临时修改存盘
    fin = s.call("finish", {"summary": "smoke"})
    out["finish_ok"] = bool(fin.get("ok"))
    if not fin.get("ok"):
        out["finish_error"] = (fin.get("error") or {}).get("message")
    call("/api/close-all-documents")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default="", help="case_id 或搭建名，逗号分隔；默认全部")
    parser.add_argument("--out", default=str(ROOT / "eval" / "assembly_smoke_v3.json"))
    args = parser.parse_args()
    wanted = {t.strip() for t in args.cases.split(",") if t.strip()}
    SMOKE.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    rows = json.loads(out_path.read_text(encoding="utf-8"))["rows"] if out_path.is_file() else []
    done = {r["case_id"] for r in rows}
    sessions: list[SmokeSession] = []
    bra._session = lambda: sessions.append(SmokeSession(timeout=900)) or sessions[-1]
    alias_backup = {p: p.read_bytes() for p in bra.GOLD.glob("reference_*.aliases.json")}
    by_build = {fname: cid for cid, fname in CASE_ALIASES.items()}
    try:
        for name, build in bra.BUILDS.items():
            cid = by_build.get(name, name)
            if (wanted and cid not in wanted and name not in wanted) or cid in done:
                continue
            print(f"== {cid}", flush=True)
            case = _load_case(CASE_ALIASES.get(cid, name))
            mobility = int(yaml.safe_load((ASSEMBLY_RUBRIC_DIR / f"{cid}.yaml").read_text(encoding="utf-8"))
                           .get("mobility", 1))
            row: dict = {"case_id": cid}
            sessions.clear()
            try:
                build()
            except Exception as exc:
                row["build_error"] = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
                call("/api/close-all-documents")
            s = sessions[-1] if sessions else None
            if s is not None:
                row["tool_calls"] = len(s.log)
                row["notes"] = [e["note"] for e in s.log if e["note"]]
                row["screw_verify"] = [{"lead": e["verified_lead_mm"], "verify": e["verify"]}
                                       for e in s.log if e["tool"] == "screw_pair"]
                row["errors"] = [e for e in s.log if not e["ok"]]
            path = SMOKE / f"reference_{name}.SLDASM"
            if "build_error" not in row and path.is_file():
                scored = rescore_with_retry(path, case, dict(s.aliases))
                row.update({k: scored.get(k) for k in ("assembly_score", "motion_score", "structure_ok",
                                                         "missing_roles", "service_hung")})
                if scored.get("assembly_score") != 100:
                    row["rubric"] = scored.get("rubric")
                if scored.get("motion_score") != 100:
                    row["motion"] = scored.get("motion")
                try:
                    row["agent"] = agent_checks(path, s.aliases, case, mobility)
                except Exception as exc:
                    row["agent"] = {"error": f"{type(exc).__name__}: {exc}"}
                    call("/api/close-all-documents")
            print(f"   rubric={row.get('assembly_score')} motion={row.get('motion_score')} "
                  f"notes={len(row.get('notes') or [])} agent={json.dumps(row.get('agent'), ensure_ascii=False)[:200]}"
                  f"{' BUILD_ERROR ' + row['build_error'] if 'build_error' in row else ''}", flush=True)
            rows.append(row)
            out_path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    finally:
        for p, data in alias_backup.items():
            p.write_bytes(data)
        call("/api/close-all-documents")


if __name__ == "__main__":
    main()
