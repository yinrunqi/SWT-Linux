"""Deterministic postcondition validators for multi-turn milestones."""

from __future__ import annotations

from typing import Callable

from .schemas import ModelObservation, PostconditionSpec, ValidationResult


def _feature_count(observation: ModelObservation, keyword: str) -> int:
    needle = keyword.lower()
    return sum(
        needle in f"{item.get('name', '')} {item.get('type', '')}".lower()
        for item in observation.features
    )


class ValidatorRegistry:
    """Run declarative checks against a structured observation."""

    @classmethod
    def validate(
        cls,
        specs: list[PostconditionSpec],
        observation: ModelObservation,
    ) -> ValidationResult:
        if not specs:
            specs = [PostconditionSpec(kind="valid_entity")]
        checks: dict[str, bool] = {}
        details: dict = {}
        for index, spec in enumerate(specs):
            handler: Callable | None = getattr(
                cls,
                f"_check_{spec.kind}",
                None,
            )
            key = f"{index}:{spec.kind}"
            if handler is None:
                checks[key] = False
                details[key] = {"error": f"未知验收条件: {spec.kind}"}
                continue
            passed, evidence = handler(observation, spec.params)
            checks[key] = passed
            details[key] = evidence
        passed = bool(checks) and all(checks.values())
        return ValidationResult(
            passed=passed,
            checks=checks,
            details=details,
            message="全部后置条件通过" if passed else "仍有后置条件未通过",
        )

    @staticmethod
    def _check_valid_entity(observation, _params):
        valid = (
            not observation.errors
            and observation.bounding_box_mm is not None
            and (observation.volume_mm3 or 0) > 0
        )
        return valid, {
            "bbox": observation.bounding_box_mm,
            "volume_mm3": observation.volume_mm3,
            "errors": observation.errors,
        }

    @staticmethod
    def _check_last_execution_success(observation, _params):
        results = observation.last_execution
        passed = bool(results) and all(
            bool(result.get("success", False))
            for result in results
        )
        return passed, {
            "operation_count": len(results),
            "failed": [
                {
                    "operation": result.get("operation"),
                    "message": result.get("message"),
                }
                for result in results
                if not result.get("success", False)
            ],
        }

    @staticmethod
    def _check_bbox_axes(observation, params):
        bbox = observation.bounding_box_mm or {}
        actual = sorted(
            float(bbox.get(key, 0))
            for key in ("sizeX", "sizeY", "sizeZ")
        )
        expected = sorted(float(item) for item in params["expected"])
        tolerance = float(params.get("tolerance", 1.0))
        passed = len(actual) == 3 and all(
            abs(value - target) <= tolerance
            for value, target in zip(actual, expected)
        )
        return passed, {
            "actual_mm": actual,
            "expected_mm": expected,
            "tolerance_mm": tolerance,
        }

    @staticmethod
    def _check_feature_count(observation, params):
        keyword = str(params["keyword"])
        minimum = int(params.get("minimum", 1))
        actual = _feature_count(observation, keyword)
        return actual >= minimum, {
            "keyword": keyword,
            "actual": actual,
            "minimum": minimum,
        }

    @staticmethod
    def _check_volume_range(observation, params):
        volume = observation.volume_mm3
        minimum = float(params.get("minimum", 0))
        maximum = float(params.get("maximum", float("inf")))
        passed = volume is not None and minimum <= volume <= maximum
        return passed, {
            "actual_mm3": volume,
            "minimum_mm3": minimum,
            "maximum_mm3": maximum,
        }

    @staticmethod
    def _check_fillet_radius(observation, params):
        target_m = float(params["radius_mm"]) / 1000
        minimum = int(params.get("minimum_features", 1))
        tolerance = float(params.get("tolerance_mm", 0.1)) / 1000
        matching = []
        for feature in observation.features:
            if "fillet" not in str(feature.get("type", "")).lower():
                continue
            dimensions = feature.get("dimensions", [])
            if any(
                abs(float(item.get("value", 0)) - target_m) <= tolerance
                for item in dimensions
            ):
                matching.append(feature.get("name", ""))
        edge_count = max(
            (
                int(result.get("edgeCount", 0))
                for result in observation.last_execution
                if result.get("operation") == "feature_fillet"
                and result.get("success")
            ),
            default=0,
        )
        require_all_edges = bool(params.get("require_all_edges", True))
        all_edges = edge_count >= int(params.get("minimum_edges", 12))
        passed = len(matching) >= minimum and (
            all_edges if require_all_edges else True
        )
        return passed, {
            "target_radius_mm": target_m * 1000,
            "matching_features": matching,
            "edge_count": edge_count,
            "all_edges_in_one_operation": all_edges,
        }

    @staticmethod
    def _check_face_pips(observation, params):
        face_key = str(params["face_key"])
        expected = int(params["circle_count"])
        radius_target = float(params.get("diameter_mm", 3.0)) / 2000
        radius_tolerance = float(params.get("radius_tolerance_mm", 0.15)) / 1000
        sketches = [
            sketch
            for sketch in observation.sketches
            if sketch.get("faceKey") == face_key
            and int(sketch.get("circleCount", 0)) == expected
            and all(
                abs(float(circle.get("radius", 0)) - radius_target)
                <= radius_tolerance
                for circle in sketch.get("circles", [])
            )
        ]
        links = [
            link
            for link in observation.feature_sketch_links
            if link.get("faceKey") == face_key
            and str(link.get("featureType", "")).upper() == "ICE"
            and abs(
                float(link.get("depth", 0)) * 1000
                - float(params.get("cut_depth_mm", 1.5))
            ) <= float(params.get("depth_tolerance_mm", 0.2))
        ]
        linked_sketches = {link.get("sketchName") for link in links}
        exact = [
            sketch for sketch in sketches
            if sketch.get("name") in linked_sketches
        ]
        minimum_ice = int(params.get("minimum_ice_count", 1))
        ice_count = _feature_count(observation, "ice")
        passed = len(exact) == 1 and len(links) == 1 and ice_count >= minimum_ice
        return passed, {
            "face_key": face_key,
            "expected_circle_count": expected,
            "matching_sketches": [item.get("name") for item in exact],
            "cut_features": [item.get("featureName") for item in links],
            "ice_count": ice_count,
        }

    @staticmethod
    def _check_state_changed(observation, params):
        previous = str(params.get("previous_fingerprint", ""))
        changed = bool(previous) and observation.state_fingerprint != previous
        return changed, {
            "previous": previous,
            "current": observation.state_fingerprint,
        }

