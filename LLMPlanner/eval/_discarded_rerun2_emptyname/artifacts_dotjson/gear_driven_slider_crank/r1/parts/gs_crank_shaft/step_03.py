
import simplecadapi as scad
from simplecadapi import ql
import math

@scad.part(id="crankshaft_cylinder", cache="off")
def build() -> scad.Solid:
    cylinder = scad.make_cylinder_rsolid(
        radius=5.0,
        height=32.0,
        bottom_face_center=(0.0, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )
    return cylinder

res = build()
body = res.part.body

vol = body.get_volume()
expected_vol = math.pi * 5.0**2 * 32.0
print(f"volume        : {vol:.4f} mm^3")
print(f"expected      : {expected_vol:.4f} mm^3")
print(f"volume ok     : {abs(vol - expected_vol) < 0.1}")

faces = ql.faces().resolve(body)
edges = ql.edges().resolve(body)
print(f"face count    : {len(faces)}")
print(f"edge count    : {len(edges)}")

face_areas = sorted(f.get_area() for f in faces)
print(f"face areas    : {[round(a,3) for a in face_areas]}")

for e in edges:
    print(f"edge length   : {e.get_length():.4f}")

scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
print("model.step written ok")
