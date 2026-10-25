"""导出 text-to-CAD 满分零件的 SolidWorks 原生特征树（FeatureManager），不调用 LLM。

来源：
  core / hard 最初 8 题：历次评测里用现行 rubric 打 100 分的运行（eval/artifacts/<run>/<case>_r<k>.SLDPRT），
      同一题有多个满分运行时取成功建模调用最少的一次
  hard 加难 22 题：eval/artifacts/hard_l1_reference（L1 oracle）
  extreme 20 题：eval/artifacts/extreme_l1v2_reference（L1_v2 oracle）

输出 eval/feature_trees/text2cad/<split>/<case>.json 与 index.json。长度单位 mm，角度单位 deg；
草图线段坐标是草图坐标系下的值，sketch_to_model 是草图到模型坐标的 3×3 旋转 + 平移（mm）。

运行（SolidWorks 已启动、没有别的任务在用）：
  venv\\Scripts\\python.exe -X utf8 eval\\export_feature_trees.py --scores=<重打分 JSON> [case ...]
不给 --scores 时只导出两套 oracle（42 题）。
"""

from __future__ import annotations

import json
import math
import re
import sys
import traceback
from pathlib import Path

import pythoncom
import win32com.client as w

LLM_ROOT = Path(__file__).resolve().parents[1]
EVAL = LLM_ROOT / "eval"
ARTIFACTS = EVAL / "artifacts"
OUT = EVAL / "feature_trees" / "text2cad"

FIRST8 = "quad_frame connecting_rod finned_housing sprocket handwheel camshaft planet_carrier impeller".split()
QUERY_TOOLS = {"get_model_state", "view_model", "finish", "set_parameters"}
# 文档自带、与建模无关的文件夹
SKIP_TYPES = {"CommentsFolder", "FavoriteFolder", "HistoryFolder", "SelectionSetFolder", "SensorFolder",
              "DocsFolder", "DetailCabinet", "SurfaceBodyFolder", "SolidBodyFolder", "EnvFolder",
              "InkMarkupFolder", "EqnFolder", "MaterialFolder"}
EXTRUDE_TYPES = {"Extrusion", "ICE", "BaseBody", "Boss", "Cut"}
END_CONDITIONS = {0: "blind", 1: "through_all", 2: "through_next", 3: "up_to_vertex", 4: "up_to_surface",
                  5: "offset_from_surface", 6: "mid_plane", 7: "up_to_body", 8: "through_all_both"}
SEGMENT_TYPES = {0: "line", 1: "arc", 2: "ellipse", 3: "spline", 4: "text", 5: "parabola"}


def _mm(v: float) -> float:
    return round(v * 1000, 5)


def _rubric_dir(case: str) -> str:
    for split, sub in (("core", "rubrics"), ("hard", "rubrics_hard"), ("extreme", "rubrics_extreme")):
        if (EVAL / sub / f"{case}.yaml").exists():
            return split
    raise KeyError(case)


def _modeling_calls(step: Path) -> int:
    cp = step.with_suffix(".checkpoint.json")
    if not cp.exists():
        return 10 ** 6
    log = json.loads(cp.read_text(encoding="utf-8")).get("tool_log") or []
    return sum(1 for t in log if t.get("ok") and t.get("tool") not in QUERY_TOOLS)


