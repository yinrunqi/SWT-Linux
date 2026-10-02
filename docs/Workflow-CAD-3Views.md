# Workflow-CAD 看图建模：由三视图生成零件

本文说明怎样用 Workflow-CAD 根据工程图（三视图）在 SolidWorks 里建出零件，以及为此新增了哪些部分。系统的整体架构、环境配置和启动方式见 [`Workflow-CAD.md`](./Workflow-CAD.md)。50 道三视图题和打分方法见 [`Workflow-CAD-Bench-3Views.md`](./Workflow-CAD-Bench-3Views.md)。

看图建模有两条流程：

- **L1 端到端（推荐，评测默认）**：图纸直接交给 L1 闭环 Agent 的规划模型，由它自己读图、调用工具建模。它和 text-to-CAD 共用同一个 Agent、同一套 24 个工具、同一份系统提示词，另外多一个对照用的 `render_views` 工具。规划模型必须是**多模态模型**，同时支持图片输入和 function calling。
- **L0 读图 + 蓝图（旧流程）**：先由视觉模型把图纸读成结构化中间表示，再生成蓝图交给 L0 多轮 Agent 执行。网页界面上传图片时走的是这条流程。

---

## 1. L1 端到端的工作方式

```
三视图 PNG + 一句题面
      │  POST /api/run  {"action_level": "L1", "image": ..., "plan_model": <多模态模型>}
      ▼
Web 后端 _run_workflow_l1
      │  首条用户消息 = 题面 + 图纸方位说明 + 图纸（图片）
      ▼
WorkflowAgent（与 text-to-CAD 相同的系统提示词和 24 个工具）
  ├─ 建模工具 → C# 服务 → SolidWorks
  └─ render_views → 导出当前零件 STEP → venv-occ 子进程投影 → 三视图图片回到对话
      │
      ▼
finish → 评测脚本导出 STEP / SLDPRT
```

### 1.1 图纸怎样交给模型

图纸作为图片放在首条用户消息里，之后每次调用模型都会带上它，所以 Agent 在建模中途可以随时回看图纸。文字部分是题面加一段固定的方位说明（`planner/workflow/agent.py` 中的 `DRAWING_NOTE`），所有题目都一样：

> 附图是零件的三视图（第三角画法）。图纸坐标与模型坐标一致：主视图沿 -Z 方向看（图上右 = +X、上 = +Y）；俯视图在主视图上方，从 +Y 往下看（图上右 = +X、下 = +Z）；右视图在主视图右方，从 +X 看（图上右 = -Z、上 = +Y）。实线是可见边，虚线是隐藏边，点画线是中心线。全部尺寸从图纸读取。

这段说明只交代画法约定，不含任何具体零件的信息。L1 系统提示词本身规定的模型坐标（+Y 向上、+Z 向前、+X 向右）与 benchmark 图纸的投影方向一致，所以模型读到的视图方位可以直接对应到草图平面。

纯文本任务的首条消息保持原来的纯字符串，text-to-CAD 的行为完全不变。

### 1.2 `render_views`：把当前零件投影出来对照

带图运行时，Agent 额外获得 `render_views` 工具（不带参数）：

1. 通过 C# 服务把当前零件导出为临时 STEP；
2. 在检查器环境 `venv-occ` 里用 HLR 投影成三视图 PNG。版式与输入图纸相同，但不带尺寸标注；
3. 工具结果以文字返回包围盒和体积，图片作为下一条用户消息发给模型，供逐个视图对照。

几点约定：

- **只保留最新一张投影图**。再次调用时，上一张会被替换成占位文字，避免上下文和 token 不断膨胀。输入图纸始终保留。
- **只投影当前零件**，不接触参考实体，也不读取任何答案。
- 零件里还没有实体时返回 `NO_BODY`；导出或投影失败时返回新增的错误码 `RENDER_FAILED`，Agent 可以继续建模。
- `render_views` 和 `get_model_state` 一样算查询工具，不计入建模调用和失败次数。
- 纯文本任务不提供这个工具。text-to-CAD 的 24 个工具和 `TOOL_SPECS` 没有变化。

