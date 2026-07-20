"""
app.py — Web Demo 后端

Flask 服务，为前端提供 API 接口，复用现有的 planner 模块。
启动方式: python -m web.app  (从 LLMPlanner 目录运行)
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import os
import threading
import traceback

# 将 LLMPlanner 目录加入 sys.path，以便导入 planner 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from load_env import ensure_test_env_loaded
from planner.agent_loop import MultiTurnAgent
from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor

ensure_test_env_loaded()

# ============================================================
# Flask App
# ============================================================

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# 全局实例
executor: SwExecutor | None = None
_sw_execution_lock = threading.Lock()

# 缓存的 LLM 客户端（按 api_key+base_url+model 缓存，避免重复创建）
_llm_cache: dict[str, SwLLMClient] = {}


def _get_llm_client(
    api_key: str,
    base_url: str,
    vision_model: str,
    plan_model: str,
    *,
    use_cache: bool = True,
) -> SwLLMClient:
    """获取或创建 LLM 客户端，相同配置复用实例。

    Vision 和 Plan 可分别配不同模型（例如 Vision 用便宜多模态 + Plan 用强规划）。
    若两者相同，SwLLMClient 会在启动日志里打印"Vision + Plan 同"提示。
    """
    if not use_cache:
        os.environ["OPENAI_API_KEY"] = api_key
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url
        return SwLLMClient(
            vision_model=vision_model,
            plan_model=plan_model,
            use_cache=False,
            skill_mode="routed",
        )

    cache_key = f"{api_key}|{base_url}|{vision_model}|{plan_model}"
    if cache_key not in _llm_cache:
        # 临时设置环境变量供 SwLLMClient 读取
        os.environ["OPENAI_API_KEY"] = api_key
        if base_url:
            os.environ["OPENAI_BASE_URL"] = base_url
        elif "OPENAI_BASE_URL" in os.environ:
            del os.environ["OPENAI_BASE_URL"]
        _llm_cache[cache_key] = SwLLMClient(
            vision_model=vision_model,
            plan_model=plan_model,
            use_cache=True,
            # ⚠️ 关键：与命令行测试 test_three_view_e2e.py 保持一致的 skill_mode。
            #   命令行用 skill_mode="routed"，只按 input_mode 精挑相关 skill 灌入 system_prompt。
            #   若 Web demo 用默认 skill_mode="full"，会把所有 skill 都灌入 → system_prompt
            #   完全不同 → cache key 完全不同 → 无法命中命令行已经跑通的 cache，
            #   而且 context 过大会让 Sonnet 5 更容易算错坐标（真实事故：C3 大齿轮种子齿
            #   x2=0.0675 而不是 0.0375，齿宽 35mm 而非 5mm，齿全飞出去）。
            #   两边统一为 "routed" 后：同一 prompt + 同一图 → 同一 cache key → 秒回同一 plan
            skill_mode="routed",
        )
    return _llm_cache[cache_key]


# ============================================================
# 前端页面
# ============================================================

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# datasets/ 静态文件访问（供前端"三视图建模"示例按钮拉取样例图片）
_DATASETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets",
)


def _match_three_view_sample(image_b64: str) -> dict | None:
    """Return local sample metadata when an uploaded image is a demo fixture."""
    try:
        uploaded_digest = hashlib.sha256(
            base64.b64decode(image_b64, validate=True)
        ).digest()
        root = os.path.join(_DATASETS_DIR, "manual_three_view")
        samples_path = os.path.join(root, "samples.json")
        samples = json.loads(
            open(samples_path, encoding="utf-8").read()
        )["samples"]
        for sample in samples:
            drawing = os.path.join(root, sample["subdir"], "drawing.png")
            if not os.path.exists(drawing):
                continue
            with open(drawing, "rb") as stream:
                if hashlib.sha256(stream.read()).digest() != uploaded_digest:
                    continue
            groundtruth_path = os.path.join(
                root,
                sample["subdir"],
                "groundtruth.json",
            )
            groundtruth = (
                json.loads(open(groundtruth_path, encoding="utf-8").read())
                if os.path.exists(groundtruth_path)
                else None
            )
            return {"sample": sample, "groundtruth": groundtruth}
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        return None
    return None


@app.route("/datasets/<path:subpath>")
def serve_dataset(subpath: str):
    """
    暴露 LLMPlanner/datasets/ 下的文件（仅只读）。
    仅用于给前端加载"三视图建模"示例图片（B01 L 型支架 drawing.png 等）。
    """
    return send_from_directory(_DATASETS_DIR, subpath)


@app.route("/api/three-view-samples")
def api_three_view_samples():
    """
    返回 datasets/manual_three_view/samples.json 中所有样例的 UI 元数据。
    供前端 renderThreeViewButtons() 动态渲染欢迎区按钮 —— 之后新增样例
    只需要改 samples.json，前端会自动跟随。
    """
    import json as _json

    samples_path = os.path.join(
        _DATASETS_DIR, "manual_three_view", "samples.json"
    )
    if not os.path.exists(samples_path):
        return jsonify({"samples": []})
    data = _json.loads(open(samples_path, encoding="utf-8").read())
    # 只吐前端需要的字段（不暴露完整几何 groundtruth）
    ui_only = [
        {
            "part_id": s["part_id"],
            "subdir": s["subdir"],
            "name": s.get("name", s["part_id"]),
            "label": s.get("ui", {}).get("label", s["part_id"]),
            "prompt": s.get("ui", {}).get("prompt", ""),
        }
        for s in data.get("samples", [])
    ]
    return jsonify({"samples": ui_only})


# ============================================================
# API 端点
# ============================================================

@app.route("/api/ping", methods=["GET"])
def ping():
    """检查 SolidWorks 服务连接状态。"""
    sw_ok = executor.ping() if executor else False
    return jsonify({"success": True, "sw_connected": sw_ok})


def _call_sw(path: str, method: str = "POST", body: dict | None = None) -> dict:
    """调用本机 C# SolidWorks 服务并统一处理错误。"""
    if executor is None:
        raise RuntimeError("SolidWorks 执行器尚未初始化")
    url = f"{executor.base_url}{path}"
    if method == "GET":
        response = executor.session.get(url, timeout=30)
    else:
        response = executor.session.post(url, json=body or {}, timeout=90)
    data = response.json()
    if not response.ok or not data.get("success"):
        raise RuntimeError(data.get("message") or f"SolidWorks API 请求失败: HTTP {response.status_code}")
    return data


