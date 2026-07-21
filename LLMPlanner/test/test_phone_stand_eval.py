from eval.eval_phone_stand_modes import score_observation
from planner.schemas import SwOperation


SPEC = {
    "overall_size": [116, 78, 118],
    "acceptance": {
        "bbox_tolerance_mm": 5,
        "minimum_volume_mm3": 80000,
        "maximum_volume_mm3": 180000,
        "minimum_cut_features": 2,
        "minimum_fillet_features": 1,
        "minimum_total_features": 4,
        "minimum_faces": 10,
    },
}


def test_complete_phone_stand_scores_one_hundred():
    observation = {
        "bounding_box_mm": {
            "sizeX": 116,
            "sizeY": 78,
            "sizeZ": 118,
        },
        "volume_mm3": 125000,
        "features": [
            {"type": "Extrusion"},
            {"type": "ICE"},
            {"type": "ICE"},
            {"type": "Fillet"},
        ],
        "faces": [{}] * 12,
    }
    result = score_observation(
        observation,
        status="completed",
        successful_actions=8,
        total_actions=8,
        spec=SPEC,
    )
    assert result["score"] == 100
    assert result["acceptance_ok"] is True


def test_basic_wedge_gets_partial_credit_without_detail_features():
    observation = {
        "bounding_box_mm": {
            "sizeX": 78,
            "sizeY": 116,
            "sizeZ": 118,
        },
        "volume_mm3": 120000,
        "features": [{"type": "Extrusion"}],
        "faces": [{}] * 6,
    }
    result = score_observation(
        observation,
        status="completed",
        successful_actions=1,
        total_actions=1,
        spec=SPEC,
    )
    assert result["score"] == 70
    assert result["acceptance_ok"] is False
    assert result["detail_points"] == 5


def test_through_all_cut_gets_required_api_depth():
    operation = SwOperation(
        operation="feature_cut_extrusion",
        params={"endCondition": "throughAll"},
    )
    assert operation.params["endCondition"] == "through_all"
    assert operation.params["depth"] == 1.0
