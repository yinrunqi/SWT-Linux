"""不经过 planner 包导入纯模块，避免 venv-occ 缺少 pydantic/requests。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, rel: str):
    path = _ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_gears():
    return _load("_planner_gears", "planner/gears.py")


def load_asm_refs():
    return _load("_asm_refs", "planner/assembly_workflow/refs.py")
