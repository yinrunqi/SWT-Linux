"""扩展测例零件 rubric：按规格生成的 OCC 实体满分，改动一个尺寸后在对应条目上失败。"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests.refs_bench import shape

from eval.assembly_scratch.bench_cases import CASES
from eval.assembly_scratch.bench_cases2 import CASES2

RUBRICS = Path(__file__).resolve().parents[2] / "eval" / "assembly_scratch" / "rubrics"
PARTS = {pid: p for c in (*CASES.values(), *CASES2.values()) for pid, p in c.parts.items()}


def _grade(pid: str, p: dict) -> dict:
    return evaluate(load_rubric(RUBRICS / f"{pid}.yaml"), Part(shape(p)))


def _failed(result: dict) -> set[str]:
    return {row["id"] for row in result["rubrics"] if not row["ok"]}


@pytest.mark.parametrize("pid", sorted(PARTS))
def test_reference(pid):
    result = _grade(pid, PARTS[pid])
    assert result["accepted"], _failed(result)


def _first(kind: str, pred=lambda p: True) -> tuple[str, dict]:
    pid = next(pid for pid in sorted(PARTS) if PARTS[pid]["kind"] == kind and pred(PARTS[pid]))
    return pid, copy.deepcopy(PARTS[pid])


def test_plate_hole_moved():
    pid, p = _first("plate", lambda p: len(p["holes"]) >= 2)
    x, y, d = p["holes"][-1]
    p["holes"][-1] = (x + 3, y, d)
    assert "shape" in _failed(_grade(pid, p))


def test_link_hole_moved():
    pid, p = _first("link")
    x, d = p["holes"][-1]
    p["holes"][-1] = (x - 4, d)
    assert _failed(_grade(pid, p))


def test_pin_too_short():
    pid, p = _first("pin")
    p["L"] -= 4
    assert "size" in _failed(_grade(pid, p))


def test_disk_hole_radius():
    pid, p = _first("disk", lambda p: p["holes"])
    p["holes"] = [(x * 1.15, y * 1.15, d) for x, y, d in p["holes"]]
    assert "shape" in _failed(_grade(pid, p))


def test_gear_wrong_teeth():
    pid, p = _first("gear")
    p["z"] += 2
    assert "teeth" in _failed(_grade(pid, p))


def test_ring_wrong_teeth():
    pid, p = _first("ring")
    p["z"] += 3
    assert "teeth" in _failed(_grade(pid, p))