Web 后端所在的 `venv` 没有 OpenCASCADE，所以投影通过子进程调用 `venv-occ\Scripts\python.exe -m geomcheck.drawing <in.step> <out.png>` 完成。检查器环境装在别处时，用环境变量 `GEOMCHECK_PYTHON` 指定它的 Python。

### 1.3 记录

`capture_trace` 返回的对话记录和评测日志里，图片都换成 `[image]` 占位，不会写入 base64。

---

## 2. 环境

在 [`Workflow-CAD.md`](./Workflow-CAD.md) 第 2 节的基础上，还需要注意：

| 项目 | 要求 |
|---|---|
| 规划模型 | 支持图片输入和 function calling 的多模态模型，通过 `plan_model` / `--plan-model` 指定。纯文本模型会在第一次调用时报错 |
| 检查器环境 | 运行 Web 后端的机器上要有 `LLMPlanner/venv-occ`，`render_views` 用它投影。不提供这个工具时（`views_tool: false`）可以没有 |
| 字体 | 投影图的标题使用 `C:\Windows\Fonts\msyh.ttc`（微软雅黑），Windows 自带 |

启动方式与 [`Workflow-CAD.md`](./Workflow-CAD.md) 第 3 节相同：SolidWorks → `run_service.bat` → `venv\Scripts\python.exe -X utf8 -m web.app`。

---

## 3. 运行

以下命令都在 `LLMPlanner` 目录下执行。

### 3.1 先跑通一道题

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py --plan-model <多模态模型> `
    --ids b01_l_bracket --repeats 1 --name smoke_three_view_l1
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view --artifacts eval\artifacts\smoke_three_view_l1
```

SolidWorks 里会建出一个 L 型支架，`eval\artifacts\smoke_three_view_l1\` 下会出现 `b01_l_bracket_r1.step`、`.SLDPRT` 和 `runs.json`。打分输出 rubric 得分和图像分。

### 3.2 批量运行 50 道三视图题

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py --plan-model <多模态模型> `
    --repeats 2 --name three_view_l1_<model>
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view `
    --artifacts eval\artifacts\three_view_l1_<model> --output eval\three_view_l1_<model>_scores.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view `
    summary eval\three_view_l1_<model>_scores.json --repeats 2
```

`eval_three_view_bench.py` 的参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--action-level` | `L1` | `L1` = 端到端；`L0` = 旧的读图 + 蓝图流程 |
| `--plan-model` | `Qwen3.5-397B-A17B` | L1 下必须换成多模态模型 |
| `--max-llm-calls` | 40 | L1 的 LLM 调用预算，与 text-to-CAD 一致 |
| `--no-views-tool` | 关 | 不提供 `render_views`，用于消融对比 |
| `--ids` | 全部 50 题 | 只跑指定的 case |
| `--repeats` | 2 | 每题独立运行次数 |
| `--lang` | `zh` | `en` 时使用 YAML 里的 `prompt_en` |
| `--resume` | 关 | 跳过已有 STEP 的运行，用于中断后续跑 |
| `--mode`、`--vision-model` | `multi_turn`、`GLM-4.6V` | 只对 L0 生效 |

每次运行前后都会关闭所有 SolidWorks 文档，结束后把 STEP 和 SLDPRT 存到 `eval/artifacts/<name>/<case>_r<k>.*`。失败的运行也会尽量保存当时的零件，同样参与打分。

`runs.json` 里 L1 每次运行记录：`status`（`finished` / `max_llm_calls` / `stalled` / `llm_error`）、`llm_calls`、`tool_calls`、`failed_modeling_calls`、`render_views_calls`、`token_usage`、`duration_seconds`，以及 `plan_model` 和 `action_level`。

### 3.3 消融：有无 `render_views`

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py --plan-model <多模态模型> `
    --repeats 2 --no-views-tool --name three_view_l1_<model>_noviews
```

两组结果用同样的命令打分。比较均分、pass@k 和平均 LLM 调用数，就能看出投影对照对结果的影响。

### 3.4 直接调用 HTTP 接口

