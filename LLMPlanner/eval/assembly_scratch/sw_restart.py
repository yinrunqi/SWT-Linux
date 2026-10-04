"""重启 SolidWorks 与 HTTP 服务（评测里 SolidWorks 崩溃或服务卡死时用）。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
SW = "http://localhost:5100"
SW_EXE = os.environ.get(
    "SOLIDWORKS_EXE", r"C:\Program Files\softwares\solidworks2025\Corp\SOLIDWORKS\SLDWORKS.exe")
SERVICE_EXE = ROOT.parent / "Chapter1.ConnectToSw" / "bin" / "Debug" / "Chapter1.ConnectToSw.exe"


def restart_solidworks(attempts: int = 3) -> None:
    """SolidWorks 启动时偶尔会起两个进程，服务连到未就绪的那个会直接退出：此时整体重来。"""
    print("  重启 SolidWorks 与服务", flush=True)
    for attempt in range(attempts):
        for image in ("Chapter1.ConnectToSw.exe", "SLDWORKS.exe"):
            subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)
        time.sleep(5)
        subprocess.Popen([SW_EXE])
        time.sleep(90 + 30 * attempt)
        log = open(Path(os.environ.get("TEMP", ".")) / "svc.log", "ab")
        service = subprocess.Popen([str(SERVICE_EXE)], cwd=str(SERVICE_EXE.parent), stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(60):
            time.sleep(2)
            if service.poll() is not None:
                break
            try:
                if requests.get(f"{SW}/api/ping", timeout=3).ok:
                    return
            except requests.RequestException:
                pass
        print(f"  服务未就绪（第 {attempt + 1} 次），重来", flush=True)
    raise RuntimeError("服务重启后仍无响应")
