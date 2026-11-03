
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    # Assumptions:
    # - front_plane = XY plane (normal +Z); sketch in XY, extrude +Z by 5mm.
    # - Origin at one hole center (0,0,0); other hole center at (120,0,0) along +X.
    # - Slot width 12mm -> rounded end radius = 6mm (width/2).
    # - Through holes diameter 5mm at each hole center.
    L = 120.0
    W = 12.0
    T = 5.0
    R_end = W / 2.0
    R_hole = 5.0 / 2.0

    # ---- feature: body_straight (base) ----
    body = scad.make_box_rsolid(
        width=L, height=W, depth=T,
        bottom_face_center=(L / 2.0, 0.0, 0.0),
    )

    # ---- feature: rounded_caps (base) ----
    cap1 = scad.make_cylinder_rsolid(
        radius=R_end, height=T,
        bottom_face_center=(0.0, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )
    cap2 = scad.make_cylinder_rsolid(
        radius=R_end, height=T,
        bottom_face_center=(L, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )

    # ---- feature: body_union (bool) ----
    solid = scad.union_rsolid(body, cap1, cap2)

    # ---- feature: through_holes (bool) ----
    hole1 = scad.make_cylinder_rsolid(
        radius=R_hole, height=T,
        bottom_face_center=(0.0, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )
    hole2 = scad.make_cylinder_rsolid(
        radius=R_hole, height=T,
        bottom_face_center=(L, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )
    solid = scad.cut_rsolid(solid, hole1, hole2)

    return solid

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
print("done")
