# SolidWorksTutorial 项目分析报告：运行机制、通用化改进与 LLM Benchmark 评测方案

> 撰写日期：2026-09-28  
> 分析范围：`Chapter1.ConnectToSw/`（C# 服务）、`LLMPlanner/planner/`（规划器）、`LLMPlanner/web/`（Web Demo）、`LLMPlanner/eval/`（评测脚本与结果）、`PERFORMANCE.md`、`docs/HANDOVER.md`  
> 本报告只基于代码与已有结果文件做静态分析，没有重新启动 SolidWorks 跑测例。

---

## 0. 结论摘要

1. **项目怎么跑**：这是一个“LLM 生成操作序列 → HTTP → C# 通过 COM 驱动已打开的 SolidWorks”的系统。C# 端（`Program.cs` 约 2.9k 行，`SwCommands.cs` 约 7.4k 行）在 `localhost:5100` 暴露约 70 个原子端点；Python 端把自然语言或三视图图片转成 `SwModelingPlan`（Pydantic 结构），再逐条调用这些端点。它有两种模式：Single-turn 是一次生成完整计划后顺序执行；Multi-turn 是先生成全局蓝图，按每批最多 5 个操作切成里程碑，每批执行后读取模型状态做确定性验收，失败则让 LLM 原位修复。
2. **通用 AI-CAD 的主要瓶颈在动作空间，不在模型**。LLM 需要直接写“米制坐标 + 射线选面 + 语义边名”这类命令式低层调用，失败高度集中在草图不闭合、旋转轮廓无效、选面错误、切除失败上（见 §1.9）。最值得做的改动是引入一层**参数化、可引用、可离线校验的 CAD 中间语言**：LLM 写设计意图和特征图，编译器负责坐标与拓扑引用，SolidWorks 只作为后端之一。
3. **当前 benchmark 可以反映趋势，但还不能准确排出模型高低**，主要有五个问题：
   - 样本少，而且每个模型只跑一次。Qwen 同一批 5 例无缓存复跑，平均分从 92 掉到 63.75。
   - 评分粒度粗，容易被钻空子：包围盒排序后比较（不看朝向），体积区间最宽约 ±38%，特征只数“≥ 最小个数”，失败的运行也能从执行分里拿到 20–25 分。
   - Multi-turn 验收有循环论证：里程碑的验收条件是从 LLM 自己的蓝图推出来的；三视图的期望尺寸来自 LLM 自己抽取的 IR。
   - 装配体测例把答案直接给了模型：prompt 里写了精确坐标，验收条件里的期望坐标和齿轮比也序列化进 `current_milestone` 发给了 LLM。零件由确定性脚本预先生成。
   - 运行时的确定性修补（例如 `stabilize_three_view_blueprint` 会直接按 IR 合成建模步骤）会和模型能力混在一起算分。
4. **建议的评测方案**：以参考实体（STEP）加原子需求清单作为隐藏 GT。主指标是严格通过率（pass@1 加多次运行一致性），辅以需求满足率和精确布尔 IoU / Chamfer 距离，报告 bootstrap 置信区间和配对检验。装配体改为“只给功能需求”的分层任务。评测几何计算从 SolidWorks 中解耦，导出 STEP 后用 OpenCascade 离线算。

---

## 1. 项目是怎么跑的

### 1.1 进程拓扑

```
浏览器 (Web Demo, :5000)   或   CLI: python -m planner.main
            │                              │
            ▼                              ▼
   LLMPlanner/web/app.py  (Flask, 串行锁 _sw_execution_lock)
            │  复用 planner/*
            ▼
   planner: SwLLMClient ──(OpenAI 兼容 API: AI Ping / juheai 等)──► LLM
            │  SwModelingPlan / AgentTurn (Pydantic)
            ▼
   planner: SwExecutor / SwObserver ──HTTP JSON──► C# HttpListener :5100
                                                    │  Program.cs 路由 → SwCommands.cs
                                                    ▼  COM Interop (Xarial.XCad 0.8.3)
                                             SolidWorks 2025 中文版（必须先手动启动，且与服务同权限）
```

几个硬约束：
- SolidWorks 是**单实例、有状态的执行器**。Web 后端用全局锁串行化请求，评测脚本在用例之间调 `/api/close-all-documents` 清场。
- 所有长度单位是**米**。LLM 需要自己完成 mm→m 换算（`skills/core/02_units.md`）。
- 中文版 SolidWorks 的基准面名（“前视基准面”等）在代码里是写死的；中文版也没有装配体模板，项目用内置的 `resources/empty_assembly_template.SLDASM` 绕过。

### 1.2 启动步骤

