"""装配试点零件 rubric：参考实体满分，错误变体在预期条目上失败。"""

from __future__ import annotations

from pathlib import Path

import pytest

from geomcheck.occ_part import Part
from geomcheck.rubric import evaluate, load_rubric
from geomcheck.tests import refs_assembly as refs

RUBRICS = Path(__file__).resolve().parents[2] / "eval" / "assembly_scratch" / "rubrics"


def _grade(name: str, shape) -> dict:
    return evaluate(load_rubric(RUBRICS / f"{name}.yaml"), Part(shape))


@pytest.mark.parametrize("name,builder", [
    ("housing", refs.housing),
    ("shaft", refs.shaft),
    ("pinion_z12", lambda: refs.gear(12)),
    ("wheel_z24", lambda: refs.gear(24)),
    ("wheel_z36", lambda: refs.gear(36)),
    ("crank", lambda: refs.link(25, 16, 10, 6)),
    ("rod", lambda: refs.link(90, 14, 10, 6)),
    ("pin", lambda: refs.shaft(40)),
])
def test_reference(name, builder):
    result = _grade(name, builder())
    assert result["accepted"], result


def test_housing_missing_hole():
    result = _grade("housing", refs.housing_two_holes())
    assert not result["accepted"]
    failed = {row["id"] for row in result["rubrics"] if not row["ok"]}
    assert "holes" in failed


def test_wrong_teeth():
    result = _grade("pinion_z12", refs.gear(16))
    assert not result["accepted"]
    failed = {row["id"] for row in result["rubrics"] if not row["ok"]}
    assert "teeth" in failed
