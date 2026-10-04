"""把 27 个测例的 gen 结果整理成旧口径、新口径两张 Markdown 大表。

    python eval/assembly_scratch/bench_table.py eval/assembly_v2_rescore_new.json \
        --extra eval/assembly_gen_sonnet5_v2_core_projector_llm400.json

重评文件（rescore_assemblies.py + rescore_unsuppress.py 的输出）提供两种口径的分数，零件合格数与
装配智能体状态取自每行 source 指向的原始结果；--extra 给出没有可重评装配体的行（直接用其中记录的分数）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval.assembly_scratch.rescore_unsuppress import source_row  # noqa: E402

CORE = ["two_stage_gearbox", "single_cylinder_engine", "adjustable_desk_lamp", "folding_umbrella",
        "drill_planetary_gearbox", "sewing_machine_drive", "film_projector_drive"]
NAMES = {
    "two_stage_gearbox": "减速箱", "single_cylinder_engine": "发动机", "adjustable_desk_lamp": "台灯",
    "folding_umbrella": "雨伞", "drill_planetary_gearbox": "电钻", "sewing_machine_drive": "缝纫机",
    "film_projector_drive": "放映机", "crank_rocker_linkage": "曲柄摇杆", "offset_slider_crank": "偏置曲柄滑块",
    "twin_cylinder_pump": "双缸泵", "pumpjack_drive": "抽油机", "windshield_wiper_linkage": "雨刮",
    "beam_engine": "梁机", "clock_motion_works": "钟表跨轮", "hand_winch": "手摇绞盘",
    "turntable_ring_drive": "内齿圈转台", "star_gear_reducer": "星型轮系", "lead_screw_stage": "丝杠滑台",
    "gear_driven_slider_crank": "齿轮曲柄滑块", "epicyclic_carrier_output": "行星架输出",
    "excavator_arm": "挖掘机臂", "scara_arm": "SCARA", "pantograph": "缩放仪", "ackermann_steering": "阿克曼转向",
    "toggle_clamp": "肘节夹钳", "parallel_gripper": "平行夹爪", "double_wishbone": "双横臂悬架",
}
STATUS = {"finished": "finish", "max_llm_calls": "用满调用", "stalled": "停滞", "llm_error": "LLM 报错"}


def _row(case_id: str, original: dict, scores: dict) -> dict:
    parts = original.get("parts") or []
    accepted = sum(bool(p.get("accepted")) for p in parts)
    asm = original.get("assembly") or {}
    return {
        "case_id": case_id,
        "group": "核心" if case_id in CORE else "扩展",
        "parts": f"{accepted}/{len(parts)}",
        "parts_all": bool(parts) and accepted == len(parts),
        "agent": f"{STATUS.get(asm.get('status'), asm.get('status'))}（{asm.get('llm_calls')} 次 LLM 调用）",
        **scores,
    }


def collect(rescore_path: Path, extra_paths: list[Path]) -> list[dict]:
    rows = []
    for r in json.loads(rescore_path.read_text(encoding="utf-8"))["rows"]:
        original = source_row(r) or {}
        # 文件被旧评测压缩过配合的行，运动结果以原始运行的记录为准（见 rescore_unsuppress.py）
        if r.get("file_suppressed_mates") or not r.get("motion"):
            motion = (original.get("assembly") or {}).get("motion") or {}
        else:
            motion = r["motion"]
        rows.append(_row(r["case_id"], original, {
            "rubric_old": r.get("assembly_score_v1"), "rubric_new": r.get("assembly_score"),
            "motion_old": r.get("motion_score_v1"), "motion_new": r.get("motion_score"),
            "structure_ok": bool(r.get("structure_ok")), "motion_passed": bool(motion.get("passed")),
        }))
    for path in extra_paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        for r in data.get("all_rows") or data["results"]:
            rows.append(_row(r["case_id"], r, {
                "rubric_old": r.get("assembly_score_v1"), "rubric_new": r.get("assembly_score"),
                "motion_old": r.get("motion_score_v1"), "motion_new": r.get("motion_score"),
                "structure_ok": bool(r.get("structure_ok")),
                "motion_passed": bool(((r.get("assembly") or {}).get("motion") or {}).get("passed")),
            }))
    order = CORE + [c for c in NAMES if c not in CORE]
    rows.sort(key=lambda row: order.index(row["case_id"]))
    for row in rows:
        for key in ("rubric_old", "rubric_new", "motion_old", "motion_new"):
            row[key] = float(row[key] or 0.0)
        row["motion_gated"] = row["motion_new"] if row["structure_ok"] else 0.0
        row["e2e"] = row["parts_all"] and row["motion_passed"]
    return rows


def _f(value: float) -> str:
    return f"{value:.1f}"


def _mean(rows: list[dict], key: str) -> str:
    return _f(sum(r[key] for r in rows) / len(rows))


def tables(rows: list[dict]) -> str:
    out = ["**旧口径**", "",
           "| 测例 | 组 | 零件合格 | 装配 rubric | 运动分 | e2e | 装配智能体 |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {NAMES[r['case_id']]} | {r['group']} | {r['parts']} | {_f(r['rubric_old'])} | "
                   f"{_f(r['motion_old'])} | {'是' if r['e2e'] else '否'} | {r['agent']} |")
    parts_ok = sum(int(r["parts"].split("/")[0]) for r in rows)
    parts_n = sum(int(r["parts"].split("/")[1]) for r in rows)
    e2e = sum(r["e2e"] for r in rows)
    out.append(f"| **平均** | | **{parts_ok}/{parts_n}** | **{_mean(rows, 'rubric_old')}** | "
               f"**{_mean(rows, 'motion_old')}** | **{e2e}/{len(rows)}** | |")
    out += ["", "**新口径**", "",
            "| 测例 | 组 | 零件合格 | 装配 rubric | 结构成立 | 运动分 | 运动分（门控） | e2e |",
            "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {NAMES[r['case_id']]} | {r['group']} | {r['parts']} | {_f(r['rubric_new'])} | "
                   f"{'是' if r['structure_ok'] else '否'} | {_f(r['motion_new'])} | {_f(r['motion_gated'])} | "
                   f"{'是' if r['e2e'] else '否'} |")
    structure = sum(r["structure_ok"] for r in rows)
    out.append(f"| **平均** | | **{parts_ok}/{parts_n}** | **{_mean(rows, 'rubric_new')}** | "
               f"**{structure}/{len(rows)}** | **{_mean(rows, 'motion_new')}** | **{_mean(rows, 'motion_gated')}** | "
               f"**{e2e}/{len(rows)}** |")
    for group in ("核心", "扩展"):
        sub = [r for r in rows if r["group"] == group]
        out.append(f"\n{group} {len(sub)} 例：旧口径 rubric {_mean(sub, 'rubric_old')} / 运动 {_mean(sub, 'motion_old')}；"
                   f"新口径 rubric {_mean(sub, 'rubric_new')} / 运动 {_mean(sub, 'motion_new')} / "
                   f"门控 {_mean(sub, 'motion_gated')}；结构成立 {sum(r['structure_ok'] for r in sub)}，"
                   f"e2e {sum(r['e2e'] for r in sub)}")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("rescore")
    parser.add_argument("--extra", nargs="*", default=[])
    parser.add_argument("--out", help="写成 UTF-8 文件；不给则打印")
    args = parser.parse_args()
    text = tables(collect(Path(args.rescore), [Path(p) for p in args.extra]))
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
