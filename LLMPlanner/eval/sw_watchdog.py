"""评测期间看护 SolidWorks：崩溃、服务无响应或内存泄漏过大时重启 SolidWorks 与 C# HTTP 服务。"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICE_EXE = REPO_ROOT / "Chapter1.ConnectToSw" / "bin" / "Debug" / "Chapter1.ConnectToSw.exe"
SW_EXE = Path(os.environ.get(
    "SW_EXE", r"C:\Program Files\softwares\solidworks2025\Corp\SOLIDWORKS\SLDWORKS.exe"
))


def _ps(script: str, timeout: float = 60) -> str:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace",
    )
    return out.stdout.strip()


def sw_private_gb() -> float | None:
    """SLDWORKS 进程的私有内存（GB）；进程不存在时为 None。"""
    out = _ps("(Get-Process SLDWORKS -ErrorAction SilentlyContinue | Select-Object -First 1).PrivateMemorySize64")
    return int(out) / 1024 ** 3 if out.isdigit() else None


def service_ok(sw_url: str) -> bool:
    try:
        return requests.get(f"{sw_url}/api/ping", timeout=15).status_code == 200
    except requests.RequestException:
        return False


def check(sw_url: str, max_private_gb: float) -> str | None:
    """需要重启时返回原因。"""
    mem = sw_private_gb()
    if mem is None:
        return "SolidWorks 进程不存在"
    if not service_ok(sw_url):
        return "SolidWorks HTTP 服务无响应"
    if max_private_gb > 0 and mem > max_private_gb:
        return f"SolidWorks 私有内存 {mem:.1f} GB 超过 {max_private_gb:g} GB"
    return None


def _sw_ready() -> bool:
    out = _ps("$p = Get-Process SLDWORKS -ErrorAction SilentlyContinue | Select-Object -First 1; "
              "if ($p -and $p.MainWindowHandle -ne 0 -and $p.Responding) { 'ready' }")
    return out == "ready"


def restart(sw_url: str, log=print) -> None:
    """结束服务与 SolidWorks，重新启动并等到服务可用；失败时抛 RuntimeError。"""
    log("  [watchdog] restarting SolidWorks and the HTTP service")
    _ps("Get-Process Chapter1.ConnectToSw, SLDWORKS -ErrorAction SilentlyContinue | Stop-Process -Force")
    time.sleep(10)
    subprocess.Popen([str(SW_EXE)], cwd=str(SW_EXE.parent))
    deadline = time.time() + 600
    while time.time() < deadline and not _sw_ready():
        time.sleep(5)
    if not _sw_ready():
        raise RuntimeError("SolidWorks 启动超时")
    # 主窗口出现后插件与 COM 注册还要一段时间
    time.sleep(30)
    for attempt in range(3):
        _ps("Get-Process Chapter1.ConnectToSw -ErrorAction SilentlyContinue | Stop-Process -Force")
        subprocess.Popen(
            [str(SERVICE_EXE)], cwd=str(SERVICE_EXE.parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        for _ in range(30):
            time.sleep(2)
            if service_ok(sw_url):
                log(f"  [watchdog] SolidWorks ready (private {sw_private_gb() or 0:.1f} GB)")
                return
        log(f"  [watchdog] service not up yet (attempt {attempt + 1}), retrying")
        time.sleep(20)
    raise RuntimeError("SolidWorks HTTP 服务启动失败")
