"""装配加速真机检查：组合状态等价/缓存耗时，以及 Agent quick motion 与 legacy profile 隔离。"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest
import requests
import yaml

from planner.assembly_workflow.session import AssemblySession


pytestmark = pytest.mark.solidworks
SW = os.environ.get("SW_API_URL", "http://localhost:5100").rstrip("/")
ROOT = Path(__file__).resolve().parents[2]
SHARE = ROOT / "share" / "Workflow-CAD-Bench-Assembly-Oracle-50" / "cases"


def _post(path: str, body: dict | None = None) -> dict:
    response = requests.post(f"{SW}{path}", json=body or {}, timeout=900)
    response.raise_for_status()
    payload = response.json()
    assert payload.get("success") is not False, payload
    return payload.get("result") if isinstance(payload.get("result"), dict) else payload


def _close() -> None:
    try:
        requests.post(f"{SW}/api/close-all-documents", json={}, timeout=120)
    except requests.RequestException:
        pass


def _open(case_dir: Path) -> Path:
    assembly = case_dir / "oracle.SLDASM"
    _post("/api/assembly/open", {"assemblyPath": str(assembly)})
    return assembly


def _session(assembly: Path, *, speed: bool) -> AssemblySession:
    session = AssemblySession(
        base_url=SW,
        timeout=900,
        workflow="v3",
        speed_optimizations=speed,
    )
    session.has_assembly = True
    session.assembly_path = str(assembly)
    return session


def test_combined_state_matches_legacy_and_cached_reads_are_faster():
    case_dir = SHARE / "50_luffing_crane"
    _close()
    try:
        assembly = _open(case_dir)
        legacy = _session(assembly, speed=False)
        optimized = _session(assembly, speed=True)

        started = time.perf_counter()
        legacy_rows = [
            {
                "components": legacy.asmwf("components").get("components") or [],
                "mates": legacy.asmwf("mates").get("mates") or [],
            }
            for _ in range(3)
        ]
        legacy_seconds = time.perf_counter() - started

        started = time.perf_counter()
        combined_rows = [optimized.asmwf("state") for _ in range(3)]
        combined_seconds = time.perf_counter() - started

        first = optimized.state_bundle(force=True)
        started = time.perf_counter()
        cached_rows = [optimized.state_bundle() for _ in range(10)]
        cached_seconds = time.perf_counter() - started

        def component_names(row):
            return sorted(item["name"] for item in row["components"])

        def mate_names(row):
            return sorted(str(item.get("name")) for item in row["mates"])

        assert component_names(legacy_rows[-1]) == component_names(combined_rows[-1])
        assert mate_names(legacy_rows[-1]) == mate_names(combined_rows[-1])
        assert all(component_names(row) == component_names(first) for row in cached_rows)
        assert optimized.state_cache_hits >= 10
        assert cached_seconds < legacy_seconds
        print(json.dumps({
            "legacy_3x_seconds": round(legacy_seconds, 4),
            "combined_3x_seconds": round(combined_seconds, 4),
            "cached_10x_seconds": round(cached_seconds, 6),
            "components": len(first["components"]),
            "mates": len(first["mates"]),
        }, ensure_ascii=False))
    finally:
        _close()


def test_quick_motion_uses_fewer_frames_than_legacy_on_same_assembly():
    case_dir = SHARE / "01_two_stage_gearbox"
    aliases = json.loads((case_dir / "metadata" / "aliases.json").read_text(encoding="utf-8"))
    spec = yaml.safe_load((case_dir / "metadata" / "spec.yaml").read_text(encoding="utf-8"))["motion"]
    args = {
        key: spec[key]
        for key in ("driver", "sweep_deg", "frames", "rpm", "expect_fixed", "expect_moving")
        if key in spec
    }
    _close()
    try:
        assembly = _open(case_dir)
        legacy = _session(assembly, speed=False)
        legacy.aliases = dict(aliases)
        started = time.perf_counter()
        full = legacy.check_motion(args)
        full_seconds = time.perf_counter() - started

        # Motion Study 连续建删会受前一轮内部状态影响；两组都从重新打开的同一文件开始。
        _close()
        assembly = _open(case_dir)
        optimized = _session(assembly, speed=True)
        optimized.aliases = dict(aliases)
        started = time.perf_counter()
        quick = optimized.check_motion(args)
        quick_seconds = time.perf_counter() - started

        assert full["frames"] == 13
        assert "sampling_profile" not in full
        assert quick["sampling_profile"] == "quick"
        assert quick["frames"] == 7
        assert quick["dense_frames"] <= 61
        assert sorted(full["stuck"]) == sorted(quick["stuck"])
        assert sorted(full["drifted_fixed"]) == sorted(quick["drifted_fixed"])
        assert quick_seconds < full_seconds
        print(json.dumps({
            "legacy_motion_seconds": round(full_seconds, 3),
            "quick_motion_seconds": round(quick_seconds, 3),
            "speedup": round(full_seconds / max(quick_seconds, 1e-9), 3),
            "legacy_frames": full["frames"],
            "quick_frames": quick["frames"],
            "quick_dense_frames": quick["dense_frames"],
            "legacy_passed": full["passed"],
            "quick_passed": quick["passed"],
        }, ensure_ascii=False))
    finally:
        _close()


def test_best_snapshot_restores_the_saved_structure(tmp_path):
    case_dir = SHARE / "01_two_stage_gearbox"
    aliases = json.loads((case_dir / "metadata" / "aliases.json").read_text(encoding="utf-8"))
    _close()
    try:
        working = tmp_path / "working.SLDASM"
        best = tmp_path / "best.SLDASM"
        shutil.copy2(case_dir / "oracle.SLDASM", working)
        _post("/api/assembly/open", {"assemblyPath": str(working)})
        session = _session(working, speed=True)
        session.aliases = dict(aliases)

        initial = session.state_bundle(force=True)
        fixed = next(item["name"] for item in initial["components"] if item.get("isFixed"))
        session.save_best_snapshot(best)
        changed = session.call("unfix_component", {"component": fixed})
        assert changed["ok"]
        assert not next(
            item["isFixed"] for item in session.state_bundle(force=True)["components"]
            if item["name"] == fixed
        )

        session.restore_best_snapshot(best, working)
        restored = session.state_bundle()
        assert next(item["isFixed"] for item in restored["components"] if item["name"] == fixed)
        assert session.saved is True
        assert working.is_file() and best.is_file()
    finally:
        _close()
