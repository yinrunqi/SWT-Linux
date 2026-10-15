# Workflow-CAD 看图建模：由三视图生成零件

本文说明怎样用 Workflow-CAD 根据工程图（三视图）在 SolidWorks 里建出零件，以及为此新增了哪些部分。系统的整体架构、环境配置和启动方式见 [`Workflow-CAD.md`](./Workflow-CAD.md)。50 道三视图题和打分方法见 [`Workflow-CAD-Bench-3Views.md`](./Workflow-CAD-Bench-3Views.md)。

看图建模有两条流程：

- **L1 / L1_v2 端到端（推荐，评测默认 L1_v2）**：图纸直接交给闭环 Agent 的规划模型，由它自己读图、调用工具建模。L1 和 text-to-CAD 共用同一个 Agent、同一套 24 个工具、同一份系统提示词，另外多一个对照用的 `render_views` 工具；L1_v2 使用 text-to-CAD 失败分析后形成的 v2 workflow，并可额外调用 `view_model` 查看 SolidWorks 当前截图。默认模型是自部署的 `Qwen3.8-Flash-Next-FP8`，规划模型必须是**多模态模型**，同时支持图片输入和 function calling。
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

评测时每次运行都会打开 `capture_trace`，把完整响应写到 `<case>_r<k>.trace.json`。这个文件包含：
- `request`：发出的请求，图纸已换成占位；
- `response.raw_llm_trace`：发给模型的整段对话，包括模型每轮的文字、工具调用和工具结果；
- `response.workflow.llm_responses`：每次调用模型的原始响应，包括 `reasoning_content`、`finish_reason` 和逐次 usage；
- `response.workflow.tool_calls`：每次工具调用的参数和结果。

L0 流程录下的是每次调用模型的原始请求和响应。请求超时或后端出错时，trace 里记 `error`。

trace 要等整次运行结束才写。L1 运行过程中另有 `<case>_r<k>.checkpoint.json`：每次拿到 LLM 回复、每轮工具执行完，都把完整对话（图片换成占位）、原始回复和工具记录写进去，中途崩溃也能查看已有的输出，并用 `--resume` 续跑（见 3.2）。

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
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py `
    --ids b01_l_bracket --repeats 1 --name three_view_l1v2_qwen38_smoke
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view --artifacts eval\artifacts\three_view_l1v2_qwen38_smoke
```

SolidWorks 里会建出一个 L 型支架，`eval\artifacts\three_view_l1v2_qwen38_smoke\` 下会出现 `b01_l_bracket_r1.step`、`.SLDPRT`、`.trace.json`（完整的 LLM 记录）和 `runs.json`。打分输出 rubric 得分和图像分。

### 3.2 批量运行 50 道三视图题

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py `
    --repeats 2 --name three_view_l1v2_qwen38
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view `
    --artifacts eval\artifacts\three_view_l1v2_qwen38 --output eval\three_view_l1v2_qwen38_scores.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view `
    summary eval\three_view_l1v2_qwen38_scores.json --repeats 2
```

`eval_three_view_bench.py` 的参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--action-level` | `L1_v2` | 与 text-to-CAD 相同的 v2 workflow；`L1` = 原版端到端；`L0` = 旧的读图 + 蓝图流程 |
| `--provider` | `qwenlocal` | 读取 `.env` 的 `QWENLOCAL_BASE_URL` 与 `QWENLOCAL_API_KEY` |
| `--plan-model` | `Qwen3.8-Flash-Next-FP8` | 自部署多模态模型 |
| `--reasoning-effort` | `medium` | 默认 Qwen 自动取 `medium`；也可显式设为 `none` / `minimal` / `low` / `high` / `xhigh` |
| `--max-llm-calls` | 40 | L1 的 LLM 调用预算，与 text-to-CAD 一致 |
| `--no-views-tool` | 关 | 不提供 `render_views`，用于消融对比 |
| `--no-view-tool` | 关 | L1_v2 不提供 `view_model`；不影响输入图纸和 `render_views` |
| `--compact-context` | 关 | L1_v2 可选的紧凑上下文模式；默认关闭，旧命令和历史评测路径不变 |
| `--ids` | 全部 50 题 | 只跑指定的 case |
| `--repeats` | 2 | 每题独立运行次数 |
| `--lang` | `zh` | `en` 时使用 YAML 里的 `prompt_en` |
| `--resume` | 关 | 保留 `runs.json` 里已完成的运行；出错或中断的运行（`llm_error`、`sw_lost`、`resume_failed`）和还没跑的运行有 checkpoint 时从 checkpoint 续跑 |
| `--sw-restarts` | 0 | SolidWorks 看门狗：每次运行前检查服务和 SolidWorks 内存，必要时重启；运行中丢失 SolidWorks 时重启并从 checkpoint 续跑，最多这么多次 |
| `--sw-max-private-gb` | 12 | 看门狗的 SolidWorks 私有内存上限 |
| `--mode`、`--vision-model` | `multi_turn`、`GLM-4.6V` | 只对 L0 生效 |

每次运行前后都会关闭所有 SolidWorks 文档，结束后把 STEP 和 SLDPRT 存到 `eval/artifacts/<name>/<case>_r<k>.*`。失败的运行也会尽量保存当时的零件，同样参与打分。

