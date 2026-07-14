"""
测试前环境配置：从 .env 加载变量，校验 API Key 与 SolidWorks 服务。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import requests

from load_env import ENV_FILE, ROOT_DIR, apply_env, emit_windows_set_commands, parse_env_file

VENV_PYTHON = ROOT_DIR / "venv" / "Scripts" / "python.exe"


def _fail(msg: str) -> int:
    print(f"[失败] {msg}", file=sys.stderr)
    return 1


def load_and_apply() -> dict[str, str]:
    if not ENV_FILE.is_file():
        example = ROOT_DIR / ".env.example"
        raise FileNotFoundError(f"请先复制 {example.name} 为 .env 并填写 OPENAI_API_KEY")
    values = parse_env_file(ENV_FILE)
    apply_env(values)
    return values


def check_environment() -> int:
    print("=" * 60)
    print("LLM 测试环境检查")
    print("=" * 60)

    try:
        load_and_apply()
    except FileNotFoundError as e:
        return _fail(str(e))

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "sk-your-key-here":
        return _fail("OPENAI_API_KEY 未设置或仍为占位符，请编辑 .env")

    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    sw_url = os.getenv("SW_API_URL", "http://localhost:5100").rstrip("/")

    print(f"  .env 路径: {ENV_FILE}")
    print(f"  OPENAI_API_KEY: {api_key[:8]}...")
    if base_url:
        print(f"  OPENAI_BASE_URL: {base_url}")
    else:
        print("  OPENAI_BASE_URL: (未设置，使用 OpenAI 默认)")
    print(f"  SW_API_URL: {sw_url}")

    try:
        resp = requests.get(f"{sw_url}/api/ping", timeout=5)
        data = resp.json()
        if not data.get("success"):
            return _fail(f"SolidWorks 服务异常: {data.get('message', resp.text)}")
    except Exception as e:
        return _fail(
            f"无法连接 SolidWorks 服务 ({sw_url}): {e}\n"
            "请先启动 SolidWorks，再运行 run_service.bat"
        )

    print("  SolidWorks API: 正常")
    print()
    print("[成功] 环境就绪，可以运行测试。")
    return 0


def run_test(script: str, extra_args: list[str]) -> int:
    code = check_environment()
    if code != 0:
        return code

    script_path = Path(script)
    if not script_path.is_file():
        if (ROOT_DIR / script).is_file():
            script_path = ROOT_DIR / script
        else:
            return _fail(f"找不到测试脚本: {script}")

    python = VENV_PYTHON if VENV_PYTHON.is_file() else Path(sys.executable)
    cmd = [str(python), "-X", "utf8", str(script_path), *extra_args]
    print(f"\n[运行] {' '.join(cmd)}\n")
    return subprocess.call(cmd, cwd=str(ROOT_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 测试环境配置与检查")
    parser.add_argument("--check", action="store_true", help="加载 .env 并检查")
    parser.add_argument("--emit-cmd", action="store_true", help="输出 Windows CMD set 命令")
    parser.add_argument("--run", metavar="SCRIPT", help="检查通过后运行测试脚本")
    parser.add_argument("args", nargs="*", help="传给测试脚本的额外参数")
    opts = parser.parse_args()

    if opts.emit_cmd:
        if not ENV_FILE.is_file():
            print("echo [错误] 未找到 .env", file=sys.stderr)
            return 1
        for line in emit_windows_set_commands():
            print(line)
        return 0

    if opts.run:
        return run_test(opts.run, opts.args)

    return check_environment()


if __name__ == "__main__":
    sys.exit(main())
