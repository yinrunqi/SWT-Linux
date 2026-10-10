"""给已有评测产物补写 <stem>.swmass.json（SolidWorks 实体数与体积），供 rubric 在 OCC 判实体无效时使用。

    venv\\Scripts\\python.exe -X utf8 eval\\backfill_swmass.py --artifacts eval\\artifacts\\<run> [--force]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pythoncom
import win32com.client as com

SW_DOC_PART = 1
SW_OPEN_SILENT = 1
SW_SOLID_BODY = 0


def _mass(sw, path: Path) -> dict:
    err = com.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    warn = com.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
    doc = sw.OpenDoc6(str(path), SW_DOC_PART, SW_OPEN_SILENT, "", err, warn)
    if doc is None:
        raise RuntimeError(f"打开失败 (error={err.value})")
    try:
        bodies = doc.GetBodies2(SW_SOLID_BODY, True) or []
        mass = doc.Extension.CreateMassProperty
        return {"body_count": len(bodies), "volume_mm3": mass.Volume * 1e9 if mass else None}
    finally:
        sw.CloseDoc(doc.GetTitle)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifacts", required=True)
    ap.add_argument("--force", action="store_true", help="覆盖已有的 .swmass.json")
    args = ap.parse_args()
    sw = com.GetActiveObject("SldWorks.Application")
    for part in sorted(Path(args.artifacts).resolve().glob("*.SLDPRT")):
        out = part.with_suffix(".swmass.json")
        if out.exists() and not args.force:
            continue
        try:
            data = _mass(sw, part)
        except Exception as exc:
            print(f"{part.name}: {type(exc).__name__}: {exc}")
            continue
        out.write_text(json.dumps(data), encoding="utf-8")
        print(f"{part.name}: bodies={data['body_count']} volume_mm3={data['volume_mm3']:.1f}")
    print("open docs:", len(sw.GetDocuments or []))


if __name__ == "__main__":
    main()
