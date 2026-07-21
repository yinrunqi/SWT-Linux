from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))

from eval_pen_holder_three_view import score_pen_holder
from planner.schemas import SwOperation


GROUNDTRUTH = {
    "expected_bbox_mm": {
        "length": 140,
        "height": 110,
        "depth": 90,
    },
    "expected_volume_mm3": 449952,
}


def _complete_observation() -> dict:
    links = [
        {"operationKind": "extrusion", "depth": 0.09},
        *[
            {"operationKind": "cut", "depth": depth / 1000}
            for depth in (36, 70, 102, 9, 9, 1.5)
        ],
    ]
    features = [
        {"name": f"Feature-{index}", "type": "ICE"}
        for index in range(12)
    ]
    features.append({"name": "Fillet1", "type": "Fillet"})
    return {
        "features": features,
        "faces": [{} for _ in range(32)],
        "feature_sketch_links": links,
    }


def test_complete_pen_holder_scores_one_hundred():
    result = {
        "status": "completed",
        "actions": "24/24",
        "bbox_mm": {
            "sizeX": 140,
            "sizeY": 110,
            "sizeZ": 90,
        },
        "volume_mm3": 449952,
        "final_observation": _complete_observation(),
    }
    scored = score_pen_holder(result, GROUNDTRUTH)
    assert scored["score"] == 100
    assert scored["acceptance_ok"] is True


def test_basic_stepped_body_keeps_partial_credit():
    observation = _complete_observation()
    observation["feature_sketch_links"] = [
        {"operationKind": "extrusion", "depth": 0.09},
    ]
    observation["features"] = observation["features"][:12]
    result = {
        "status": "blocked",
        "actions": "4/6",
        "bbox_mm": {
            "sizeX": 140,
            "sizeY": 110,
            "sizeZ": 90,
        },
        "volume_mm3": 944640,
        "final_observation": observation,
    }
    scored = score_pen_holder(result, GROUNDTRUTH)
    assert 40 < scored["score"] < 80
    assert scored["acceptance_ok"] is False


def test_provider_nested_args_and_axis_shorthand_are_normalized():
    line = SwOperation.model_validate({
        "operation": "sketch_line",
        "params": {
            "args": {
                "x1": -0.07,
                "x2": -0.018,
                "y2": 0.044,
            },
        },
    })
    assert line.params == {
        "x1": -0.07,
        "x2": -0.018,
        "y1": 0.044,
        "y2": 0.044,
    }
