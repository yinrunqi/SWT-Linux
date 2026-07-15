"""Evaluate the first five Web Demo text prompts on a 100-point scale.

Each case is worth 20 points:
- required plan operations: 4
- successful execution steps: 8
- bounding-box axes within tolerance: 4
- expected feature counts: 4
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


@dataclass(frozen=True)
class EvalCase:
    name: str
    prompt: str
    required_ops: tuple[str, ...]
    expected_bbox_mm: tuple[float, float, float]
    bbox_tolerance_mm: float
    feature_checks: tuple[tuple[str, int], ...]


@dataclass
class CaseResult:
    name: str
    score: float
    duration_seconds: float
    plan_steps: int
    successful_steps: int
    unexecuted_steps: int
    failed_steps: list[dict[str, Any]]
    required_operations: dict[str, bool]
    bbox_mm: dict[str, float] | None
    bbox_axes_ok: dict[str, bool]
    feature_counts: dict[str, int]
    feature_checks_ok: dict[str, bool]
    error: str | None = None


DICE_PROMPT = (
    "请帮我建模一颗标准的六面骰子（色子），要求如下：\n"
    "1. 骰子主体是一个边长 20mm 的正方体；\n"
    "2. 正方体的 12 条棱边都做 R1.5mm 的圆角，让边角圆润；\n"
    "3. 六个面上分别有 1、2、3、4、5、6 个圆形点数；每个点数是一个直径约 3mm、"
    "深约 1.5mm 的圆形凹坑（向内凹陷，不是凸起）；\n"
    "4. 遵循标准骰子规则：相对的两个面点数之和为 7（即 1 对 6、2 对 5、3 对 4）；\n"
    "5. 各面点数的常规排布：1 点在面中心；2 点和 3 点沿面的一条对角线均匀分布；"
    "4 点在面的四个角附近；5 点是四个角加面中心；6 点是两列、每列三个点。\n"
    "请自行计算每个面以及每个凹点在模型空间中的位置，完成完整建模。"
)

CASES = (
    EvalCase(
        name="四脚板凳",
        prompt=(
            "创建一个四脚板凳：凳面为 200mm×200mm×20mm 的方形平板，"
            "在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 150mm），"
            "腿的圆心距凳面边缘 30mm。"
        ),
        required_ops=("new_part", "feature_extrusion"),
        expected_bbox_mm=(200, 170, 200),
        bbox_tolerance_mm=20,
        feature_checks=(("Extrusion", 5),),
    ),
    EvalCase(
        name="圆角板凳",
        prompt=(
            "创建一个四脚板凳并对凳面做圆角处理：凳面为 200mm×200mm×20mm 的方形平板，"
            "在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 150mm），"
            "腿的圆心距凳面边缘 30mm。最后对凳面顶部的 4 条边做 R5mm 圆角。"
        ),
        required_ops=("new_part", "feature_extrusion", "feature_fillet"),
        expected_bbox_mm=(200, 170, 200),
        bbox_tolerance_mm=20,
        feature_checks=(("Extrusion", 5), ("Fillet", 1)),
    ),
    EvalCase(
        name="收纳盒",
        prompt=(
            "创建一个长方形收纳盒：外部尺寸为长 120mm、宽 80mm、高 50mm，"
            "盒壁厚度 8mm，盒子内部是一个开口向上的空腔（顶部敞开），"
            "底部厚度也是 8mm。最后对盒子顶部开口的四条边做 R3mm 圆角。"
        ),
        required_ops=("new_part", "feature_extrusion", "feature_cut_extrusion"),
        expected_bbox_mm=(120, 50, 80),
        bbox_tolerance_mm=20,
        feature_checks=(("Extrusion", 1), ("Cut", 1)),
    ),
    EvalCase(
        name="简化骰子",
        prompt=(
            "创建一个骰子的基础外形：先做一个边长 20mm 的正方体，"
            "然后对正方体的所有 12 条棱边做 R2mm 的圆角，使其成为一个边角圆润的立方块。"
        ),
        required_ops=("new_part", "feature_extrusion", "feature_fillet"),
        expected_bbox_mm=(20, 20, 20),
        bbox_tolerance_mm=5,
        feature_checks=(("Extrusion", 1), ("Fillet", 1)),
    ),
    EvalCase(
        name="标准骰子",
        prompt=DICE_PROMPT,
        required_ops=(
            "new_part",
            "feature_extrusion",
            "feature_fillet",
            "feature_cut_extrusion",
        ),
        expected_bbox_mm=(20, 20, 20),
        bbox_tolerance_mm=3,
        feature_checks=(("Fillet", 1), ("Cut", 1)),
    ),
)


def get_json(url: str, timeout: int = 60) -> dict[str, Any]:
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json()


def feature_count(features: list[dict[str, Any]], keyword: str) -> int:
    needle = keyword.lower()
    return sum(
        1
        for feature in features
        if needle in str(feature.get("type", "")).lower()
        or needle in str(feature.get("name", "")).lower()
    )


def evaluate_case(
    case: EvalCase,
    web_url: str,
    sw_url: str,
    vision_model: str,
    plan_model: str,
) -> CaseResult:
    started = time.perf_counter()
    print(f"\n{'=' * 72}\n{case.name}\n{'=' * 72}", flush=True)
    try:
        response = requests.post(
            f"{web_url}/api/run",
            json={
                "instruction": case.prompt,
                "provider": "aiping",
                "api_key": "",
                "base_url": "https://aiping.cn/api/v1",
                "vision_model": vision_model,
                "plan_model": plan_model,
                "dry_run": False,
            },
            timeout=900,
        )
        payload = response.json()
        if not response.ok or not payload.get("success"):
            raise RuntimeError(payload.get("error") or f"HTTP {response.status_code}")

        steps = payload["plan"]["steps"]
        results = payload["results"]
        operations = [step["operation"] for step in steps]
        required_ok = {op: op in operations for op in case.required_ops}
        successful_steps = sum(bool(result.get("success")) for result in results)

        bbox_payload = get_json(f"{sw_url}/api/body-bounding-box")
        bbox = bbox_payload["boundingBox"]
        bbox_mm = {
            "sizeX": bbox["sizeX"] * 1000,
            "sizeY": bbox["sizeY"] * 1000,
            "sizeZ": bbox["sizeZ"] * 1000,
        }
        expected = dict(zip(("sizeX", "sizeY", "sizeZ"), case.expected_bbox_mm))
        bbox_axes_ok = {
            axis: abs(bbox_mm[axis] - expected[axis]) <= case.bbox_tolerance_mm
            for axis in expected
        }

        tree_payload = get_json(f"{sw_url}/api/feature-tree")
        features = tree_payload.get("features", [])
        feature_counts = {
            keyword: feature_count(features, keyword)
            for keyword, _ in case.feature_checks
        }
        feature_checks_ok = {
            keyword: feature_counts[keyword] >= minimum
            for keyword, minimum in case.feature_checks
        }

        plan_score = 4 * sum(required_ok.values()) / len(required_ok)
        # Executor stops after a failed step, so use the complete plan length as denominator.
        execution_score = 8 * successful_steps / max(len(steps), 1)
        bbox_score = 4 * sum(bbox_axes_ok.values()) / len(bbox_axes_ok)
        feature_score = 4 * sum(feature_checks_ok.values()) / len(feature_checks_ok)
        score = round(plan_score + execution_score + bbox_score + feature_score, 2)

        result = CaseResult(
            name=case.name,
            score=score,
            duration_seconds=round(time.perf_counter() - started, 2),
            plan_steps=len(steps),
            successful_steps=successful_steps,
            unexecuted_steps=max(len(steps) - len(results), 0),
            failed_steps=[
                {
                    "step": result.get("step"),
                    "operation": result.get("operation"),
                    "message": result.get("message"),
                }
                for result in results
                if not result.get("success")
            ],
            required_operations=required_ok,
            bbox_mm={key: round(value, 3) for key, value in bbox_mm.items()},
            bbox_axes_ok=bbox_axes_ok,
            feature_counts=feature_counts,
            feature_checks_ok=feature_checks_ok,
        )
        print(
            f"得分 {score:.2f}/20；执行 {successful_steps}/{len(steps)}；"
            f"包围盒 {result.bbox_mm}",
            flush=True,
        )
        return result
    except Exception as exc:
        print(f"评测失败: {exc}", flush=True)
        return CaseResult(
            name=case.name,
            score=0,
            duration_seconds=round(time.perf_counter() - started, 2),
            plan_steps=0,
            successful_steps=0,
            unexecuted_steps=0,
            failed_steps=[],
            required_operations={},
            bbox_mm=None,
            bbox_axes_ok={},
            feature_counts={},
            feature_checks_ok={},
            error=str(exc),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-url", default="http://localhost:5000")
    parser.add_argument("--sw-url", default="http://localhost:5100")
    parser.add_argument("--vision-model", default="GLM-4.6V")
    parser.add_argument("--plan-model", default="Qwen3.5-397B-A17B")
    parser.add_argument("--output", type=Path, default=Path("eval/text_first5_result.json"))
    args = parser.parse_args()

    results = [
        evaluate_case(
            case,
            args.web_url.rstrip("/"),
            args.sw_url.rstrip("/"),
            args.vision_model,
            args.plan_model,
        )
        for case in CASES
    ]
    total_score = round(sum(result.score for result in results), 2)
    report = {
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "provider": "AI Ping",
        "base_url": "https://aiping.cn/api/v1",
        "vision_model": args.vision_model,
        "plan_model": args.plan_model,
        "score": total_score,
        "max_score": 100,
        "cases": [asdict(result) for result in results],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"\n总分: {total_score:.2f}/100")
    print(f"报告: {args.output}")
    return 0 if total_score == 100 else 1


if __name__ == "__main__":
    raise SystemExit(main())
