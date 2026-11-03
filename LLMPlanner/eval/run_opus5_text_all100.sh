#!/bin/bash
# Claude Opus 5, L1_v2 + compact-context, all 100 text-to-CAD cases.
#
# From LLMPlanner:
#   bash eval/run_opus5_text_all100.sh
#
# Writes:
#   eval/text_workflow_l1v2_compact_opus5_all100_en_result.json
#   eval/text_workflow_l1v2_compact_opus5_all100_en.log
#   eval/text_workflow_l1v2_compact_opus5_all100_en_summary.txt
#     Score, pass@1, average prompt/completion/total tokens, core_form
#   eval/artifacts/text_workflow_l1v2_compact_opus5_all100_en_result/
#
# Windows must already be running SolidWorks, the C# service on port 5100,
# and this reverse tunnel (leave the window open):
#   ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -N \
#     -R 127.0.0.1:15110:127.0.0.1:5100 \
#     -R 127.0.0.1:2225:127.0.0.1:22 \
#     yrq@116.177.253.45
# STEP and SLDPRT are saved under
#   C:\Users\38409\Desktop\Workflow-CAD\SolidWorksTutorial
#
# --resume keeps finished cases and continues an interrupted case from its
# checkpoint. Do not pass --reasoning-effort; the provider default is used.
# --sw-restarts 0: the Linux watchdog cannot restart the desktop SolidWorks.

set -u
cd "$(dirname "$0")/.."
PY="./venv-t2c/bin/python"
LOG="eval/text_workflow_l1v2_compact_opus5_all100_en.log"
OUT="eval/text_workflow_l1v2_compact_opus5_all100_en_result.json"

if ! curl -sf -m 5 -H 'Host: localhost' http://127.0.0.1:15110/api/ping >/dev/null; then
  echo "SolidWorks tunnel is down. Start the ssh -R command in this script's header on the Windows desktop." >&2
  exit 1
fi

if ! ss -ltn | grep -q '127.0.0.1:15100'; then
  nohup "$PY" eval/sw_host_proxy.py >> eval/sw_host_proxy.log 2>&1 &
  echo "started sw proxy pid $!"
fi
if ! curl -sf -m 3 -o /dev/null http://127.0.0.1:5000/; then
  nohup "$PY" -X utf8 -m web.app --port 5000 --sw-url http://127.0.0.1:15100 >> eval/web_app.log 2>&1 &
  echo "started web pid $!"
fi
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -sf -m 3 http://127.0.0.1:15100/api/ping >/dev/null && break
  sleep 1
done
if ! curl -sf -m 5 http://127.0.0.1:15100/api/ping >/dev/null; then
  echo "proxy http://127.0.0.1:15100/api/ping failed" >&2
  exit 1
fi

if ! pgrep -f 'eval/watch_opus5_text_all100.py' >/dev/null; then
  nohup python3 eval/watch_opus5_text_all100.py >> eval/text_workflow_l1v2_compact_opus5_all100_en_summary.log 2>&1 &
  echo "started summary watcher pid $!"
fi

{
  echo "===== $(date -Iseconds) ====="
  echo -n "HEAD "
  git -C .. rev-parse HEAD
  git -C .. status --short
} | tee -a "$LOG"

"$PY" -X utf8 -u eval/eval_text_singleturn_qwen.py \
  --action-level L1_v2 --compact-context \
  --provider ttswitch --model claude-opus-5 \
  --split all100 --lang en --repeats 1 --max-llm-calls 40 \
  --pause-every 3 --sw-restarts 0 --resume \
  --web-url http://127.0.0.1:5000 --sw-url http://127.0.0.1:15100 \
  --windows-root 'C:\Users\38409\Desktop\Workflow-CAD\SolidWorksTutorial' \
  --output "$OUT" \
  2>&1 | tee -a "$LOG"
echo "eval exit=${PIPESTATUS[0]}" | tee -a "$LOG"
