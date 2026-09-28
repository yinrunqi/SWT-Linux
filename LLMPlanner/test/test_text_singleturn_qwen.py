from pathlib import Path

from eval.eval_text_singleturn_qwen import CASES, score_case


def test_all_new_benchmark_prompts_match_web_catalog():
    catalog = (
        Path(__file__).parents[1]
        / "web"
        / "static"
        / "prompt-catalog.js"
    ).read_text(encoding="utf-8")
    dev = [case for case in CASES if case.split == "dev"]
    assert len(dev) == 15
    for case in dev:
        if case.case_id == "phone_stand":
            assert "const PHONE_STAND_PROMPT" in catalog
            assert "底座长110mm、宽78mm、厚8mm" in catalog
            assert "3个36×8mm的水平贯穿散热槽" in catalog
        else:
            assert case.prompt in catalog


def test_heldout_cases_are_separate_and_well_formed():
    heldout = [case for case in CASES if case.split == "heldout"]
    assert len(heldout) >= 12
    assert len({case.case_id for case in CASES}) == len(CASES)
    for case in heldout:
        low, high = case.volume_range_mm3
        assert 0 < low < high
        assert case.expected_details
        assert all(value > 0 for value in case.expected_bbox_mm)


def test_perfect_flange_scores_one_hundred():
    case = next(item for item in CASES if item.case_id == "flange")
    steps = [
        {"operation": "feature_extrusion"},
        {"operation": "feature_cut_extrusion"},
        {"operation": "feature_cut_extrusion"},
    ]
    payload = {
        "plan": {"steps": steps},
        "results": [
            {"operation": item["operation"], "success": True}
            for item in steps
        ],
    }
    observation = {
        "bounding_box_mm": {
            "sizeX": 100,
            "sizeY": 12,
            "sizeZ": 100,
        },
        "volume_mm3": 82146,
        "features": [],
        "faces": [{}] * 20,
        "sketches": [
            {"name": "base", "circleCount": 1},
            {"name": "center", "circleCount": 1},
            {"name": "bolts", "circleCount": 6},
        ],
        "feature_sketch_links": [
            {"operationKind": "extrusion", "sketchName": "base"},
            {"operationKind": "cut", "sketchName": "center"},
            {"operationKind": "cut", "sketchName": "bolts"},
        ],
    }
    scored = score_case(case, payload, observation)
    assert scored["score"] == 100
    assert scored["acceptance_ok"] is True
    assert scored["actual_details"]["circles"] == 8


def test_failed_execution_keeps_partial_product_credit():
    case = next(item for item in CASES if item.case_id == "hex_nut")
    payload = {
        "plan": {
            "steps": [
                {"operation": "feature_extrusion"},
                {"operation": "feature_cut_extrusion"},
            ],
        },
        "results": [
            {"operation": "feature_extrusion", "success": True},
            {"operation": "feature_cut_extrusion", "success": False},
        ],
    }
    observation = {
        "bounding_box_mm": {
            "sizeX": 30,
            "sizeY": 8,
            "sizeZ": 34.641,
        },
        "volume_mm3": 6200,
        "features": [],
        "faces": [{}] * 8,
        "sketches": [],
        "feature_sketch_links": [
            {"operationKind": "extrusion", "sketchName": "base"},
        ],
    }
    scored = score_case(case, payload, observation)
    assert 50 < scored["score"] < 100
    assert scored["acceptance_ok"] is False


def test_missing_body_gets_no_dimension_credit():
    case = next(item for item in CASES if item.case_id == "wrench")
    scored = score_case(
        case,
        {
            "plan": {"steps": [{"operation": "sketch_polygon"}]},
            "results": [{"operation": "sketch_polygon", "success": True}],
        },
        {},
    )
    assert scored["dimension_points"] == 0
    assert scored["dimension_checks"] == [False, False, False]
