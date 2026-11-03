import subprocess
r = subprocess.run(["find",".","-name","scad_parts.py"],capture_output=True,text=True)
print(r.stdout, r.stderr)
r2 = subprocess.run(["cat","./scad_parts.py"],capture_output=True,text=True)
print(r2.stdout[:3000], r2.stderr)
