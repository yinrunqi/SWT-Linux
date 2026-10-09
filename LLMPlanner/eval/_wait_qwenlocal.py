"""Poll the self-served Qwen endpoint until /models lists the target model."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from load_env import ensure_test_env_loaded

ensure_test_env_loaded()
BASE = os.environ["QWENLOCAL_BASE_URL"].rstrip("/")
KEY = os.environ.get("QWENLOCAL_API_KEY", "")
MODEL = "Qwen3.8-Flash-Next-FP8"
DEADLINE = time.time() + 6 * 3600
attempt = 0
while time.time() < DEADLINE:
    attempt += 1
    try:
        response = requests.get(
            f"{BASE}/models",
            headers={"Authorization": f"Bearer {KEY}"},
            timeout=20,
        )
        ids = [item.get("id") for item in (response.json().get("data") or [])] if response.ok else []
        print(f"attempt={attempt} status={response.status_code} has_model={MODEL in ids}", flush=True)
        if response.ok and MODEL in ids:
            print("Qwen_ready", flush=True)
            raise SystemExit(0)
    except requests.RequestException as exc:
        print(f"attempt={attempt} {type(exc).__name__}: {str(exc)[:160]}", flush=True)
    time.sleep(20)
print("Qwen_timeout", flush=True)
raise SystemExit(2)
