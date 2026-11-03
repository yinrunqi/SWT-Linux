#!/bin/bash
# Qwen3.8-Flash-Next-FP8 跑 SimpleCADAPI 装配基线（50 例，中文题面）。LLM 经本机代理 + SSH 反向隧道访问，远端不持有 Key。
set -euo pipefail
cd /home/yrq/sca_qwen_eval/LLMPlanner
PY=/home/yrq/SolidWorksTutorial/LLMPlanner/venv-scad/bin/python
OUT=eval/assembly_simplecad_qwen38_medium.json
export OPENAI_BASE_URL=http://127.0.0.1:15800/v1
export OPENAI_API_KEY=unused
nohup "$PY" -X utf8 -u eval/eval_assembly_simplecad.py \
  --cases all --model Qwen3.8-Flash-Next-FP8 --reasoning-effort medium --max-tokens 32000 --temperature none \
  --max-asm-calls 100 --max-part-calls 40 --case-workers 4 --part-workers 3 --repeats 1 \
  --output "$OUT" > "${OUT%.json}.log" 2>&1 &
echo "PID=$!"
