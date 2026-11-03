
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    # 矩形: X in [-60,60], Y in [-10,70] -> width=120, height=80, center=(0,30,0)
    rect = scad.make_rectangle_rface(width=120.0, height=80.0, center=(0.0, 30.0, 0.0), normal=(0.0, 0.0, 1.0))
    base = scad.extrude_rsolid(profile=rect, direction=(0.0, 0.0, 1.0), distance=8.0)

    # 贯穿孔: 直径5.2, 位置(0,60), 沿Z方向贯穿, 稍微overshoot
    hole_face = scad.make_circle_rface(center=(0.0, 60.0, -1.0), radius=2.6, normal=(0.0, 0.0, 1.0))
    hole = scad.extrude_rsolid(profile=hole_face, direction=(0.0, 0.0, 1.0), distance=10.0)

    result = scad.cut_rsolid(base, hole)
    return result

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
print("done")
