"""clearance_mm 与 local_to_world 使用同一套 SolidWorks Transform 约定（平移 m、行向量旋转），结果是 mm。"""

from __future__ import annotations

import numpy as np
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder

from geomcheck.assembly_motion import clearance_mm, local_to_world
from geomcheck.tests.refs_three_view import write_step

IDENTITY = [1, 0, 0, 0, 1, 0, 0, 0, 1]


def _matrix(axes, origin_m):
    return list(axes) + list(origin_m) + [1, 0, 0, 0]


def test_translation_is_in_metres_and_gap_in_mm(tmp_path):
    a, b = tmp_path / "a.step", tmp_path / "b.step"
    write_step(BRepPrimAPI_MakeCylinder(5.0, 10.0).Shape(), a)
    write_step(BRepPrimAPI_MakeCylinder(5.0, 10.0).Shape(), b)
    gap = clearance_mm(a, b, _matrix(IDENTITY, [0, 0, 0]), _matrix(IDENTITY, [0.0115, 0, 0]))
    assert abs(gap - 1.5) < 1e-6


def test_rotation_matches_local_to_world(tmp_path):
    bar, pin = tmp_path / "bar.step", tmp_path / "pin.step"
    write_step(BRepPrimAPI_MakeBox(20.0, 2.0, 2.0).Shape(), bar)
    write_step(BRepPrimAPI_MakeCylinder(5.0, 2.0).Shape(), pin)
    # 绕 Z 转 +90°：零件 X 轴指向装配 +Y，零件 Y 轴指向 -X
    turned = _matrix([0, 1, 0, -1, 0, 0, 0, 0, 1], [0, 0, 0])
    assert np.allclose(local_to_world(turned, [20, 0, 0]), [0, 20, 0])
    # 杆端在 y=20，销圆心在 (0, 30)、半径 5，间隙 5 mm；旋转方向反了会落到 -Y 侧
    gap = clearance_mm(bar, pin, turned, _matrix(IDENTITY, [0, 0.030, 0]))
    assert abs(gap - 5.0) < 1e-6