def sources(scores_json: Path | None) -> dict[str, dict]:
    """case -> {split, sldprt, source}。scores_json 是用现行 rubric 重打历史 STEP 的结果（每行 step、score、accepted）。"""
    out: dict[str, dict] = {}
    for run, note in (("hard_l1_reference", "L1 oracle"), ("extreme_l1v2_reference", "L1_v2 oracle")):
        for p in sorted((ARTIFACTS / run).glob("*_r1.SLDPRT")):
            case = p.stem[:-3]
            out[case] = {"split": _rubric_dir(case), "sldprt": p, "source": f"{run} ({note})"}
    if scores_json is None:
        return out
    core = sorted(p.stem for p in (EVAL / "rubrics").glob("*.yaml"))
    wanted = set(core) | set(FIRST8)
    best: dict[str, tuple[int, int, Path]] = {}
    for row in json.loads(scores_json.read_text(encoding="utf-8")):
        step = Path(row["step"])
        if not step.is_absolute():
            step = LLM_ROOT / step
        case = re.sub(r"_r\d+$", "", step.stem)
        if case not in wanted or row.get("score") != 100.0 or not row.get("accepted"):
            continue
        sld = step.with_suffix(".SLDPRT")
        if not sld.exists():
            continue
        key = (_modeling_calls(step), -sld.stat().st_mtime_ns, sld)  # 建模调用少的优先，其次取较新的
        if case not in best or key[:2] < best[case][:2]:
            best[case] = key
    for case, (_, _, sld) in best.items():
        out[case] = {"split": _rubric_dir(case), "sldprt": sld, "source": f"{sld.parent.name} (model run, rubric 100)"}
    return out


class Exporter:
    def __init__(self) -> None:
        self.sw = w.GetActiveObject("SldWorks.Application")

    def close_all(self) -> None:
        self.sw.CloseAllDocuments(True)

    def export(self, path: Path) -> dict:
        err = w.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        warn = w.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)
        doc = self.sw.OpenDoc6(str(path), 1, 1 | 2, "", err, warn)  # 零件；静默 + 只读
        if doc is None:
            raise RuntimeError(f"OpenDoc6 failed: error={err.value} warning={warn.value}")
        try:
            features = []
            feat = doc.FirstFeature
            while feat:
                if feat.GetTypeName2 not in SKIP_TYPES:
                    features.append(self._feature(feat))
                feat = feat.GetNextFeature
            mp = doc.Extension.CreateMassProperty
            return {"features": features, "sw_volume_mm3": round(mp.Volume * 1e9, 3),
                    "sw_revision": self.sw.RevisionNumber}
        finally:
            self.sw.CloseDoc(doc.GetTitle)

    def _feature(self, feat) -> dict:
        name, ftype = feat.Name, feat.GetTypeName2
        item: dict = {"name": name, "type": ftype}
        if feat.IsSuppressed:
            item["suppressed"] = True
        parents = feat.GetParents
        if parents:
            item["parents"] = [p.Name for p in parents]
        dims = self._dimensions(feat, name)
        if dims:
            item["dimensions"] = dims
        subs = []
        sub = feat.GetFirstSubFeature
        while sub:
            subs.append({"name": sub.Name, "type": sub.GetTypeName2})
            sub = sub.GetNextSubFeature
        if subs:
            item["children"] = subs
        if ftype in EXTRUDE_TYPES:
            item["extrude"] = self._extrude(feat)
        if ftype in ("ProfileFeature", "3DProfileFeature"):
            item["sketch"] = self._sketch(feat.GetSpecificFeature2)
        return item

    @staticmethod
    def _dimensions(feat, name: str) -> list[dict]:
        out, seen = [], set()
        dd = feat.GetFirstDisplayDimension
        while dd:
            dim = dd.GetDimension2(0)
            full = dim.FullName
            if f"@{name}@" in full and full not in seen:
                seen.add(full)
                kind, v = dim.GetType, dim.SystemValue  # 0 长度（m）、1 角度（rad）、2 整数（如阵列数量）
                if kind == 1:
                    value, unit = round(math.degrees(v), 5), "deg"
                elif kind == 2:
                    value, unit = int(round(v)), "count"
                else:
                    value, unit = _mm(v), "mm"
                out.append({"name": full.split("@")[0], "value": value, "unit": unit})
            dd = feat.GetNextDisplayDimension(dd)
        return out

    @staticmethod
    def _extrude(feat) -> dict:
        data = feat.GetDefinition
        out = {}
        try:
            for forward, key in ((True, "dir1"), (False, "dir2")):
                if key == "dir2" and not data.BothDirections:
                    continue
                end = data.GetEndCondition(forward)
                d = {"end": END_CONDITIONS.get(end, end)}
                if end in (0, 6):
                    d["depth_mm"] = _mm(data.GetDepth(forward))
                if data.GetDraftWhileExtruding(forward):
                    d["draft_deg"] = round(math.degrees(data.GetDraftAngle(forward)), 5)
                    d["draft_outward"] = bool(data.GetDraftOutward(forward))
                out[key] = d
            out["reverse"] = bool(data.ReverseDirection)
        except pythoncom.com_error as e:
            out["error"] = str(e)[:120]
        return out

    @staticmethod
    def _sketch(sk) -> dict:
        out: dict = {}
        xf = sk.ModelToSketchTransform
        if xf is not None:
            inv = xf.Inverse
            a = list(inv.ArrayData)  # 3×3 旋转（行主序）、平移（m）、缩放
            out["sketch_to_model"] = {"rotation": [round(x, 9) for x in a[:9]],
                                      "translation_mm": [_mm(x) for x in a[9:12]]}
        segs = []
        for s in sk.GetSketchSegments or ():
            kind = SEGMENT_TYPES.get(s.GetType, s.GetType)
            seg: dict = {"type": kind}
            if s.ConstructionGeometry:
                seg["construction"] = True
            if kind == "line":
                seg["start"], seg["end"] = _pt(s.GetStartPoint2), _pt(s.GetEndPoint2)
            elif kind == "arc":
                c, p0, p1 = _pt(s.GetCenterPoint2), _pt(s.GetStartPoint2), _pt(s.GetEndPoint2)
                seg |= {"center": c, "radius": _mm(s.GetRadius), "start": p0, "end": p1,
                        "circle": p0 == p1, "direction": s.GetRotationDir}
            elif kind == "spline":
                seg["points"] = [_pt(p) for p in s.GetPoints2 or ()]  # 样条的插值点（SketchPoint）
            segs.append(seg)
        out["segments"] = segs
        return out


