"""
app.py — Web Demo 后端

Flask 服务，为前端提供 API 接口，复用现有的 planner 模块。
启动方式: python -m web.app  (从 LLMPlanner 目录运行)
"""

from __future__ import annotations

import argparse
import sys
import os
import threading
import traceback
from datetime import datetime
from pathlib import Path

PLANNER_ROOT = Path(__file__).resolve().parents[1]
# 将 LLMPlanner 目录加入 sys.path，以便导入 planner 模块
sys.path.insert(0, str(PLANNER_ROOT))

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from load_env import ensure_test_env_loaded
from planner.agent_loop import MultiTurnAgent
from planner.llm_client import DEFAULT_MODEL, SwLLMClient
from planner.executor import SwExecutor
from planner.schemas import SwModelingPlan

ensure_test_env_loaded()

# 浏览器里选的 API 服务 → 服务端 .env 中对应的 Key / Base URL 变量；
# ttswitch（默认）与自定义接口用 OPENAI_*。
_PROVIDER_ENV = {
    "aiping": ("AIPING_API_KEY", "AIPING_BASE_URL"),
    "juheai": ("JUHEAI_API_KEY", "JUHEAI_BASE_URL"),
    "qwenlocal": ("QWENLOCAL_API_KEY", "QWENLOCAL_BASE_URL"),
}


def _provider_env(provider: str) -> tuple[str, str]:
    return _PROVIDER_ENV.get(provider, ("OPENAI_API_KEY", "OPENAI_BASE_URL"))

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
    reasoning_effort: str | None = None,
    plan_max_tokens: int = 16000,
    enable_thinking: bool | None = None,
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
            reasoning_effort=reasoning_effort,
            plan_max_tokens=plan_max_tokens,
            enable_thinking=enable_thinking,
        )

    cache_key = (
        f"{api_key}|{base_url}|{vision_model}|{plan_model}|"
        f"{reasoning_effort or ''}|{plan_max_tokens}|{enable_thinking}"
    )
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
            reasoning_effort=reasoning_effort,
            plan_max_tokens=plan_max_tokens,
            enable_thinking=enable_thinking,
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
    # Demo 只提供最小通用指令；详细 test.text_hint / ui.prompt 留在评测数据中，
    # 禁止把 operation 级答案作为生产 Web 输入回灌给模型。
    ui_only = [
        {
            "part_id": s["part_id"],
            "subdir": s["subdir"],
            "name": s.get("name", s["part_id"]),
            "label": s.get("ui", {}).get("label", s["part_id"]),
            "prompt": (
                s.get("test", {}).get("requirements")
                or (
                    "请仅根据这张三视图读取全部标注尺寸、轮廓和特征，"
                    "选择合适的通用建模策略并精确建模；"
                    "未标注的信息请明确说明假设。"
                )
            ),
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


_GEARBOX_GEAR_TOKENS = {
    "input": "input_pinion_z12",
    "intermediate_wheel": "intermediate_wheel_z24",
    "intermediate_pinion": "intermediate_pinion_z12",
    "output": "output_wheel_z36",
}


def _resolve_gearbox_component(
    observation: dict,
    role: str,
) -> str:
    """Resolve a stable UI role to the actual SolidWorks instance name."""
    token = _GEARBOX_GEAR_TOKENS.get(role)
    if token is None:
        raise ValueError(f"未知齿轮角色: {role}")
    result = observation.get("result", {})
    for component in result.get("components", []):
        name = str(component.get("name", ""))
        path = str(component.get("path", ""))
        if token.lower() in f"{name}|{path}".lower():
            return name
    raise RuntimeError(f"装配体中找不到齿轮组件: {token}")


@app.route("/api/gearbox-animation/load", methods=["POST"])
def load_gearbox_animation():
    """Open the generated gearbox and drive the selected gear via SW API."""
    data = request.get_json(force=True) or {}
    role = str(data.get("gear_role", "input")).strip()
    try:
        rpm = max(1.0, min(float(data.get("rpm", 60)), 600.0))
        turns = max(0.25, min(float(data.get("turns", 5)), 50.0))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "rpm 和 turns 必须是数字",
        }), 400
    assembly_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "eval",
        "artifacts",
        "two_stage_gearbox",
        "two_stage_gearbox.SLDASM",
    )
    try:
        if not os.path.exists(assembly_path):
            raise FileNotFoundError(
                "双级减速箱尚未生成，请先点击“Multi-turn 构建双级减速箱”"
            )
        with _sw_execution_lock:
            _call_sw(
                "/api/assembly/open",
                body={"assemblyPath": assembly_path},
            )
            observation = _call_sw(
                "/api/assembly/observation",
                method="GET",
            )
            component_name = _resolve_gearbox_component(
                observation,
                role,
            )
            _call_sw(
                "/api/assembly/unfix-component",
                body={"componentName": component_name},
            )
            duration_seconds = turns * 60.0 / rpm
            motor = _call_sw(
                "/api/assembly/motion/create-gear-motor",
                body={
                    "componentName": component_name,
                    "rpm": rpm,
                    "durationSeconds": duration_seconds,
                    "reverseDirection": bool(
                        data.get("reverse_direction", False)
                    ),
                },
            )
            _call_sw("/api/close-inactive-documents")
        return jsonify({
            "success": True,
            "message": (
                f"已选中 {component_name}，准备旋转 {turns:g} 圈"
            ),
            "selected_component": component_name,
            "gear_role": role,
            "result": motor.get("result", {}),
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route(
    "/api/assembly-examples/two-stage-gearbox/run",
    methods=["POST"],
)
def run_two_stage_gearbox():
    """Run the parameterized multi-turn gearbox demo from a clean session."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    plan_model = (
        data.get("plan_model", "").strip()
        or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    )
    vision_model = (
        data.get("vision_model", "").strip()
        or os.getenv("VISION_MODEL", plan_model)
    )
    try:
        max_turns = max(1, min(int(data.get("max_turns") or 18), 100))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "max_turns 必须是整数",
        }), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({
            "success": False,
            "error": "SolidWorks Executor 尚未初始化",
        }), 503

    from eval.eval_two_stage_gearbox_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _acceptance_specs,
        _observation,
        _task_plan,
        _user_goal,
        prepare_parts,
    )
    from planner.validators import ValidatorRegistry
    import json as _json

    api = SwApi(executor.base_url)
    try:
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            paths, build_log = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=bool(data.get("force_parts", False)),
            )
            assembly_path = ARTIFACT_DIR / "two_stage_gearbox.SLDASM"
            goal = _user_goal(paths, assembly_path)
            task_plan = _task_plan(goal, assembly_path)
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=True,
            )
            usage_before = llm_client.usage_snapshot()
            agent = MultiTurnAgent(
                llm_client,
                executor,
                max_turns=max_turns,
            )
            result = agent.run(goal, task_plan=task_plan)
            token_usage = llm_client.usage_since(usage_before)
            acceptance = ValidatorRegistry.validate(
                _acceptance_specs(assembly_path),
                result.final_observation,
            )
            cleanup = api.close_inactive()
            result_dict = result.model_dump(mode="json")
            passed = result.status == "completed" and acceptance.passed
            return jsonify({
                "success": True,
                "mode": "multi_turn",
                "example": "two_stage_gearbox",
                "multi_turn": result_dict,
                "turns": result_dict["turns"],
                "final_observation": result_dict["final_observation"],
                "task_plan": task_plan.model_dump(mode="json"),
                "acceptance": acceptance.model_dump(mode="json"),
                "build_log": build_log,
                "window_cleanup": cleanup,
                "cache_enabled": True,
                "token_usage": token_usage,
                "summary": {
                    "total": result.total_actions,
                    "succeeded": result.successful_actions,
                    "all_ok": passed,
                    "status": (
                        "completed" if passed else result.status
                    ),
                    "llm_calls": result.llm_calls,
                    "turn_count": len(result.turns),
                    "token_usage": token_usage,
                },
            })
    except Exception as exc:
        traceback.print_exc()
        try:
            api.close_all()
        except Exception:
            pass
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 500


@app.route(
    "/api/assembly-examples/single-cylinder-engine/run",
    methods=["POST"],
)
def run_single_cylinder_engine():
    """Run the multi-turn crank-slider engine assembly and validation."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    plan_model = (
        data.get("plan_model", "").strip()
        or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    )
    vision_model = (
        data.get("vision_model", "").strip()
        or os.getenv("VISION_MODEL", plan_model)
    )
    try:
        max_turns = max(1, min(int(data.get("max_turns") or 24), 100))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "max_turns 必须是整数",
        }), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({
            "success": False,
            "error": "SolidWorks Executor 尚未初始化",
        }), 503

    from eval.eval_single_cylinder_engine_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _acceptance_specs,
        _task_plan,
        _user_goal,
        prepare_parts,
        validate_kinematics,
    )
    from planner.validators import ValidatorRegistry
    import json as _json

    api = SwApi(executor.base_url)
    try:
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            paths, build_log = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=bool(data.get("force_parts", False)),
            )
            assembly_path = ARTIFACT_DIR / "single_cylinder_engine.SLDASM"
            goal = _user_goal(paths, spec, assembly_path)
            task_plan = _task_plan(goal, spec, assembly_path)
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=True,
            )
            usage_before = llm_client.usage_snapshot()
            agent = MultiTurnAgent(
                llm_client,
                executor,
                max_turns=max_turns,
            )
            result = agent.run(goal, task_plan=task_plan)
            token_usage = llm_client.usage_since(usage_before)
            static_acceptance = ValidatorRegistry.validate(
                _acceptance_specs(spec, assembly_path),
                result.final_observation,
            )
            kinematic_acceptance = (
                validate_kinematics(
                    api,
                    spec,
                    result.final_observation,
                )
                if result.status == "completed"
                and static_acceptance.passed
                else {"passed": False, "samples": []}
            )
            cleanup = api.close_inactive()
            result_dict = result.model_dump(mode="json")
            passed = (
                result.status == "completed"
                and static_acceptance.passed
                and kinematic_acceptance["passed"]
            )
            return jsonify({
                "success": True,
                "mode": "multi_turn",
                "example": "single_cylinder_engine",
                "multi_turn": result_dict,
                "turns": result_dict["turns"],
                "final_observation": result_dict["final_observation"],
                "task_plan": task_plan.model_dump(mode="json"),
                "acceptance": static_acceptance.model_dump(mode="json"),
                "kinematic_acceptance": kinematic_acceptance,
                "build_log": build_log,
                "window_cleanup": cleanup,
                "cache_enabled": True,
                "token_usage": token_usage,
                "summary": {
                    "total": result.total_actions,
                    "succeeded": result.successful_actions,
                    "all_ok": passed,
                    "status": "completed" if passed else result.status,
                    "llm_calls": result.llm_calls,
                    "turn_count": len(result.turns),
                    "token_usage": token_usage,
                },
            })
    except Exception as exc:
        traceback.print_exc()
        try:
            api.close_all()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/engine-animation/pose", methods=["POST"])
