
import simplecadapi as scad

@scad.part(id="lamp_arm", cache="off")
def build():
    R = 6.0   # rounded end radius (width/2)
    L = 105.0 # hole center distance
    W = 12.0  # width
    H = 5.0   # extrude thickness along +Z
    hole_r = 2.5

    # ---- feature: body_profile (sketch) ----
    circ1 = scad.make_circle_rface(center=(0,0,0), radius=R, normal=(0,0,1))
    circ2 = scad.make_circle_rface(center=(L,0,0), radius=R, normal=(0,0,1))
    rect = scad.make_rectangle_rface(width=L, height=W, center=(L/2,0,0), normal=(0,0,1))

    # ---- feature: body_extrude (basic body op) ----
    s1 = scad.extrude_rsolid(circ1, direction=(0,0,1), distance=H)
    s2 = scad.extrude_rsolid(circ2, direction=(0,0,1), distance=H)
    s3 = scad.extrude_rsolid(rect, direction=(0,0,1), distance=H)

    # ---- feature: body_union (bool) ----
    body = scad.union_rsolid(s1, s2, s3)

    # ---- feature: end_holes (modifier) ----
    hole1 = scad.make_cylinder_rsolid(radius=hole_r, height=H*3, bottom_face_center=(0,0,-H), axis=(0,0,1))
    hole2 = scad.make_cylinder_rsolid(radius=hole_r, height=H*3, bottom_face_center=(L,0,-H), axis=(0,0,1))

    result = scad.cut_rsolid(body, hole1, hole2)
    return result

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
