
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    # 齿轮：模数2, 齿数36, 压力角30°, 齿宽8mm, 轴沿Z从0到+8
    gear = scad.make_spur_gear_rsolid(
        n_teeth=36,
        module=2.0,
        pressure_angle=30.0,
        gear_height=8.0,
    )
    # 轴孔直径10mm，贯穿齿轮，稍微超出以保证完全切穿
    bore = scad.make_cylinder_rsolid(
        radius=5.0,
        height=10.0,
        bottom_face_center=(0, 0, -1.0),
        axis=(0, 0, 1),
    )
    result = scad.cut_rsolid(gear, bore)
    return result

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