续跑与 text-to-CAD 的 L1 评测相同：先在新零件上按原顺序重放已执行的工具恢复模型，再接着原对话继续，已经拿到的回复不会重新请求，图纸会重新放回首条消息；重放结果与记录不一致时记 `resume_failed`。不加 `--resume` 时旧 checkpoint 会被删掉，从头开始。

`runs.json` 里 L1 每次运行记录：`status`（`finished` / `max_llm_calls` / `stalled` / `llm_error`）、`llm_calls`、`tool_calls`、`failed_modeling_calls`、`render_views_calls`、`token_usage`、`duration_seconds`、`plan_model`、`action_level`，以及 trace 文件路径 `trace`。紧凑模式另记 `compact_context`、`length_retries`、`compact_verifier_calls`、`compact_fallbacks` 和逐次 `request_modes`。

### 3.3 自部署 Qwen3.8-Flash-Next-FP8 + L1_v2

这组设置与 text-to-CAD track 一致：`qwenlocal`、`L1_v2`、`reasoning_effort=medium`、最多 40 次 LLM 调用。模型名命中 Agent 的统一配置后，上下文上限自动按 73,728 token 处理；接近上限时，两条 track 使用同一段逻辑压缩当轮 `max_tokens`。

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py `
    --provider qwenlocal --plan-model Qwen3.8-Flash-Next-FP8 `
    --action-level L1_v2 --reasoning-effort medium --max-llm-calls 40 `
    --ids b01_l_bracket --repeats 1 --name three_view_l1v2_qwen38_smoke
```

自部署端仍须支持图片输入和 function calling；text-to-CAD 能运行只能证明工具调用正常，不能证明视觉输入正常。先通过单题 smoke 后再去掉 `--ids` 跑全量。

### 3.4 可选的紧凑上下文模式

历史评测方式仍是默认值。只有显式加 `--compact-context` 才启用三项 token 优化：

1. 发给模型的请求副本把较早的成功工具结果压成 ledger，保留 feature 名、包围盒、体积、实体数、关键圆柱和错误原因；最近两轮保持完整。完整历史仍写入 checkpoint/trace，并用于 SolidWorks replay。
2. `get_model_state`、`view_model` 或 `render_views` 后用短 verifier 请求核对，只携带原图、最新截图/状态，并只开放 `finish` 和 `continue_full_context`。若需修改，下一轮恢复完整历史。
3. 初始输出余量提高到 32k（未使用的上限不计 token）；仍被 `length` 截断时，用压缩后的 recovery 请求重试。实际截断调用继续计入 `llm_calls` 和 `token_usage`，不会隐藏消耗。

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py `
    --action-level L1_v2 --compact-context `
    --ids b01_l_bracket --repeats 1 --name three_view_l1v2_compact_smoke
```

HTTP API 对应字段是严格布尔值 `"compact_context": true`，只支持 `L1_v2`。旧 checkpoint 没有新增字段也能续跑；压缩只作用于请求副本，不破坏 checkpoint 里的完整消息和 `tool_log`。

### 3.5 消融：有无 `render_views`

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py --plan-model <多模态模型> `
    --repeats 2 --no-views-tool --name three_view_l1_<model>_noviews
```

两组结果用同样的命令打分。比较均分、pass@k 和平均 LLM 调用数，就能看出投影对照对结果的影响。

### 3.6 直接调用 HTTP 接口

```python
import base64, requests

drawing = base64.b64encode(open(r"eval\three_view\drawings\d02_bearing_block.png", "rb").read()).decode()
resp = requests.post("http://localhost:5000/api/run", json={
    "instruction": "根据所附三视图（第三角画法，单位 mm）建模轴承座，单一实体。",
    "image": "data:image/png;base64," + drawing,   # 也可以只传 base64；不带前缀时按文件头判断格式
    "action_level": "L1_v2",
    "plan_model": "<多模态模型>",
    "max_turns": 40,              # L1 的 LLM 调用预算
    "views_tool": True,           # 是否提供 render_views
    "compact_context": False,     # 默认旧路径；true 才启用紧凑上下文
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
| `planner/workflow/agent.py` | `WorkflowAgent.run(goal, image_b64=None, image_mime="image/png")`；`DRAWING_NOTE`；`render_views` 图片作为用户消息发送，只保留最新一张；可选 `compact_context` 状态机 |
| `planner/workflow/context_compact.py` | 紧凑模式的旧工具 ledger、最终 verifier 与 length recovery 请求构造；只改请求副本 |
| `planner/workflow/tools.py` | `RENDER_VIEWS_SPEC`（放在 `EXTRA_TOOL_SPECS`，按需启用）；`WorkflowSession.render_views`；`GEOMCHECK_PYTHON` |
| `planner/workflow/errors.py` | 新错误码 `RENDER_FAILED` |
| `web/app.py` | L1 接受 `image` 与 `views_tool`；`_image_mime` 判断图片格式；`render_views` 计为查询工具 |
| `geomcheck/drawing.py` | 命令行入口：`python -m geomcheck.drawing in.step out.png [标题]`，投影出不带标注的三视图 |
| `eval/eval_three_view_bench.py` | 默认自部署 Qwen + L1_v2 + medium；`--reasoning-effort`、`--max-llm-calls`、`--no-views-tool`、`--no-view-tool`、`--compact-context` 与 text-to-CAD 对齐；记录调用统计 |
| `test/test_compact_context.py` | 紧凑模式的默认关闭、ledger、verifier finish/回退、length recovery、checkpoint/resume 离线测试 |
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
