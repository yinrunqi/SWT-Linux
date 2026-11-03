
import simplecadapi as scad
import math

@scad.part(id="crankshaft_cylinder", cache="off")
def build() -> scad.Solid:
    # Assumption: "曲柄轴" here means a plain cylindrical shaft segment,
    # diameter 10 mm (radius 5 mm), length 32 mm, axis along Z,
    # extruded from Z=0 to Z=32 (bottom_face_center at origin, axis +Z).
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

faces = scad.geo.face().resolve(body)
edges = scad.geo.edge().resolve(body)
print(f"face count    : {len(faces)}")
print(f"edge count    : {len(edges)}")

face_areas = sorted(f.get_area() for f in faces)
print(f"face areas    : {[round(a,3) for a in face_areas]}")
# expect 2 circular end faces of area pi*r^2 = 78.54, and one lateral face of area 2*pi*r*h = 1005.31

for e in edges:
    print(f"edge length   : {e.get_length():.4f}")

scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
print("model.step written ok")
