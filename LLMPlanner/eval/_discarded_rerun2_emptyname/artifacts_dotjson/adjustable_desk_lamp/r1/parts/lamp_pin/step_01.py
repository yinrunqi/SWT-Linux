
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    # 铰销: 直径5mm(半径2.5), 长10mm, 轴线沿Z, 从Z=0到Z=10
    pin = scad.make_cylinder_rsolid(
        radius=2.5,
        height=10.0,
        bottom_face_center=(0.0, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )
    return pin

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
