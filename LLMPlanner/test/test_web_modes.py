"""Protocol regression tests for Web Demo single-turn and multi-turn modes."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import web.app as web_app
from planner.schemas import ModelObservation, MultiTurnResult, SwModelingPlan, SwOperation


class FakeClient:
    def plan(self, instruction, image_base64=None):
        return SwModelingPlan(
            user_intent=instruction,
            steps=[SwOperation(operation="new_part")],
        )

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
        assert response.status_code == 400
        print("[PASS] MVP rejects unsupported multi-turn image input")

        assert hasattr(web_app._sw_execution_lock, "acquire")
        print("[PASS] shared SolidWorks execution lock is configured")
    finally:
        web_app.executor = original_executor
        web_app._get_llm_client = original_get_client
        web_app.MultiTurnAgent = original_agent


if __name__ == "__main__":
    run_tests()
    print("\nAll Web mode tests passed.")
