"""Workflow-CAD-Bench 零件题（text-to-CAD 与三视图 track）的题目导出与结果汇总，与建模系统无关。

用法（在 LLMPlanner 目录、venv-occ 中）：
    python -m geomcheck.bench tasks --output tasks.jsonl
    python -m geomcheck.bench tasks --lang en --output tasks_en.jsonl
    python -m geomcheck.rubric --artifacts <STEP 目录> --output scores.json
    python -m geomcheck.bench summary scores.json --repeats 2

三视图 track：--rubric-dir eval/rubrics_three_view；题目带 image 字段（图纸 PNG），
打分用 python -m geomcheck.three_view（rubric + 图像分），summary 会附带平均图像分。

STEP 文件命名为 ``<case>_r<k>.step``（k 从 1 开始）；缺失的运行记 0 分、不合格。
"""

from __future__ import annotations

import argparse
import json
import random
from math import comb
from pathlib import Path

import yaml

KINDS = {
    "Everyday objects": "phone_stand stool rounded_stool storage_box standard_die mug vase key soap_dish",
    "Common machined parts": "hex_nut wrench gear_disc flange pulley stepped_shaft socket_bolt l_bracket "
                             "spacer_ring mounting_plate stepped_block knob hex_standoff u_channel "
                             "flanged_bushing wedge_block spoked_wheel end_cap link_bar",
    "Multi-feature parts": "fork_end three_lug_housing pillow_block twin_arm_rocker clevis_bracket "
                           "diamond_flange support_seat crank",
    "Operation-specific parts": "gusset_bracket corner_rib_stand open_box lidless_housing counterbore_plate "
                                "csk_cover side_pad through_boss sheet_angle sheet_channel flanged_elbow "
                                "bent_tube square_to_round rect_to_circle",
    # 困难子集，rubric 在 eval/rubrics_hard（--rubric-dir 指定），不计入上面 50 题
    "Showcase parts": "quad_frame connecting_rod finned_housing sprocket handwheel camshaft planet_carrier impeller",
    "Showcase parts (hard)": "crankshaft brake_disc wheel_rim geneva_wheel spiral_staircase engine_piston "
                             "pipe_manifold gearbox_housing heat_sink honeycomb_panel gear_pump_body cold_plate "
                             "truss_beam turbine_disk bt40_holder hydraulic_manifold rocket_fin_can "
                             "ratchet_wheel injector_plate robot_arm_link menger_sponge u_joint_yoke",
    # 三视图 track，rubric 在 eval/rubrics_three_view，题面附图纸（tasks 中的 image 字段）
    "Three-view basic parts": "b01_l_bracket b02_plate_with_holes b03_t_bracket b04_keycap b05_keycap_full "
                              "b06_lock_striker b07_switch_panel b11_gear_disc b22_round_bowl",
    "Three-view composite parts": "c1_mech_keyboard c2_gearbox c3_gear_pair c4_desktop_pen_holder",
    # 自行设计、由参考件投影出图
    "Three-view projected basic parts": "d01_v_block d04_flange_cover d05_stepped_shaft d06_u_clevis "
                                        "d07_eccentric_cam d09_angle_bracket d10_clamp_plate "
                                        "d11_dovetail_slide d12_cut_block d18_gear_rack "
                                        "d21_t_slot_block d22_flanged_bushing d23_cross_plate d24_u_channel "
                                        "d25_step_pin d26_square_flange d27_guide_rail d35_shaft_collar",
    "Three-view projected composite parts": "d02_bearing_block d03_rib_bracket d08_pipe_tee d13_v_pulley "
                                            "d14_link_rod d15_motor_mount d16_heat_sink d17_pipe_clamp "
                                            "d19_hinge_leaf d20_ribbed_base d28_round_cup d29_hanger_bracket "
                                            "d30_valve_body d31_lever_arm d32_tool_holder d33_shell_housing "
                                            "d34_fixture_plate d36_flanged_tube d37_column_base",
}
ORDER = [c for v in KINDS.values() for c in v.split()]
KIND_OF = {c: k for k, v in KINDS.items() for c in v.split()}


PROMPT_FIELDS = {"zh": "prompt", "en": "prompt_en"}


def load_tasks(rubric_dir: Path, lang: str = "zh") -> list[dict]:
    field = PROMPT_FIELDS[lang]
    tasks = {}
    for p in rubric_dir.glob("*.yaml"):
        r = yaml.safe_load(p.read_text(encoding="utf-8"))
        tasks[r["case"]] = r
    out = []
    for c in sorted(tasks, key=lambda c: (ORDER.index(c) if c in ORDER else len(ORDER), c)):
        task = {"case": c, "kind": KIND_OF.get(c, ""), "prompt": tasks[c][field]}
        if "image" in tasks[c]:
            task["image"] = tasks[c]["image"]  # 相对 LLMPlanner 目录的图纸路径
        out.append(task)
    return out


