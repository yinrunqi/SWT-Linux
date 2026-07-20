"""Regression tests for state-aware Multi-turn caching."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from planner.llm_client import SwLLMClient
from planner.schemas import AgentTurn, ModelObservation


class _Cache:
    def __init__(self, value: str):
        self.value = value
        self.requested_keys: list[str] = []

    def get(self, key: str):
        self.requested_keys.append(key)
        return self.value

    def set(self, key: str, value: str):
        self.value = value

    def delete(self, _key: str):
        self.value = None


def test_agent_turn_cache_skips_provider_call() -> None:
    cached_turn = AgentTurn(
        decision="blocked",
        milestone_id="m1",
        progress_summary="cached",
        check_summary="",
        next_milestone="",
    )
    client = object.__new__(SwLLMClient)
    client.use_cache = True
    client.cache = _Cache(cached_turn.model_dump_json())
    client.plan_model = "test-model"
    client._assemble_system_prompt = lambda *_args, **_kwargs: "system"

    class _ExplodingProvider:
        @property
        def chat(self):
            raise AssertionError("provider must not be called on cache hit")

    client.client = _ExplodingProvider()
    result = client.decide_agent_turn(
        "goal",
        ModelObservation(state_fingerprint="same-state"),
        {"recent_turns": []},
        1,
    )

    assert result.progress_summary == "cached"
    assert client.cache.requested_keys


def test_usage_since_reports_request_delta() -> None:
    client = object.__new__(SwLLMClient)
    client.token_usage = {
        "prompt_tokens": 130,
        "completion_tokens": 70,
        "total_tokens": 200,
        "calls": 3,
        "by_model": {
            "model": {
                "prompt_tokens": 130,
                "completion_tokens": 70,
                "total_tokens": 200,
                "calls": 3,
            }
        },
    }
    before = {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
        "calls": 2,
        "by_model": {
            "model": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "calls": 2,
            }
        },
    }

    assert client.usage_since(before) == {
        "prompt_tokens": 30,
        "completion_tokens": 30,
        "total_tokens": 60,
        "calls": 1,
        "by_model": {
            "model": {
                "prompt_tokens": 30,
                "completion_tokens": 30,
                "total_tokens": 60,
                "calls": 1,
            }
        },
    }
