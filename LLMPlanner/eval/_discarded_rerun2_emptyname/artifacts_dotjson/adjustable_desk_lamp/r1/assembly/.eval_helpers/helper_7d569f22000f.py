
import hashlib, json, sys
from pathlib import Path
import simplecadapi as scad
from simplecadapi.inspect import brep

manifest = json.loads(Path("parts/manifest.json").read_text(encoding="utf-8"))
for pid, item in manifest["parts"].items():
    brep.copy_step_region_rpath(item["step"], item["brep"])
    item["sha256"] = hashlib.sha256(Path(item["brep"]).read_bytes()).hexdigest()

    @scad.part(id=pid, cache="off", inputs=(scad.file_input(item["brep"]),), project_root=Path.cwd())
    def plain():
        return scad.make_part_rpart(pid, scad.load_brep_region_rsolid(item["brep"], item["sha256"]))

    item["geometry_hash"] = plain().definition.interface_hashes.geometry
Path("parts/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("prepared", len(manifest["parts"]))