```python
import base64, requests

drawing = base64.b64encode(open(r"eval\three_view\drawings\d02_bearing_block.png", "rb").read()).decode()
resp = requests.post("http://localhost:5000/api/run", json={
    "instruction": "根据所附三视图（第三角画法，单位 mm）建模轴承座，单一实体。",
    "image": "data:image/png;base64," + drawing,   # 也可以只传 base64；不带前缀时按文件头判断格式
    "action_level": "L1",
    "plan_model": "<多模态模型>",
    "max_turns": 40,              # L1 的 LLM 调用预算
    "views_tool": True,           # 是否提供 render_views
    "use_cache": False,
}, timeout=3600).json()
print(resp["summary"])
```

建模完成后的保存和导出方式与 [`Workflow-CAD.md`](./Workflow-CAD.md) 第 4.1 节相同。

---

## 4. L0 旧流程

`--action-level L0`（或请求里不写 `action_level`）时，带图请求走原来的多轮流程：

1. `vision_model` 把图纸读成结构化中间表示：总体尺寸、各视图里的特征（`planner/llm_client.py` 中的 `_extract_three_view_ir`）；
2. 由中间表示生成建模目标和初始蓝图，并做稳定化处理（`planner/operation_optimizer.py` 中的 `stabilize_three_view_blueprint`）；
3. 蓝图按特征分批，交给 L0 多轮 Agent 用原子操作执行。

这条流程可以搭配纯文本的规划模型，但用不上 L1 的工具、预检和回滚，读图错误也无法在建模中途纠正。网页界面上传图片时目前走的就是这条流程；要用 L1 看图建模，请通过 HTTP 接口或评测脚本。

---

## 5. 新增与改动的代码

| 文件 | 改动 |
|---|---|
| `planner/workflow/agent.py` | `WorkflowAgent.run(goal, image_b64=None, image_mime="image/png")`；`DRAWING_NOTE`；`render_views` 图片作为用户消息发送，只保留最新一张；返回的消息记录去掉图片 |
| `planner/workflow/tools.py` | `RENDER_VIEWS_SPEC`（放在 `EXTRA_TOOL_SPECS`，按需启用）；`WorkflowSession.render_views`；`GEOMCHECK_PYTHON` |
| `planner/workflow/errors.py` | 新错误码 `RENDER_FAILED` |
| `web/app.py` | L1 接受 `image` 与 `views_tool`；`_image_mime` 判断图片格式；`render_views` 计为查询工具 |
| `geomcheck/drawing.py` | 命令行入口：`python -m geomcheck.drawing in.step out.png [标题]`，投影出不带标注的三视图 |
| `eval/eval_three_view_bench.py` | 默认 L1；新增 `--action-level`、`--max-llm-calls`、`--no-views-tool`；记录调用统计 |
| `test/test_workflow_three_view.py` | 离线测试（8 个）：图纸放进首条消息、系统提示词不变、纯文本消息不变、投影图只保留最新一张、`render_views` 按需启用、空零件与投影失败的错误码、真实投影子进程、图片格式判断 |

离线测试不需要 SolidWorks，也不调用模型：

```powershell
venv\Scripts\python.exe -m pytest test/test_workflow_three_view.py -q -p no:cacheprovider
```

---

## 6. 常见问题

- **第一次调用模型就报错**：多半是规划模型不支持图片输入，换成多模态模型。L1 还要求模型支持 function calling。
- **`render_views` 返回 `RENDER_FAILED`**：先确认 `venv-occ\Scripts\python.exe -m geomcheck.drawing <某个 STEP> out.png` 能单独运行；检查器环境不在默认位置时设置 `GEOMCHECK_PYTHON`。这个工具失败不会中断建模。
- **token 用量明显高于 text-to-CAD**：每次调用都会带上图纸，再加最多一张投影图，这是预期的。可以用 `--no-views-tool` 的结果对比投影部分占了多少。
- **泛化原则**：与 text-to-CAD 相同，不能针对具体题目修改提示词、方位说明或工具。`DRAWING_NOTE` 只描述通用的画法约定。
