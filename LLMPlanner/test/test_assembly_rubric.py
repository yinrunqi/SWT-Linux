"""装配级 rubric 的离线单测：用合成的配合（实体几何为装配坐标）构造减速箱与曲柄滑块。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from geomcheck.assembly_rubric import classify_rows, evaluate, mate_rows  # noqa: E402

RUBRICS = ROOT / "eval" / "assembly_scratch" / "assembly_rubrics"
Z = [0, 0, 1]


def _comp(name, x=0.0, y=0.0, z=0.0, fixed=False, status=2):
    return {"name": name, "isFixed": fixed, "constrainedStatus": status,
            "transform": [1, 0, 0, 0, 1, 0, 0, 0, 1, x, y, z, 1, 0, 0, 0]}


def _cyl(comp, x, y=0.0, d=Z):
    return {"component": comp, "refType": 4, "params": [x, y, 0, *d, 0.005, 0]}


def _plane(comp, point, normal=Z):
    return {"component": comp, "refType": 3, "params": [*point, *normal, 0, 0]}


def _mate(name, mtype, ea, eb, **extra):
    return {"name": name, "mateType": mtype, "suppressed": False, "errorCode": 0,
            "components": [ea["component"], eb["component"]], "entities": [ea, eb], **extra}


def revolute(a, b, x, z=0.0, y=0.0):
    return [_mate(f"c_{a}_{b}", 1, _cyl(a, x, y), _cyl(b, x, y)),
            _mate(f"p_{a}_{b}", 0, _plane(a, [x, y, z]), _plane(b, [x, y, z]))]


def keyed(a, b, x, z, y=0.0):
    return [_mate(f"c_{a}_{b}", 1, _cyl(a, x, y), _cyl(b, x, y), lockRotation=True),
            _mate(f"p_{a}_{b}", 0, _plane(a, [x, y, z]), _plane(b, [x, y, z]))]


def gear(a, b, xa, xb, za, zb, ya=0.0, yb=0.0):
    return _mate(f"g_{a}_{b}", 10, _cyl(a, xa, ya), _cyl(b, xb, yb),
                 gearRatioNumerator=za, gearRatioDenominator=zb, reverse=True)


def gearbox():
    comps = [_comp("housing-1", fixed=True, status=3)]
    mates = []
    for name, x in (("input_shaft", 0), ("lay_shaft", 0.054), ("output_shaft", 0.126)):
        comps.append(_comp(f"{name}-1", x))
        mates += revolute(f"{name}-1", "housing-1", x)
    for name, shaft, x, z in (("input_pinion", "input_shaft", 0, 0.015), ("lay_wheel", "lay_shaft", 0.054, 0.015),
                              ("lay_pinion", "lay_shaft", 0.054, 0.027), ("output_wheel", "output_shaft", 0.126, 0.027)):
        comps.append(_comp(f"{name}-1", x, 0, z))
        mates += keyed(f"{name}-1", f"{shaft}-1", x, z)
    mates.append(gear("input_pinion-1", "lay_wheel-1", 0, 0.054, 12, 24))
    mates.append(gear("lay_pinion-1", "output_wheel-1", 0.054, 0.126, 12, 36))
    aliases = {c["name"][:-2]: c["name"] for c in comps}
    return comps, mates, aliases


def _rubric(case_id):
    return yaml.safe_load((RUBRICS / f"{case_id}.yaml").read_text(encoding="utf-8"))


def test_classify_basic_joints():
    rev = [r for m in revolute("a", "b", 0.01) for r in mate_rows(m)[0]]
    assert classify_rows(rev)["type"] == "revolute"
    key = [r for m in keyed("a", "b", 0.01, 0.02) for r in mate_rows(m)[0]]
    assert classify_rows(key)["type"] == "fixed"
    cyl = mate_rows(revolute("a", "b", 0.01)[0])[0]
    assert classify_rows(cyl)["type"] == "cylindrical"
    # 两个不平行的面重合 → 滑块
    slide = [_mate("s1", 0, _plane("a", [0, 0, 0], [1, 0, 0]), _plane("b", [0, 0, 0], [1, 0, 0])),
             _mate("s2", 0, _plane("a", [0, 0, 0], Z), _plane("b", [0, 0, 0], Z))]
    info = classify_rows([r for m in slide for r in mate_rows(m)[0]])
    assert info["type"] == "slider" and abs(abs(info["axis"][1]) - 1) < 1e-9


def test_reference_gearbox_full_marks():
    comps, mates, aliases = gearbox()
    out = evaluate(comps, mates, aliases, _rubric("two_stage_gearbox"))
    assert out["mobility"]["actual"] == 1, out["mobility"]
    assert out["score"] == 100, out


def test_gearbox_faults_are_caught():
    comps, mates, aliases = gearbox()
    rubric = _rubric("two_stage_gearbox")

    loose = copy.deepcopy(mates)
    next(m for m in loose if m["name"] == "c_output_wheel-1_output_shaft-1")["lockRotation"] = False
    out = evaluate(comps, loose, aliases, rubric)
    assert out["mobility"]["actual"] == 2 and out["parts"]["rigid_groups"] < 1

    no_gear = [m for m in mates if m["name"] != "g_lay_pinion-1_output_wheel-1"]
    out = evaluate(comps, no_gear, aliases, rubric)
    # 缺的那对只剩中心距一项合格：(1 + 1/4) / 2
    assert out["mobility"]["actual"] == 2 and out["parts"]["couplings"] == 0.625

    floating = [m for m in mates if "output_shaft-1" not in m["components"]]
    out = evaluate(comps, floating, aliases, rubric)
    assert "output_shaft" in out["floating"] and out["parts"]["grounded"] < 1

    broken = copy.deepcopy(mates)
    broken[0]["errorCode"] = 1
    out = evaluate(comps, broken, aliases, rubric)
    assert out["parts"]["not_over_defined"] == 0

    # 齿数比写反（小齿轮带大齿轮却写成 24:12）与同向外啮合都要扣分
    flipped = copy.deepcopy(mates)
    g = next(m for m in flipped if m["name"] == "g_input_pinion-1_lay_wheel-1")
    g["gearRatioNumerator"], g["gearRatioDenominator"], g["reverse"] = 24, 12, False
    out = evaluate(comps, flipped, aliases, rubric)
    bad = next(c for c in out["couplings"] if c["pair"] == ["input_pinion", "lay_wheel"])
    assert bad["ratio_ok"] is False and bad["direction_ok"] is False and bad["score"] == 0.5

    moved = copy.deepcopy(comps)
    next(c for c in moved if c["name"] == "output_wheel-1")["transform"][9] = 0.130
    out = evaluate(moved, mates, aliases, rubric)
    bad = next(c for c in out["couplings"] if c["pair"] == ["lay_pinion", "output_wheel"])
    assert bad["center_ok"] is False and bad["center_distance_mm"] == 76.0


def test_crank_slider_engine():
    """frame 固定；曲柄绕原点转；曲柄销固定在曲柄上（x=25mm），连杆绕曲柄销转；
    活塞销固定在活塞上，连杆绕活塞销转；活塞沿 Y 滑动；凸轮轴在 x=-72mm 与 16/32 齿轮啮合。"""
    y_p = 0.0875  # 曲柄水平时活塞销高度 sqrt(90^2-25^2)
    comps = [_comp("frame-1", fixed=True, status=3)]
    names = ["crank_shaft", "crank", "crank_pin", "rod", "piston_pin", "piston", "flywheel", "crank_gear",
             "camshaft", "cam_gear", "cam"]
    comps += [_comp(f"{n}-1") for n in names]
    m = []
    m += revolute("crank_shaft-1", "frame-1", 0)
    m += keyed("crank-1", "crank_shaft-1", 0, 0.008)
    m += keyed("flywheel-1", "crank_shaft-1", 0, -0.01)
    m += keyed("crank_gear-1", "crank_shaft-1", 0, -0.02)
    m += keyed("crank_pin-1", "crank-1", 0.025, 0.008)
    m += revolute("rod-1", "crank_pin-1", 0.025, 0.014)
    m += revolute("rod-1", "piston_pin-1", 0, 0.014, y=y_p)
    m += keyed("piston_pin-1", "piston-1", 0, 0.02, y=y_p)
    # 活塞右视基准面贴机体右视基准面（单看是平面副），z 与倾斜由连杆闭环限定
    m.append(_mate("slide", 0, _plane("piston-1", [0, y_p, 0], [1, 0, 0]), _plane("frame-1", [0, 0, 0], [1, 0, 0])))
    m += revolute("camshaft-1", "frame-1", -0.072, -0.02)
    m += keyed("cam_gear-1", "camshaft-1", -0.072, -0.02)
    m += keyed("cam-1", "camshaft-1", -0.072, -0.03)
    m.append(gear("crank_gear-1", "cam_gear-1", 0, -0.072, 16, 32))
    for c in comps:
        if c["name"] in ("cam_gear-1", "camshaft-1", "cam-1"):
            c["transform"][9] = -0.072
    aliases = {c["name"][:-2]: c["name"] for c in comps}
    out = evaluate(comps, m, aliases, _rubric("single_cylinder_engine"))
    assert [j["actual"] for j in out["joints"]] == ["revolute", "revolute", "revolute", "planar", "revolute"], out["joints"]
    assert out["mobility"]["actual"] == 1, out["mobility"]
    assert out["score"] == 100, out
