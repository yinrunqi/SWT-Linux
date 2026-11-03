
import simplecadapi as scad

res = scad.load_step_rbrepmodel("model.step")
bb = res.get_bounding_box() if hasattr(res, "get_bounding_box") else None
print(bb)
