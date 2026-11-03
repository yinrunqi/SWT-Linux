import subprocess
print(subprocess.run(["cat","parts/scad_parts.py"],cwd=".",capture_output=True,text=True).stdout)
print(subprocess.run(["ls","parts"],capture_output=True,text=True).stdout)
