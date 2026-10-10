"""OCC 判实体无效时，实体与体积检查改用 SolidWorks 质量属性；实体有效时仍以 OCC 为准。"""

from types import SimpleNamespace

from geomcheck.rubric import Ctx, ck_single_valid_solid, ck_solid_count, ck_volume


def _ctx(valid: bool, sw_mass):
    part = SimpleNamespace(n_solids=1, n_shells=1, is_valid=lambda: valid, volume=lambda: -2.3e6, sw_mass=sw_mass)
    return Ctx(part, 0.1, 0.5, 0.01, {})


def test_invalid_occ_solid_uses_solidworks_mass():
    ctx = _ctx(False, {"body_count": 1, "volume_mm3": 585197.8})
    assert ck_single_valid_solid(ctx, True)[0]
    assert ck_solid_count(ctx, 1)[0]
    ok, detail = ck_volume(ctx, 586585.17)
    assert ok and detail["source"] == "solidworks"


def test_without_sidecar_invalid_solid_still_fails():
    ctx = _ctx(False, None)
    assert not ck_single_valid_solid(ctx, True)[0]
    assert not ck_volume(ctx, 586585.17)[0]


def test_valid_occ_solid_ignores_sidecar():
    ctx = _ctx(True, {"body_count": 2, "volume_mm3": 586585.17})
    assert ck_single_valid_solid(ctx, True)[0]
    assert not ck_volume(ctx, 586585.17)[0]
