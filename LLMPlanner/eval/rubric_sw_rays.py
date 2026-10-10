"""OCC 读出的实体无效时，用 SolidWorks 真机做射线求交给单个 STEP 打分（需要 SolidWorks 正在运行）。

射线类检查（ray/radial/line_material）改由 SLDPRT 在 SolidWorks 里求交，其余检查仍用 OCC 与 .swmass.json。

    venv-occ\\Scripts\\python.exe -X utf8 eval\\rubric_sw_rays.py --step <stem>.step --rubric <case>.yaml [--output out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pythoncom
import win32com.client as com

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from geomcheck.rubric import _print, evaluate, load_part, load_rubric  # noqa: E402

SW_DOC_PART = 1
SW_OPEN_SILENT = 1
SW_SOLID_BODY = 0
RAY_NORMALS, RAY_TOPOLS, RAY_UNBLOCK = 1, 2, 8
HIT_RADIUS_M = 1e-7
MERGE_MM = 1e-4
STEP_MM = 1e-4
MAX_CASTS = 200


def _cast(doc, body, start, d) -> list[tuple[float, float]]:
    """从 start（mm）沿 d 的一次求交：[(离 start 的距离 mm, 法向·d)]；SolidWorks 只报到第一次离开材料为止。"""
    count = doc.Extension.RayIntersections(
        com.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, [body]),
        com.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) / 1000 for v in start]),
        com.VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in d]),
        RAY_NORMALS | RAY_TOPOLS | RAY_UNBLOCK, HIT_RADIUS_M, 0.0, True,
    )
    raw = doc.GetRayIntersectionsPoints if count else None
    out = []
    for k in range(count or 0):
        x, y, z, nx, ny, nz = raw[9 * k + 3: 9 * k + 9]
        out.append((float(np.dot(np.array([x, y, z]) * 1000 - start, d)), float(np.dot([nx, ny, nz], d))))
    return out


def _sw_ray_oracle(doc, body, reach_mm: float):
    def intervals(o, d, t0, t1):
        base = t0 - reach_mm
        hits, cursor = [], base
        for _ in range(MAX_CASTS):
            found = [(cursor + s, f) for s, f in _cast(doc, body, o + cursor * d, d) if s > STEP_MM]
            if not found or cursor > t1:
                break
            hits += [(t, 1 if f < 0 else -1) for t, f in found if abs(f) > 1e-9]
            cursor = max(t for t, _ in found)
        hits.sort()
        # 射线穿过棱边时两侧面各报一次
        merged = [h for i, h in enumerate(hits) if not (i and abs(h[0] - hits[i - 1][0]) < MERGE_MM and h[1] == hits[i - 1][1])]
        out, depth, enter = [], 0, None
        for t, step in merged:
            depth += step
            if step > 0 and depth == 1:
                enter = t
            elif step < 0 and depth == 0 and enter is not None:
                lo, hi = max(enter, t0), min(t, t1)
                if hi - lo > 1e-9:
                    out.append((lo, hi))
                enter = None
        return out

    return intervals


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", required=True)
    ap.add_argument("--rubric", required=True)
    ap.add_argument("--sldprt", help="默认与 STEP 同名")
    ap.add_argument("--output")
    args = ap.parse_args()
    step = Path(args.step).resolve()
    sldprt = Path(args.sldprt).resolve() if args.sldprt else step.with_suffix(".SLDPRT")
    part = load_part(step)
    sw = com.GetActiveObject("SldWorks.Application")
    err = com.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warn = com.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(str(sldprt), SW_DOC_PART, SW_OPEN_SILENT, "", err, warn)
    if doc is None:
        raise SystemExit(f"打开 {sldprt.name} 失败 (error={err.value})")
    try:
        bodies = doc.GetBodies2(SW_SOLID_BODY, True) or []
        if len(bodies) != 1:
            raise SystemExit(f"{sldprt.name} 有 {len(bodies)} 个实体，只支持单实体")
        box = np.asarray(doc.GetPartBox(True), float) * 1000
        part.ray_oracle = _sw_ray_oracle(doc, bodies[0], float(np.linalg.norm(box[3:] - box[:3])) + 10)
        res = evaluate(load_rubric(args.rubric), part)
    finally:
        sw.CloseDoc(doc.GetTitle)
    _print(res, step.name)
    if args.output:
        Path(args.output).write_text(json.dumps([{"step": str(step), "ray_source": "solidworks", **res}],
                                                ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