def set_engine_animation_pose():
    """Open the generated engine and set a deterministic crank angle."""
    data = request.get_json(force=True) or {}
    try:
        angle_deg = float(data.get("angle_deg", 0)) % 360
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "angle_deg 必须是数字",
        }), 400

    from eval.eval_single_cylinder_engine_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _observation,
        _pose_payload,
        _resolve_components,
    )
    import json as _json

    assembly_path = ARTIFACT_DIR / "single_cylinder_engine.SLDASM"
    api = SwApi(executor.base_url) if executor is not None else None
    try:
        if api is None:
            raise RuntimeError("SolidWorks Executor 尚未初始化")
        if not assembly_path.is_file():
            raise FileNotFoundError(
                "发动机装配体尚未生成，请先运行 Multi-turn 构建"
            )
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            api.call(
                "/api/assembly/open",
                {"assemblyPath": str(assembly_path)},
            )
            names = _resolve_components(_observation(api))
            pose = api.call(
                "/api/assembly/mechanism/crank-slider-pose",
                _pose_payload(names, spec, angle_deg),
            )["result"]
            cleanup = api.close_inactive()
        return jsonify({
            "success": True,
            "message": f"曲柄角已设置为 {angle_deg:g}°",
            "angle_deg": angle_deg,
            "slider_mm": float(pose["sliderDistance"]) * 1000,
            "result": pose,
            "window_cleanup": cleanup,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route(
    "/api/assembly-examples/adjustable-desk-lamp/run",
    methods=["POST"],
)
def run_adjustable_desk_lamp():
    """Build and machine-check the multi-turn adjustable desk lamp."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    plan_model = (
        data.get("plan_model", "").strip()
        or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    )
    vision_model = (
        data.get("vision_model", "").strip()
        or os.getenv("VISION_MODEL", plan_model)
    )
    try:
        max_turns = max(1, min(int(data.get("max_turns") or 28), 100))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "max_turns 必须是整数",
        }), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({
            "success": False,
            "error": "SolidWorks Executor 尚未初始化",
        }), 503

    from eval.eval_adjustable_desk_lamp_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _acceptance_specs,
        _task_plan,
        _user_goal,
        prepare_parts,
        score_result,
        validate_kinematics,
    )
    from planner.validators import ValidatorRegistry
    import json as _json

    api = SwApi(executor.base_url)
    try:
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            paths, build_log = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=bool(data.get("force_parts", False)),
            )
            assembly_path = ARTIFACT_DIR / "adjustable_desk_lamp.SLDASM"
            goal = _user_goal(paths, spec, assembly_path)
            task_plan = _task_plan(goal, spec, assembly_path)
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=True,
            )
            usage_before = llm_client.usage_snapshot()
            agent = MultiTurnAgent(
                llm_client,
                executor,
                max_turns=max_turns,
            )
            result = agent.run(goal, task_plan=task_plan)
            token_usage = llm_client.usage_since(usage_before)
            static_acceptance = ValidatorRegistry.validate(
                _acceptance_specs(spec, assembly_path),
                result.final_observation,
            )
            kinematic_acceptance = (
                validate_kinematics(
                    api,
                    spec,
                    result.final_observation,
                )
                if result.status == "completed"
                and static_acceptance.passed
                else {"passed": False, "samples": []}
            )
            passed = (
                result.status == "completed"
                and static_acceptance.passed
                and kinematic_acceptance["passed"]
            )
            score = score_result(
                status=result.status,
                static_passed=static_acceptance.passed,
                kinematics=kinematic_acceptance,
                succeeded_actions=result.successful_actions,
                total_actions=result.total_actions,
            )
            cleanup = api.close_inactive()
            result_dict = result.model_dump(mode="json")
            return jsonify({
                "success": True,
                "mode": "multi_turn",
                "example": "adjustable_desk_lamp",
                "multi_turn": result_dict,
                "turns": result_dict["turns"],
                "final_observation": result_dict["final_observation"],
                "task_plan": task_plan.model_dump(mode="json"),
                "acceptance": static_acceptance.model_dump(mode="json"),
                "kinematic_acceptance": kinematic_acceptance,
                "score": score,
                "build_log": build_log,
                "window_cleanup": cleanup,
                "cache_enabled": True,
                "token_usage": token_usage,
                "summary": {
                    "total": result.total_actions,
                    "succeeded": result.successful_actions,
                    "all_ok": passed,
                    "score": score,
                    "status": "completed" if passed else result.status,
                    "llm_calls": result.llm_calls,
                    "turn_count": len(result.turns),
                    "token_usage": token_usage,
                },
            })
    except Exception as exc:
        traceback.print_exc()
        try:
            api.close_all()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/lamp-animation/pose", methods=["POST"])
def set_lamp_animation_pose():
    """Open the generated lamp and set a deterministic arm-height pose."""
    data = request.get_json(force=True) or {}
    try:
        control_percent = max(
            0.0,
            min(float(data.get("control_percent", 50)), 100.0),
        )
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "control_percent 必须是数字",
        }), 400

    from eval.eval_adjustable_desk_lamp_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _angles_for_control,
        _observation,
        _pose_payload,
        _resolve_components,
    )
    import json as _json

    assembly_path = ARTIFACT_DIR / "adjustable_desk_lamp.SLDASM"
    api = SwApi(executor.base_url) if executor is not None else None
    try:
        if api is None:
            raise RuntimeError("SolidWorks Executor 尚未初始化")
        if not assembly_path.is_file():
            raise FileNotFoundError(
                "台灯装配体尚未生成，请先运行 Multi-turn 构建"
            )
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            api.call(
                "/api/assembly/open",
                {"assemblyPath": str(assembly_path)},
            )
            names = _resolve_components(_observation(api))
            lower_angle, upper_relative = _angles_for_control(
                spec,
                control_percent,
            )
            pose = api.call(
                "/api/assembly/mechanism/planar-chain-pose",
                _pose_payload(
                    names,
                    spec,
                    lower_angle,
                    upper_relative,
                ),
            )["result"]
            cleanup = api.close_inactive()
        return jsonify({
            "success": True,
            "message": f"台灯高度已设置为 {control_percent:g}%",
            "control_percent": control_percent,
            "lower_angle_deg": lower_angle,
            "upper_relative_angle_deg": upper_relative,
            "end_xyz_mm": [
                float(value) * 1000
                for value in pose["endXYZ"]
            ],
            "result": pose,
            "window_cleanup": cleanup,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route(
    "/api/assembly-examples/radial-folding-umbrella/run",
    methods=["POST"],
)
def run_radial_folding_umbrella():
    """Build and machine-check the multi-turn radial umbrella."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    plan_model = (
        data.get("plan_model", "").strip()
        or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    )
    vision_model = (
        data.get("vision_model", "").strip()
        or os.getenv("VISION_MODEL", plan_model)
    )
    try:
        max_turns = max(1, min(int(data.get("max_turns") or 30), 100))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "max_turns 必须是整数",
        }), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({
            "success": False,
            "error": "SolidWorks Executor 尚未初始化",
        }), 503

    from eval.eval_radial_folding_umbrella_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _acceptance_specs,
        _task_plan,
        _user_goal,
        prepare_parts,
        score_result,
        validate_radial_kinematics,
    )
    from planner.validators import ValidatorRegistry
    import json as _json

    api = SwApi(executor.base_url)
    try:
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            paths, build_log = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=bool(data.get("force_parts", False)),
            )
            assembly_path = (
                ARTIFACT_DIR / "radial_folding_umbrella.SLDASM"
            )
            goal = _user_goal(paths, spec, assembly_path)
            task_plan = _task_plan(goal, spec, assembly_path)
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=True,
            )
            usage_before = llm_client.usage_snapshot()
            agent = MultiTurnAgent(
                llm_client,
                executor,
                max_turns=max_turns,
            )
            result = agent.run(goal, task_plan=task_plan)
            token_usage = llm_client.usage_since(usage_before)
            static_acceptance = ValidatorRegistry.validate(
                _acceptance_specs(spec, assembly_path),
                result.final_observation,
            )
            radial_acceptance = (
                validate_radial_kinematics(
                    api,
                    spec,
                    result.final_observation,
                )
                if result.status == "completed"
                and static_acceptance.passed
                else {"passed": False, "samples": []}
            )
            passed = (
                result.status == "completed"
                and static_acceptance.passed
                and radial_acceptance["passed"]
            )
            score = score_result(
                status=result.status,
                static_passed=static_acceptance.passed,
                kinematics=radial_acceptance,
                successful_actions=result.successful_actions,
                total_actions=result.total_actions,
            )
            cleanup = api.close_inactive()
            result_dict = result.model_dump(mode="json")
            return jsonify({
                "success": True,
                "mode": "multi_turn",
                "example": "radial_folding_umbrella",
                "multi_turn": result_dict,
                "turns": result_dict["turns"],
                "final_observation": result_dict["final_observation"],
                "task_plan": task_plan.model_dump(mode="json"),
                "acceptance": static_acceptance.model_dump(mode="json"),
                "radial_acceptance": radial_acceptance,
                "score": score,
                "build_log": build_log,
                "window_cleanup": cleanup,
                "cache_enabled": True,
                "token_usage": token_usage,
                "summary": {
                    "total": result.total_actions,
                    "succeeded": result.successful_actions,
                    "all_ok": passed,
                    "score": score,
                    "status": "completed" if passed else result.status,
                    "llm_calls": result.llm_calls,
                    "turn_count": len(result.turns),
                    "token_usage": token_usage,
                },
            })
    except Exception as exc:
        traceback.print_exc()
        try:
            api.close_all()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/umbrella-animation/pose", methods=["POST"])
