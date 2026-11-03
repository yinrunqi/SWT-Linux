
import simplecadapi as scad
from simplecadapi.inspect.brep import inspect_step_rbrepinspection

insp = inspect_step_rbrepinspection(path="model.step")
print(insp)