def _boot(xs: list[float], n: int = 2000, seed: int = 0) -> tuple[float, float]:
    rnd = random.Random(seed)
    ms = sorted(sum(rnd.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n) - 1]


def _pass_at_k(n: int, c: int, k: int) -> float:
    return 1.0 if n - c < k else 1.0 - comb(n - c, k) / comb(n, k)


def _stats(runs: dict[str, list[dict]], repeats: int) -> dict:
    flat = [r for v in runs.values() for r in v]
    scores = [r["score"] for r in flat]
    lo, hi = _boot(scores)
    out = {
        "tasks": len(runs), "runs": len(flat),
        "score": round(sum(scores) / len(flat), 1), "ci95": [round(lo, 1), round(hi, 1)],
        "accepted": sum(r["accepted"] for r in flat),
        "core_form": round(sum(r["hard_ok"] for r in flat) / len(flat), 3),
    }
    for k in range(1, repeats + 1):
        out[f"pass@{k}"] = round(
            sum(_pass_at_k(len(v), sum(r["accepted"] for r in v), k) for v in runs.values()) / len(runs), 3)
    images = [r["image"] for r in flat if r.get("image") is not None]
    if images:
        out["image_score"] = round(sum(images) / len(flat), 1)  # 缺失的运行按 0 计
    return out


def summarize(score_files: list[Path], rubric_dir: Path, repeats: int) -> dict:
    graded = {}
    for f in score_files:
        for x in json.loads(f.read_text(encoding="utf-8")):
            graded[Path(x["step"]).stem] = x
    with_image = any("image" in g for g in graded.values())
    runs = {}
    for t in load_tasks(rubric_dir):
        c = t["case"]
        runs[c] = []
        for k in range(1, repeats + 1):
            g = graded.get(f"{c}_r{k}")
            run = {"score": g["score"] if g else 0.0, "accepted": bool(g and g["accepted"]),
                   "hard_ok": bool(g and g["hard_ok"]), "missing": g is None}
            if with_image:
                run["image"] = g["image"]["image_score"] if g and "image" in g else 0.0
            runs[c].append(run)
    report = {"all": _stats(runs, repeats), "by_kind": {}, "per_task": {}}
    for kind in KINDS:
        sub = {c: v for c, v in runs.items() if KIND_OF.get(c) == kind}
        if sub:
            report["by_kind"][kind] = _stats(sub, repeats)
    for c, v in runs.items():
        report["per_task"][c] = {"scores": [r["score"] for r in v], "accepted": sum(r["accepted"] for r in v),
                                 "missing": sum(r["missing"] for r in v)}
        if with_image:
            report["per_task"][c]["image_scores"] = [r["image"] for r in v]
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rubric-dir", default="eval/rubrics")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tasks", help="导出题目 (case, kind, prompt) 为 JSONL")
    t.add_argument("--output", default="tasks.jsonl")
    t.add_argument("--lang", choices=sorted(PROMPT_FIELDS), default="zh",
                   help="题面语言：zh 导出 prompt 字段，en 导出 prompt_en 字段")
    s = sub.add_parser("summary", help="汇总 geomcheck.rubric 的打分结果")
    s.add_argument("scores", nargs="+", type=Path)
    s.add_argument("--repeats", type=int, default=2)
    s.add_argument("--output")
    args = ap.parse_args()
    rubric_dir = Path(args.rubric_dir)
    if args.cmd == "tasks":
        tasks = load_tasks(rubric_dir, args.lang)
        Path(args.output).write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in tasks),
                                     encoding="utf-8")
        print(f"{len(tasks)} tasks -> {args.output}")
        return
    report = summarize(args.scores, rubric_dir, args.repeats)
    a = report["all"]
    print(f"ALL  tasks={a['tasks']} runs={a['runs']} score={a['score']} ci95={a['ci95']} "
          + " ".join(f"{k}={a[k]}" for k in a if k.startswith("pass@")) + f" core_form={a['core_form']}"
          + (f" image_score={a['image_score']}" if "image_score" in a else ""))
    for kind, b in report["by_kind"].items():
        print(f"  {kind:<26} tasks={b['tasks']:>2} score={b['score']:>5} pass@1={b['pass@1']}"
              + (f" image_score={b['image_score']}" if "image_score" in b else ""))
    missing = sum(v["missing"] for v in report["per_task"].values())
    if missing:
        print(f"  missing STEP files scored 0: {missing}")
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
