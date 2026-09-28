"""从 CAD-1K SolidWorks 子集的交付零件中提取特征树，并与 narration 做粗对齐。

仅用于挖掘和分析人类工程师的建模 workflow，不作为评测集。
需要 SolidWorks 与 C# 服务（默认 http://localhost:5100）在运行。

每个任务输出（本地，位于已 gitignore 的 raw/{id}/）：
    features.json   特征树、包围盒、体积、草图几何统计、打开时的 errors/warnings
    output.step     交付零件的 STEP（供离线几何分析）
    alignment.json  特征 <-> narration 片段的粗对齐表
汇总（可入库，只含派生统计）：
    datasets/cad1k/stats.json

用法:
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\extract_features.py
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\extract_features.py --limit 3 --force
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\extract_features.py --stats-only
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
LLM_ROOT = HERE.parent.parent
if str(LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLM_ROOT))

from eval import metrics as geo_metrics  # noqa: E402

RAW_DIR = HERE / "raw"
STATS_PATH = HERE / "stats.json"

# narration 片段的粗分类，按顺序匹配第一个命中的类别。
NARRATION_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("undo_or_delete", ("undo", "delete", "deleted", "removed", "ctrl+z", "rolled back")),
    ("drawing", ("drawing", "sheet", "title block", "section view", "detail view",
                 "isometric view", "projected view", "annotation", "balloon")),
    ("feature_edit", ("edit feature", "edited the", "edit sketch", "rebuild", "modified the")),
    ("fillet_chamfer", ("fillet", "chamfer")),
    ("hole", ("hole wizard", "counterbore", "countersink", "tapped", "hole")),
    ("pattern_mirror", ("pattern", "mirror")),
    ("shell_rib_draft", ("shell", "rib", "draft")),
    ("revolve", ("revolve",)),
    ("sweep_loft", ("sweep", "loft")),
    ("cut", ("cut", "extruded cut", "cut-extrude")),
    ("extrude", ("extrude", "boss", "extrusion")),
    ("dimension_constraint", ("dimension", "smart dimension", "relation", "constraint",
                              "coincident", "tangent", "concentric", "horizontal", "vertical")),
    ("sketch", ("sketch", "line", "circle", "arc", "rectangle", "polygon", "centerline",
                "trim", "offset", "spline", "slot")),
    ("reference_geometry", ("plane", "axis", "reference geometry", "coordinate system")),
    ("reference_doc", ("pdf", "reference sheet", "input.pdf", "specification", "reviewed the task")),
    ("view_navigation", ("zoom", "rotate", "pan", "view orientation", "rotated the view",
                         "inspected", "reviewed", "examined")),
    ("file_io", ("save", "saved", "save as", "export", "opened", "new part", "new document")),
)

DEFAULT_PLANE_NAMES = frozenset({
    "Front Plane", "Top Plane", "Right Plane", "前视基准面", "上视基准面", "右视基准面",
})


def narration_category(segment: dict) -> str:
    text = f"{segment.get('action', '')} {segment.get('intent', '')}".lower()
    for category, keywords in NARRATION_CATEGORIES:
        if any(keyword in text for keyword in keywords):
            return category
    return "other"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _post(sw_url: str, path: str, body: dict, timeout: int = 180) -> dict:
    response = requests.post(f"{sw_url}{path}", json=body, timeout=timeout)
    payload = response.json()
    if not response.ok or not payload.get("success"):
        raise RuntimeError(payload.get("message") or f"HTTP {response.status_code}")
    return payload


def _get(sw_url: str, path: str, timeout: int = 180) -> dict:
    response = requests.get(f"{sw_url}{path}", timeout=timeout)
    payload = response.json()
    if not response.ok or payload.get("success") is False:
        raise RuntimeError(payload.get("message") or f"HTTP {response.status_code}")
    return payload


# swFileLoadError_e 中常见的打开失败原因
OPEN_ERROR_FLAGS = {
    1: "generic",
    2: "file_not_found",
    8192: "future_version",  # 由比本机更新的 SolidWorks 保存
}


def _open_error_kind(message: str) -> str | None:
    marker = "errors="
    if marker not in message:
        return None
    try:
        code = int(message.split(marker, 1)[1].split(",", 1)[0].split(")", 1)[0])
    except ValueError:
        return None
    kinds = [name for flag, name in OPEN_ERROR_FLAGS.items() if code & flag]
    return "+".join(kinds) if kinds else f"code_{code}"


def extract_part(part: Path, step_path: Path | None, sw_url: str) -> dict:
    record: dict = {"file": part.name, "part_bytes": part.stat().st_size}
    started = time.perf_counter()
    try:
        _post(sw_url, "/api/close-all-documents", {}, timeout=60)
        opened = _post(sw_url, "/api/open-part", {"partPath": str(part.resolve())})
        record["open"] = opened.get("result")
        observation = _get(sw_url, "/api/model-observation")
        record["features"] = observation.get("features") or []
        record["bounding_box_m"] = observation.get("boundingBox")
        record["volume_mm3"] = observation.get("volumeMm3")
        record["faces"] = observation.get("faces") or []
        record["sketch_stats"] = _get(sw_url, "/api/sketch-stats").get("sketches") or []
        if step_path is not None:
            exported = _post(sw_url, "/api/export-step", {"stepPath": str(step_path.resolve())})
            record["step"] = exported.get("result")
        record["error"] = None
    except Exception as exc:  # noqa: BLE001
        record["error"] = str(exc)
        record["error_kind"] = _open_error_kind(str(exc))
    finally:
        try:
            _post(sw_url, "/api/close-all-documents", {}, timeout=60)
        except Exception:  # noqa: BLE001
            pass
    record["duration_seconds"] = round(time.perf_counter() - started, 2)
    return record


def extract_task(task_dir: Path, sw_url: str, *, export_step: bool) -> dict:
    """单零件任务交付 output.sldprt；装配任务交付 output.sldasm + output_N.sldprt。"""
    output_dir = task_dir / "output_files"
    parts = sorted(output_dir.glob("*.sldprt")) if output_dir.exists() else []
    if not parts:
        return {"task_id": task_dir.name, "error": "missing output.sldprt"}
    records = [
        extract_part(part, (task_dir / f"{part.stem}.step") if export_step else None, sw_url)
        for part in parts
    ]
    ok = [item for item in records if not item.get("error")]
    return {
        "task_id": task_dir.name,
        "is_assembly": any(output_dir.glob("*.sldasm")),
        "parts": records,
        "error": None if ok else records[0].get("error"),
        "error_kind": None if ok else records[0].get("error_kind"),
    }


def _parts_of(record: dict) -> list[dict]:
    """兼容早期只提取 output.sldprt 的单零件记录格式。"""
    if "parts" in record:
        return record["parts"]
    if "features" in record or record.get("error") and record.get("error") != "missing output.sldprt":
        legacy = dict(record)
        legacy.setdefault("file", "output.sldprt")
        if legacy.get("error") and "error_kind" not in legacy:
            legacy["error_kind"] = _open_error_kind(str(legacy["error"]))
        return [legacy]
    return []


def _modeling_features(features: list[dict]) -> list[dict]:
    """去掉文件夹、原点、默认基准面和草图，保留实体特征与用户新建的参考几何。"""
    rows = []
    for item in features:
        category = geo_metrics.classify_feature(item)
        if category in (None, "sketches"):
            continue
        if category == "ref_planes" and item.get("name") in DEFAULT_PLANE_NAMES:
            continue
        rows.append(item)
    return rows


def align(features: list[dict], segments: list[dict]) -> list[dict]:
    """按特征名在 narration 中首次/末次被提及的时间做粗对齐。"""
    rows = []
    texts = [
        f"{seg.get('action', '')} {seg.get('details', '')}".lower()
        for seg in segments
    ]
    for feature in _modeling_features(features):
        name = str(feature.get("name") or "")
        needle = name.lower()
        hits = [index for index, text in enumerate(texts) if needle and needle in text]
        rows.append({
            "feature": name,
            "type": feature.get("type"),
            "category": geo_metrics.classify_feature(feature),
            "mentions": len(hits),
            "first_s": segments[hits[0]]["start_s"] if hits else None,
            "last_s": segments[hits[-1]]["end_s"] if hits else None,
        })
    return rows


def summarize_task(task_dir: Path, record: dict) -> dict:
    task_id = task_dir.name
    summary: dict = {"task_id": task_id}
    try:
        task_desc = _load_json(task_dir / "task_desc.json")
        summary["task"] = task_desc.get("task")
    except (OSError, ValueError):
        pass
    try:
        rubrics = _load_json(task_dir / "rubrics.json").get("rubrics") or []
        summary["rubric_count"] = len(rubrics)
        summary["rubric_drawing_count"] = sum(
            "drawing" in str(item.get("requirement", "")).lower() for item in rubrics
        )
    except (OSError, ValueError):
        rubrics = []
    try:
        metadata = _load_json(task_dir / "metadata.json")
        summary["duration_min"] = round(float(metadata.get("total_duration_ms", 0)) / 60000, 1)
        summary["event_count"] = metadata.get("event_count")
    except (OSError, ValueError):
        pass
    segments: list[dict] = []
    try:
        narration = _load_json(task_dir / "narration.json")
        segments = narration.get("annotations") if isinstance(narration, dict) else narration
        segments = segments or []
    except (OSError, ValueError):
        pass
    summary["narration_segments"] = len(segments)
    time_by_category: Counter[str] = Counter()
    for seg in segments:
        duration = max(0.0, float(seg.get("end_s", 0)) - float(seg.get("start_s", 0)))
        time_by_category[narration_category(seg)] += duration
    total_time = sum(time_by_category.values()) or 1.0
    summary["narration_time_share"] = {
        key: round(value / total_time, 4) for key, value in time_by_category.most_common()
    }

    parts = _parts_of(record)
    summary["is_assembly"] = bool(record.get("is_assembly"))
    summary["part_count"] = len(parts)
    summary["open_error"] = record.get("error")
    summary["part_failures"] = [
        {"file": part.get("file"), "error_kind": part.get("error_kind"), "error": part.get("error")}
        for part in parts
        if part.get("error")
    ]
    opened = [part for part in parts if part.get("features")]
    summary["parts_opened"] = len(opened)
    if not opened:
        return summary

    sequences, type_counts, metric_totals, alignment = [], Counter(), Counter(), []
    part_summaries = []
    for part in opened:
        features = part["features"]
        modeling = _modeling_features(features)
        sequence = [geo_metrics.classify_feature(item) for item in modeling]
        sequences.append(sequence)
        type_counts.update(item.get("type") for item in modeling)
        metric_totals.update(geo_metrics.model_metrics(features, part.get("sketch_stats") or []))
        bbox = part.get("bounding_box_m") or {}
        part_summaries.append({
            "file": part.get("file"),
            "feature_count": len(modeling),
            "sketch_count": len(part.get("sketch_stats") or []),
            "face_count": len(part.get("faces") or []),
            "volume_mm3": part.get("volume_mm3"),
            "bbox_mm": [
                round(float(bbox.get(key) or 0) * 1000, 3) for key in ("sizeX", "sizeY", "sizeZ")
            ],
        })
        # 装配任务里各零件的特征名常重复（Boss-Extrude1 …），按名对齐只作参考。
        alignment.extend({"part": part.get("file"), **row} for row in align(features, segments))

    summary["feature_count"] = sum(len(seq) for seq in sequences)
    summary["feature_sequences"] = sequences
    summary["feature_types"] = dict(type_counts)
    summary["metrics"] = dict(metric_totals)
    summary["parts"] = part_summaries
    (task_dir / "alignment.json").write_text(
        json.dumps(alignment, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary["aligned_features"] = sum(row["mentions"] > 0 for row in alignment)
    return summary


def aggregate(summaries: list[dict]) -> dict:
    ok = [item for item in summaries if item.get("feature_count")]
    category_totals: Counter[str] = Counter()
    type_totals: Counter[str] = Counter()
    bigrams: Counter[str] = Counter()
    time_share: Counter[str] = Counter()
    part_counts: list[int] = []
    for item in ok:
        type_totals.update(item.get("feature_types") or {})
        for sequence in item.get("feature_sequences") or []:
            category_totals.update(sequence)
            bigrams.update(f"{a} -> {b}" for a, b in zip(sequence, sequence[1:]))
        part_counts.extend(part["feature_count"] for part in item.get("parts") or [])
    for item in summaries:
        time_share.update(item.get("narration_time_share") or {})
    counts = sorted(part_counts)
    aligned = sum(item.get("aligned_features", 0) for item in ok)
    total_features = sum(counts)
    failure_kinds = Counter(
        failure.get("error_kind") or "other"
        for item in summaries
        for failure in item.get("part_failures") or []
    )
    return {
        "tasks_total": len(summaries),
        "tasks_with_features": len(ok),
        "assembly_tasks": sum(bool(item.get("is_assembly")) for item in summaries),
        "parts_total": sum(item.get("part_count", 0) for item in summaries),
        "parts_opened": sum(item.get("parts_opened", 0) for item in summaries),
        "part_failure_kinds": dict(failure_kinds),
        "open_failures": [
            {"task_id": item["task_id"], "failures": item.get("part_failures")}
            for item in summaries
            if item.get("part_failures")
        ],
        "tasks_not_extracted": [
            item["task_id"] for item in summaries if not item.get("part_count")
        ],
        "feature_count_per_part": {
            "mean": round(total_features / len(counts), 2) if counts else 0,
            "median": counts[len(counts) // 2] if counts else 0,
            "max": counts[-1] if counts else 0,
        },
        "feature_category_totals": dict(category_totals.most_common()),
        "feature_type_totals": dict(type_totals.most_common()),
        "feature_bigrams_top30": dict(bigrams.most_common(30)),
        "tasks_using": {
            category: sum(
                any(category in sequence for sequence in item.get("feature_sequences") or [])
                for item in ok
            )
            for category in category_totals
        },
        "narration_time_share_mean": {
            key: round(value / max(1, len(summaries)), 4)
            for key, value in time_share.most_common()
        },
        "alignment_coverage": round(aligned / total_features, 4) if total_features else 0,
        "tasks": summaries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--force", action="store_true", help="重新打开已提取过的任务")
    parser.add_argument("--no-step", action="store_true")
    parser.add_argument("--stats-only", action="store_true", help="不连 SolidWorks，只根据已有 features.json 汇总")
    args = parser.parse_args()

    task_dirs = sorted(
        path for path in RAW_DIR.iterdir()
        if path.is_dir() and (path / "task_desc.json").exists()
    )
    if args.ids:
        task_dirs = [path for path in task_dirs if path.name in set(args.ids)]
    if args.limit:
        task_dirs = task_dirs[: args.limit]

    summaries = []
    for index, task_dir in enumerate(task_dirs, 1):
        features_path = task_dir / "features.json"
        if features_path.exists() and (args.stats_only or not args.force):
            record = _load_json(features_path)
            source = "cached"
        elif args.stats_only:
            record = {"task_id": task_dir.name, "parts": [], "error": None}
            source = "skipped"
        else:
            record = extract_task(task_dir, args.sw_url.rstrip("/"), export_step=not args.no_step)
            if record.get("error") == "missing output.sldprt":
                source = "incomplete"
            else:
                features_path.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                source = "extracted"
        summary = summarize_task(task_dir, record)
        summaries.append(summary)
        print(
            f"[{index}/{len(task_dirs)}] {task_dir.name} {source} "
            f"features={summary.get('feature_count')} error={summary.get('open_error')}",
            flush=True,
        )

    stats = aggregate(summaries)
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"[cad1k] tasks={stats['tasks_total']} with_features={stats['tasks_with_features']} "
        f"open_failures={len(stats['open_failures'])} -> {STATS_PATH}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
