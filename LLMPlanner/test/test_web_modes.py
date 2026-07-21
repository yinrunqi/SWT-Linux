"""Protocol regression tests for Web Demo single-turn and multi-turn modes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import web.app as web_app
from planner.schemas import (
    ModelObservation,
    MultiTurnResult,
    SwModelingPlan,
    SwOperation,
    ThreeViewIR,
)


class FakeClient:
    def __init__(self):
        self._calls = 0

    def plan(self, instruction, image_base64=None):
        self._calls += 2
        return SwModelingPlan(
            user_intent=instruction,
            steps=[SwOperation(operation="new_part")],
        )

    def _extract_three_view_ir(self, instruction, image_b64):
        return ThreeViewIR(
            part_type="test",
            overall_size_mm={"length": 20, "height": 20, "depth": 20},
        )

    def compose_multiturn_three_view_goal(self, instruction, _ir):
        return instruction

    def _compose_stage_b_prompt(self, instruction, _ir):
        return instruction

    def _plan_text(self, instruction, input_mode="text"):
        return self.plan(instruction)

    def usage_snapshot(self):
        return {
            "prompt_tokens": 10 * self._calls,
            "completion_tokens": 5 * self._calls,
            "total_tokens": 15 * self._calls,
            "calls": self._calls,
            "by_model": {},
        }

    def usage_since(self, before):
        now = self.usage_snapshot()
        return {
            key: now[key] - before[key]
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "calls",
            )
        } | {"by_model": {}}

    def close(self):
        return None


class FakeExecutor:
    def execute_plan(self, plan, dry_run=False):
        return [{
            "step": 1,
            "operation": "new_part",
            "success": True,
            "message": "ok",
        }]


class FakeMultiTurnAgent:
    def __init__(self, llm_client, executor, max_turns=8):
        self.max_turns = max_turns

    def run(self, instruction):
        observation = ModelObservation(
            features=[{"type": "Extrusion"}],
            bounding_box_mm={"sizeX": 20, "sizeY": 20, "sizeZ": 20},
            volume_mm3=8000,
            state_fingerprint="fake",
        )
        return MultiTurnResult(
            status="completed",
            user_goal=instruction,
            final_observation=observation,
            total_actions=4,
            successful_actions=4,
            llm_calls=2,
            duration_seconds=1.0,
            message="done",
        )

    def run_blueprint(self, instruction, task_plan, batches, llm_calls=0):
        return self.run(instruction).model_copy(
            update={"llm_calls": llm_calls}
        )


def run_tests() -> None:
    original_executor = web_app.executor
    original_get_client = web_app._get_llm_client
    original_agent = web_app.MultiTurnAgent
    web_app.executor = FakeExecutor()
    web_app._get_llm_client = lambda *_args, **_kwargs: FakeClient()
    web_app.MultiTurnAgent = FakeMultiTurnAgent
    client = web_app.app.test_client()
    base_request = {
        "instruction": "创建 20mm 立方体",
        "api_key": "test-key",
        "base_url": "https://example.invalid/v1",
        "vision_model": "vision",
        "plan_model": "plan",
    }

    try:
        response = client.post("/api/run", json=base_request)
        payload = response.get_json()
        assert response.status_code == 200
        assert set(payload) == {"success", "plan", "results", "summary"}
        assert payload["summary"]["all_ok"] is True
        print("[PASS] omitted mode preserves exact single-turn response")

        response = client.post(
            "/api/run",
            json={**base_request, "mode": "single_turn"},
        )
        payload = response.get_json()
        assert response.status_code == 200
        assert payload["mode"] == "single_turn"
        assert payload["token_usage"]["total_tokens"] > 0
        print("[PASS] explicit single-turn reports request token usage")

        multi_request = {**base_request, "mode": "multi_turn", "max_turns": 5}
        response = client.post("/api/run", json=multi_request)
        payload = response.get_json()
        assert response.status_code == 200
        assert payload["mode"] == "multi_turn"
        assert payload["summary"]["status"] == "completed"
        assert payload["summary"]["llm_calls"] == 2
        assert payload["multi_turn"]["final_observation"]["volume_mm3"] == 8000
        print("[PASS] multi-turn response exposes turns and final observation")

        response = client.post(
            "/api/run",
            json={**base_request, "mode": "unknown"},
        )
        assert response.status_code == 400
        print("[PASS] unknown modeling mode is rejected")

        response = client.post(
            "/api/run",
            json={**base_request, "mode": "multi_turn", "image": "abc"},
        )
        assert response.status_code == 200
        assert response.get_json()["vision_ir"]["part_type"] == "test"
        print("[PASS] multi-turn accepts three-view image input")

        assert not hasattr(web_app, "_match_three_view_sample")
        print("[PASS] runtime modeling does not inspect evaluation fixtures")

        samples_response = client.get("/api/three-view-samples")
        assert samples_response.status_code == 200
        samples = samples_response.get_json()["samples"]
        forbidden_tokens = (
            "sketch_",
            "feature_",
            "select_face_and_sketch",
            "edgeSelection",
        )
        assert samples
        assert all(
            not any(token in sample["prompt"] for token in forbidden_tokens)
            for sample in samples
        )
        pen_holder = next(
            sample
            for sample in samples
            if sample["part_id"] == "C4"
        )
        assert pen_holder["subdir"] == "C4_desktop_pen_holder"
        assert "笔筒" in pen_holder["label"]
        print("[PASS] Web sample prompts contain no operation-level answers")

        assert hasattr(web_app._sw_execution_lock, "acquire")
        print("[PASS] shared SolidWorks execution lock is configured")

        page = client.get("/").get_data(as_text=True)
        script = client.get("/app.js").get_data(as_text=True)
        assert 'id="gearbox-run-btn"' in page
        assert 'id="gearbox-gear-select"' in page
        assert 'id="gearbox-motion-play-btn"' in page
        assert "/api/assembly-examples/two-stage-gearbox/run" in script
        assert "/api/gearbox-animation/load" in script
        print("[PASS] gearbox multi-turn demo is exposed in assembly UI")

        assert 'id="engine-run-btn"' in page
        assert 'id="engine-angle"' in page
        assert 'id="engine-pose-btn"' in page
        assert (
            "/api/assembly-examples/single-cylinder-engine/run"
            in script
        )
        assert "/api/engine-animation/pose" in script
        print("[PASS] crank-slider engine demo is exposed in assembly UI")

        assert 'id="lamp-run-btn"' in page
        assert 'id="lamp-height"' in page
        assert 'id="lamp-pose-btn"' in page
        assert (
            "/api/assembly-examples/adjustable-desk-lamp/run"
            in script
        )
        assert "/api/lamp-animation/pose" in script
        print("[PASS] adjustable desk lamp demo is exposed in assembly UI")

        assert 'id="umbrella-run-btn"' in page
        assert 'id="umbrella-openness"' in page
        assert 'id="umbrella-pose-btn"' in page
        assert (
            "/api/assembly-examples/radial-folding-umbrella/run"
            in script
        )
        assert "/api/umbrella-animation/pose" in script
        print("[PASS] radial folding umbrella demo is exposed in assembly UI")

        assert 'id="drill-run-btn"' in page
        assert 'id="drill-angle"' in page
        assert 'id="drill-opening"' in page
        assert 'id="drill-pose-btn"' in page
        assert "/api/assembly-examples/cordless-drill/run" in script
        assert "/api/drill-animation/pose" in script
        print("[PASS] planetary cordless drill demo is exposed in assembly UI")

        assert 'id="projector-run-btn"' in page
        assert 'id="projector-angle"' in page
        assert 'id="projector-pose-btn"' in page
        assert "/api/assembly-examples/film-projector/run" in script
        assert "/api/projector-animation/pose" in script
        print("[PASS] film projector demo is exposed in assembly UI")

        assert 'id="phone-stand-single-btn"' in page
        assert 'id="phone-stand-multi-btn"' in page
        assert "runPhoneStand" in script
        assert "PHONE_STAND_PROMPT" in script
        print("[PASS] phone stand exposes both modeling modes")

        component = web_app._resolve_gearbox_component(
            {
                "result": {
                    "components": [{
                        "name": "input_pinion_z12-1",
                        "path": r"C:\parts\input_pinion_z12.SLDPRT",
                    }]
                }
            },
            "input",
        )
        assert component == "input_pinion_z12-1"
        print("[PASS] gearbox gear roles resolve to observed instances")
    finally:
        web_app.executor = original_executor
        web_app._get_llm_client = original_get_client
        web_app.MultiTurnAgent = original_agent


if __name__ == "__main__":
    run_tests()
    print("\nAll Web mode tests passed.")
