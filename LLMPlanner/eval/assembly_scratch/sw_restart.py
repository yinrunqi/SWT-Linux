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


def _kill_port_owner(port: int) -> None:
    """清掉镜像名清理漏掉的旧监听者，避免新服务误把旧服务的 ping 当成自己已就绪。"""
    result = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, errors="replace",
    )
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 5 and fields[1].endswith(f":{port}") and fields[3].upper() == "LISTENING":
            subprocess.run(["taskkill", "/F", "/PID", fields[-1]], capture_output=True)


def _api_ready() -> bool:
    """不仅检查监听，还确认服务所连接的 SolidWorks COM 可正常响应。"""
    try:
        if not requests.get(f"{SW}/api/ping", timeout=3).ok:
            return False
        response = requests.post(f"{SW}/api/close-all-documents", timeout=10)
        return response.ok and bool(response.json().get("success"))
    except (requests.RequestException, ValueError):
        return False


def restart_solidworks(attempts: int = 3) -> None:
    """SolidWorks 启动时偶尔会起两个进程，服务连到未就绪的那个会直接退出：此时整体重来。"""
    print("  重启 SolidWorks 与服务", flush=True)
    for attempt in range(attempts):
        for image in ("Chapter1.ConnectToSw.exe", "SLDWORKS.exe"):
            subprocess.run(["taskkill", "/F", "/IM", image], capture_output=True)
        time.sleep(5)
        _kill_port_owner(5100)
        time.sleep(2)
        subprocess.Popen([SW_EXE])
        time.sleep(90 + 30 * attempt)
        log = open(Path(os.environ.get("TEMP", ".")) / "svc.log", "ab")
        service = subprocess.Popen([str(SERVICE_EXE)], cwd=str(SERVICE_EXE.parent), stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        log.close()
        healthy = 0
        for _ in range(60):
            time.sleep(2)
            if service.poll() is not None:
                # 可能是另一轮已经成功启动了服务，本进程仅因端口冲突退出。
                if _api_ready():
                    return
                break
            try:
                if requests.get(f"{SW}/api/ping", timeout=3).ok:
                    healthy += 1
                    if healthy >= 3:
                        if _api_ready():
                            return
                        healthy = 0
                    continue
            except requests.RequestException:
                healthy = 0
        print(f"  服务未就绪（第 {attempt + 1} 次），重来", flush=True)
    raise RuntimeError("服务重启后仍无响应")
