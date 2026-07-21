from eval.eval_phone_stand_modes import score_observation
from planner.agent_loop import MultiTurnAgent
from planner.executor import OPERATION_ENDPOINT_MAP
from planner.observer import SwObserver
from planner.schemas import (
    ModelObservation,
    PostconditionSpec,
    SwOperation,
)
from planner.validators import ValidatorRegistry


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


def test_observed_face_selection_contract_and_compaction():
    operation = SwOperation(
        operation="select_observed_face_and_sketch",
        params={"faceIndex": 7},
    )
    assert OPERATION_ENDPOINT_MAP[operation.operation][1] == (
        "/api/select-observed-face-and-sketch"
    )
    compact = SwObserver._compact_faces({
        "faces": [{
            "index": 7,
            "faceKey": "+Z",
            "surfaceType": "plane",
            "center": {"x": 0, "y": 0, "z": 0.01},
            "normal": {"x": 0, "y": 0, "z": 1},
            "area": 0.001,
            "geometricSignature": "plane:0:0:0.01:0.001",
            "persistRef": "large-volatile-reference",
        }],
    })
    assert compact[0]["index"] == 7
    assert "persistRef" not in compact[0]


def test_repair_ray_is_stabilized_to_aligned_observed_face():
    observation = ModelObservation(faces=[
        {
            "index": 4,
            "surfaceType": "plane",
            "center": {"x": 0.094, "y": 0.063, "z": -0.039},
            "normal": {"x": 0, "y": 0, "z": -1},
            "area": 0.01,
        },
        {
            "index": 9,
            "surfaceType": "plane",
            "center": {"x": 0.1, "y": 0.068, "z": 0},
            "normal": {"x": -0.96, "y": -0.28, "z": 0},
            "area": 0.02,
        },
        {
            "index": 12,
            "surfaceType": "plane",
            "center": {"x": 0.058, "y": 0.06, "z": -0.008},
            "normal": {"x": 0, "y": 0, "z": -1},
            "area": 0.001,
        },
    ])
    actions = [SwOperation(
        operation="select_face_and_sketch",
        params={
            "rx": 0.058,
            "ry": 0.06,
            "rz": -0.05,
            "dx": 0,
            "dy": 0,
            "dz": 1,
        },
    )]
    stabilized = MultiTurnAgent._stabilize_repair_face_selection(
        actions,
        observation,
    )
    assert stabilized[0].operation.value == (
        "select_observed_face_and_sketch"
    )
    assert stabilized[0].params == {"faceIndex": 4}


def test_feature_count_uses_semantic_operation_ledger_for_ice():
    observation = ModelObservation(
        features=[
            {"name": "Base", "type": "Extrusion"},
            *[
                {"name": f"ICE-{index}", "type": "ICE"}
                for index in range(6)
            ],
        ],
        feature_sketch_links=[
            *[
                {"operationKind": "extrusion"}
                for _ in range(3)
            ],
            *[
                {"operationKind": "cut"}
                for _ in range(4)
            ],
        ],
    )
    result = ValidatorRegistry.validate([
        PostconditionSpec(
            kind="feature_count",
            params={"keyword": "extr", "minimum": 3},
        ),
        PostconditionSpec(
            kind="feature_count",
            params={"keyword": "ice", "minimum": 5},
        ),
    ], observation)
    assert result.checks["0:feature_count"] is True
    assert result.checks["1:feature_count"] is False
