#!/bin/bash
# 重跑「用满 50 次调用、没交出装配包」的测例：只重跑装配，复用零件。
set -euo pipefail
LP=/home/yrq/SolidWorksTutorial/LLMPlanner
cd "$LP"
LOG=eval/assembly_simplecad_sonnet5.budget_rerun.log
ART=eval/artifacts/assembly_simplecad_sonnet5

# 从 final.json 选：budget_exhausted 且未交付装配包；排除已有有效导出的（如台灯）
CASES=$(venv-scad/bin/python - <<'PY'
import json
from pathlib import Path
rows = json.load(open("eval/assembly_simplecad_sonnet5_final.json", encoding="utf-8"))["rows"]
# case_id -> yaml stem：优先读 artifacts 里旧 run.json
art = Path("eval/artifacts/assembly_simplecad_sonnet5")
need = []
for r in rows:
    a = r.get("agent") or {}
    if a.get("status") != "budget_exhausted":
        continue
    if a.get("export_ok") or a.get("delivered"):
        continue  # 台灯：用满预算但有有效包
    if r.get("infra_error") and a.get("status") == "error":
        continue
    cid = r["case_id"]
    runp = art / cid / "r1" / "run.json"
    name = None
    if runp.exists():
        name = json.loads(runp.read_text(encoding="utf-8")).get("case_name")
    if not name:
        # 常见别名回退
        aliases = {
            "two_stage_gearbox": "gearbox", "single_cylinder_engine": "engine",
            "adjustable_desk_lamp": "desk_lamp", "folding_umbrella": "umbrella",
            "drill_planetary_gearbox": "drill_gearbox", "sewing_machine_drive": "sewing_machine",
            "film_projector_drive": "projector", "crank_rocker_linkage": "crank_rocker",
            "offset_slider_crank": "offset_slider_crank", "twin_cylinder_pump": "twin_pump",
            "pumpjack_drive": "pumpjack", "windshield_wiper_linkage": "wiper_linkage",
            "beam_engine": "beam_engine", "clock_motion_works": "clock_motion",
            "hand_winch": "hand_winch", "turntable_ring_drive": "turntable",
            "star_gear_reducer": "star_reducer", "lead_screw_stage": "lead_screw_stage",
            "gear_driven_slider_crank": "geared_slider_crank",
            "epicyclic_carrier_output": "planetary_carrier", "excavator_arm": "excavator_arm",
            "scara_arm": "scara_arm", "pantograph": "pantograph",
            "ackermann_steering": "ackermann_steering", "toggle_clamp": "toggle_clamp",
            "parallel_gripper": "parallel_gripper", "double_wishbone": "double_wishbone",
        }
        name = aliases.get(cid, cid)
    need.append((cid, name))
print(",".join(n for _, n in need))
for cid, name in need:
    print(f"# {cid} -> {name}", file=__import__("sys").stderr)
print(f"# total={len(need)}", file=__import__("sys").stderr)
PY
)

echo "cases=$CASES"
IFS=',' read -ra NAMES <<< "$CASES"

# 归档旧装配产物，保留 parts/
for name in "${NAMES[@]}"; do
  # 找 case_id 目录
  cid=$(venv-scad/bin/python - <<PY
import json
from pathlib import Path
art = Path("$ART")
for p in art.glob("*/r1/run.json"):
    r = json.loads(p.read_text(encoding="utf-8"))
    if r.get("case_name") == "$name" or p.parents[1].name == "$name":
        print(p.parents[1].name); break
else:
    # 用别名反查
    aliases = {
        "gearbox":"two_stage_gearbox","engine":"single_cylinder_engine",
        "desk_lamp":"adjustable_desk_lamp","umbrella":"folding_umbrella",
        "drill_gearbox":"drill_planetary_gearbox","sewing_machine":"sewing_machine_drive",
        "projector":"film_projector_drive","crank_rocker":"crank_rocker_linkage",
        "twin_pump":"twin_cylinder_pump","pumpjack":"pumpjack_drive",
        "wiper_linkage":"windshield_wiper_linkage","clock_motion":"clock_motion_works",
        "turntable":"turntable_ring_drive","star_reducer":"star_gear_reducer",
        "geared_slider_crank":"gear_driven_slider_crank","planetary_carrier":"epicyclic_carrier_output",
    }
    print(aliases.get("$name", "$name"))
PY
)
  d="$ART/$cid/r1"
  [ -d "$d" ] || { echo "missing $d"; continue; }
  arch="$d/assembly_budget_exhausted_attempt"
  if [ -e "$arch" ]; then arch="${arch}_$(date +%H%M%S)"; fi
  mkdir -p "$arch"
  for f in assembly assembly.json run.json assembly_export.json; do
    [ -e "$d/$f" ] && mv "$d/$f" "$arch/"
  done
  echo "archived $cid -> $arch (parts kept=$([ -d "$d/parts" ] && echo yes || echo no))"
done

source /home/yrq/ttsw-gateway/env.sh >/dev/null 2>&1
export OPENAI_API_KEY="$(fetch_ttsw_token)"
export OPENAI_BASE_URL=http://127.0.0.1:15721/tencent/v1

# 冒烟
venv-scad/bin/python - <<'PY'
from openai import OpenAI
import os
c = OpenAI(base_url=os.environ["OPENAI_BASE_URL"], api_key=os.environ["OPENAI_API_KEY"], timeout=120)
r = c.chat.completions.create(model="claude-sonnet-5", messages=[{"role":"user","content":"reply OK"}], max_tokens=8)
print("smoke", r.model, r.choices[0].message.content)
PY

nohup venv-scad/bin/python -X utf8 -u eval/eval_assembly_simplecad.py \
  --cases "$CASES" --model claude-sonnet-5 --case-workers 4 --repeats 1 \
  --output eval/assembly_simplecad_sonnet5.json \
  > "$LOG" 2>&1 &
echo "PID=$! log=$LOG"
sleep 25
head -40 "$LOG"