def set_umbrella_animation_pose():
    """Open the generated umbrella and set deterministic openness."""
    data = request.get_json(force=True) or {}
    try:
        openness_percent = max(
            0.0,
            min(float(data.get("openness_percent", 100)), 100.0),
        )
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "openness_percent 必须是数字",
        }), 400

    from eval.eval_radial_folding_umbrella_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _observation,
        _pose_for_openness,
        _pose_payload,
        _resolve_components,
    )
    import json as _json

    assembly_path = ARTIFACT_DIR / "radial_folding_umbrella.SLDASM"
    api = SwApi(executor.base_url) if executor is not None else None
    try:
        if api is None:
            raise RuntimeError("SolidWorks Executor 尚未初始化")
        if not assembly_path.is_file():
            raise FileNotFoundError(
                "折叠伞装配体尚未生成，请先运行 Multi-turn 构建"
            )
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            api.call(
                "/api/assembly/open",
                {"assemblyPath": str(assembly_path)},
            )
            names = _resolve_components(_observation(api))
            rib_down_angle_deg = _pose_for_openness(
                spec,
                openness_percent,
            )
            pose = api.call(
                "/api/assembly/mechanism/radial-deployable-pose",
                _pose_payload(names, spec, rib_down_angle_deg),
            )["result"]
            cleanup = api.close_inactive()
        return jsonify({
            "success": True,
            "message": f"折叠伞开合度已设置为 {openness_percent:g}%",
            "openness_percent": openness_percent,
            "rib_down_angle_deg": rib_down_angle_deg,
            "runner_drop_mm": float(pose["runnerDrop"]) * 1000,
            "result": pose,
            "window_cleanup": cleanup,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route(
    "/api/assembly-examples/cordless-drill/run",
    methods=["POST"],
)
def run_cordless_drill():
    """Build and machine-check the multi-turn cordless drill."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    plan_model = (
        data.get("plan_model", "").strip()
        or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    )
    vision_model = (
        data.get("vision_model", "").strip()
        or os.getenv("VISION_MODEL", plan_model)
    )
    try:
        max_turns = max(1, min(int(data.get("max_turns") or 34), 100))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "max_turns 必须是整数",
        }), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({
            "success": False,
            "error": "SolidWorks Executor 尚未初始化",
        }), 503

    from eval.eval_cordless_drill_planetary_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _acceptance_specs,
        _task_plan,
        _user_goal,
        prepare_parts,
        score_result,
        validate_mechanisms,
    )
    from planner.validators import ValidatorRegistry
    import json as _json

    api = SwApi(executor.base_url)
    try:
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            paths, build_log = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=bool(data.get("force_parts", False)),
            )
            assembly_path = ARTIFACT_DIR / "cordless_drill.SLDASM"
            goal = _user_goal(paths, spec, assembly_path)
            task_plan = _task_plan(goal, spec, assembly_path)
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=True,
            )
            usage_before = llm_client.usage_snapshot()
            agent = MultiTurnAgent(
                llm_client,
                executor,
                max_turns=max_turns,
            )
            result = agent.run(goal, task_plan=task_plan)
            token_usage = llm_client.usage_since(usage_before)
            static_acceptance = ValidatorRegistry.validate(
                _acceptance_specs(spec, assembly_path),
                result.final_observation,
            )
            mechanism_acceptance = (
                validate_mechanisms(
                    api,
                    spec,
                    result.final_observation,
                )
                if result.status == "completed"
                and static_acceptance.passed
                else {
                    "passed": False,
                    "planetary_samples": [],
                    "chuck_samples": [],
                }
            )
            passed = (
                result.status == "completed"
                and static_acceptance.passed
                and mechanism_acceptance["passed"]
            )
            score = score_result(
                status=result.status,
                static_passed=static_acceptance.passed,
                mechanisms=mechanism_acceptance,
                successful_actions=result.successful_actions,
                total_actions=result.total_actions,
            )
            cleanup = api.close_inactive()
            result_dict = result.model_dump(mode="json")
            return jsonify({
                "success": True,
                "mode": "multi_turn",
                "example": "cordless_drill_planetary",
                "multi_turn": result_dict,
                "turns": result_dict["turns"],
                "final_observation": result_dict["final_observation"],
                "task_plan": task_plan.model_dump(mode="json"),
                "acceptance": static_acceptance.model_dump(mode="json"),
                "mechanism_acceptance": mechanism_acceptance,
                "score": score,
                "build_log": build_log,
                "window_cleanup": cleanup,
                "cache_enabled": True,
                "token_usage": token_usage,
                "summary": {
                    "total": result.total_actions,
                    "succeeded": result.successful_actions,
                    "all_ok": passed,
                    "score": score,
                    "status": "completed" if passed else result.status,
                    "llm_calls": result.llm_calls,
                    "turn_count": len(result.turns),
                    "token_usage": token_usage,
                },
            })
    except Exception as exc:
        traceback.print_exc()
        try:
            api.close_all()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/drill-animation/pose", methods=["POST"])
def set_drill_animation_pose():
    """Set deterministic planetary rotation and chuck opening."""
    data = request.get_json(force=True) or {}
    try:
        motor_angle_deg = float(data.get("motor_angle_deg", 0))
        opening_percent = max(
            0.0,
            min(float(data.get("opening_percent", 50)), 100.0),
        )
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "电机角度和夹头开度必须是数字",
        }), 400

    from eval.eval_cordless_drill_planetary_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _chuck_payload,
        _jaw_radius_for_control,
        _observation,
        _planetary_payload,
        _resolve_components,
    )
    import json as _json

    assembly_path = ARTIFACT_DIR / "cordless_drill.SLDASM"
    api = SwApi(executor.base_url) if executor is not None else None
    try:
        if api is None:
            raise RuntimeError("SolidWorks Executor 尚未初始化")
        if not assembly_path.is_file():
            raise FileNotFoundError(
                "手电钻装配体尚未生成，请先运行 Multi-turn 构建"
            )
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            api.call(
                "/api/assembly/open",
                {"assemblyPath": str(assembly_path)},
            )
            names = _resolve_components(_observation(api))
            planetary = api.call(
                "/api/assembly/mechanism/planetary-train-pose",
                _planetary_payload(names, spec, motor_angle_deg),
            )["result"]
            jaw_radius = _jaw_radius_for_control(
                spec,
                opening_percent,
            )
            chuck = api.call(
                "/api/assembly/mechanism/radial-sliders-pose",
                _chuck_payload(
                    names,
                    spec,
                    jaw_radius,
                    phase_offset_deg=float(
                        planetary["outputAngleDeg"]
                    ),
                ),
            )["result"]
            cleanup = api.close_inactive()
        return jsonify({
            "success": True,
            "message": (
                f"电机已转到 {motor_angle_deg:g}°，"
                f"夹头开度 {opening_percent:g}%"
            ),
            "motor_angle_deg": motor_angle_deg,
            "output_angle_deg": planetary["outputAngleDeg"],
            "opening_percent": opening_percent,
            "jaw_center_radius_mm": jaw_radius,
            "inner_radius_mm": (
                sum(
                    float(item["innerTipRadius"])
                    for item in chuck["sliders"]
                )
                / len(chuck["sliders"])
                * 1000
            ),
            "planetary": planetary,
            "chuck": chuck,
            "window_cleanup": cleanup,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route(
    "/api/assembly-examples/household-sewing-machine/run",
    methods=["POST"],
)
def run_household_sewing_machine():
    """Build and machine-check the multi-turn household sewing machine."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    plan_model = (
        data.get("plan_model", "").strip()
        or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    )
    vision_model = (
        data.get("vision_model", "").strip()
        or os.getenv("VISION_MODEL", plan_model)
    )
    try:
        max_turns = max(1, min(int(data.get("max_turns") or 42), 100))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "max_turns 必须是整数",
        }), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({
            "success": False,
            "error": "SolidWorks Executor 尚未初始化",
        }), 503

    from eval.eval_household_sewing_machine_multiturn import (
        ARTIFACT_DIR,
        ASSEMBLY_NAME,
        SPEC_PATH,
        SwApi,
        _acceptance_specs,
        _observation,
        _task_plan,
        _user_goal,
        prepare_parts,
        score_result,
        validate_mechanisms,
    )
    from planner.validators import ValidatorRegistry
    import json as _json

    api = SwApi(executor.base_url)
    try:
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            paths, build_log = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=bool(data.get("force_parts", False)),
            )
            assembly_path = ARTIFACT_DIR / ASSEMBLY_NAME
            goal = _user_goal(paths, spec, assembly_path)
            task_plan = _task_plan(goal, spec, assembly_path)
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=True,
            )
            usage_before = llm_client.usage_snapshot()
            agent = MultiTurnAgent(
                llm_client,
                executor,
                max_turns=max_turns,
            )
            result = agent.run(goal, task_plan=task_plan)
            token_usage = llm_client.usage_since(usage_before)
            if result.status == "completed":
                api.call("/api/assembly/save")
                validation_observation = _observation(api)
            else:
                validation_observation = result.final_observation
            static_acceptance = ValidatorRegistry.validate(
                _acceptance_specs(spec, assembly_path),
                validation_observation,
            )
            mechanism_acceptance = (
                validate_mechanisms(
                    api,
                    spec,
                    validation_observation,
                )
                if result.status == "completed"
                and static_acceptance.passed
                else {
                    "passed": False,
                    "needle": {"samples": []},
                    "rotary": {"samples": []},
                    "four_bar": {"samples": []},
                    "cam": {"samples": []},
                }
            )
            passed = (
                result.status == "completed"
                and static_acceptance.passed
                and mechanism_acceptance["passed"]
            )
            score = score_result(
                status=result.status,
                static_passed=static_acceptance.passed,
                mechanisms=mechanism_acceptance,
                successful_actions=result.successful_actions,
                total_actions=result.total_actions,
            )
            cleanup = api.close_inactive()
            result_dict = result.model_dump(mode="json")
            return jsonify({
                "success": True,
                "mode": "multi_turn",
                "example": "household_sewing_machine",
                "multi_turn": result_dict,
                "turns": result_dict["turns"],
                "final_observation": result_dict["final_observation"],
                "task_plan": task_plan.model_dump(mode="json"),
                "acceptance": static_acceptance.model_dump(mode="json"),
                "mechanism_acceptance": mechanism_acceptance,
                "score": score,
                "build_log": build_log,
                "window_cleanup": cleanup,
                "cache_enabled": True,
                "token_usage": token_usage,
                "summary": {
                    "total": result.total_actions,
                    "succeeded": result.successful_actions,
                    "all_ok": passed,
                    "score": score,
                    "status": "completed" if passed else result.status,
                    "llm_calls": result.llm_calls,
                    "turn_count": len(result.turns),
                    "token_usage": token_usage,
                },
            })
    except Exception as exc:
        traceback.print_exc()
        try:
            api.close_all()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/sewing-machine-animation/pose", methods=["POST"])