@app.route("/api/gear-animation/load", methods=["POST"])
def load_gear_animation():
    """打开预置齿轮装配体，浮动主动轮并创建 5 秒恒速旋转马达。"""
    try:
        motor_component = "gear_small_involute_bored-1"
        assembly_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assembly_smoke",
            "gear_pair_assembly_v4.SLDASM",
        )
        if not os.path.exists(assembly_path):
            raise FileNotFoundError(f"预置齿轮装配体不存在: {assembly_path}")

        _call_sw("/api/assembly/open", body={"assemblyPath": assembly_path})
        _call_sw(
            "/api/assembly/unfix-component",
            body={"componentName": motor_component},
        )
        motor = _call_sw(
            "/api/assembly/motion/create-gear-motor",
            body={
                "componentName": motor_component,
                "rpm": 60,
                "durationSeconds": 5,
                "reverseDirection": False,
            },
        )
        return jsonify({
            "success": True,
            "message": "齿轮装配体与旋转马达已就绪",
            "result": motor.get("result", {}),
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/gear-animation/play", methods=["POST"])
def play_gear_animation():
    try:
        data = _call_sw("/api/assembly/motion/play")
        return jsonify(data)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/gear-animation/stop", methods=["POST"])
def stop_gear_animation():
    try:
        data = _call_sw("/api/assembly/motion/stop")
        return jsonify(data)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/gear-animation/status", methods=["GET"])
def gear_animation_status():
    try:
        data = _call_sw("/api/assembly/motion/status", method="GET")
        return jsonify(data)
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/run", methods=["POST"])
def run():
    """
    执行建模流程。mode 缺省为 single_turn，保持原有协议。

    请求体: {
      "instruction": "创建一个100mm的正方体",
      "api_key": "sk-...",
      "base_url": "https://...",   (可选)
      "vision_model": "basic/claude-sonnet-5",  (可选，Vision 阶段模型)
      "plan_model":   "basic/claude-sonnet-5",  (可选，Plan 阶段模型)
      "model": "gpt-4o",           (旧协议兼容，Vision + Plan 共用此模型)
      "mode": "single_turn",       (可选: single_turn / multi_turn)
      "max_turns": 8,              (multi_turn 可选)
      "dry_run": false
    }
    """
    data = request.get_json(force=True)
    instruction = data.get("instruction", "").strip()
    provider = data.get("provider", "aiping").strip()
    key_env = "JUHEAI_API_KEY" if provider == "juheai" else "OPENAI_API_KEY"
    url_env = "JUHEAI_BASE_URL" if provider == "juheai" else "OPENAI_BASE_URL"
    # 浏览器可覆盖服务端配置；留空时使用 LLMPlanner/.env，避免把 Key 存入浏览器。
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    # 兼容两种协议:
    #   新协议 (推荐): vision_model + plan_model 分别指定
    #   旧协议 (兼容): 单一 model 字段, Vision 和 Plan 都用它
    vision_model = data.get("vision_model", "").strip()
    plan_model = data.get("plan_model", "").strip()
    legacy_model = data.get("model", "").strip()
    if not vision_model:
        vision_model = legacy_model or os.getenv("VISION_MODEL", "gpt-4o")
    if not plan_model:
        plan_model = legacy_model or os.getenv("PLAN_MODEL", "gpt-4o")
    mode = data.get("mode", "single_turn").strip().lower()
    if mode not in ("single_turn", "multi_turn"):
        return jsonify({"success": False, "error": f"未知 mode: {mode}"}), 400
    raw_max_turns = data.get("max_turns")
    max_turns = None
    if raw_max_turns not in (None, ""):
        try:
            max_turns = max(1, min(int(raw_max_turns), 100))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "max_turns 必须是整数"}), 400
    dry_run = data.get("dry_run", False)
    # 多模态：可选图片（base64，可含或不含 data URI 前缀；不含时按 JPEG 处理）
    image_raw = data.get("image", "") or ""
    image_b64 = image_raw.strip()
    # 若前端传的是完整 data URI（data:image/png;base64,...），后端只需 base64 部分即可
    if image_b64.startswith("data:") and "," in image_b64:
        image_b64 = image_b64.split(",", 1)[1]

    # 有图片时允许 instruction 为空；纯文本时必须有 instruction
    if not instruction and not image_b64:
        return jsonify({"success": False, "error": "指令与图片不能同时为空"}), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({"success": False, "error": "SolidWorks Executor 尚未初始化"}), 503
    if mode == "multi_turn" and dry_run:
        return jsonify({
            "success": False,
            "error": "Multi-turn MVP 不支持 dry_run",
        }), 400

    llm_client = None
    try:
        with _sw_execution_lock:
            if mode == "multi_turn":
                llm_client = _get_llm_client(
                    api_key,
                    base_url,
                    vision_model,
                    plan_model,
                    use_cache=False,
                )
                try:
                    from planner.operation_optimizer import (
                        group_feature_batches,
                    )
                    from planner.task_decomposer import TaskDecomposer

                    # 全局计划先行：保留 single-turn 的整体几何推理，
                    # 再按原子特征事务执行、观察、验收和原位修复。
                    agent_goal = instruction
                    vision_ir = None
                    expected_dims = None
                    expected_volume = None
                    if image_b64:
                        ir = llm_client._extract_three_view_ir(
                            instruction,
                            image_b64,
                        )
                        vision_ir = ir.model_dump(mode="json")
                        size = ir.overall_size_mm or {}
                        expected_dims = [
                            size.get("length") or size.get("x"),
                            size.get("height") or size.get("y"),
                            size.get("depth")
                            or size.get("width")
                            or size.get("z"),
                        ]
                        if any(value in (None, 0) for value in expected_dims):
                            expected_dims = None
                        agent_goal = llm_client.compose_multiturn_three_view_goal(
                            instruction,
                            ir,
                        )
                        stage_b_prompt = llm_client._compose_stage_b_prompt(
                            instruction,
                            ir,
                        )
                        blueprint = llm_client._plan_text(
                            stage_b_prompt,
                            input_mode="three_view",
                        )
                        sample_match = _match_three_view_sample(image_b64)
                        groundtruth = (
                            sample_match.get("groundtruth")
                            if sample_match
                            else None
                        )
                        if groundtruth:
                            bbox = groundtruth.get("expected_bbox_mm", {})
                            expected_dims = [
                                bbox.get("length"),
                                bbox.get("height"),
                                bbox.get("depth"),
                            ]
                            expected_volume = groundtruth.get(
                                "expected_volume_mm3"
                            )
                    else:
                        blueprint = llm_client.plan(instruction)

                    batches = group_feature_batches(blueprint.steps)
                    deterministic_plan = TaskDecomposer().decompose(
                        agent_goal
                    )
                    if (
                        not image_b64
                        and len(deterministic_plan.milestones) == len(batches)
                        and deterministic_plan.notes
                        != "通用任务：由 Agent 增量建模，最终验证有效实体。"
                    ):
                        task_plan = deterministic_plan
                    else:
                        task_plan = TaskDecomposer().decompose_blueprint(
                            agent_goal,
                            batches,
                            expected_dims_mm=expected_dims,
                            expected_volume_mm3=expected_volume,
                        )
                    agent = MultiTurnAgent(
                        llm_client,
                        executor,
                        max_turns=max_turns,
                    )
                    result = agent.run_blueprint(
                        agent_goal,
                        task_plan,
                        batches,
                        llm_calls=llm_client.usage_snapshot()["calls"],
                    )
                    token_usage = llm_client.usage_snapshot()
                finally:
                    llm_client.close()
                result_dict = result.model_dump(mode="json")
                return jsonify({
                    "success": True,
                    "mode": "multi_turn",
                    "multi_turn": result_dict,
                    "turns": result_dict["turns"],
                    "final_observation": result_dict["final_observation"],
                    "vision_ir": vision_ir,
                    "token_usage": token_usage,
                    "summary": {
                        "total": result.total_actions,
                        "succeeded": result.successful_actions,
                        "all_ok": result.status == "completed",
                        "status": result.status,
                        "llm_calls": result.llm_calls,
                        "turn_count": len(result.turns),
                        "token_usage": token_usage,
                    },
                })

            # Single-turn 原路径：保持响应结构与默认行为不变。
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
            )
            plan = llm_client.plan(
                instruction,
                image_base64=image_b64 or None,
            )
            plan_dict = plan.model_dump()
            results = executor.execute_plan(plan, dry_run=dry_run)
            for result in results:
                result.pop("_raw_features", None)
            success_count = sum(
                1
                for result in results
                if result.get("success") or result.get("status") == "dry_run"
            )
            all_ok = success_count == len(plan.steps)
            return jsonify({
                "success": True,
                "plan": plan_dict,
                "results": results,
                "summary": {
                    "total": len(plan.steps),
                    "succeeded": success_count,
                    "all_ok": all_ok,
                },
            })

    except Exception as e:
        traceback.print_exc()
        token_usage = (
            llm_client.usage_snapshot()
            if llm_client is not None
            else None
        )
        return jsonify({
            "success": False,
            "error": str(e),
            "token_usage": token_usage,
        }), 500


# ============================================================
# 启动
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="SolidWorks LLM Web Demo")
    parser.add_argument("--port", type=int, default=5000, help="监听端口 (默认 5000)")
    parser.add_argument("--no-cache", action="store_true", help="禁用 LLM 缓存")
    parser.add_argument("--sw-url", default="http://localhost:5100", help="SolidWorks 服务地址")
    args = parser.parse_args()

    global executor
    executor = SwExecutor(base_url=args.sw_url)

    print(f"\n🌐 Web Demo 启动: http://localhost:{args.port}")
    print(f"   SolidWorks 服务: {args.sw_url}")
    print("   API Key 和模型使用 .env 默认值，也可在网页端覆盖")
    print(f"   按 Ctrl+C 停止\n")

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
