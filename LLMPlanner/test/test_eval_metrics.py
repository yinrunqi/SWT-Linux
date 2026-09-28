from eval import metrics
from eval.eval_text_singleturn_qwen import CASES, score_case_v2

DEFAULT_TREE = [
    {"name": "Front Plane", "type": "RefPlane"},
    {"name": "Top Plane", "type": "RefPlane"},
    {"name": "Right Plane", "type": "RefPlane"},
    {"name": "Origin", "type": "OriginProfileFeature"},
]


def _case(case_id):
    return next(item for item in CASES if item.case_id == case_id)


def test_ice_is_split_by_feature_name_in_both_languages():
    features = DEFAULT_TREE + [
        {"name": "Boss-Extrude1", "type": "Extrusion"},
        {"name": "Cut-Extrude1", "type": "ICE"},
        {"name": "Boss-Extrude2", "type": "ICE"},
        {"name": "切除-拉伸2", "type": "ICE"},
        {"name": "凸台-拉伸3", "type": "ICE"},
        {"name": "Fillet1", "type": "Fillet"},
        {"name": "Fillet2", "type": "Fillet", "suppressed": True},
    ]
    counts = metrics.feature_counts(features)
    assert counts["extrusions"] == 3
    assert counts["cuts"] == 2
    assert counts["fillets"] == 1
    assert counts["ref_planes"] == 0


def test_sketch_counts_use_real_geometry_and_consumers():
    sketches = [
        {"lines": 6, "circles": 1, "regularPolygon": True,
         "consumers": [{"name": "Boss-Extrude1", "type": "Extrusion"}]},
        {"circles": 6, "consumers": [{"name": "Cut-Extrude1", "type": "ICE"}]},
        {"lines": 2, "arcs": 2, "constructionLines": 1,
         "consumers": [{"name": "Revolve-Cut1", "type": "RevCut"}]},
    ]
    counts = metrics.sketch_counts(sketches)
    assert counts["circles"] == 7
    assert counts["cut_circles"] == 6
    assert counts["polygons"] == 1
    assert counts["slots"] == 1
    assert counts["centerlines"] == 1


def test_pass_at_k_matches_known_values():
    assert metrics.pass_at_k(5, 0, 1) == 0.0
    assert metrics.pass_at_k(5, 5, 3) == 1.0
    assert abs(metrics.pass_at_k(4, 1, 1) - 0.25) < 1e-9
    assert abs(metrics.pass_at_k(4, 1, 2) - 0.5) < 1e-9


def test_bootstrap_ci_brackets_mean():
    low, high = metrics.bootstrap_ci([60, 70, 80, 90, 100])
    assert low <= 80 <= high
    assert metrics.bootstrap_ci([50]) is None


def test_strict_orientation_penalizes_rotated_bbox():
    loose, _, loose_ok = metrics.dimension_score([12, 100, 100], (100, 12, 100), 1.0)
    strict, _, strict_ok = metrics.dimension_score(
        [12, 100, 100], (100, 12, 100), 1.0, strict_orientation=True
    )
    assert loose == 30 and all(loose_ok)
    assert strict < loose and not all(strict_ok)


def test_v2_perfect_flange_from_feature_tree_only():
    observation = {
        "bounding_box_mm": {"sizeX": 100, "sizeY": 12, "sizeZ": 100},
        "volume_mm3": 82146,
        "features": DEFAULT_TREE + [
            {"name": "Boss-Extrude1", "type": "Extrusion"},
            {"name": "Cut-Extrude1", "type": "ICE"},
            {"name": "Cut-Extrude2", "type": "ICE"},
        ],
        "sketches": [],
        "feature_sketch_links": [],
    }
    sketch_stats = [
        {"circles": 1, "consumers": [{"name": "Boss-Extrude1", "type": "Extrusion"}]},
        {"circles": 1, "consumers": [{"name": "Cut-Extrude1", "type": "ICE"}]},
        {"circles": 6, "consumers": [{"name": "Cut-Extrude2", "type": "ICE"}]},
    ]
    payload = {"results": [{"success": True}] * 3}
    scored = score_case_v2(_case("flange"), payload, observation, sketch_stats)
    assert scored["score"] == 100
    assert scored["acceptance_ok"] is True


def test_v2_does_not_reward_short_plans_or_operation_counts():
    """计划只写 1 步、全部成功，但模型里没有旋转特征：v2 不给旋转细节分。"""
    observation = {
        "bounding_box_mm": {"sizeX": 10, "sizeY": 10, "sizeZ": 10},
        "volume_mm3": 1000,
        "features": DEFAULT_TREE + [{"name": "Boss-Extrude1", "type": "Extrusion"}],
    }
    payload = {"plan": {"steps": [{"operation": "feature_revolve"}]},
               "results": [{"operation": "feature_revolve", "success": True}]}
    case = next(item for item in CASES if dict(item.expected_details).get("revolves"))
    scored = score_case_v2(case, payload, observation, [])
    assert scored["actual_details"]["revolves"] == 0
    assert scored["detail_checks"]["revolves"] is False


def test_v2_failed_steps_reduce_robustness_but_final_model_matters():
    observation = {
        "bounding_box_mm": {"sizeX": 100, "sizeY": 12, "sizeZ": 100},
        "volume_mm3": 82146,
        "features": DEFAULT_TREE + [{"name": "Boss-Extrude1", "type": "Extrusion"}],
    }
    payload = {"results": [{"success": True}, {"success": False}, {"success": False}]}
    scored = score_case_v2(_case("flange"), payload, observation, [])
    assert scored["robustness_points"] == 5.0
    assert scored["validity_points"] == 15.0
    empty = score_case_v2(_case("flange"), payload, {}, [])
    assert empty["score"] == 0