def set_sewing_machine_animation_pose():
    """Set all four sewing mechanisms to a deterministic shaft angle."""
    data = request.get_json(force=True) or {}
    try:
        angle_deg = float(data.get("main_shaft_angle_deg", 0)) % 720.0
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "主轴角必须是数字",
        }), 400

    from eval.eval_household_sewing_machine_multiturn import (
        ARTIFACT_DIR,
        ASSEMBLY_NAME,
        SPEC_PATH,
        SwApi,
        _cam_follower_payload,
        _crank_slider_payload,
        _four_bar_geometry_mm,
        _four_bar_payload,
        _needle_distance_mm,
        _observation,
        _resolve_components,
        _synchronized_rotary_payload,
    )
    import json as _json

    assembly_path = ARTIFACT_DIR / ASSEMBLY_NAME
    api = SwApi(executor.base_url) if executor is not None else None
    try:
        if api is None:
            raise RuntimeError("SolidWorks Executor 尚未初始化")
        if not assembly_path.is_file():
            raise FileNotFoundError(
                "缝纫机装配体尚未生成，请先运行 Multi-turn 构建"
            )
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            api.call(
                "/api/assembly/open",
                {"assemblyPath": str(assembly_path)},
            )
            names = _resolve_components(_observation(api))
            needle = api.call(
                "/api/assembly/mechanism/crank-slider-pose",
                _crank_slider_payload(names, spec, angle_deg),
            )["result"]
            rotary = api.call(
                "/api/assembly/mechanism/synchronized-rotary-pose",
                _synchronized_rotary_payload(names, spec, angle_deg),
            )["result"]
            four_bar = api.call(
                "/api/assembly/mechanism/four-bar-pose",
                _four_bar_payload(names, spec, angle_deg % 360.0),
            )["result"]
            cam = api.call(
                "/api/assembly/mechanism/cam-follower-pose",
                _cam_follower_payload(names, spec, angle_deg),
            )["result"]
            api.call("/api/assembly/save")
            cleanup = api.close_inactive()
        feed = _four_bar_geometry_mm(spec, angle_deg % 360.0)[
            "coupler_point"
        ]
        return jsonify({
            "success": True,
            "message": f"缝纫机主轴已转到 {angle_deg:g}°",
            "main_shaft_angle_deg": angle_deg,
            "needle_position_mm": float(
                needle.get(
                    "sliderDistance",
                    _needle_distance_mm(spec, angle_deg) / 1000,
                )
            ) * 1000,
            "feed_x_mm": feed[0],
            "feed_y_mm": feed[1],
            "presser_lift_mm": float(
                cam.get(
                    "followerLift",
                    cam.get("lift", 0),
                )
            ) * 1000,
            "needle": needle,
            "rotary": rotary,
            "four_bar": four_bar,
            "cam": cam,
            "window_cleanup": cleanup,
        })
    except Exception as exc:
        traceback.print_exc()
        try:
            if api is not None:
                api.close_all()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route(
    "/api/assembly-examples/film-projector/run",
    methods=["POST"],
)
def run_film_projector():
    """Run the multi-turn vintage film projector assembly challenge."""
    data = request.get_json(force=True) or {}
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
    api_key = data.get("api_key", "").strip() or os.getenv(key_env, "")
    base_url = data.get("base_url", "").strip() or os.getenv(url_env, "")
    plan_model = (
        data.get("plan_model", "").strip()
        or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    )
    vision_model = (
        data.get("vision_model", "").strip()
        or os.getenv("VISION_MODEL", plan_model)
    )
    try:
        max_turns = max(1, min(int(data.get("max_turns") or 28), 100))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "max_turns 必须是整数",
        }), 400
    if not api_key:
        return jsonify({"success": False, "error": "请填写 API Key"}), 400
    if executor is None:
        return jsonify({
            "success": False,
            "error": "SolidWorks Executor 尚未初始化",
        }), 503

    from eval.eval_film_projector_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _acceptance_specs,
        _task_plan,
        _user_goal,
        prepare_parts,
        validate_indexing,
    )
    from planner.validators import ValidatorRegistry
    import json as _json

    api = SwApi(executor.base_url)
    try:
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            paths, build_log = prepare_parts(
                api,
                spec,
                ARTIFACT_DIR,
                force=bool(data.get("force_parts", False)),
            )
            assembly_path = ARTIFACT_DIR / "vintage_film_projector.SLDASM"
            goal = _user_goal(paths, spec, assembly_path)
            task_plan = _task_plan(goal, spec, assembly_path)
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=True,
            )
            usage_before = llm_client.usage_snapshot()
            agent = MultiTurnAgent(
                llm_client,
                executor,
                max_turns=max_turns,
            )
            result = agent.run(goal, task_plan=task_plan)
            token_usage = llm_client.usage_since(usage_before)
            static_acceptance = ValidatorRegistry.validate(
                _acceptance_specs(spec, assembly_path),
                result.final_observation,
            )
            indexing_acceptance = (
                validate_indexing(
                    api,
                    spec,
                    result.final_observation,
                )
                if result.status == "completed"
                and static_acceptance.passed
                else {"passed": False, "samples": []}
            )
            cleanup = api.close_inactive()
            result_dict = result.model_dump(mode="json")
            passed = (
                result.status == "completed"
                and static_acceptance.passed
                and indexing_acceptance["passed"]
            )
            return jsonify({
                "success": True,
                "mode": "multi_turn",
                "example": "film_projector",
                "multi_turn": result_dict,
                "turns": result_dict["turns"],
                "final_observation": result_dict["final_observation"],
                "task_plan": task_plan.model_dump(mode="json"),
                "acceptance": static_acceptance.model_dump(mode="json"),
                "indexing_acceptance": indexing_acceptance,
                "build_log": build_log,
                "window_cleanup": cleanup,
                "cache_enabled": True,
                "token_usage": token_usage,
                "summary": {
                    "total": result.total_actions,
                    "succeeded": result.successful_actions,
                    "all_ok": passed,
                    "status": "completed" if passed else result.status,
                    "llm_calls": result.llm_calls,
                    "turn_count": len(result.turns),
                    "token_usage": token_usage,
                },
            })
    except Exception as exc:
        traceback.print_exc()
        try:
            api.close_all()
        except Exception:
            pass
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route("/api/projector-animation/pose", methods=["POST"])
def set_projector_animation_pose():
    """Set one deterministic input angle of the film indexer."""
    data = request.get_json(force=True) or {}
    try:
        input_angle_deg = max(
            0.0,
            min(float(data.get("input_angle_deg", 0)), 1440.0),
        )
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "error": "input_angle_deg 必须是数字",
        }), 400

    from eval.eval_film_projector_multiturn import (
        ARTIFACT_DIR,
        SPEC_PATH,
        SwApi,
        _observation,
        _pose_payload,
        _resolve_components,
    )
    import json as _json

    assembly_path = ARTIFACT_DIR / "vintage_film_projector.SLDASM"
    api = SwApi(executor.base_url) if executor is not None else None
    try:
        if api is None:
            raise RuntimeError("SolidWorks Executor 尚未初始化")
        if not assembly_path.is_file():
            raise FileNotFoundError(
                "电影放映机尚未生成，请先运行 Multi-turn 构建"
            )
        with _sw_execution_lock:
            spec = _json.loads(SPEC_PATH.read_text(encoding="utf-8"))
            api.call(
                "/api/assembly/open",
                {"assemblyPath": str(assembly_path)},
            )
            names = _resolve_components(_observation(api))
            pose = api.call(
                "/api/assembly/mechanism/indexed-rotary-pose",
                _pose_payload(names, spec, input_angle_deg),
            )["result"]
            cleanup = api.close_inactive()
        return jsonify({
            "success": True,
            "message": f"输入拨盘已设置为 {input_angle_deg:g}°",
            "input_angle_deg": input_angle_deg,
            "output_angle_deg": pose["outputAngleDeg"],
            "engaged": pose["engaged"],
            "dwell": pose["dwell"],
            "result": pose,
            "window_cleanup": cleanup,
        })
    except Exception as exc:
        traceback.print_exc()
        return jsonify({"success": False, "error": str(exc)}), 500


