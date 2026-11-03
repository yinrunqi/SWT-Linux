"""SimpleCADAPI 装配基线：LLM 用 simplecadapi 建零件、再用它的装配约束搭机构（Workflow-CAD-Bench-Assembly 的 gen 条件）。

LLM 与建模全部在本机（venv-scad）完成，打分在装有 SolidWorks 的 Windows 上做（eval/assembly_scratch/score_simplecad.py）。
每个测例的产物（eval/artifacts/<输出名>/<case_id>/r<k>/）：
    parts/<part_id>/            零件智能体工作目录：step_XX.py、model.step、transcript.json、llm_responses.json
    parts/<part_id>.step        交付的零件；parts/<part_id>.json 为运行记录与零件接口 rubric 结果
    assembly/                   装配智能体工作目录：parts/（STEP、.scadbrep、manifest）、scad_parts.py、脚本、
                                assembly.scadpkg、transcript.json、llm_responses.json
    assembly_export.json        从 assembly.scadpkg 抽取的组件、求解位姿、连接器坐标系、约束（打分只读这个和零件 STEP）
    run.json                    本测例汇总（不含分数）
rubric 或打分方式改动后，只需在 Windows 上重跑 score_simplecad.py，不用重跑 LLM。

用法（在 LLMPlanner 目录）：
    OPENAI_BASE_URL=http://127.0.0.1:15721/tencent/v1 OPENAI_API_KEY=... \\
        venv-scad/bin/python -X utf8 -u eval/eval_assembly_simplecad.py --cases all --model claude-sonnet-5 \\
        --output eval/assembly_simplecad_sonnet5.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml
from openai import OpenAI

LLM_ROOT = Path(__file__).resolve().parent.parent
if str(LLM_ROOT) not in sys.path:
    sys.path.insert(0, str(LLM_ROOT))

from eval.eval_simplecadapi import (  # noqa: E402
    PREAMBLE as PART_PREAMBLE_BASE,
    SKILL_ROOT,
    TOOLS,
    ContextExceeded,
    _usage_add,
    chat,
    log,
    tool_read_doc,
    tool_run_python,
)

CASES_DIR = LLM_ROOT / "eval" / "assembly_scratch"
PART_RUBRICS = CASES_DIR / "rubrics"
OCC_PY = LLM_ROOT / "venv-occ" / "bin" / "python"
CORE_CASES = ["gearbox", "engine", "desk_lamp", "umbrella", "drill_gearbox", "sewing_machine", "projector"]


def case_names() -> list[str]:
    others = sorted(p.stem for p in CASES_DIR.glob("*.yaml") if p.stem not in CORE_CASES)
    return CORE_CASES + others


def load_case(name: str) -> dict:
    return yaml.safe_load((CASES_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def _write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def _read(rel: str) -> str:
    return (SKILL_ROOT / rel).read_text(encoding="utf-8")


def _api_names() -> str:
    return " ".join(sorted(p.stem for p in (SKILL_ROOT / "references/docs/api").glob("*.md") if p.stem != "README"))


# ---------------------------------------------------------------- 零件阶段

_PART_FRAME_OLD = "- 只交一个零件：单一封闭实体，单位 mm。位置和朝向不限，除非题面给了世界坐标。"
_PART_FRAME_NEW = """- 只交一个零件：单一封闭实体，单位 mm。
- 这个零件之后要参与装配：零件坐标系就是交付坐标系，严格按题面给的坐标、草图平面和拉伸方向建模，不要平移或居中。
  题面的平面名沿用另一个 CAD 系统：front_plane = XY 平面（法向 +Z），top_plane = XZ 平面（法向 +Y），right_plane = YZ 平面（法向 +X）。
