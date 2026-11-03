
import simplecadapi as scad

@scad.part(id="crankshaft_cylinder", cache="off")
def build() -> scad.Solid:
    # Assumption: "曲柄轴" is a plain cylindrical shaft, no additional features.
    # Diameter 10 mm → radius 5 mm; height 32 mm; axis along +Z from Z=0.
    cylinder = scad.make_cylinder_rsolid(
        radius=5.0,
        height=32.0,
        bottom_face_center=(0.0, 0.0, 0.0),
        axis=(0.0, 0.0, 1.0),
    )
    return cylinder

res = build()
body = res.part.body

# Validation
vol = body.get_volume()
import math
expected_vol = math.pi * 5.0**2 * 32.0
print(f"volume        : {vol:.4f} mm³")
print(f"expected      : {expected_vol:.4f} mm³")
print(f"volume ok     : {abs(vol - expected_vol) < 0.1}")

# Bounding box
bb = body.get_bounding_box()
print(f"bounding box  : {bb}")

# Face / edge count via QL
faces = scad.geo.face().resolve(body)
edges = scad.geo.edge().resolve(body)
print(f"face count    : {len(faces)}")
print(f"edge count    : {len(edges)}")

# Export
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
print("model.step written ok")