WORKFLOW_QUERY_TOOLS = {"get_model_state", "set_parameters", "render_views", "view_model", "finish"}


def _image_mime(image_b64: str, declared: str = "") -> str:
    if declared:
        return declared
    if image_b64.startswith("iVBORw0KGgo"):
        return "image/png"
    if image_b64.startswith("R0lGOD"):
        return "image/gif"
    if image_b64.startswith("UklGR"):
        return "image/webp"
    return "image/jpeg"


REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh")
# action_level → workflow 版本；L1 保持原样，L1_v2 是在 L1 失败轨迹上改进后的版本
WORKFLOW_VERSIONS = {"L1": "v1", "L1_v2": "v2"}


def _run_workflow_l1(
    instruction: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    max_llm_calls: int,
    capture_trace: bool,
    image_b64: str = "",
    image_mime: str = "image/png",
    views_tool: bool = True,
    reasoning_effort: str | None = None,
    checkpoint_path: str = "",
    resume: bool = False,
    version: str = "v1",
    view_tool: bool = True,
    compact_context: bool = False,
) -> dict:
    """L1：工程师级工具 + 闭环 Agent。results 只列建模工具调用，供打分统计失败次数。

    带图纸时（看图建模）图纸直接交给规划模型，所以 model 必须支持图片输入；
    views_tool 打开时 Agent 额外获得 render_views，可以把当前零件投影成三视图与图纸对照。
    view_tool：L1_v2 默认提供 view_model（SolidWorks 截图），纯文本模型要关掉。
    """
    import uuid

    from openai import OpenAI

    from planner.workflow.agent import WorkflowAgent
    from planner.workflow.tools_v2 import create_session

    extra_tools = ["render_views"] if image_b64 and views_tool else None
    # TT Switch 只有在同一 X-Session-Id 下才复用 prompt 缓存；显式缓存模型不带此头时每轮都按缓存写计费。
    # 每次运行一个新值，不跨任务共享上下文。其他 OpenAI 兼容服务会忽略这个头。
    # 截断重试会把输出上限放到 32000，推理模型生成这么长需要数分钟
    client = OpenAI(
        api_key=api_key,
        base_url=base_url or None,
        timeout=600,
        max_retries=2,
        default_headers={"X-Session-Id": f"workflow-l1-{uuid.uuid4().hex}"},
    )
    session = create_session(version, base_url=executor.base_url, executor=executor, extra_tools=extra_tools)
    session.start_part()
    label = Path(checkpoint_path).name.removesuffix(".checkpoint.json") if checkpoint_path else "web"

    def log(line: str) -> None:
        print(f"[{datetime.now():%H:%M:%S}] [{label}] {line}", flush=True)

    agent = WorkflowAgent(
        client, model, session, max_llm_calls=max_llm_calls, extra_tools=extra_tools,
        reasoning_effort=reasoning_effort, stream=True, checkpoint_path=checkpoint_path or None, version=version,
        view_tool=view_tool, compact_context=compact_context, log=log,
    )
    outcome = agent.run(instruction, image_b64=image_b64 or None, image_mime=image_mime, resume=resume)
    modeling_calls = [item for item in outcome["tool_calls"] if item["tool"] not in WORKFLOW_QUERY_TOOLS]
    results = [
        {
            "step": index,
            "operation": item["tool"],
            "success": item["ok"],
            "message": item["message"] or ("OK" if item["ok"] else ""),
            "error_code": item["error_code"],
        }
        for index, item in enumerate(modeling_calls, 1)
    ]
    token_usage = outcome["token_usage"]
    payload = {
        "success": True,
        "mode": "workflow",
        "action_level": "L1" if version == "v1" else "L1_v2",
        "plan": {
            "user_intent": instruction,
            "steps": [{"operation": item["tool"], "params": item["args"]} for item in modeling_calls],
            "notes": outcome["summary"],
        },
        "results": results,
        "workflow": {key: value for key, value in outcome.items() if key != "messages"},
        "token_usage": token_usage,
        "summary": {
            "total": len(results),
            "succeeded": sum(item["success"] for item in results),
            "all_ok": outcome["status"] == "finished",
            "status": outcome["status"],
            "llm_calls": outcome["llm_calls"],
            "tool_calls": len(outcome["tool_calls"]),
            "token_usage": token_usage,
        },
    }
    if capture_trace:
        payload["raw_llm_trace"] = outcome["messages"]
    return payload


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
      "model": "gemini-3.5-flash", (旧协议兼容，Vision + Plan 共用此模型)
      "mode": "single_turn",       (可选: single_turn / multi_turn)
      "action_level": "L0",        (可选: L0 原子操作 / L1 工程师级工具 + 闭环 Agent；L1 时 max_turns 为 LLM 调用上限)
      "max_turns": 8,              (multi_turn 可选)
      "use_cache": true,           (可选；评测冷启动可设 false)
      "capture_trace": false,      (可选；返回原始 LLM 请求/响应)
      "image": "<base64>",         (可选；L1 时直接交给 plan_model，须为多模态模型)
      "views_tool": true,          (可选；L1 带图时是否提供 render_views)
      "view_tool": true,           (可选；L1_v2 是否提供 view_model 截图工具，纯文本模型设 false)
      "compact_context": false,    (可选；L1_v2 紧凑上下文优化，默认关闭以保持历史评测行为)
      "dry_run": false
    }
    """
    data = request.get_json(force=True)
    instruction = data.get("instruction", "").strip()
    provider = data.get("provider", "ttswitch").strip()
    key_env, url_env = _provider_env(provider)
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
        vision_model = legacy_model or os.getenv("VISION_MODEL", DEFAULT_MODEL)
    if not plan_model:
        plan_model = legacy_model or os.getenv("PLAN_MODEL", DEFAULT_MODEL)
    mode = data.get("mode", "single_turn").strip().lower()
    if mode not in ("single_turn", "multi_turn"):
        return jsonify({"success": False, "error": f"未知 mode: {mode}"}), 400
    action_level = str(data.get("action_level", "L0")).strip().upper()
    if action_level == "L1_V2":
        action_level = "L1_v2"
    if action_level not in ("L0", *WORKFLOW_VERSIONS):
        return jsonify({"success": False, "error": f"未知 action_level: {action_level}"}), 400
    raw_max_turns = data.get("max_turns")
    max_turns = None
    if raw_max_turns not in (None, ""):
        try:
            max_turns = max(1, min(int(raw_max_turns), 100))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "max_turns 必须是整数"}), 400
    try:
        plan_max_tokens = max(1, min(int(data.get("max_plan_tokens") or 16000), 64000))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "max_plan_tokens 必须是整数"}), 400
    enable_thinking = data.get("enable_thinking")
    if enable_thinking is not None and not isinstance(enable_thinking, bool):
        return jsonify({"success": False, "error": "enable_thinking 必须是布尔值"}), 400
    compact_context = data.get("compact_context", False)
    if not isinstance(compact_context, bool):
        return jsonify({"success": False, "error": "compact_context 必须是布尔值"}), 400
    if compact_context and action_level != "L1_v2":
        return jsonify({"success": False, "error": "compact_context 仅支持 L1_v2"}), 400
    dry_run = data.get("dry_run", False)
    use_cache = bool(data.get("use_cache", True))
    capture_trace = bool(data.get("capture_trace", False))
    reasoning_effort = str(data.get("reasoning_effort") or "").strip().lower() or None
    if reasoning_effort is not None:
        if reasoning_effort not in REASONING_EFFORTS:
            return jsonify({
                "success": False,
                "error": f"未知 reasoning_effort: {reasoning_effort}，可选 {', '.join(REASONING_EFFORTS)}",
            }), 400
    # 多模态：可选图片（base64，可含或不含 data URI 前缀；不含时按 JPEG 处理）
    image_raw = data.get("image", "") or ""
    image_b64 = image_raw.strip()
    declared_mime = ""
    # 若前端传的是完整 data URI（data:image/png;base64,...），后端只需 base64 部分即可
    if image_b64.startswith("data:") and "," in image_b64:
        header, image_b64 = image_b64.split(",", 1)
        declared_mime = header[5:].split(";", 1)[0]

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

    if action_level in WORKFLOW_VERSIONS:
        if dry_run:
            return jsonify({"success": False, "error": "L1 workflow 不支持 dry_run"}), 400
        checkpoint_path = str(data.get("checkpoint_path") or "").strip()
        if checkpoint_path:
            resolved = Path(checkpoint_path).resolve()
            if resolved.suffix != ".json" or not resolved.is_relative_to(PLANNER_ROOT / "eval"):
                return jsonify({"success": False, "error": "checkpoint_path 必须是 LLMPlanner/eval 下的 .json 文件"}), 400
            checkpoint_path = str(resolved)
        try:
            with _sw_execution_lock:
                return jsonify(_run_workflow_l1(
                    instruction,
                    api_key=api_key,
                    base_url=base_url,
                    model=plan_model,
                    max_llm_calls=max_turns or 40,
                    capture_trace=capture_trace,
                    image_b64=image_b64,
                    image_mime=_image_mime(image_b64, declared_mime) if image_b64 else "image/png",
                    views_tool=bool(data.get("views_tool", True)),
                    reasoning_effort=reasoning_effort,
                    checkpoint_path=checkpoint_path,
                    resume=bool(data.get("resume", False)),
                    version=WORKFLOW_VERSIONS[action_level],
                    view_tool=bool(data.get("view_tool", True)),
                    compact_context=compact_context,
                ))
        except Exception as exc:
            traceback.print_exc()
            return jsonify({"success": False, "error": str(exc)}), 500

    llm_client = None
    try:
        with _sw_execution_lock:
            if mode == "multi_turn":
                llm_client = _get_llm_client(
                    api_key,
                    base_url,
                    vision_model,
                    plan_model,
                    use_cache=use_cache,
                    reasoning_effort=reasoning_effort,
                    plan_max_tokens=plan_max_tokens,
                    enable_thinking=enable_thinking,
                )
                if capture_trace:
                    llm_client.begin_trace_capture()
                usage_before = llm_client.usage_snapshot()
                from planner.operation_optimizer import (
                    group_feature_batches,
                    stabilize_three_view_blueprint,
                )
                from planner.task_decomposer import TaskDecomposer

                # 全局计划先行：稳定的 Vision IR / 初始蓝图允许命中缓存；
                # 每轮 Agent 决策另按完整 observation + ledger 做状态缓存。
                agent_goal = instruction
                vision_ir = None
                expected_dims = None
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
                    blueprint = stabilize_three_view_blueprint(
                        SwModelingPlan(
                            user_intent=agent_goal,
                            steps=[],
                            notes="",
                        ),
                        ir,
                    )
                    if not blueprint.steps:
                        blueprint = llm_client._plan_text(
                            stage_b_prompt,
                            input_mode="three_view",
                        )
                        blueprint = stabilize_three_view_blueprint(
                            blueprint,
                            ir,
                        )
                else:
                    blueprint = llm_client.plan(instruction)

                batches = group_feature_batches(blueprint.steps)
                task_plan = TaskDecomposer().decompose_blueprint(
                    agent_goal,
                    batches,
                    expected_dims_mm=expected_dims,
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
                    llm_calls=llm_client.usage_since(
                        usage_before
                    )["calls"],
                )
                token_usage = llm_client.usage_since(usage_before)
                result_dict = result.model_dump(mode="json")
                response_payload = {
                    "success": True,
                    "mode": "multi_turn",
                    "multi_turn": result_dict,
                    "turns": result_dict["turns"],
                    "final_observation": result_dict["final_observation"],
                    "vision_ir": vision_ir,
                    "blueprint": blueprint.model_dump(mode="json"),
                    "task_plan": task_plan.model_dump(mode="json"),
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
                }
                if capture_trace:
                    response_payload["raw_llm_trace"] = (
                        llm_client.end_trace_capture()
                    )
                return jsonify(response_payload)

            # Single-turn 原路径：保持响应结构与默认行为不变。
            llm_client = _get_llm_client(
                api_key,
                base_url,
                vision_model,
                plan_model,
                use_cache=use_cache,
                reasoning_effort=reasoning_effort,
                plan_max_tokens=plan_max_tokens,
                enable_thinking=enable_thinking,
            )
            if capture_trace:
                llm_client.begin_trace_capture()
            usage_before = llm_client.usage_snapshot()
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
            response_payload = {
                "success": True,
                "plan": plan_dict,
                "results": results,
                "summary": {
                    "total": len(plan.steps),
                    "succeeded": success_count,
                    "all_ok": all_ok,
                },
            }
            # Preserve the historical response exactly when mode is omitted.
            # Explicit-mode callers (Web comparison/eval) also receive
            # request-local usage without affecting legacy clients.
            if "mode" in data:
                token_usage = llm_client.usage_since(usage_before)
                response_payload["mode"] = "single_turn"
                response_payload["token_usage"] = token_usage
                response_payload["summary"]["token_usage"] = token_usage
            if capture_trace:
                response_payload["raw_llm_trace"] = (
                    llm_client.end_trace_capture()
                )
            return jsonify(response_payload)

    except Exception as e:
        traceback.print_exc()
        token_usage = (
            llm_client.usage_snapshot()
            if llm_client is not None
            else None
        )
        error_payload = {
            "success": False,
            "error": str(e),
            "token_usage": token_usage,
        }
        if capture_trace and llm_client is not None:
            error_payload["raw_llm_trace"] = llm_client.end_trace_capture()
        return jsonify(error_payload), 500


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
