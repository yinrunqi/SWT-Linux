
import simplecadapi as scad
model = scad.load_step_rshape("model.step")
insp = scad.inspect_shape_rbrepinspection(shape=model)
print(insp)
