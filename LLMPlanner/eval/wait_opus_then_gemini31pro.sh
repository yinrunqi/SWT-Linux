#!/bin/bash
# Wait until the opus5-all100 eval process exits, then start gemini-3.1-pro all100.
# The tmux session itself stays up (its panes are idle bash), so completion is
# the eval python and run_opus5_text_all100.sh both being gone.
#
# From LLMPlanner:
#   nohup bash eval/wait_opus_then_gemini31pro.sh >> eval/wait_opus_then_gemini31pro.log 2>&1 &

set -u
cd "$(dirname "$0")/.."
LOG="eval/wait_opus_then_gemini31pro.log"
SESSION="gemini31pro-all100"

log() {
  echo "$(date -Iseconds) $*" >> "$LOG"
}

opus_busy() {
  pgrep -f 'eval/eval_text_singleturn_qwen.py.*--model claude-opus-5' >/dev/null \
    || pgrep -f 'bash eval/run_opus5_text_all100.sh' >/dev/null
}

log "waiting for opus5-all100 eval to exit"
waited=0
while opus_busy; do
  sleep 30
  waited=$((waited + 30))
  if (( waited % 600 == 0 )); then
    log "still waiting (${waited}s)"
  fi
done
log "opus eval processes are gone; waiting 20s and checking again"
sleep 20
if opus_busy; then
  log "opus eval came back; keep waiting"
  exec "$0"
fi

log "waiting for the SolidWorks tunnel"
until curl -sf -m 5 -H 'Host: localhost' http://127.0.0.1:15110/api/ping >/dev/null; do
  log "tunnel down; retry in 60s"
  sleep 60
done

if pgrep -f 'eval/eval_text_singleturn_qwen.py.*--model gemini-3.1-pro' >/dev/null; then
  log "gemini-3.1-pro eval is already running; not starting another"
  exit 0
fi
if tmux has-session -t "$SESSION" 2>/dev/null; then
  log "tmux $SESSION already exists; not starting another"
  exit 0
fi

# web.app imported agent.py before the gemini context limit was added.
# Restart only that process so the new table is loaded. Leave the SW proxy up.
if pgrep -f 'web.app --port 5000' >/dev/null; then
  log "restarting web.app so gemini context limits load"
  pkill -f 'web.app --port 5000' || true
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if ! ss -ltn "sport = :5000" | grep -q LISTEN; then
      break
    fi
    sleep 1
  done
  if ss -ltn "sport = :5000" | grep -q LISTEN; then
    log "port 5000 still in use after SIGTERM; sending SIGKILL"
    pkill -9 -f 'web.app --port 5000' || true
    sleep 2
  fi
fi
if ss -ltn "sport = :5000" | grep -q LISTEN; then
  log "port 5000 did not free; refusing to start gemini against the old web.app"
  exit 1
fi

log "starting tmux $SESSION"
tmux new-session -d -s "$SESSION" -c "$PWD" -n eval \
  "bash eval/run_gemini31pro_text_all100.sh"
log "started tmux $SESSION"
