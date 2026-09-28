"""只依赖真实模型状态的几何/特征指标与统计工具。

指标全部来自 SolidWorks 特征树（``features[].type`` 即 ``GetTypeName2``）和
``/api/sketch-stats`` 返回的真实草图几何，不读取本服务在建模时记录的
``feature_sketch_links`` / ``sketches`` 元数据，也不统计“成功执行了几次某操作”。
因此同一套函数既能给 LLM 生成的模型打分，也能统计外部打开的 SLDPRT（如 CAD-1K）。
"""

from __future__ import annotations

import math
import random
from collections import Counter
from typing import Iterable, Sequence

# GetTypeName2 -> 类别。ICE 同时用于凸台和切除拉伸，需要再看特征名。
_TYPE_CATEGORY = {
    "Extrusion": "extrusions",
    "Boss": "extrusions",
    "BossThin": "extrusions",
    "Cut": "cuts",
    "CutThin": "cuts",
    "Revolution": "revolves",
    "RevolutionThin": "revolves",
    "RevCut": "revolve_cuts",
    "Sweep": "sweeps",
    "SweepCut": "sweep_cuts",
    "Blend": "lofts",
    "BlendCut": "loft_cuts",
    "Fillet": "fillets",
    "VarFillet": "fillets",
    "FullRoundFillet": "fillets",
    "Chamfer": "chamfers",
    "HoleWzd": "holes",
    "AdvHoleWzd": "holes",
    "SimpleHole": "holes",
    "Shell": "shells",
    "LPattern": "patterns",
    "CirPattern": "patterns",
    "SketchPattern": "patterns",
    "CurvePattern": "patterns",
    "TablePattern": "patterns",
    "MirrorPattern": "mirrors",
    "MirrorSolid": "mirrors",
    "Rib": "ribs",
    "Draft": "drafts",
    "Thread": "threads",
    "Dome": "domes",
    "RefPlane": "ref_planes",
    "RefAxis": "ref_axes",
    "ProfileFeature": "sketches",
    "3DProfileFeature": "sketches",
}

_CUT_NAME_HINTS = ("cut", "切除")
_BOSS_NAME_HINTS = ("boss", "凸台", "extrude", "拉伸")
_DEFAULT_REF_PLANES = 3  # 前视/上视/右视 基准面总是存在

# 切除类特征：cuts 指标把所有“去除材料”的特征都算上。
_REMOVAL_CATEGORIES = ("cuts", "revolve_cuts", "sweep_cuts", "loft_cuts")


def classify_feature(feature: dict) -> str | None:
    feature_type = str(feature.get("type") or "")
    if feature_type == "ICE":
        name = str(feature.get("name") or "").lower()
        if any(hint in name for hint in _CUT_NAME_HINTS):
            return "cuts"
        if any(hint in name for hint in _BOSS_NAME_HINTS):
            return "extrusions"
        return "ice_unknown"
    return _TYPE_CATEGORY.get(feature_type)


