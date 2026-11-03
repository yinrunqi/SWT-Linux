
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    # ---- feature: base_plate (base) ----
    # rectangle on front_plane (XY plane, normal +Z), X:-7..45, Y:-8..8
    width = 52.0
    height = 16.0
    cx = (-7.0 + 45.0) / 2.0
    cy = (-8.0 + 8.0) / 2.0
    rect = scad.make_rectangle_rface(width=width, height=height, center=(cx, cy, 0.0), normal=(0.0, 0.0, 1.0))
    base = scad.extrude_rsolid(profile=rect, direction=(0.0, 0.0, 1.0), distance=5.0)

    # ---- feature: wrist_hole (cut) ----
    # through hole diameter 5mm at origin, overshoot both ends
    hole_face = scad.make_circle_rface(center=(0.0, 0.0, -1.0), radius=2.5, normal=(0.0, 0.0, 1.0))
    hole = scad.extrude_rsolid(profile=hole_face, direction=(0.0, 0.0, 1.0), distance=7.0)

    result = scad.cut_rsolid(base, hole)
    return result

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
print("done")
