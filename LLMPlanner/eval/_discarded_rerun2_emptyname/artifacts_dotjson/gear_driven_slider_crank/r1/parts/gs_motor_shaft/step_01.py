
import simplecadapi as scad

@scad.part(id="motor_shaft", cache="off")
def build() -> scad.Solid:
    # 轴：直径10mm(半径5mm)，长26mm，沿Z轴从Z=0向+Z拉伸
    shaft = scad.make_cylinder_rsolid(
        radius=5.0,
        height=26.0,
        bottom_face_center=(0.0, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )
    return shaft

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
