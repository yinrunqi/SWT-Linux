"""运行 8 例装配加速 paired A/B；每题交替 legacy / optimized 顺序并可断点续跑。"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CASES = (
    "two_stage_gearbox",
    "single_cylinder_engine",
    "film_projector_drive",
    "drill_planetary_gearbox",
    "turntable_ring_drive",
    "clock_motion_works",
    "epicyclic_carrier_output",
    "crank_rocker_linkage",
)
OUTPUTS = {
    "legacy": ROOT / "eval" / "assembly_speed_ab_qwen38flash_8_legacy.json",
    "optimized": ROOT / "eval" / "assembly_speed_ab_qwen38flash_8_optimized.json",
}
ARTIFACTS = {
    mode: ROOT / "eval" / "artifacts" / f"assembly_speed_ab_qwen38flash_8_{mode}"
    for mode in OUTPUTS
}
INTERRUPTED = {"llm_error", "sw_lost", "resume_failed", "context_exceeded", "infra_exception"}
MAX_ATTEMPTS = 5


def command(case: str, mode: str) -> list[str]:
    args = [
        sys.executable,
        "-m",
        "eval.eval_assembly_scratch",
        "--cases",
        case,
        "--condition",
        "gold",
        "--repeats",
        "1",
        "--action-level",
        "L1",
        "--workflow",
        "v3",
        "--provider",
        "qwenlocal",
        "--model",
        "Qwen3.8-Flash-Next-FP8",
        "--compact-context",
        "--asm-compact-context",
        "--reasoning-effort",
        "medium",
        "--max-asm-calls",
        "100",
        "--asm-max-tokens",
        "16000",
        "--infra-retries",
        "1",
        "--output-dir",
        str(ARTIFACTS[mode]),
        "--output",
        str(OUTPUTS[mode]),
        "--resume",
    ]
    if mode == "optimized":
        args.append("--asm-speed-optimizations")
    return args


def complete(case: str, mode: str) -> bool:
    output = OUTPUTS[mode]
    if not output.is_file():
        return False
    report = json.loads(output.read_text(encoding="utf-8"))
    row = next(
        (
            item
            for item in report.get("results") or []
            if item.get("case_id") == case
            and item.get("condition") == "gold"
            and item.get("repeat") == 1
            and item.get("workflow") == "v3"
        ),
        None,
    )
    if not row:
        return False
    assembly = row.get("assembly") or {}
    return not (
        row.get("infra_contaminated")
        or assembly.get("infra_error")
        or assembly.get("status") in INTERRUPTED
    )


def main() -> None:
    for index, case in enumerate(CASES):
        modes = ("legacy", "optimized") if index % 2 == 0 else ("optimized", "legacy")
        for mode in modes:
            for attempt in range(1, MAX_ATTEMPTS + 1):
                if complete(case, mode):
                    print(
                        f"\n=== paired A/B {index + 1}/{len(CASES)} {case} {mode} === 已完成，跳过",
                        flush=True,
                    )
                    break
                print(
                    f"\n=== paired A/B {index + 1}/{len(CASES)} {case} {mode} "
                    f"attempt {attempt}/{MAX_ATTEMPTS} ===",
                    flush=True,
                )
                try:
                    subprocess.run(command(case, mode), cwd=ROOT, check=True)
                except subprocess.CalledProcessError as exc:
                    print(f"子评测异常退出：{exc}；10 秒后断点重试", flush=True)
                    time.sleep(10)
                    continue
                if complete(case, mode):
                    break
                print("结果仍是可恢复的中断状态；10 秒后从 checkpoint 继续", flush=True)
                time.sleep(10)
            else:
                raise RuntimeError(f"{case} {mode} 连续 {MAX_ATTEMPTS} 次仍未得到完整结果")


if __name__ == "__main__":
    main()