- 题面里提到的 spur_gear、internal_gear 等是另一个系统的工具名，这里没有；齿轮请用 SimpleCADAPI 的标准件库或自己建渐开线齿形，
  齿顶要是圆柱面（按齿顶圆裁齿形）。"""
assert _PART_FRAME_OLD in PART_PREAMBLE_BASE
PART_PREAMBLE = PART_PREAMBLE_BASE.replace(_PART_FRAME_OLD, _PART_FRAME_NEW)


def part_system_prompt(spec: dict) -> str:
    text = PART_PREAMBLE.format(
        api_names=_api_names(), skill=_read("SKILL.md"),
        part_modeling=_read("references/domains/part-modeling.md"),
    )
    if spec.get("extra_tools"):
        text += "\n\n===== references/domains/standard-parts.md =====\n" + _read("references/domains/standard-parts.md")
    return text


# ---------------------------------------------------------------- 装配阶段

ASM_PREAMBLE = """你是一个 CAD 装配 agent，使用 SimpleCADAPI（Python 包 simplecadapi，已安装）把评测提供的零件装配成机构。

## 评测模式（覆盖下面 skill 中与之冲突的规定）
- 没有用户可以回答问题。不要提问、不要等待确认；题面没写清的地方，选最常见的合理理解，并在代码注释里写明假设。
- 没有子代理、没有渲染评审、不需要写 REQUIREMENTS.md / BUILD_PLAN.md 等过程文件。验证方式是在脚本里打印求解报告、
  残差、各组件位姿、连接器世界坐标等数值并自行核对。
- 题面是为另一个 CAD 系统写的，里面提到的 check_motion、joint、mate、ground 等工具名在这里不存在；
  按题意用 SimpleCADAPI 的等价做法（ground、约束、求解报告）实现即可。
- 零件已由上一阶段生成，放在工作目录 parts/ 下，只能用工作目录里的 scad_parts.py 加载，不得改动零件几何
  （不要对加载出的实体做布尔、圆角、移动等任何几何操作），只能给零件加 connector。每个零件定义这样写：

```python
import simplecadapi as scad
from scad_parts import ROLES, load_part_solid, part_inputs

@scad.part(id="<零件 id>", cache="off", inputs=part_inputs("<零件 id>"))
def build_xxx() -> scad.Part:
    part = scad.make_part_rpart("<零件 id>", load_part_solid("<零件 id>"))
    part = scad.add_connector_rpart(part, scad.make_placement_connector_rconnector(
        "bore", scad.make_placement_rplacement((0, 0, 0))))   # 原点在孔轴线上，Z 轴沿孔轴
    return part
```

- 装配体只有一层：一个 `@scad.assemble`（definitions 里列出全部零件定义的构建结果），不要嵌套子装配。
  component_id 必须等于下面"角色"表里的角色名，每个角色一个组件；同一个零件可以被多个角色复用。
- 位姿和运动都用约束表达：ground 题面里固定的件；件与件之间用 fixed / revolute / prismatic 约束连接；
  齿轮、带轮、齿条的传动关系用 gear / belt / rack_pinion 约束表达。
- 评测器会读取你的约束（连接的两个组件、两端连接器的坐标系、类型和参数）和求解后的位姿，在另一个 CAD 系统里按它们重建配合，
  然后驱动机构检查运动、干涉和传动比。所以：
  - 约束必须反映真实的机构关系：该转的用 revolute，该刚性连在一起的用 fixed，滑块用 prismatic；不要为了求解方便把运动副写成 fixed，
    也不要漏掉闭环机构里收尾的那个运动副。
  - revolute 绕连接器的 Z 轴转，两端连接器的原点要落在对应孔/轴的轴线上；prismatic 沿连接器的 Z 轴滑动。
  - 求解后的位姿就是交付位姿：零件之间不得穿模（相啮合的齿轮要按齿相位错开）。
- 交付物：工作目录下的 `assembly.scadpkg`：

```python
res = build_assembly()          # @scad.assemble 的返回值
scad.capture(res, "assembly.scadpkg", include_scene=False)
```

- 调用 finish 时评测器会检查：包能否读出、每个角色是否都有组件、零件几何是否未改动；不通过会返回原因。

