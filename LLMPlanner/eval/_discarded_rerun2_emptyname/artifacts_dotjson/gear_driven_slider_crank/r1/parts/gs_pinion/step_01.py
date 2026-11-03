
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    # 渐开线直齿轮：模数2，齿数12，压力角30度，齿宽8mm，轴线沿Z，从Z=0到Z=8
    gear = scad.std.gear.make_spur_gear_rsolid(
        n_teeth=12,
        module=2.0,
        pressure_angle=30.0,
        gear_height=8.0,
    )
    # 轴孔直径10mm，贯穿齿轮，沿Z方向，稍微超出两端保证贯穿
    hole = scad.make_cylinder_rsolid(
        radius=5.0,
        height=10.0,
        bottom_face_center=(0.0, 0.0, -1.0),
        axis=(0.0, 0.0, 1.0),
    )
    result = scad.cut_rsolid(gear, hole, skip_non_intersecting=False)
    return result

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
