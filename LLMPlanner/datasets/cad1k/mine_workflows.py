"""从 CAD-1K 已提取的特征树、narration 和 rubric 中挖掘工程师 workflow。

只做离线分析，不作为评测集。输入是 extract_features.py 生成的 raw/{id}/features.json
以及数据集自带的 narration.json / rubrics.json / task_desc.json。

挖掘三类信号：
  1. 特征序列：实体特征 token（带草图轮廓签名）的 n-gram，按任务数计支持度；
  2. narration 细粒度操作：草图工具、终止条件、参考几何、编辑方式等；
  3. rubric 设计意图：沉头孔、节圆均布孔、筋板、键槽等工程语义。
另外按特征数和种类给零件排复杂度，供挑选 demo。

输出 datasets/cad1k/workflows.json（只含派生统计与任务 id，不复制原文）。

用法:
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\mine_workflows.py
    venv\\Scripts\\python.exe -X utf8 datasets\\cad1k\\mine_workflows.py --show-examples
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
LLM_ROOT = HERE.parent.parent
if str(LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLM_ROOT))

from eval import metrics as geo_metrics  # noqa: E402

RAW_DIR = HERE / "raw"
OUT_PATH = HERE / "workflows.json"

DEFAULT_PLANE_NAMES = frozenset({
    "Front Plane", "Top Plane", "Right Plane", "前视基准面", "上视基准面", "右视基准面",
})

TOKEN_OF_CATEGORY = {
    "extrusions": "EXT", "cuts": "CUT", "revolves": "REV", "revolve_cuts": "REVCUT",
    "sweeps": "SWEEP", "sweep_cuts": "SWEEPCUT", "lofts": "LOFT", "loft_cuts": "LOFTCUT",
    "fillets": "FIL", "chamfers": "CHAM", "holes": "HOLE", "shells": "SHELL",
    "patterns": "PAT", "mirrors": "MIR", "ribs": "RIB", "drafts": "DRAFT",
    "threads": "THREAD", "domes": "DOME", "ref_planes": "PLANE", "ref_axes": "AXIS",
}
SKETCH_DRIVEN = {"EXT", "CUT", "REV", "REVCUT", "SWEEP", "SWEEPCUT", "LOFT", "LOFTCUT", "RIB"}

# narration 细粒度操作：(操作名, 分组, 必须同时命中的关键词组)。每组内任一关键词命中即可。
NARRATION_OPS: tuple[tuple[str, str, tuple[tuple[str, ...], ...]], ...] = (
    ("centerline / construction", "sketch", (("centerline", "construction line", "construction geometry"),)),
    ("trim / extend", "sketch", (("trim", "extend entities", "extended the"),)),
    ("offset entities", "sketch", (("offset entities", "offset the sketch", "offset sketch"),)),
    ("convert entities", "sketch", (("convert entities", "converted the edge", "converted edges"),)),
    ("mirror entities (sketch)", "sketch", (("mirror entities", "mirrored the sketch", "sketch mirror"),)),
    ("sketch fillet / chamfer", "sketch", (("sketch fillet", "sketch chamfer"),)),
    ("sketch pattern", "sketch", (("sketch pattern", "circular sketch pattern", "linear sketch pattern"),)),
    ("tangent / 3-point arc", "sketch", (("tangent arc", "3-point arc", "three-point arc", "centerpoint arc"),)),
    ("slot", "sketch", (("slot",),)),
    ("polygon", "sketch", (("polygon", "hexagon"),)),
    ("spline", "sketch", (("spline",),)),
    ("sketch on face", "sketch", (("sketch", "sketched"), ("on the face", "on the top face", "selected face",
                                                            "planar face", "on the front face", "on the end face",
                                                            "face of the"))),
    ("relations (tangent/concentric/equal/symmetric)", "constraint",
     (("tangent relation", "concentric", "equal relation", "symmetric", "collinear", "midpoint", "coradial"),)),
    ("fully defined sketch", "constraint", (("fully defined", "fully constrain"),)),
    ("equations / global variables", "constraint", (("equation", "global variable", "linked value"),)),
    ("end: through all", "end_condition", (("through all",),)),
    ("end: mid plane", "end_condition", (("mid plane", "midplane", "mid-plane"),)),
    ("end: up to surface/vertex/next", "end_condition", (("up to surface", "up to vertex", "up to next",
                                                          "up to body", "offset from surface"),)),
    ("end: two directions", "end_condition", (("direction 2", "two directions", "both directions"),)),
    ("thin feature", "end_condition", (("thin feature",),)),
    ("reverse direction", "end_condition", (("reverse direction", "reversed the direction", "flip side",
                                             "flipped the direction"),)),
    ("offset plane", "reference", (("plane",), ("offset", "distance"))),
    ("angled plane", "reference", (("plane",), ("angle", "degrees", "inclined"))),
    ("reference axis", "reference", (("reference axis", "temporary axis", "temporary axes"),)),
    ("hole wizard", "hole", (("hole wizard",),)),
    ("counterbore / countersink", "hole", (("counterbore", "countersink", "counter-bore", "c'bore"),)),
    ("tapped hole / thread", "hole", (("tapped", "cosmetic thread", "thread"),)),
    ("linear pattern", "pattern", (("linear pattern",),)),
    ("circular pattern", "pattern", (("circular pattern",),)),
    ("mirror feature", "pattern", (("mirror",), ("feature", "body", "about the", "plane"))),
    ("fillet", "detail", (("fillet",),)),
    ("chamfer", "detail", (("chamfer",),)),
    ("shell", "detail", (("shell",),)),
    ("rib", "detail", ((" rib",),)),
    ("draft", "detail", (("draft",),)),
    ("sweep", "advanced", (("sweep",),)),
    ("loft", "advanced", (("loft",),)),
    ("revolve", "advanced", (("revolve",),)),
    ("edit sketch / edit feature", "edit", (("edit sketch", "edit feature", "edited the sketch",
                                             "edited the feature"),)),
    ("rollback / reorder / suppress", "edit", (("rollback", "roll back", "reorder", "suppress"),)),
    ("undo / delete", "edit", (("undo", "ctrl+z", "deleted"),)),
    ("measure / mass properties", "check", (("measure", "mass properties", "evaluate"),)),
    ("section view (model)", "check", (("section view",),)),
)

# rubric 设计意图：工程语义短语 -> 候选宏工具
RUBRIC_INTENTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bolt circle / equally spaced holes", ("equally spaced", "evenly spaced", "pitch circle", "bolt circle",
                                            "pcd", "equispaced", "uniformly spaced")),
    ("counterbore / countersink hole", ("counterbore", "countersink", "counter bore", "spotface")),
    ("tapped / threaded hole", ("tapped", "thread", "m3", "m4", "m5", "m6", "m8", "m10", "m12")),
    ("through hole / bore", ("through hole", "through-hole", "bore")),
    ("blind hole / pocket", ("blind", "pocket", "recess", "counter-sunk depth")),
    ("slot", ("slot",)),
    ("keyway", ("keyway", "key way", "key slot")),
    ("rib / gusset / web", ("rib", "gusset", "web")),
    ("boss / hub", ("boss", "hub")),
    ("flange", ("flange",)),
    ("fillet radius", ("fillet", "radius r", " r1", " r2", " r3", " r5", "rounded")),
    ("chamfer", ("chamfer",)),
    ("angled feature (degrees)", ("degree", "°", "inclined", "angled")),
    ("symmetry", ("symmetric", "mirror")),
    ("wall thickness / shell", ("wall thickness", "shell", "hollow")),
    ("taper / draft", ("taper", "draft", "conical")),
    ("hexagon", ("hexagon", "hex ")),
    ("groove", ("groove", "undercut")),
    ("spokes / arms", ("spoke", "arm")),
    ("teeth / serration / knurl", ("teeth", "tooth", "serration", "knurl", "spline shaft")),
    ("step / shoulder", ("step", "shoulder", "stepped")),
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _parts_of(record: dict) -> list[dict]:
    if "parts" in record:
        return record["parts"]
    if "features" in record:
        legacy = dict(record)
        legacy.setdefault("file", "output.sldprt")
        return [legacy]
    return []


def profile_signature(sketches: list[dict]) -> str:
    """把被某特征消费的草图几何压成一个粗签名。"""
    if not sketches:
        return ""
    total = Counter()
    for sketch in sketches:
        for key in ("lines", "arcs", "circles", "splines", "ellipses", "constructionLines"):
            total[key] += int(sketch.get(key) or 0)
        total["poly"] += int(bool(sketch.get("regularPolygon")))
    lines, arcs, circles = total["lines"], total["arcs"], total["circles"]
    if total["poly"]:
        base = "poly"
    elif total["splines"] or total["ellipses"]:
        base = "spline"
    elif circles and not lines and not arcs:
        base = "circle" if circles == 1 else "circles"
    elif lines and not arcs and not circles:
        base = "lines"
    elif lines == 2 and arcs == 2 and not circles:
        base = "slot"
    elif lines or arcs or circles:
        base = "mixed"
    else:
        base = "empty"
    return base + ("+cl" if total["constructionLines"] else "")


def part_tokens(part: dict) -> list[dict]:
    features = part.get("features") or []
    consumed: dict[str, list[dict]] = defaultdict(list)
    for sketch in part.get("sketch_stats") or []:
        for consumer in sketch.get("consumers") or []:
            consumed[str(consumer.get("name"))].append(sketch)
    tokens = []
    for feature in features:
        if feature.get("suppressed"):
            continue
        category = geo_metrics.classify_feature(feature)
        if category in (None, "sketches", "ice_unknown"):
            continue
        if category == "ref_planes" and feature.get("name") in DEFAULT_PLANE_NAMES:
            continue
        token = TOKEN_OF_CATEGORY.get(category)
        if not token:
            continue
        sketches = consumed.get(str(feature.get("name")), [])
        signature = profile_signature(sketches) if token in SKETCH_DRIVEN else ""
        tokens.append({
            "token": token,
            "sig": signature,
            "circles": sum(int(s.get("circles") or 0) for s in sketches),
        })
    return tokens


def ngrams(sequence: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(sequence[i:i + n]) for i in range(len(sequence) - n + 1)}


def _text_of(segment: dict) -> str:
    return f" {segment.get('action', '')} {segment.get('intent', '')} {segment.get('details', '')} ".lower()


def _match(text: str, groups: tuple[tuple[str, ...], ...]) -> bool:
    return all(any(keyword in text for keyword in group) for group in groups)


def _motifs(tokens: list[dict]) -> Counter:
    """工程语义 motif（候选宏工具），按零件内出现次数计。"""
    motifs: Counter = Counter()
    names = [t["token"] for t in tokens]
    for i, token in enumerate(tokens):
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if token["token"] in ("EXT", "REV") and token["sig"].startswith("circles"):
            motifs["annular boss (tube / bushing in one sketch)"] += 1
        if token["token"] == "REV" and "+cl" in token["sig"]:
            motifs["revolve profile about a centerline"] += 1
        if token["token"] in ("CUT", "HOLE") and token["circles"] >= 3:
            motifs["multi-hole cut in one sketch (>=3 circles)"] += 1
        if token["token"] in ("CUT", "HOLE") and nxt and nxt["token"] == "PAT":
            motifs["hole/cut then pattern"] += 1
        if token["token"] == "PLANE" and nxt and nxt["token"] in ("EXT", "CUT", "RIB", "REV", "SWEEP", "LOFT"):
            motifs[f"reference plane -> {nxt['token']}"] += 1
        if token["token"] == "MIR" and prev:
            motifs[f"{prev['token']} -> mirror"] += 1
        if token["token"] == "RIB":
            motifs["rib from open profile"] += 1
        if token["token"] == "CUT" and token["sig"] == "slot":
            motifs["slot cut"] += 1
        if token["token"] in ("EXT", "CUT") and token["sig"].startswith("poly"):
            motifs[f"polygon {token['token'].lower()} (hex etc.)"] += 1
        if token["token"] == "SHELL":
            motifs["shell (hollow with wall)"] += 1
        if token["token"] in ("SWEEP", "SWEEPCUT", "LOFT"):
            motifs[f"{token['token'].lower()} along path / between profiles"] += 1
    tail = 0
    for name in reversed(names):
        if name in ("FIL", "CHAM"):
            tail += 1
        else:
            break
    if tail >= 2:
        motifs["finishing fillet/chamfer chain (>=2 at end)"] += 1
    if names and names[0] in ("EXT", "REV") and "CUT" in names:
        motifs["base solid then subtractive details"] += 1
    return motifs


def mine(show_examples: bool) -> dict:
    tasks = sorted(path for path in RAW_DIR.iterdir() if path.is_dir())
    token_ngrams = {n: Counter() for n in (2, 3, 4)}
    sig_bigrams: Counter = Counter()
    motif_tasks: Counter = Counter()
    motif_counts: Counter = Counter()
    op_tasks: Counter = Counter()
    op_segments: Counter = Counter()
    op_seconds: Counter = Counter()
    intent_tasks: Counter = Counter()
    motif_examples: dict[str, list[str]] = defaultdict(list)
    ranking = []

    for task_dir in tasks:
        features_path = task_dir / "features.json"
        if not features_path.exists():
            continue
        record = _load(features_path)
        task_ngrams = {n: set() for n in token_ngrams}
        task_sig = set()
        task_motifs: Counter = Counter()
        for part in _parts_of(record):
            if part.get("error") or not part.get("features"):
                continue
            tokens = part_tokens(part)
            names = [t["token"] for t in tokens]
            for n in token_ngrams:
                task_ngrams[n] |= ngrams(names, n)
            labels = [f"{t['token']}[{t['sig']}]" if t["sig"] else t["token"] for t in tokens]
            task_sig |= ngrams(labels, 2)
            task_motifs.update(_motifs(tokens))
            solid = [t for t in tokens if t["token"] not in ("PLANE", "AXIS")]
            kinds = {t["token"] for t in solid}
            box = part.get("bounding_box_m") or {}
            ranking.append({
                "task_id": task_dir.name,
                "part": part.get("file", "output.sldprt"),
                "solid_features": len(solid),
                "distinct_kinds": len(kinds),
                "ref_planes": names.count("PLANE"),
                "faces": len(part.get("faces") or []),
                "sketches": len(part.get("sketch_stats") or []),
                "bbox_mm": [round(float(box.get(k) or 0) * 1000, 1) for k in ("sizeX", "sizeY", "sizeZ")],
                "volume_mm3": round(float(part.get("volume_mm3") or 0), 1),
                "sequence": names,
                "complexity": len(solid) + 2 * len(kinds) + 0.1 * len(part.get("faces") or []),
            })
        for n in token_ngrams:
            token_ngrams[n].update(task_ngrams[n])
        sig_bigrams.update(task_sig)
        motif_tasks.update(set(task_motifs))
        motif_counts.update(task_motifs)
        for motif in task_motifs:
            motif_examples[motif].append(task_dir.name)

        narration_path = task_dir / "narration.json"
        if narration_path.exists():
            segments = _load(narration_path).get("annotations") or []
            seen = set()
            for segment in segments:
                text = _text_of(segment)
                if "drawing" in text or "sheet" in text or "title block" in text:
                    continue
                duration = float(segment.get("end_s") or 0) - float(segment.get("start_s") or 0)
                for name, _group, groups in NARRATION_OPS:
                    if _match(text, groups):
                        op_segments[name] += 1
                        op_seconds[name] += duration
                        seen.add(name)
            op_tasks.update(seen)

        rubric_path = task_dir / "rubrics.json"
        if rubric_path.exists():
            text = " ".join(
                str(item.get("requirement", "")) for item in _load(rubric_path).get("rubrics") or []
            ).lower()
            intent_tasks.update(
                name for name, keywords in RUBRIC_INTENTS if any(keyword in text for keyword in keywords)
            )

    ranking.sort(key=lambda row: -row["complexity"])
    n_tasks = len(tasks)

    def top(counter: Counter, k: int, min_support: int = 3) -> list[dict]:
        return [
            {"pattern": " -> ".join(key) if isinstance(key, tuple) else key, "tasks": count}
            for key, count in counter.most_common(k) if count >= min_support
        ]

    groups = {name: group for name, group, _ in NARRATION_OPS}
    result = {
        "tasks": n_tasks,
        "feature_ngrams": {str(n): top(counter, 25) for n, counter in token_ngrams.items()},
        "signature_bigrams": top(sig_bigrams, 40),
        "motifs": [
            {"motif": name, "tasks": motif_tasks[name], "instances": motif_counts[name],
             "example_tasks": motif_examples[name][:5]}
            for name, _ in motif_tasks.most_common()
        ],
        "narration_ops": [
            {"op": name, "group": groups[name], "tasks": op_tasks[name],
             "segments": op_segments[name], "minutes": round(op_seconds[name] / 60, 1)}
            for name, _ in op_tasks.most_common()
        ],
        "rubric_intents": [{"intent": name, "tasks": count} for name, count in intent_tasks.most_common()],
        "complex_parts": [
            {k: v for k, v in row.items() if k != "complexity"} | {"complexity": round(row["complexity"], 1)}
            for row in ranking[:30]
        ],
    }
    if show_examples:
        for row in ranking[:30]:
            desc = _load(RAW_DIR / row["task_id"] / "task_desc.json")
            print(f"[{row['complexity']:.1f}] {row['task_id'][:8]} {row['part']} "
                  f"feat={row['solid_features']} kinds={row['distinct_kinds']} planes={row['ref_planes']} "
                  f"faces={row['faces']} bbox={row['bbox_mm']}")
            print("    ", " ".join(row["sequence"]))
            print("    ", desc.get("task", ""))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show-examples", action="store_true", help="在控制台打印复杂零件的任务描述（不写入文件）")
    args = parser.parse_args()
    result = mine(args.show_examples)
    OUT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT_PATH} ({result['tasks']} tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
