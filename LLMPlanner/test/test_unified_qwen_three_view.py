from eval.eval_unified_qwen_three_view_five import score_generic


def test_generic_three_view_score_reaches_one_hundred():
    groundtruth = {
        "expected_bbox_mm": {
            "length": 86,
            "height": 86,
            "depth": 10,
        },
        "expected_volume_mm3": 66000,
        "holes": [],
        "operations": [
            {
                "type": "cutout_on_face",
                "shape": "rectangle",
            },
            {
                "type": "cutout_on_face",
                "shape": "circle",
                "through_all": True,
                "positions": [[1, 1], [-1, 1], [1, -1], [-1, -1]],
            },
            {"type": "fillet_edges"},
        ],
    }
    result = {
        "status": "completed",
        "bbox_mm": {"sizeX": 86, "sizeY": 86, "sizeZ": 10},
        "volume_mm3": 66000,
        "actions": "12/12",
        "final_observation": {
            "features": [{"name": "圆角1", "type": "Fillet"}],
            "faces": [{}] * 20,
            "sketches": [
                {"name": "SketchCut1", "circleCount": 0},
                {"name": "SketchCut2", "circleCount": 4},
            ],
            "feature_sketch_links": [
                {
                    "operationKind": "cut",
                    "sketchName": "SketchCut1",
                    "depth": 0.003,
                },
                {
                    "operationKind": "cut",
                    "sketchName": "SketchCut2",
                    "depth": 0.010,
                },
            ],
        },
    }
    scored = score_generic(result, groundtruth)
    assert scored["score"] == 100
    assert scored["actual_details"]["cut_circles"] == 4
    assert scored["actual_details"]["cuts"] == 2


def test_generic_score_awards_partial_geometry_credit():
    groundtruth = {
        "expected_bbox_mm": {
            "length": 60,
            "height": 40,
            "depth": 20,
        },
        "expected_volume_mm3": 36000,
        "holes": [],
        "operations": [],
    }
    result = {
        "status": "blocked",
        "bbox_mm": {"sizeX": 60, "sizeY": 40, "sizeZ": 15},
        "volume_mm3": 30000,
        "actions": "3/4",
        "final_observation": {},
    }
    scored = score_generic(result, groundtruth)
    assert 20 < scored["score"] < 50
    assert scored["execution_points"] == 7.5