1. `build_service.bat`：用 MSBuild 编译 `.NET Framework 4.7.2` 控制台程序，产物是 `bin/Debug/Chapter1.ConnectToSw.exe`。
2. 手动打开 SolidWorks（普通权限）。
3. `run_service.bat`：服务通过 `SwApplicationFactory.FromProcess()` 连上 SolidWorks 进程，开始监听 5100 端口。
4. `LLMPlanner` 的 venv 里配置 `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `PLANNER_MODEL`（`load_env.py` 会读 `.env`），然后二选一：
   - `python -m planner.main [--dry-run] "指令"`：命令行，只有 Single-turn；
   - `python -X utf8 -m web.app --port 5000`：Web Demo，支持 Single/Multi-turn、三视图上传、装配体和机构动画控件。

### 1.3 Single-turn 数据流

`SwLLMClient.plan()`（`planner/llm_client.py`）的流程：

1. **组装 Skill Prompt**：`SkillRegistry` 扫描 `planner/skills/**.md`。`routed` 模式下注入全部 `core/*`（操作手册、单位、规则、编辑），再按 YAML frontmatter 里的 `triggers` 关键词命中 `recipes/`、`input_modes/`。
2. **查缓存**：`diskcache`，key = SHA256(model, system_prompt, user_prompt[, image])。Skill 一改，key 就变。
3. **调用 LLM**：优先用结构化输出（JSON Schema），失败时退回 JSON mode 加手动抽取 JSON，外面包一层 `tenacity` 重试，`temperature=0`。
4. **得到 `SwModelingPlan`**：`steps: [SwOperation{operation, params, description}]`。`OperationType` 有 50 多种：零件草图/特征、选面、尺寸修改、装配、机构姿态。
5. **执行**：`SwExecutor.execute_plan()` 把每个 op 映射成一个 HTTP POST 顺序发出。每步返回 `{success, message}`，失败即停。

### 1.4 三视图（多模态）数据流

- **阶段 A（Vision）**：图片加 `three_view.md` 中“阶段 A”部分的规则，输出 `ThreeViewIR`（`part_type`、`base_view`、`overall_size_mm`、`profiles[]`、`dimensions[]`、`features[]`、`assumptions`）。
- **阶段 B（Text）**：把 IR 序列化成文字 prompt，走 `_plan_text(input_mode="three_view")`。
- **端到端缓存**：key = (plan_model, 文字提示, 图片哈希)。这是为了解决 Vision 输出不稳定导致缓存总是未命中的问题。
- **Web 端的 Multi-turn 三视图还会先调 `stabilize_three_view_blueprint`**（`operation_optimizer.py`）。它按 IR 做确定性修补，比如检测到“顶面尺寸”时直接合成“矩形 + 拔模拉伸”的完整步骤；只有合成结果为空时才让 LLM 出蓝图。

### 1.5 Multi-turn 数据流（Web `/api/run` + `mode=multi_turn`）

```
全局蓝图 (LLM 一次生成，或三视图确定性合成)
   → operation_optimizer.group_feature_batches  (连续 line/circle 压缩为 polyline/circles，每批 ≤5 op，按“选面/草图/特征”原子事务切分)
   → TaskDecomposer.decompose_blueprint         (每批 → 1 个 Milestone + 自动推导的 postconditions)
   → MultiTurnAgent.run_blueprint:
        for 每个批次:
            执行 → SwObserver.collect() → ValidatorRegistry.validate(postconditions)
            通过 → 下一批
            不通过 → 最多 3 次 LLM 修复轮 (decide_agent_turn, repair_required=True)
                     修复动作中的射线选面会被 _stabilize_repair_face_selection
                     替换成“按 observation.faces 的索引选面”
        全部通过 → completed，否则 blocked
```

另一个入口 `MultiTurnAgent.run()` 是纯账本驱动：每轮由 LLM 针对当前里程碑决定最多 5 个动作。装配体评测脚本用的就是这个入口，TaskPlan 由评测脚本手写。

配套机制：
- `MilestoneLedger`：里程碑状态机。
- `CompressedMemory`：给 LLM 的压缩上下文。
- `IdempotencyGuard`：拒绝在同一里程碑上重复同样的失败动作。
- Agent 决策缓存：按“语义 observation + 账本 + 轮次”做 key，执行成功后才写入。

### 1.6 观察与验收

- `SwObserver` 优先请求聚合端点 `GET /api/model-observation`，拿到特征树（含尺寸）、面列表（类型/中心/法向/面积/几何签名）、草图摘要、“特征–草图”关联、包围盒、体积，并计算 `state_fingerprint`。装配体走 `/api/assembly/observation`（组件路径、Transform、固定状态、Mate 类型与齿轮比），20 个组件以内时再自动做一次干涉检查。
- `ValidatorRegistry` 是声明式后置条件，包括 `valid_entity`、`bbox_axes`（**排序后**逐轴比较）、`volume_range`、`feature_count`（关键词“≥ minimum”）、`fillet_radius`、`face_pips`（骰子专用）、`component_count`、`component_positions`、`mate_type_count`、`gear_ratios`、`no_interference`、`assembly_saved` 等。

### 1.7 装配体与“机构”能力

- 装配原子操作：新建/打开/保存、插入组件、固定/浮动、重合/同心/齿轮配合、干涉检查。齿轮配合的中心孔圆柱面由 C# 端根据组件 Transform 自动找到。
- 机构姿态 API（`/api/assembly/mechanism/*`）：曲柄滑块、同步旋转、四连杆、凸轮从动、平面串联链、径向展开、行星轮系、径向滑块、间歇分度。**本质上是 C# 端解析求解运动学后直接写组件 Transform**，不是让 SolidWorks 的配合求解器或 Motion Study 去算。Motion Study 只在齿轮对/减速箱动画里用到。
- Web Demo 里的台灯、折叠伞、电钻、缝纫机、放映机、发动机等滑块控件都是确定性调用，不消耗 LLM token。

### 1.8 现有评测体系

| 评测脚本 | 对象 | 评分方式 |
|---|---|---|
| `eval_text_singleturn_qwen.py`（GLM、Kimi 复用） | 15 个文本零件 | 执行 25 + 三轴尺寸 30 + 体积 20 + 细节特征计数 25 |
| `eval_three_view_multiturn.py` 等 | B/C 系列三视图 | 包围盒、体积、状态 |
| `eval_phone_stand_modes.py`、`eval_modes_three.py` | 同一零件的 Single/Multi-turn 对比 | 尺寸/体积/拓扑/细节/执行 |
| `eval_assembly_model_benchmark.py` | 减速箱、发动机、放映机、缝纫机 | 静态验收 40 + 机构采样 40 + 执行 20 |
| 各 `eval_*_multiturn.py` + `*_spec.json` | 单个装配体 | 里程碑 + 运动学采样验收 |

这些脚本已经做得比较好的地方：冷缓存运行、用例间清场、记录 token 和费用、保存原始 Provider 轨迹（从 Kimi 第 10 例开始），并且明确承认“部分分高不等于交付率高”。另外，`.cursor/rules/llm-modeling-generalization.mdc` 规定运行时不得根据 case 调整策略，这条规则本身是对的。

### 1.9 已有结果中反映出的能力画像

来自 `PERFORMANCE.md`（2026-07-22，文本 Single-turn，统一 100 分量表）：

| 模型 | 平均分 | 严格通过 | 已知 tokens |
|---|---:|---:|---:|
| Kimi-K2.5 | 62.97 | 3/15 | 139,822 |
| GLM-4.6V | 58.55 | 1/15 | 300,165 |
| Qwen3.5-397B-A17B | 48.35 | 4/15 | ≥164,162 |

失败原因高度集中：
- 草图不闭合（扳手、钥匙、圆角板凳腿）；
- 旋转轮廓无效或误用旋转（马克杯、内六角螺栓、阶梯轴）；
- 外围槽、螺栓孔、环槽切除失败（齿轮盘、法兰、皮带轮）；
- 坐标或角度换算错误导致实体尺寸大一个数量级（GLM 手机支架 856×901mm）；
- 协议格式错误（点用数组而不是对象）、返回空计划、单次响应超过 800 s。

这些几乎都是**“低层命令式动作空间”带来的问题**，而不是“模型不懂马克杯长什么样”。这正是 §2 的出发点。

装配体（2026-07-20，三模型）：Qwen 92.69 分、GLM 66.67 分、MiniMax 26.67 分。其中 0 分的项大多是 `error` 状态，比如超时或 usage 没有回传，混入了基础设施失败（见 §3.1）。

---

## 2. 如何改成更通用、效果更好的 AI 辅助 CAD 系统

### 2.1 核心诊断

| 现状 | 后果 |
|---|---|
| LLM 直接输出米制绝对坐标、矩形对角点、圆弧起终点 | 算术错误（例如 `x2 = cx + w` 而不是 `w/2`）、单位错误、朝向错误 |
| 选面靠射线（rx..dz），选边靠“top_front”这类方盒语义名 | 圆角或开孔之后拓扑一变，射线就失效；圆柱件没法用语义名 |
| 草图是逐条图元命令，没有约束求解，也不做闭合预检 | 草图不闭合要到 SolidWorks 执行时才发现，而且多半在第 N 步 |
| 特征集合有限：没有 shell/sweep/loft/mirror/hole/rib/参考面等，slot 退化成矩形，圆周阵列不稳定 | LLM 只能用“多次切除”去近似，步数和出错点都会增加 |
| 机构 API 按机构类型逐个定制（伞、行星轮系、缝纫机要用的 cam/four-bar） | 每新增一类产品就要新写一个解析求解器，不能真正泛化 |
| Multi-turn 验收条件来自 LLM 自己的蓝图 | 只能发现“执行失败”，发现不了“设计错误” |
| 只能在 Windows 加一个 SolidWorks 实例上串行运行 | 评测吞吐低，也没法接 CI 或并行多次采样 |

### 2.2 改进一：引入参数化 CAD 中间语言（最高优先级）

让 LLM 输出**设计程序**，而不是 API 调用序列。有两条路线：

- **A. 采用现成的 Python CAD DSL**（CadQuery / build123d 风格）。现有 LLM 对这类代码的先验很强，CAD-Recode、Text2CAD 等研究也大量采用程序化 CAD 表示。
- **B. 自定义 JSON 特征图 IR**。更可控，也便于做 schema 约束解码。

无论选哪条，都要具备以下能力：

1. **命名参数与表达式**：`base_len = 110; wall = 6; hole_x = base_len/2 - 15`。算术由解释器完成，不再由 LLM 心算。参数还能直接映射成 SolidWorks 方程式/全局变量，模型因此天然可编辑。
2. **相对工作平面和语义选择器**：例如 `faces(">Z")`、`faces(created_by="boss1").max(area)`、`edges("|Z").of(face)`、“孔 H1 的圆边”。编译器在当前 B-rep 上把选择器解析成具体实体，从根本上替代射线猜测。当前 `select_observed_face_and_sketch` 是朝这个方向迈出的一小步。
3. **约束草图**：闭合轮廓作为一个整体提交（polyline/arc 混合环），Python 端先用 shapely 等工具做闭合性、自交和面积预检，失败就在不碰 SolidWorks 的情况下立即反馈给 LLM。可以选择性接入 2D 约束求解（水平、相切、对称、尺寸）。
4. **多后端编译**：同一份 IR 可以编译到
   - OpenCascade（CadQuery/OCP）：跨平台、可并行、秒级，用于开发、CI 和评测的几何计算；
   - SolidWorks（现有 C# 服务）：用于产出最终的原生 `.SLDPRT/.SLDASM` 和特征树。

   两个后端之间做一致性测试（体积、包围盒、拓扑一致）。这样就不再被单一的 SolidWorks 实例卡住。

### 2.3 改进二：补齐建模原语，优先补“一次操作表达一类几何”的高层特征

按“能消除多少条出错路径”排序：

1. **Shell（抽壳）**：杯、盒、碗、外壳类零件目前都靠切除近似。
2. **Hole（孔向导）/ 螺纹孔、沉头孔**：法兰、螺栓、面板类零件。
3. **Mirror / 对称拉伸**：左右对称件。
4. **Sweep / Loft / Rib / Draft**：把手、扳手手柄、加强筋、拔模。
5. **参考几何**：偏移基准面、轴、点，用于在斜面或空间位置上草图。
6. **修复 Slot 和圆周阵列**；支持多实体布尔（合并、相减、相交）。
7. **标准件库**：Toolbox 螺栓、轴承、齿轮生成器。齿轮渐开线目前是在 Python 里生成点集，应当收敛为一个 `gear(module, z, pa, width, bore)` 高层原语。

原则上沿用现有规则：**一个高层原语对应一类几何，而不是一个物体**。

### 2.4 改进三：规划器从“生成步骤”升级为“需求 → 设计 → 验证”

1. **需求抽取（Spec 阶段）**：先把自然语言或图纸转成结构化的**设计规格**，包括参数表、功能约束（“贯穿孔 Ø10 同轴”“总高 118”“左右对称”）、未指定项和假设。遇到歧义就向用户提问，而不是默默假设。
2. **分层分解**：产品 → 零件/子装配 → 特征。每一层都可以单独验证和缓存。
3. **按规格验收**：Multi-turn 的 postcondition 应当从 Spec（用户意图）推导，而不是从蓝图（LLM 的解法）推导，这样才能发现“执行成功但设计错了”。现在的 `decompose_blueprint` 只能检查“我做了我计划做的事”。
4. **多模态自检**：每个里程碑结束后渲染等轴测图、三视图，必要时加剖面图，交给 VLM 与需求对照做批评，和结构化观察互补。结构化观察擅长查尺寸，看不出“挡唇在后面而不是前面”这类语义错误。
5. **工具化测量**：给 Agent 提供 `measure(distance/angle/radius)`、`section(plane)`、`check_wall_thickness`、`ray_query` 等查询工具，让它能主动验证，而不是只能读被动快照。
6. **失败知识沉淀**：把 C# 端的错误标准化为错误码（例如 `SKETCH_OPEN_PROFILE`、`REVOLVE_PROFILE_CROSSES_AXIS`、`SELECT_NO_FACE_HIT`），每个错误码附上可操作的修复提示。这比在 Skill 里堆自然语言规则更稳定。

### 2.5 改进四：装配体与机构的通用化

1. **用配合关系定位，而不是给坐标**：LLM 表达“轴 A 与孔 B 同心，端面贴合，齿轮 A:B 啮合”，由 SolidWorks 的配合求解器或自研约束求解定位。现在的评测是把坐标直接给出来的，这一层能力完全没有被测到。
2. **统一的运动学图**：把目前约 9 个定制机构 API 收敛为一个“关节图（旋转/移动/齿轮/凸轮/闭环）+ 通用数值求解器”。输入关节类型、参数和驱动量，输出所有刚体位姿。或者直接用 SolidWorks Motion Study 或 mate 驱动。这样新增一类产品不需要再写 C#。
3. **零件与装配联合生成**：让 LLM 同时设计零件（例如根据中心距和传动比反推齿数和模数），而不是只插入预先准备好的零件。
4. **功能验证**：自由度计数（和期望 DOF 对比）、沿运动行程扫描干涉、传动比测量、极限位置检查。现有的运动学采样验收做得不错，可以直接复用。

### 2.6 改进五：工程与产品化

- **拆分 C# 巨石**：`SwCommands.cs` 7.4k 行，建议按草图、特征、选择、观察、装配、机构拆开，同时抽出“语言无关的实体名常量”，以支持英文版 SolidWorks。
- **撤销与回滚**：每个里程碑前记录特征树位置，失败时回滚到该位置（`EditRollback` 或删除特征），不要在半成品上叠加修复。
- **会话化编辑**：支持“把底座加长 20mm”这类对已有模型的修改（现在有 `modify_dimension`，但缺少与参数表绑定的意图层）。
- **模型路由与成本**：Vision、规划、修复分别路由到不同模型；设置超时预算（现有日志里有单次响应 800–900 s 的情况，应当有更硬的每次调用超时，并快速失败）。
- **API 文档 RAG**：本地已有 SolidWorks API XML（`offline-solidworks-api-docs`），可以用来支撑“新增原语”的开发 Agent，但不应注入给建模 LLM。

### 2.7 建议路线图

| 阶段 | 内容 | 预期收益 |
|---|---|---|
| P0（1–2 周） | 草图闭合/自交在 Python 端预检；标准化错误码；补 `export-step` 端点；补 Shell/Mirror/Hole | 直接消除文本测例中最多的一类失败；为离线评测铺路 |
| P1（2–4 周） | 参数化 IR + 语义选择器 + OCC 后端 + SolidWorks 编译器 | 算术和选面错误大幅下降；评测可以并行 |
| P2（1–2 月） | Spec 阶段、按规格验收、渲染 + VLM 自检、回滚 | 能发现“设计错误”；Multi-turn 真正闭环 |
| P3 | 基于配合的装配 + 通用运动学图 + 零件和装配联合设计 | 装配能力从“填 API”变成“做设计” |

---

## 3. 作为 LLM Benchmark：如何更好、更准确地评测不同大模型

### 3.1 现有评测的具体问题

| # | 问题 | 代码或数据证据 | 影响 |
|---|---|---|---|
| 1 | **样本量小，单次运行** | 文本 15 例、三视图 5–12 例、装配 3–4 例，每个模型跑 1 次 | Qwen 5 例无缓存复跑平均分变化 −28.25；模型间 4–15 分的差距落在噪声范围内 |
| 2 | **包围盒排序后比较** | `_dimension_score` 和 `_check_bbox_axes` 都对三轴做了 `sorted()` | 朝向错误、轴对调的零件也能拿满尺寸分 |
| 3 | **体积区间过宽** | 手机支架 `(80000, 180000)`，中心 13 万，允许约 ±38%；板凳 ±7.7% | 缺筋、缺槽、缺挡唇都可能仍在区间内 |
| 4 | **细节分只数特征个数（≥）** | `expected_details=(("cuts",2),...)`，任意 2 次切除即可 | 切错位置、尺寸不对也能得分；只数特征树也和“等价建模方法”冲突 |
| 5 | **执行分给失败运行“保底”** | 执行 20–25 分按成功步数比例给；装配 `blocked` 也给 5 分 | 平均分和严格通过率背离（GLM 平均 58.55，但只通过 1/15） |
| 6 | **Multi-turn 自证式验收** | `decompose_blueprint` 从蓝图的 op 推出 `feature_count`；三视图期望尺寸取自 LLM 自己的 IR | 蓝图错了照样“completed”；Agent 内部的“通过”不能当作评测的“正确” |
| 7 | **装配体测例泄露答案** | `_user_goal` 给出每个组件的精确坐标；`_task_plan` 的 postconditions（期望坐标、齿轮比 [1,2,3]）经 `current_milestone.model_dump()` 发给 LLM；零件由 `prepare_parts` 确定性生成 | 测到的主要是“按说明填 API 参数和实例名”的能力，而不是装配设计能力 |
| 8 | **运行时修补混入得分** | `stabilize_three_view_blueprint` 可以不经 LLM 合成整套步骤；修复轮把射线换成 faceIndex；C# 射线自动反转；面选兜底 | 分数衡量的是“模型 + 启发式”的组合，没办法归因到模型 |
| 9 | **量表和版本混用** | `HISTORICAL_CASES` 里混有旧 20 分量表和缓存复跑的结果（报告里已注意到并做了修正） | 不同批次不可比 |
| 10 | **基础设施失败算作模型 0 分** | 超时 900 s、usage 未回传、`error` 状态直接 0 分 | MiniMax 3 例、GLM 放映机的 0 分无法区分是模型问题还是 Provider 问题 |
| 11 | **没有参考几何** | GT 只有包围盒、体积区间、计数 | 没法衡量“形状像不像”，也没法给等价建模方法公平打分 |
| 12 | **没有独立的隐藏测试集** | Web Demo 示例、评测用例、开发调试用的是同一批 | Skill 和规则会慢慢过拟合到这批用例（规则里禁止这样做，但评测数据上没有隔离） |

### 3.2 设计原则

1. **评测和被测系统完全隔离**：GT、期望值、验收条件只在评测器里存在，任何字段都不进入 prompt、账本或 memory。Agent 可以有自己的内部检查，但只能基于 Spec 中用户可见的信息。
2. **按结果评判，不按过程评判**：评判最终几何和功能，而不是 API 调用序列或特征树的写法。这样才能公平对待等价建模方法。
3. **严格通过率是主指标，部分分是诊断指标**：两者分开报告，不要把部分分合成一个平均数当排名依据。
4. **统计上站得住**：多次采样，给出置信区间，做配对比较。
5. **可复现、可归因**：冻结评测版本，冷缓存，完整记录轨迹，并分层报告“裸模型 / 标准 harness / 全部修补”三档。

### 3.3 任务集设计：分层 + 能力标签

| 层级 | 任务类型 | 示例 | 主要考察 |
|---|---|---|---|
| L1 | 单特征基础体 | 圆柱、六棱柱、带孔板 | 单位、坐标、基础操作 |
| L2 | 多特征棱柱零件 | 支架、面板、手机支架 | 特征组合、选面、布尔 |
| L3 | 回转/壳/扫掠类 | 杯、花瓶、阶梯轴、螺栓 | 旋转轮廓、抽壳、回转切除 |
| L4 | 图纸到模型 | 三视图、带尺寸标注的工程图 | 视觉读图、尺寸抽取、视图对应 |
| L5 | **编辑任务** | 在给定模型上“加长 20mm”“加 4 个 M5 沉头孔” | 理解已有模型、局部修改、不破坏其余部分 |
| L6 | 装配（零件给定、**只给功能需求**） | “让 12:36 两级减速、输出同向、无干涉” | 配合推理、定位、传动设计 |
| L7 | 产品级（零件和装配都要生成） | “设计一个 2:1 齿轮箱，中心距 60” | 分解、参数联动、功能闭环 |
| 附加 | 欠约束 / 不可行 / 自相矛盾的需求 | “外径 20、内径 30 的管” | 应当澄清或拒绝，而不是硬做 |

每个 case 标注能力标签（`revolve`、`shell`、`pattern`、`face_select`、`symmetry`、`mate_gear`、`kinematics` 等），用于能力雷达图和失败归因。

规模建议：公开开发集约 50–100 例，**隐藏测试集约 100–200 例**，只在发布前跑。每例提供 2–3 个改写版本（中/英、数值顺序打乱、口语化）用来测稳健性。L1–L3 可以从 DeepCAD、Fusion 360 Gallery、Text2CAD 等公开数据中采样再做人工复核；L4 可以参考 TriView2CAD / CAD-VGDrawing（项目 README 已经规划了这两个数据集）。

### 3.4 Case 规格（GT 只放在评测侧）

```json
{
  "case_id": "L2_phone_stand_v2",
  "split": "hidden_test",
  "tags": ["multi_feature", "angled_face", "symmetry", "through_slot", "fillet"],
  "prompts": {
    "zh": "创建一个一体式桌面手机支架……",
    "zh_paraphrase": "……",
    "en": "……"
  },
  "frame": "prompt_defined",
  "reference": { "step": "gt/L2_phone_stand_v2.step", "params": "gt/params.json" },
  "hard_requirements": [
    { "id": "R1", "kind": "single_solid" },
    { "id": "R2", "kind": "bbox_axis", "axis": "X", "value_mm": 78, "tol_mm": 0.5 },
    { "id": "R3", "kind": "bbox_axis", "axis": "Z", "value_mm": 118, "tol_mm": 1.0 },
    { "id": "R4", "kind": "through_slots", "count": 3, "size_mm": [36, 8], "tol_mm": 0.3 },
    { "id": "R5", "kind": "planar_face_angle", "to": "XY", "deg": 74, "tol_deg": 0.5 },
    { "id": "R6", "kind": "mirror_symmetric", "plane": "YZ", "tol_mm": 0.2 },
    { "id": "R7", "kind": "bottom_flat", "min_area_ratio": 0.8 }
  ],
  "soft_requirements": [
    { "id": "S1", "kind": "fillet_radius_present", "radius_mm": 4, "tol_mm": 0.1 }
  ],
  "geometry_metrics": { "iou_pass": 0.95, "chamfer_pass_ratio": 0.005 }
}
```

要点：
- **`hard_requirements` 里每一项都是一个可执行的几何探针**，在 OCC 里对导出的 STEP 求值。这些探针不看特征树，也就不在乎建模方法。
- **坐标系由 prompt 定义**时，按原朝向比较，不排序。如果 prompt 没规定坐标系，就先做规范化对齐（主惯性轴 + 质心），再比较，并在报告中标明用了对齐。
- 体积、面积作为辅助指标，用相对误差计分，不用宽区间。

### 3.5 指标体系

**A. 主指标**
- **严格通过（Strict Pass）**：全部硬需求满足，且 IoU ≥ 阈值（默认 0.95），且是有效的单一实体（或满足装配有效性）。
- **pass@1**：k 次独立采样的平均通过率（k = 3–5，温度 0 和常用温度各报一组）。
- **pass^k（一致性）**：k 次全部通过的比例。它衡量“能否稳定交付”，对产品意义很大。

**B. 诊断指标**
- **需求满足率（RSR）**：满足的硬需求数 / 硬需求总数，也可按需求加权。
- **几何保真度**：
  - 体积 IoU：在 OCC 里做精确布尔，vol(A∩B)/vol(A∪B)；
  - Chamfer 距离 / Hausdorff 距离，按包围盒对角线归一化；
  - 分轴包围盒误差（不排序）、体积和表面积相对误差。
- **拓扑正确性**：实体数、亏格或贯穿孔数（Betti 数）、按类型统计的面数（平面/圆柱/圆锥/环面）、孔径和圆角半径的直方图匹配。
- **可编辑性 / 设计意图**（针对 L5 和产品级任务）：追加一句编辑指令，检查模型是否正确再生，并且未改动的区域 IoU 保持不变（“局部性”）；特征树有没有冗余或失败特征。

**C. 装配 / 功能指标**
- 组件与配合有效，没有过定义或欠定义；自由度与期望一致。
- 驱动输入后沿行程采样：输出位移/角度误差、传动比、闭环误差。现有 `kinematic_acceptance` 可以直接复用。
- 全行程干涉扫描，而不是只检查一个姿态。

**D. 效率指标（单独报告，不进质量分）**
- tokens（prompt/completion）、费用、端到端时间、LLM 调用次数、Agent 轮数、工具调用失败次数和每次成功所需的修复次数。
- 在“质量–成本”平面上画 Pareto 前沿，而不是合成一个分数。

**E. 过程指标（Agent 行为）**
- 首次执行成功率、修复成功率（失败后 N 轮内修好的比例）、重复失败动作的比例、提前 finish 或错误 blocked 的比例。
- 协议合规率：schema 校验失败、空计划、格式错误。这部分要和建模能力分开统计。

### 3.6 统计与报告方法

1. 每个（模型, case, prompt 变体）至少跑 3 次，最好 5 次。
2. **bootstrap 95% 置信区间**：对 case 重采样，给出通过率、RSR、IoU 的区间。
3. **配对比较**：两个模型在同一批 case 上比较时，通过/不通过用 McNemar 检验，连续指标用 Wilcoxon 符号秩检验。只有在显著时才说“A 优于 B”。
4. 按能力标签和层级分组报告，同时报告宏平均（按层级等权）和微平均（按 case）。
5. 报告里固定写明：评测版本哈希、harness 等级、模型 ID 和版本日期、温度、最大 token、超时、是否冷缓存、Provider、测试日期。

### 3.7 评测协议

- **冷缓存**：禁止读缓存。开发时可以打开缓存，但结果文件要打上 `cache_used` 标记，并且拒绝被汇总。
- **Harness 分档**，同一模型跑三档，差值即为“修补贡献”：
  - `raw`：Single-turn，无任何确定性修补（关闭 `stabilize_three_view_blueprint`、射线自动反转、faceIndex 替换）；
  - `agent`：标准 Multi-turn，只允许使用通用反馈（观察 + 错误码 + 基于 Spec 的检查）；
  - `full`：产品配置，所有修补都打开。
- **修补记录**：凡是启发式改写了 LLM 输出的地方都要记日志（例如“蓝图被 IR 合成替换”“射线被 faceIndex 替换”），报告里给出“LLM 原始输出直接可执行的比例”。
- **基础设施失败单独处理**：Provider 5xx、网络超时、usage 缺失标为 `infra_error`，在同一配置下最多重试 N 次，并记录重试次数；重试后仍失败的，在主表里单独一列展示，不算进模型的 0 分。模型自身的超时（例如生成超过 T 秒）则算模型失败，T 需要预先固定。
- **环境隔离**：每个 case 前后清场（现在已经在做），并在 case 开始时校验“打开的文档数为 0，且没有活动草图”，校验不过就重启服务。
- **几何评测离线化**：执行完导出 STEP（需要新增 `/api/export-step`，内部用 `SaveAs3` 导出为 `.step`），交给 Python 端的 OCC 评测器。评测不再依赖 SolidWorks 状态，也可以事后重算和换指标。
- **完整轨迹**：继续保存原始 messages、响应、计划、每步执行结果和最终 observation（Kimi 批次后半段已经这样做），另外保存 STEP 和渲染图，便于人工复核。

### 3.8 LLM/VLM 作为评审的使用边界

- 只用于几何探针覆盖不到的维度，例如“看起来像不像一个手机支架”“有没有明显多余的结构”。评审必须有固定 rubric、多视角渲染图和参考图，并且和人工打分做校准（例如 50 例，报告 Spearman 相关或 Cohen κ）。
- 不作为通过与否的主判据，也不用和被测模型同一家族的模型当评审。

### 3.9 针对装配体 benchmark 的重构

| 当前 | 建议 |
|---|---|
| prompt 给出每个组件的精确坐标 | L6 只给功能需求（传动比、输出方向、中心距约束、无干涉）；坐标是被测输出，按功能和几何约束检查 |
| TaskPlan 手写，postconditions 连同期望值发给 LLM | 评测侧的 GT 不下发；Agent 的里程碑由 Agent 自己基于 Spec 产生，或者评测只提供不含期望值的通用阶段骨架 |
| 零件由脚本确定性预生成 | L6 继续预生成零件（作为控制变量）；L7 要求模型生成零件 |
| 静态 40 / 功能 40 / 执行 20 | 严格通过（功能全满足）为主；静态和功能的通过率作为诊断；执行分改为过程指标，不计入质量 |
| 单姿态干涉 | 全行程采样干涉，加上 DOF 检查 |

### 3.10 在本仓库里的落地步骤

1. **C# 端**：新增 `/api/export-step`；`/api/model-observation` 里补充“当前活动草图/未闭合轮廓”状态，用于环境校验。
2. **`LLMPlanner/eval/core/`**（新目录）：
   - `case_schema.py`：Pydantic 版 case 规格（§3.4），区分 `public_dev` 和 `hidden_test`；
   - `geom_probe.py`：基于 OCP/CadQuery 的几何探针，包括 bbox_axis、through_holes、face_angle、mirror_symmetric、fillet_radius、IoU、Chamfer；
   - `runner.py`：统一的运行器，负责模型 × case × 变体 × seed 循环、harness 分档、冷缓存断言、`infra_error` 分类和重试、轨迹和 STEP 归档；
   - `report.py`：生成通过率、pass^k、RSR、IoU、bootstrap 置信区间、McNemar/Wilcoxon、能力雷达、Pareto 图。
3. **迁移现有 15 个文本测例和 12 个三视图测例**：为每个 case 用确定性脚本（或人工在 SolidWorks 里建模）生成一次参考 STEP，写出硬需求清单。现有 `samples.json` 和 `groundtruth.json` 可以作为起点。
4. **修正现有评分器的明显问题**（在新框架落地前的过渡）：
   - bbox 不再排序（prompt 里已经定义了朝向）；
   - 体积区间收紧到 ±5%，或者改成连续的相对误差计分；
   - 把 `cuts>=2` 这类计数换成位置和尺寸探针；
   - 执行分移出质量总分；
   - 装配测例中，把 postconditions 的期望值从 `current_milestone` 里剥离后再发给 LLM。
5. **建立隐藏测试集**：放在单独的目录或私有仓库；只在发版时运行；Skill 和规则的改动必须同时报告开发集和隐藏集的结果（这与 `llm-modeling-generalization.mdc` 的要求一致）。

---

## 附录 A：关键文件速查

| 文件 | 作用 |
|---|---|
| `Chapter1.ConnectToSw/Program.cs` | HttpListener、约 70 个路由、handler |
| `Chapter1.ConnectToSw/SwCommands.cs` | COM 封装：草图/特征/选面/观察/装配/机构解析求解 |
| `LLMPlanner/planner/llm_client.py` | Skill 组装、缓存、结构化输出、三视图两阶段、Agent 决策、任务分解 |
| `LLMPlanner/planner/schemas.py` | `OperationType`、`SwModelingPlan`、`Milestone`、`ModelObservation`、`ThreeViewIR` |
| `LLMPlanner/planner/agent_loop.py` | `MultiTurnAgent.run`（账本驱动）和 `run_blueprint`（蓝图 + 修复） |
| `LLMPlanner/planner/task_decomposer.py` | 从蓝图或 IR 推导里程碑和 postconditions |
| `LLMPlanner/planner/validators.py` | 确定性验收器 |
| `LLMPlanner/planner/observer.py` | 状态快照和 fingerprint |
| `LLMPlanner/planner/operation_optimizer.py` | 草图压缩、批次切分、三视图确定性修补 |
| `LLMPlanner/planner/skills/` | core / agent / recipes / input_modes 各类 Skill |
| `LLMPlanner/web/app.py` | Flask：`/api/run`（single/multi），以及各机构动画端点 |
| `LLMPlanner/eval/*.py`、`*_spec.json` | 各评测脚本和装配体规格 |
| `PERFORMANCE.md` | 历次评测结果的原始记录 |

## 附录 B：本报告引用的主要代码事实

- 包围盒排序比较：`validators.py::_check_bbox_axes`，`eval_text_singleturn_qwen.py::_dimension_score`。
- 蓝图自推导验收：`task_decomposer.py::decompose_blueprint`（按 op 类型累计 `feature_count`）。
- 验收条件对 LLM 可见：`llm_client.py::decide_agent_turn` 把 `current_milestone.model_dump(mode="json")` 放进 payload；`eval_two_stage_gearbox_multiturn.py::_task_plan` 的 postconditions 带有 `translation_mm` 和 `gear_ratios.expected`。
- 装配 prompt 含坐标：`eval_two_stage_gearbox_multiturn.py::_user_goal`。
- 三视图确定性合成：`operation_optimizer.py::stabilize_three_view_blueprint`；`web/app.py` 在 multi_turn 分支里先用它合成，再决定是否调用 LLM。
- 评分权重：文本 25/30/20/25（`score_case`）；装配 40/40/20（`eval_assembly_model_benchmark.py::score_agent_run`）。
