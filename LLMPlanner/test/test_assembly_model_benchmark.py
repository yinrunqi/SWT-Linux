from eval.eval_assembly_model_benchmark import (
    merge_machine_evidence,
    render_markdown,
    score_agent_run,
    summarize,
)


def _completed_result():
    return {
        "total_actions": 10,
        "successful_actions": 10,
        "milestones": [
            {"status": "completed"},
            {"status": "completed"},
        ],
    }


def test_gearbox_full_score_is_one_hundred():
    run = {
        "status": "completed",
        "passed": True,
        "acceptance": {
            "checks": {
                "0:valid_assembly": True,
                "1:component_count": True,
                "2:component_positions": True,
                "3:mate_type_count": True,
                "4:mate_type_count": True,
                "5:gear_ratios": True,
                "6:no_interference": True,
                "7:assembly_saved": True,
            },
        },
        "result": _completed_result(),
    }
    score = score_agent_run("gearbox", run)
    assert score["score"] == 100
    assert score["static_points"] == 40
    assert score["functional_points"] == 40
    assert score["execution_points"] == 20


def test_engine_partial_score_preserves_machine_evidence():
    run = {
        "status": "blocked",
        "passed": False,
        "static_acceptance": {
            "checks": {
                "0:valid_assembly": True,
                "1:component_count": False,
            },
        },
        "kinematic_acceptance": {
            "samples": [
                {"passed": True},
                {"passed": False},
            ],
            "cycle_closed": True,
            "actual_stroke_mm": 40,
            "expected_stroke_mm": 50,
        },
        "result": {
            "total_actions": 10,
            "successful_actions": 8,
            "milestones": [
                {"status": "completed"},
                {"status": "failed"},
            ],
        },
    }
    score = score_agent_run("engine", run)
    assert score["static_points"] == 20
    assert score["functional_points"] == 20
    assert score["execution_points"] == 11.5
    assert score["score"] == 51.5


def test_error_without_machine_evidence_scores_zero():
    score = score_agent_run(
        "projector",
        {"status": "error", "passed": False},
    )
    assert score["score"] == 0


def test_sewing_machine_scores_all_four_mechanism_groups():
    run = {
        "status": "completed",
        "passed": True,
        "static_acceptance": {
            "checks": {
                "0:valid_assembly": True,
                "1:component_count": True,
                "2:assembly_saved": True,
            },
        },
        "mechanism_acceptance": {
            "needle": {
                "samples": [{"passed": True}] * 2,
                "cycle_closed": True,
            },
            "rotary": {
                "samples": [{"passed": True}] * 2,
                "cycle_closed": True,
            },
            "four_bar": {
                "samples": [{"passed": True}] * 2,
                "cycle_closed": True,
            },
            "cam": {
                "samples": [{"passed": True}] * 2,
                "cycle_closed": True,
            },
        },
        "result": _completed_result(),
    }
    score = score_agent_run("sewing_machine", run)
    assert score["score"] == 100
    assert score["sample_count"] == 8
    assert score["global_check_rate"] == 1


def test_recovered_machine_evidence_scores_product_not_execution():
    primary = {
        "status": "error",
        "passed": False,
        "error": "provider protocol error",
    }
    recovered = {
        "status": "completed",
        "passed": True,
        "static_acceptance": {
            "checks": {
                "0:valid_assembly": True,
                "1:component_count": True,
            },
        },
        "indexing_acceptance": {
            "samples": [{"passed": True}, {"passed": True}],
            "dwell_locked": True,
            "full_cycle_closed": True,
        },
    }
    merged = merge_machine_evidence(primary, recovered)
    assert merged["status"] == "error"
    assert merged["passed"] is False
    assert merged["post_run_machine_passed"] is True
    score = score_agent_run("projector", merged)
    assert score["static_points"] == 40
    assert score["functional_points"] == 40
    assert score["execution_points"] == 0
    assert score["score"] == 80


def test_recovered_validation_can_complete_successful_agent_run():
    merged = merge_machine_evidence(
        {
            "status": "completed",
            "passed": False,
            "result": _completed_result(),
        },
        {
            "status": "completed",
            "passed": True,
            "static_acceptance": {"checks": {"0:valid_assembly": True}},
            "mechanism_acceptance": {"passed": True},
        },
    )
    assert merged["status"] == "completed"
    assert merged["passed"] is True
    assert merged["post_run_machine_passed"] is True


def test_summary_ranks_quality_before_token_efficiency():
    rows = [
        {
            "model": "model-a",
            "case_id": "gearbox",
            "case_label": "双级减速箱",
            "score": 100,
            "passed": True,
            "status": "completed",
            "duration_seconds": 10,
            "token_usage": {"total_tokens": 1000},
        },
        {
            "model": "model-b",
            "case_id": "gearbox",
            "case_label": "双级减速箱",
            "score": 80,
            "passed": False,
            "status": "blocked",
            "duration_seconds": 5,
            "token_usage": {"total_tokens": 100},
        },
    ]
    summary = summarize(
        rows,
        ["model-a", "model-b"],
        ["gearbox"],
    )
    assert summary["model_summaries"][0]["model"] == "model-a"
    markdown = render_markdown(summary, "test")
    assert "model-a" in markdown
    assert "100.00" in markdown
