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
        self._document_type = "part"

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

    def _post(
        self,
        path: str,
        payload: dict | None = None,
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            response = self.session.post(
                f"{self.base_url}{path}",
                json=payload or {},
                timeout=90,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("success", False):
                return None, str(data.get("message") or "查询失败")
            return data, None
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
        executions = last_execution or []
        for result in executions:
            operation = str(result.get("operation", ""))
            if operation == "new_part":
                self._document_type = "part"
            elif operation.startswith("assembly_"):
                self._document_type = "assembly"

        if self._document_type == "assembly":
            return self._collect_assembly(executions)

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
                last_execution=executions,
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
            last_execution=executions,
            errors=errors,
        )

    def _collect_assembly(
        self,
        last_execution: list[dict],
    ) -> ModelObservation:
        payload, observation_error = self._get(
            "/api/assembly/observation"
        )
        result = (
            payload.get("result", {})
            if isinstance(payload, dict)
            else {}
        )
        interference_payload, interference_error = self._post(
            "/api/assembly/check-interference"
        )
        interference = (
            interference_payload.get("result")
            if isinstance(interference_payload, dict)
            else None
        )
        errors = {}
        if observation_error:
            errors["assembly_observation"] = observation_error
        if interference_error:
            errors["assembly_interference"] = interference_error
        return self._build_observation(
            features=[],
            faces=[],
            sketches=[],
            feature_sketch_links=[],
            bounding_box_mm=None,
            volume_mm3=None,
            document_type="assembly",
            assembly_path=str(result.get("assemblyPath", "")),
            components=result.get("components", []),
            mates=result.get("mates", []),
            interference_summary=interference,
            last_execution=last_execution,
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
        document_type: str = "part",
        assembly_path: str = "",
        components: list[dict] | None = None,
        mates: list[dict] | None = None,
        interference_summary: dict | None = None,
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
            "document_type": document_type,
            "assembly_path": assembly_path,
            "components": components or [],
            "mates": mates or [],
            "interference_summary": interference_summary,
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
            document_type=document_type,
            assembly_path=assembly_path,
            components=components or [],
            mates=mates or [],
            interference_summary=interference_summary,
            last_execution=last_execution,
            errors=errors,
            state_fingerprint=fingerprint,
        )
