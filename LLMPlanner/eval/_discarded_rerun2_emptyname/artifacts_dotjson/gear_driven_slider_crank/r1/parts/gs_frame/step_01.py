
import simplecadapi as scad

@scad.part(id="part", cache="off")
def build() -> scad.Solid:
    # ---- feature: base_plate (base) ----
    # 矩形: X -70..110 -> width=180, center_x=20; Y -45..45 -> height=90, center_y=0
    rect = scad.make_rectangle_rface(width=180, height=90, center=(20, 0, 0), normal=(0,0,1))
    plate = scad.extrude_rsolid(profile=rect, direction=(0,0,1), distance=8)

    # ---- feature: through_holes (bool) ----
    # 两个通孔直径10mm，贯穿板厚8mm，拉伸稍微超出以保证完全贯穿
    hole1_face = scad.make_circle_rface(center=(-48, 0, -1), radius=5, normal=(0,0,1))
    hole1 = scad.extrude_rsolid(profile=hole1_face, direction=(0,0,1), distance=10)
    hole2_face = scad.make_circle_rface(center=(0, 0, -1), radius=5, normal=(0,0,1))
    hole2 = scad.extrude_rsolid(profile=hole2_face, direction=(0,0,1), distance=10)

    result = scad.cut_rsolid(plate, [hole1, hole2])
    return result

res = build()
print("volume", res.part.body.get_volume())
scad.capture(res, "model.scadpkg", include_scene=False)
scad.exporter.export_product_package_to_step("model.scadpkg", "model.step")
print("done")
