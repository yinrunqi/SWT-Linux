"""
从 LLMPlanner/.env 加载测试用环境变量（无第三方依赖）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
ENV_FILE = ROOT_DIR / ".env"

KNOWN_KEYS = frozenset({
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "JUHEAI_API_KEY",
    "JUHEAI_BASE_URL",
    "AIPING_API_KEY",
    "AIPING_BASE_URL",
    "QWENLOCAL_API_KEY",
    "QWENLOCAL_BASE_URL",
    "GLMLOCAL_API_KEY",
    "GLMLOCAL_BASE_URL",
    "PLANNER_MODEL",
    "VISION_MODEL",
    "PLAN_MODEL",
    "SW_API_URL",
    "PYTHONUTF8",
    "PYTHONIOENCODING",
})

_loaded = False


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}

    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key] = value
    return result


def apply_env(
    values: dict[str, str],
    *,
    only_known: bool = True,
    override: bool = False,
) -> None:
    for key, value in values.items():
        if only_known and key not in KNOWN_KEYS:
            continue
        if override:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # 上述变量只影响子进程；当前 Windows 进程仍可能是 GBK，打印 emoji 会抛异常。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def ensure_test_env_loaded() -> bool:
    global _loaded
    if _loaded:
        return ENV_FILE.is_file()
    _loaded = True
    if ENV_FILE.is_file():
        apply_env(parse_env_file(ENV_FILE))
    return ENV_FILE.is_file()


def emit_windows_set_commands(path: Path | None = None) -> list[str]:
    env_path = path or ENV_FILE
    lines: list[str] = []
    for key, value in parse_env_file(env_path).items():
        if key not in KNOWN_KEYS:
            continue
        escaped = value.replace("%", "%%")
        lines.append(f'set "{key}={escaped}"')
    lines.append('set "PYTHONUTF8=1"')
    lines.append('set "PYTHONIOENCODING=utf-8"')
    return lines