def feature_counts(features: Iterable[dict]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for feature in features or []:
        if feature.get("suppressed"):
            continue
        category = classify_feature(feature)
        if category:
            counts[category] += 1
    counts["ref_planes"] = max(0, counts["ref_planes"] - _DEFAULT_REF_PLANES)
    return dict(counts)


def _is_slot(sketch: dict) -> bool:
    return int(sketch.get("lines") or 0) == 2 and int(sketch.get("arcs") or 0) == 2


def _is_removal_consumer(consumer: dict) -> bool:
    return classify_feature(consumer) in _REMOVAL_CATEGORIES


def sketch_counts(sketch_stats: Iterable[dict]) -> dict[str, int]:
    """只统计被某个特征消费的草图；画了但没用上的草图不构成几何。"""
    circles = polygons = centerlines = slots = cut_circles = 0
    lines = arcs = splines = 0
    for sketch in sketch_stats or []:
        if not sketch.get("consumers"):
            continue
        n_circles = int(sketch.get("circles") or 0)
        circles += n_circles
        polygons += bool(sketch.get("regularPolygon"))
        centerlines += int(sketch.get("constructionLines") or 0)
        slots += _is_slot(sketch)
        lines += int(sketch.get("lines") or 0)
        arcs += int(sketch.get("arcs") or 0)
        splines += int(sketch.get("splines") or 0)
        if any(_is_removal_consumer(item) for item in sketch.get("consumers") or []):
            cut_circles += n_circles
    return {
        "circles": circles,
        "polygons": polygons,
        "centerlines": centerlines,
        "slots": slots,
        "cut_circles": cut_circles,
        "sketch_lines": lines,
        "sketch_arcs": arcs,
        "sketch_splines": splines,
    }


def model_metrics(features: Iterable[dict], sketch_stats: Iterable[dict]) -> dict[str, int]:
    counts = feature_counts(features)
    metrics = {
        "extrusions": counts.get("extrusions", 0),
        "cuts": sum(counts.get(name, 0) for name in _REMOVAL_CATEGORIES),
        "revolves": counts.get("revolves", 0),
        "fillets": counts.get("fillets", 0),
        "chamfers": counts.get("chamfers", 0),
        "holes": counts.get("holes", 0),
        "shells": counts.get("shells", 0),
        "patterns": counts.get("patterns", 0),
        "mirrors": counts.get("mirrors", 0),
        "sweeps": counts.get("sweeps", 0),
        "lofts": counts.get("lofts", 0),
        "ribs": counts.get("ribs", 0),
    }
    metrics["solid_features"] = sum(
        metrics[name] for name in ("extrusions", "revolves", "sweeps", "lofts")
    )
    metrics.update(sketch_counts(sketch_stats))
    return metrics


def dimension_score(
    actual: Sequence[float],
    expected: Sequence[float],
    tolerance: float,
    *,
    strict_orientation: bool = False,
    points_per_axis: float = 10.0,
) -> tuple[float, list[float], list[bool]]:
    """包围盒三轴得分。默认按排序后比较（与朝向无关），strict 模式按 X/Y/Z 对齐比较。"""
    actual_values = list(actual) if strict_orientation else sorted(actual)
    expected_values = list(expected) if strict_orientation else sorted(expected)
    errors = [abs(a - e) for a, e in zip(actual_values, expected_values)]
    if not any(value > 0 for value in actual_values):
        return 0.0, [round(value, 3) for value in errors], [False] * len(expected_values)
    passed = [error <= tolerance for error in errors]
    points = [
        points_per_axis
        if ok
        else points_per_axis * max(0.0, 1.0 - (error - tolerance) / (2 * tolerance))
        for error, ok in zip(errors, passed)
    ]
    return round(sum(points), 2), [round(value, 3) for value in errors], passed


def volume_score(actual: float, expected_range: Sequence[float], max_points: float = 20.0) -> tuple[float, bool]:
    low, high = expected_range
    if low <= actual <= high:
        return max_points, True
    if actual <= 0:
        return 0.0, False
    distance = low - actual if actual < low else actual - high
    return round(max_points * max(0.0, 1.0 - distance / (high - low)), 2), False


def pass_at_k(n: int, c: int, k: int) -> float:
    """Chen et al. 2021 的无偏估计：n 次采样中 c 次通过时的 pass@k。"""
    if n <= 0 or k <= 0:
        return 0.0
    k = min(k, n)
    if n - c < k:
        return 1.0
    return 1.0 - math.prod(1.0 - k / i for i in range(n - c + 1, n + 1))


def bootstrap_ci(
    values: Sequence[float],
    *,
    iterations: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float] | None:
    """对均值做百分位 bootstrap 置信区间；单个样本时返回 None。"""
    data = [float(value) for value in values]
    if len(data) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(
        sum(rng.choice(data) for _ in data) / len(data)
        for _ in range(iterations)
    )
    low = means[int(alpha / 2 * iterations)]
    high = means[min(iterations - 1, int((1 - alpha / 2) * iterations))]
    return round(low, 3), round(high, 3)
