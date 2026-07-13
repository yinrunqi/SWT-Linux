"""
app.py — Web Demo 后端

Flask 服务，为前端提供 API 接口，复用现有的 planner 模块。
启动方式: python -m web.app  (从 LLMPlanner 目录运行)
"""

from __future__ import annotations

import argparse
import sys
import os
import traceback

# 将 LLMPlanner 目录加入 sys.path，以便导入 planner 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from planner.llm_client import SwLLMClient
from planner.executor import SwExecutor

# ============================================================
# Flask App
# ============================================================

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# 全局实例
executor: SwExecutor | None = None

# 缓存的 LLM 客户端（按 api_key+base_url+model 缓存，避免重复创建）
_llm_cache: dict[str, SwLLMClient] = {}


def _get_llm_client(
    api_key: str,
    base_url: str,
    vision_model: str,
    plan_model: str,
) -> SwLLMClient:
    """获取或创建 LLM 客户端，相同配置复用实例。

    Vision 和 Plan 可分别配不同模型（例如 Vision 用便宜多模态 + Plan 用强规划）。
    若两者相同，SwLLMClient 会在启动日志里打印"Vision + Plan 同"提示。
    """
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


@app.route("/api/run", methods=["POST"])
def run():
    """
    一次性执行完整流程：LLM 规划 + SolidWorks 执行。

    请求体: {
      "instruction": "创建一个100mm的正方体",
      "api_key": "sk-...",
      "base_url": "https://...",   (可选)
      "vision_model": "basic/claude-sonnet-5",  (可选，Vision 阶段模型)
      "plan_model":   "basic/claude-sonnet-5",  (可选，Plan 阶段模型)
      "model": "gpt-4o",           (旧协议兼容，Vision + Plan 共用此模型)
      "dry_run": false
    }
    """
    data = request.get_json(force=True)
    instruction = data.get("instruction", "").strip()
    api_key = data.get("api_key", "").strip()
    base_url = data.get("base_url", "").strip()
    # 兼容两种协议:
    #   新协议 (推荐): vision_model + plan_model 分别指定
    #   旧协议 (兼容): 单一 model 字段, Vision 和 Plan 都用它
    vision_model = data.get("vision_model", "").strip()
    plan_model = data.get("plan_model", "").strip()
    legacy_model = data.get("model", "").strip()
    if not vision_model:
        vision_model = legacy_model or "gpt-4o"
    if not plan_model:
        plan_model = legacy_model or "gpt-4o"
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

    try:
        # 获取 LLM 客户端（Vision / Plan 可为不同模型）
        llm_client = _get_llm_client(api_key, base_url, vision_model, plan_model)

        # 1. LLM 规划（有图走多模态两阶段；无图走纯文本）
        plan = llm_client.plan(instruction, image_base64=image_b64 or None)
        plan_dict = plan.model_dump()

        # 2. 执行
        results = executor.execute_plan(plan, dry_run=dry_run)

        # 清理 _raw_features（不需要发给前端）
        for r in results:
            r.pop("_raw_features", None)

        success_count = sum(1 for r in results if r.get("success") or r.get("status") == "dry_run")
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
        return jsonify({"success": False, "error": str(e)}), 500


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
    print(f"   API Key 和模型在网页端配置")
    print(f"   按 Ctrl+C 停止\n")

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