def _pt(p) -> list[float]:
    return [_mm(p.X), _mm(p.Y), _mm(p.Z)]


def main() -> None:
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    scores = Path(next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--scores=")), "")) or None
    srcs = sources(scores if scores and scores.is_file() else None)
    if only:
        srcs = {c: v for c, v in srcs.items() if c in only}
    ex = Exporter()
    ex.close_all()
    index_path = OUT / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    try:
        for i, (case, src) in enumerate(sorted(srcs.items(), key=lambda kv: (kv[1]["split"], kv[0]))):
            target = OUT / src["split"] / f"{case}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                tree = ex.export(src["sldprt"])
            except Exception as e:  # noqa: BLE001
                where = traceback.extract_tb(e.__traceback__)[-1]
                print(f"[{i + 1}/{len(srcs)}] {case}: FAILED {e!r} at line {where.lineno}"[:300], flush=True)
                continue
            finally:
                ex.close_all()
            rel = src["sldprt"].relative_to(LLM_ROOT).as_posix()
            record = {"case": case, "split": src["split"], "source": src["source"], "sldprt": rel} | tree
            target.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            # 默认的三个基准面和原点没有父特征
            modeling = [f for f in tree["features"]
                        if f["type"] not in ("RefPlane", "OriginProfileFeature") or f.get("parents")]
            index[case] = {"split": src["split"], "source": src["source"], "sldprt": rel,
                           "features": len(modeling), "sw_volume_mm3": tree["sw_volume_mm3"]}
            print(f"[{i + 1}/{len(srcs)}] {case}: {len(modeling)} features, volume {tree['sw_volume_mm3']}", flush=True)
    finally:
        ex.close_all()
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(dict(sorted(index.items())), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
