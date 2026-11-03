
import simplecadapi as scad

@scad.part(id="hinge_pin", cache="off")
def build() -> scad.Solid:
    # ---- feature: pin_cylinder (basic body) ----
    # 直径6mm -> 半径3mm，长12mm，轴线沿Z，从Z=0向+Z拉伸
    face = scad.make_circle_rface(center=(0.0, 0.0, 0.0), radius=3.0, normal=(0.0, 0.0, 1.0))
    solid = scad.extrude_rsolid(profile=face, direction=(0.0, 0.0, 1.0), distance=12.0)
    return solid

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
