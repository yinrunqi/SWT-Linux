"""SolidWorks model-state observer for the multi-turn agent."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import requests

from .schemas import ModelObservation


class SwObserver:
    """Collect a compact, deterministic snapshot from existing C# APIs."""

    def __init__(
        self,
        base_url: str = "http://localhost:5100",
        session: requests.Session | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self._owns_session = session is None

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def _get(self, path: str) -> tuple[dict[str, Any] | None, str | None]:
        try:
            response = self.session.get(f"{self.base_url}{path}", timeout=60)
            response.raise_for_status()
            payload = response.json()
            if not payload.get("success", False):
                return None, str(payload.get("message") or "查询失败")
            return payload, None
        except Exception as exc:
            return None, str(exc)

    @staticmethod
    def _compact_features(payload: dict[str, Any] | None) -> list[dict]:
        if payload is None:
            return []
        compact: list[dict] = []
        for feature in payload.get("features", []):
            compact.append({
                "index": feature.get("index"),
                "name": feature.get("name", ""),
                "type": feature.get("type", ""),
                "suppressed": bool(feature.get("suppressed", False)),
                "dimensions": feature.get("dimensions", []),
            })
        return compact

    @staticmethod
    def _bbox_mm(payload: dict[str, Any] | None) -> dict | None:
        if payload is None:
            return None
        bbox = payload.get("boundingBox")
        if not isinstance(bbox, dict):
            return None
        result: dict[str, float] = {}
        for key, value in bbox.items():
            if isinstance(value, (int, float)):
                result[key] = round(float(value) * 1000, 6)
        return result or None

    def collect(self, last_execution: list[dict] | None = None) -> ModelObservation:
        snapshot, snapshot_error = self._get("/api/model-observation")
        if snapshot is not None:
            features = self._compact_features(snapshot)
            bounding_box_mm = self._bbox_mm(snapshot)
            raw_volume = snapshot.get("volumeMm3")
            volume_mm3 = (
                round(float(raw_volume), 6)
                if isinstance(raw_volume, (int, float))
                else None
            )
            return self._build_observation(
                features=features,
                faces=snapshot.get("faces", []),
                sketches=snapshot.get("sketches", []),
                feature_sketch_links=snapshot.get(
                    "featureSketchLinks",
                    [],
                ),
                bounding_box_mm=bounding_box_mm,
                volume_mm3=volume_mm3,
                last_execution=last_execution or [],
                errors={},
            )

        feature_payload, feature_error = self._get("/api/feature-tree")
        bbox_payload, bbox_error = self._get("/api/body-bounding-box")
        volume_payload, volume_error = self._get("/api/body-volume")

        errors = {
            name: error
            for name, error in (
                ("feature_tree", feature_error),
                ("bounding_box", bbox_error),
                ("volume", volume_error),
            )
            if error
        }
        features = self._compact_features(feature_payload)
        bounding_box_mm = self._bbox_mm(bbox_payload)
        volume_mm3 = None
        if volume_payload is not None:
            raw_volume = volume_payload.get("volumeMm3")
            if isinstance(raw_volume, (int, float)):
                volume_mm3 = round(float(raw_volume), 6)

        if snapshot_error and "404" not in snapshot_error:
            errors["model_observation"] = snapshot_error
        return self._build_observation(
            features=features,
            faces=[],
            sketches=[],
            feature_sketch_links=[],
            bounding_box_mm=bounding_box_mm,
            volume_mm3=volume_mm3,
            last_execution=last_execution or [],
            errors=errors,
        )

    @staticmethod
    def _build_observation(
        *,
        features: list[dict],
        faces: list[dict],
        sketches: list[dict],
        feature_sketch_links: list[dict],
        bounding_box_mm: dict | None,
        volume_mm3: float | None,
        last_execution: list[dict],
        errors: dict[str, str],
    ) -> ModelObservation:
        fingerprint_payload = {
            "features": features,
            "faces": faces,
            "sketches": sketches,
            "feature_sketch_links": feature_sketch_links,
            "bounding_box_mm": bounding_box_mm,
            "volume_mm3": volume_mm3,
            "errors": errors,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

        return ModelObservation(
            features=features,
            faces=faces,
            sketches=sketches,
            feature_sketch_links=feature_sketch_links,
            bounding_box_mm=bounding_box_mm,
            volume_mm3=volume_mm3,
            last_execution=last_execution,
            errors=errors,
            state_fingerprint=fingerprint,
        )