## 工具
- read_doc(path)：读取 skill 文档（路径相对 skill 根目录，例如 `references/docs/api/add_gear_constraint_rassembly.md`）；传目录则列出内容。
- run_python(code)：把 code 作为完整脚本在独立 Python 进程中执行（不保留上次的变量），返回退出码和输出。工作目录跨调用保留。
- finish()：确认 assembly.scadpkg 已写好后调用，结束本题。调用次数有上限，请尽早产出可交付的包，再逐步修正。

## 可用 API 页面（references/docs/api/<名称>.md）
{api_names}

下面是 SimpleCADAPI skill 的路由文件 SKILL.md，以及装配相关文档原文。

===== SKILL.md =====
{skill}

===== references/domains/assembly-and-product.md =====
{asm_domain}

===== references/workflows/assembly-product-build.md =====
{asm_workflow}

===== references/discipline/assembly-positioning.md =====
{positioning}

===== references/docs/core/declarative_constraints.md =====
{constraints}
"""


def asm_system_prompt() -> str:
    return ASM_PREAMBLE.format(
        api_names=_api_names(), skill=_read("SKILL.md"),
        asm_domain=_read("references/domains/assembly-and-product.md"),
        asm_workflow=_read("references/workflows/assembly-product-build.md"),
        positioning=_read("references/discipline/assembly-positioning.md"),
        constraints=_read("references/docs/core/declarative_constraints.md"),
    )


SCAD_PARTS_PY = '''"""评测提供的零件加载工具，不要修改。"""
import json
from pathlib import Path

import simplecadapi as scad

_MANIFEST = json.loads((Path(__file__).parent / "parts" / "manifest.json").read_text(encoding="utf-8"))
PARTS = _MANIFEST["parts"]   # 零件 id -> {brep, sha256, step}
ROLES = _MANIFEST["roles"]   # 角色 -> 零件 id


def part_inputs(part_id: str):
    return (scad.file_input(PARTS[part_id]["brep"]),)


def load_part_solid(part_id: str) -> scad.Solid:
    """零件坐标系与上一阶段交付的 STEP 一致。"""
    item = PARTS[part_id]
    return scad.load_brep_region_rsolid(item["brep"], item["sha256"])
'''

PREP_SCRIPT = r'''
import hashlib, json, sys
from pathlib import Path
import simplecadapi as scad
from simplecadapi.inspect import brep

manifest = json.loads(Path("parts/manifest.json").read_text(encoding="utf-8"))
for pid, item in manifest["parts"].items():
    brep.copy_step_region_rpath(item["step"], item["brep"])
    item["sha256"] = hashlib.sha256(Path(item["brep"]).read_bytes()).hexdigest()

    @scad.part(id=pid, cache="off", inputs=(scad.file_input(item["brep"]),), project_root=Path.cwd())
    def plain():
        return scad.make_part_rpart(pid, scad.load_brep_region_rsolid(item["brep"], item["sha256"]))

    item["geometry_hash"] = plain().definition.interface_hashes.geometry
Path("parts/manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
print("prepared", len(manifest["parts"]))
'''

EXTRACT_SCRIPT = r'''
import json, sys
from pathlib import Path
import simplecadapi as scad

S = 1e9  # 包内长度单位：1e-9 mm 的整数；方向向量同样放大 1e9


def frame(f):
    return {k: [v / S for v in f[k]] for k in ("origin", "x_axis", "y_axis", "z_axis")}


def main(work, out):
    work = Path(work)
    manifest = json.loads((work / "parts" / "manifest.json").read_text(encoding="utf-8"))
    pkg = scad.read_product_package(work / "assembly.scadpkg")
    if pkg.root_kind != "assembly":
        raise SystemExit(f"包的根不是装配体（{pkg.root_kind}）")
    rd = pkg.root_definition
    defs = dict(rd.resolved_definitions)
    problems = []
    nested = [d for d, v in defs.items() if getattr(v, "definition_kind", "") == "assembly"]
    if nested:
        problems.append(f"不支持嵌套子装配：{nested}")
    by_hash = {item["geometry_hash"]: pid for pid, item in manifest["parts"].items()}
    geometry = {}
    for did, d in defs.items():
        ref = (manifest["parts"].get(did) or {}).get("geometry_hash")
        if ref is not None:
            source = did if d.interface_hashes.geometry == ref else None
        else:
            source = by_hash.get(d.interface_hashes.geometry)
        if ref is not None and d.interface_hashes.geometry != ref:
            problems.append(f"零件 {did} 的几何与提供的零件不一致（不得修改零件几何）")
        elif ref is None and source is None:
            problems.append(f"零件定义 {did} 不是评测提供的零件 id，几何也与任何提供的零件不一致")
        geometry[did] = {"matches_provided": source is not None, "source_part_id": source}
    snap = rd.solved_snapshot or {}
    solved = {p["instance_id"]: p["placement"] for p in snap.get("component_placements") or []}
    components = []
    for inst in rd.instances:
        placement = solved.get(inst.instance_id) or inst.placement
        components.append({"role": inst.instance_id, "part_id": inst.definition_id,
                           "source_part_id": (geometry.get(inst.definition_id) or {}).get("source_part_id"),
                           "placement": frame(placement), "solved": inst.instance_id in solved})
    connectors = {}
    for c in pkg.occurrence_graph.manifest.get("connectors") or []:
        if c.get("local_frame") is None:
            problems.append(f"连接器 {c['component_id']}.{c['connector_id']} 没有坐标系")
            continue
        connectors.setdefault(c["component_id"], {})[c["connector_id"]] = {
            "anchor_kind": c.get("anchor_kind"), "local_frame": frame(c["local_frame"])}
    constraints = []
    for r in rd.relations:
        r = dict(r)
        item = {"id": r["constraint_id"], "kind": r["constraint_kind"],
                "a": dict(r["connector_a"]), "b": dict(r["connector_b"])}
        for key in ("drive_angle_degrees", "drive_distance", "pitch_radius_a", "pitch_radius_b",
                    "pulley_radius_a", "pulley_radius_b", "pitch_radius", "phase_offset"):
            if r.get(key) is not None:
                item[key] = r[key]
        for key in ("angle_limit", "distance_limit"):
            if r.get(key) is not None:
                item[key] = r[key]
        constraints.append(item)
    roles = set(manifest["roles"])
    have = {c["role"] for c in components}
    missing = sorted(roles - have)
    extra = sorted(have - roles)
    for c in components:
        want = manifest["roles"].get(c["role"])
        source = c["source_part_id"]
        same = source is not None and manifest["parts"][source]["geometry_hash"] == manifest["parts"][want]["geometry_hash"] \
            if want is not None else True
        if not same:
            problems.append(f"角色 {c['role']} 应使用零件 {want}，实际是 {c['part_id']}")
    export = {
        "components": components,
        "grounded": list(rd.grounded_instance_ids),
        "connectors": connectors,
        "constraints": constraints,
        "constraint_report": snap.get("constraint_report"),
        "missing_roles": missing,
        "extra_components": extra,
        "geometry": geometry,
        "problems": problems,
    }
    Path(out).write_text(json.dumps(export, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"components": len(components), "constraints": len(constraints),
                      "missing_roles": missing, "extra_components": extra, "problems": problems}, ensure_ascii=False))


main(sys.argv[1], sys.argv[2])
'''


def _run_helper(script: str, work: Path, *args: str, timeout: float = 600) -> subprocess.CompletedProcess:
    """@scad.part 需要构建函数的源文件且源文件须在项目根内，所以脚本写到工作目录下的隐藏目录再运行。"""
    helper_dir = work / ".eval_helpers"
    helper_dir.mkdir(exist_ok=True)
    path = helper_dir / f"helper_{hashlib.sha256(script.encode()).hexdigest()[:12]}.py"
    if not path.exists():
        path.write_text(script, encoding="utf-8")
    env = dict(os.environ, SIMPLECAD_CACHE_MODE="off", SIMPLECAD_CACHE_DIR=str(work / ".simplecad_cache"),
               PYTHONIOENCODING="utf-8")
    return subprocess.run([sys.executable, "-X", "utf8", str(path), *args], cwd=work, env=env,
                          capture_output=True, text=True, timeout=timeout)


def _tail_lines(text: str, n: int = 15) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("\x1b", "*"))]
    return "\n".join(lines[-n:])


def extract(work: Path, out: Path) -> tuple[bool, str]:
    if not (work / "assembly.scadpkg").exists():
        return False, "assembly.scadpkg 不存在，请先 capture"
    try:
        p = _run_helper(EXTRACT_SCRIPT, work, str(work), str(out), timeout=300)
    except subprocess.TimeoutExpired:
        return False, "读取 assembly.scadpkg 超时"
    if p.returncode != 0:
        return False, "读取 assembly.scadpkg 失败：\n" + _tail_lines(p.stderr)
    data = json.loads(out.read_text(encoding="utf-8"))
    issues = list(data["problems"])
    if data["missing_roles"]:
        issues.append(f"缺少角色组件：{data['missing_roles']}")
    if data["extra_components"]:
        issues.append(f"多余组件（component_id 必须是角色名）：{data['extra_components']}")
    if not data["grounded"]:
        issues.append("没有接地的组件")
    if issues:
        return False, "交卷检查未通过：\n- " + "\n- ".join(issues)
    return True, p.stdout.strip()


# ---------------------------------------------------------------- 通用智能体循环

def run_agent(client: OpenAI, args, *, system: str, user: str, work: Path, max_calls: int,
              deliverable: str, finish_check) -> dict:
    session = uuid.uuid4().hex
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    raw_responses: list[dict] = []
    usage: dict = {}
    counts = {"read_doc": 0, "run_python": 0, "finish": 0, "finish_rejected": 0, "no_tool": 0}
    status, error, n_script, calls = "budget_exhausted", None, 0, 0
    t0 = time.time()

    def record_now() -> dict:
        rec = {"status": status, "error": error, "llm_calls": calls, "tool_counts": counts,
               "token_usage": usage, "elapsed_s": round(time.time() - t0, 1),
               "max_calls": max_calls, "reasoning_effort": args.reasoning_effort,
               "max_tokens": args.max_tokens, "temperature": args.temperature}
        _write_json(work / "transcript.json", {**rec, "messages": messages})
        _write_json(work / "llm_responses.json", raw_responses)
        return rec

    try:
        while calls < max_calls:
            if raw_responses:
                record_now()
            calls += 1
            try:
                r = chat(client, args.model, messages, session, args.temperature, args.reasoning_effort,
                         args.max_tokens)
            except ContextExceeded as exc:
                calls -= 1
                status, error = "context_exceeded", str(exc)
                break
            raw_responses.append(r.model_dump() if hasattr(r, "model_dump") else json.loads(r.json()))
            _usage_add(usage, r.usage)
            msg = r.choices[0].message
            entry = {"role": "assistant", "content": msg.content or ""}
            reasoning = getattr(msg, "reasoning_content", None)
            if reasoning:
                entry["reasoning_content"] = reasoning
            if msg.tool_calls:
                entry["tool_calls"] = [{"id": tc.id, "type": "function",
                                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                       for tc in msg.tool_calls]
            messages.append(entry)
            if not msg.tool_calls:
                counts["no_tool"] += 1
                messages.append({"role": "user", "content":
                                 f"继续：用 run_python 写出 {deliverable}，完成后调用 finish。不要提问。"})
                continue
            done = False
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    a = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    a = None
                if a is None:
                    result = "错误：参数不是合法 JSON"
                elif name == "read_doc":
                    counts["read_doc"] += 1
                    result = tool_read_doc(str(a.get("path", "")))
                elif name == "run_python":
                    counts["run_python"] += 1
                    n_script += 1
                    result = tool_run_python(str(a.get("code", "")), work, n_script, args.script_timeout, deliverable)
                elif name == "finish":
                    counts["finish"] += 1
                    ok, result = finish_check()
                    if ok:
                        result, done = "已交卷。\n" + result, True
                    else:
                        counts["finish_rejected"] += 1
                else:
                    result = f"错误：未知工具 {name}"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            if done:
                status = "finished"
                break
    except Exception as e:  # noqa: BLE001
        status, error = "error", f"{type(e).__name__}: {e}"
    return record_now()


# ---------------------------------------------------------------- 流水线

def grade_part(step: Path, part_id: str) -> dict:
    code = (
        "import json,sys\n"
        "from geomcheck.occ_part import Part\n"
        "from geomcheck.rubric import evaluate, load_rubric\n"
        "r = evaluate(load_rubric(sys.argv[2]), Part.from_step(sys.argv[1]))\n"
        "print(json.dumps(r, default=str))\n"
    )
    try:
        p = subprocess.run([str(OCC_PY), "-X", "utf8", "-c", code, str(step), str(PART_RUBRICS / f"{part_id}.yaml")],
                           cwd=LLM_ROOT, capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            return {"accepted": False, "score": 0.0, "error": _tail_lines(p.stderr, 6)}
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        return {"accepted": False, "score": 0.0, "error": f"{type(exc).__name__}: {exc}"}


def run_part(client, args, spec: dict, out: Path) -> dict:
    pid = spec["id"]
    rec_path = out / "parts" / f"{pid}.json"
    if rec_path.exists() and not args.rerun:
        return json.loads(rec_path.read_text(encoding="utf-8"))
    work = out / "parts" / pid
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    step = work / "model.step"
    record = run_agent(
        client, args, system=part_system_prompt(spec), user=spec["prompt"], work=work,
        max_calls=args.max_part_calls, deliverable="model.step",
        finish_check=lambda: (True, "") if step.exists() else (False, "错误：model.step 不存在，请先导出。"),
    )
    delivered = out / "parts" / f"{pid}.step"
    grade = None
    if step.exists():
        shutil.copyfile(step, delivered)
        grade = grade_part(delivered, pid)
    result = {
        "part_id": pid, **record,
        "step": str(delivered.relative_to(out)) if step.exists() else None,
        "accepted": bool(grade and grade.get("accepted")),
        "score": None if grade is None else grade.get("score"),
        "grade": grade,
        "trace": str((work / "transcript.json").relative_to(out)),
    }
    _write_json(rec_path, result)
    log(f"  part {out.parent.name}/{pid:<18} {record['status']:<16} calls={record['llm_calls']:<3} "
        f"accepted={result['accepted']} score={result['score']}")
    return result


def prepare_assembly(case: dict, parts: list[dict], out: Path) -> tuple[Path, dict[str, str]]:
    work = out / "assembly"
    if work.exists():
        shutil.rmtree(work)
    (work / "parts").mkdir(parents=True)
    available = {}
    for p in parts:
        if p.get("step"):
            shutil.copyfile(out / p["step"], work / "parts" / f"{p['part_id']}.step")
            available[p["part_id"]] = {"step": f"parts/{p['part_id']}.step", "brep": f"parts/{p['part_id']}.scadbrep"}
    roles = {role: pid for role, pid in case["gold_roles"].items() if pid in available}
    missing = {role: pid for role, pid in case["gold_roles"].items() if pid not in available}
    _write_json(work / "parts" / "manifest.json", {"parts": available, "roles": roles})
    (work / "scad_parts.py").write_text(SCAD_PARTS_PY, encoding="utf-8")
    p = _run_helper(PREP_SCRIPT, work, timeout=600)
    if p.returncode != 0:
        raise RuntimeError("准备装配零件失败：" + _tail_lines(p.stderr))
    return work, missing


def assembly_user_prompt(case: dict, work: Path, missing: dict[str, str]) -> str:
    manifest = json.loads((work / "parts" / "manifest.json").read_text(encoding="utf-8"))
    roles = "\n".join(f"- {role}：零件 {pid}（{manifest['parts'][pid]['step']}）" for role, pid in manifest["roles"].items())
    specs = {spec["id"]: spec["prompt"].strip() for spec in case["parts"]}
    used = sorted(set(manifest["roles"].values()))
    part_desc = "\n\n".join(f"### {pid}\n{specs[pid]}" for pid in used)
    text = case["assembly"]["prompt"].strip() + "\n\n## 角色（component_id → 零件）\n" + roles
    if missing:
        text += "\n\n以下角色的零件没有生成成功，无法装配，忽略即可：" + "、".join(missing)
    text += "\n\n## 各零件的建模说明（零件坐标系以此为准，几何可用 QL 在加载出的实体上查询核对）\n" + part_desc
    return text


def run_case(client, args, name: str, repeat: int, out_root: Path) -> dict:
    case = load_case(name)
    out = out_root / case["case_id"] / f"r{repeat}"
    run_path = out / "run.json"
    if run_path.exists() and not args.rerun:
        return json.loads(run_path.read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    log(f"=== {case['case_id']} r{repeat}: {len(case['parts'])} 个零件")
    with ThreadPoolExecutor(max_workers=args.part_workers) as pool:
        parts = list(pool.map(lambda s: run_part(client, args, s, out), case["parts"]))
    asm_path = out / "assembly.json"
    if asm_path.exists() and not args.rerun:
        assembly = json.loads(asm_path.read_text(encoding="utf-8"))
    else:
        export = out / "assembly_export.json"
        export.unlink(missing_ok=True)
        shutil.rmtree(out / "sw", ignore_errors=True)
        work, missing = prepare_assembly(case, parts, out)
        record = run_agent(
            client, args, system=asm_system_prompt(), user=assembly_user_prompt(case, work, missing), work=work,
            max_calls=args.max_asm_calls, deliverable="assembly.scadpkg",
            finish_check=lambda: extract(work, export),
        )
        ok, detail = extract(work, export) if (work / "assembly.scadpkg").exists() else (False, "没有交付 assembly.scadpkg")
        assembly = {**record, "delivered": (work / "assembly.scadpkg").exists(), "export_ok": ok,
                    "export_detail": detail, "missing_part_roles": sorted(missing),
                    "export": "assembly_export.json" if export.exists() else None,
                    "export_sha256": hashlib.sha256(export.read_bytes()).hexdigest() if export.exists() else None,
                    "trace": "assembly/transcript.json"}
        _write_json(asm_path, assembly)
        log(f"  asm  {case['case_id']:<28} {record['status']:<16} calls={record['llm_calls']:<3} export_ok={ok}")
    result = {
        "case_id": case["case_id"], "case_name": name, "repeat": repeat, "condition": "gen",
        "model": args.model, "parts": parts, "assembly": assembly,
        "part_pass_rate": sum(1 for p in parts if p["accepted"]) / max(1, len(parts)),
    }
    _write_json(run_path, result)
    return result


def reextract(out_root: Path, names: list[str]) -> None:
    """提取规则改动后，从已保存的 assembly.scadpkg 重新生成 assembly_export.json 并更新记录，不调 LLM。"""
    case_ids = {load_case(n)["case_id"] for n in names}
    for asm_path in sorted(out_root.glob("*/r*/assembly.json")):
        out = asm_path.parent
        if out.parent.name not in case_ids or not (out / "assembly" / "assembly.scadpkg").is_file():
            continue
        export = out / "assembly_export.json"
        ok, detail = extract(out / "assembly", export)
        assembly = json.loads(asm_path.read_text(encoding="utf-8"))
        assembly.update(export_ok=ok, export_detail=detail,
                        export="assembly_export.json" if export.exists() else None,
                        export_sha256=hashlib.sha256(export.read_bytes()).hexdigest() if export.exists() else None)
        _write_json(asm_path, assembly)
        run_path = out / "run.json"
        if run_path.exists():
            run = json.loads(run_path.read_text(encoding="utf-8"))
            run["assembly"] = assembly
            _write_json(run_path, run)
        log(f"  reextract {out.parent.name:<28} export_ok={ok}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", default="all", help="all / core / bench / 逗号分隔的 yaml 文件名")
    ap.add_argument("--model", required=True)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--case-workers", type=int, default=4)
    ap.add_argument("--part-workers", type=int, default=3)
    ap.add_argument("--max-part-calls", type=int, default=40)
    ap.add_argument("--max-asm-calls", type=int, default=50)
    ap.add_argument("--script-timeout", type=float, default=300)
    ap.add_argument("--temperature", type=lambda s: None if s.lower() == "none" else float(s), default=0.0,
                    help="模型不支持时自动去掉；none 表示不传，用服务端默认")
    ap.add_argument("--reasoning-effort", choices=["low", "medium", "high", "xhigh"], default=None,
                    help="不传时用上游默认（实测与 high 相当）")
    ap.add_argument("--max-tokens", type=int, default=None,
                    help="单次输出上限（含隐藏思考）；开 reasoning_effort 且不传时上游默认 16000")
    ap.add_argument("--output", required=True, help="汇总 JSON；产物在 eval/artifacts/<输出文件名>/")
    ap.add_argument("--rerun", action="store_true", help="忽略已有记录重新跑")
    ap.add_argument("--reextract", action="store_true", help="只从已保存的装配包重新提取，不调 LLM")
    args = ap.parse_args()

    all_names = case_names()
    if args.cases == "all":
        names = all_names
    elif args.cases == "core":
        names = CORE_CASES
    elif args.cases == "bench":
        names = [n for n in all_names if n not in CORE_CASES]
    else:
        names = [t.strip() for t in args.cases.split(",") if t.strip()]
        unknown = [n for n in names if n not in all_names]
        if unknown:
            ap.error(f"未知测例 {unknown}，可选：{', '.join(all_names)}")
    output = Path(args.output)
    out_root = LLM_ROOT / "eval" / "artifacts" / output.stem
    out_root.mkdir(parents=True, exist_ok=True)
    if args.reextract:
        reextract(out_root, names)
        return
    client = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:15721/tencent/v1"),
                    api_key=os.environ["OPENAI_API_KEY"], timeout=900, max_retries=0)
    jobs = [(n, k) for n in names for k in range(1, args.repeats + 1)]
    log(f"model={args.model} cases={len(names)} runs={len(jobs)} -> {out_root}")
    rows = []

    def one(job):
        try:
            return run_case(client, args, job[0], job[1], out_root)
        except Exception as exc:  # noqa: BLE001
            log(f"  !! {job[0]} r{job[1]} 失败：{type(exc).__name__}: {exc}")
            return {"case_name": job[0], "repeat": job[1], "error": f"{type(exc).__name__}: {exc}"}

    def write_summary():
        # 只补跑部分测例时，汇总也要包含产物目录里其他已完成的测例
        runs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(out_root.glob("*/r*/run.json"))]
        have = {(r["case_name"], r["repeat"]) for r in runs}
        runs += [r for r in rows if "error" in r and (r["case_name"], r["repeat"]) not in have]
        _write_json(output, {"model": args.model, "artifacts": str(out_root.relative_to(LLM_ROOT)), "runs": runs})

    with ThreadPoolExecutor(max_workers=args.case_workers) as pool:
        for row in pool.map(one, jobs):
            rows.append(row)
            write_summary()
    write_summary()
    log(f"完成 {len(rows)} 个测例运行")


if __name__ == "__main__":
    main()
