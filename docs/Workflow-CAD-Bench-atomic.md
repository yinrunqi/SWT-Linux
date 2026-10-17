# 完整 Atomic API 与 Workflow 对照（2026-10-15）

## 目的

在不改变模型服务的前提下，对比：

- **L0 Atomic API**：模型一次性生成 `SwModelingPlan`，顺序执行完整原子操作计划；没有执行后观察、修正、重试或回滚。
- **L1_v2 Workflow**：模型可根据执行结果闭环调用工具，最多 40 次 LLM 调用。

本轮 L0 使用已完整对齐 C# 零件建模能力的 `atomic_api_complete` / `part-api-v1` 动作空间。L1_v2 对照使用已记录的英文 Workflow 结果，不改动 `planner/workflow/**`。

## 公共设置

- 模型：自部署 `Qwen3.8-Flash-Next-FP8`；
- OpenAI 兼容服务，温度 0，`reasoning_effort=medium`；
- 英文题面、禁用缓存、每题独立冷启动、每题运行 1 次；
- L0 单轮最大输出为 32k token；服务端上下文窗口为 73,728 token；
- L0 任务：50 个核心题、30 个 hard 题、20 个 extreme 题，共 100 个不同任务；
- 分数以导出的 STEP 离线 rubric 为准。可导出 STEP 的 79 个任务均已离线重打分，和运行记录中的 rubric 字段逐题一致；其余 21 个任务在模型未给出可执行计划时无 STEP，记 0 分。

L0 结果：

- `LLMPlanner/eval/text_atomic_l0_qwen38flash_medium_core50_en_result.json`
- `LLMPlanner/eval/text_atomic_l0_qwen38flash_medium_hard30_en_result.json`
- `LLMPlanner/eval/text_atomic_l0_qwen38flash_medium_extreme20_en_result.json`

对应离线 rubric：

- `LLMPlanner/eval/rubric_atomic_l0_qwen38flash_medium_core50_en.json`
- `LLMPlanner/eval/rubric_atomic_l0_qwen38flash_medium_hard30_en.json`
- `LLMPlanner/eval/rubric_atomic_l0_qwen38flash_medium_extreme20_en.json`

L1_v2 对照分别来自 `text_workflow_l1v2_qwen38flash_v3_{core,hard}_en_result.json` 和 extreme 的 r3 结果；其协议与模型设置相同，但具备闭环工具调用能力。

## 结果

| 题集 | 方法 | 题数 | rubric 均分（95% CI） | 合格率 | 核心形体率 | 平均 token | 平均 LLM 调用 | 平均工具/原子操作 | 平均耗时 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Core | L0 Atomic | 50 | 61.53（[50.58, 72.89]） | 20/50（40%） | 33/50（66%） | 18.2k | 1.06 | 10.28 | 114 s |
| Core | L1_v2 Workflow | 50 | 98.50（[96.45, 100.00]） | 47/50（94%） | 50/50（100%） | 108.6k | 6.44 | 6.88 | 85 s |
| Hard | L0 Atomic | 30 | 27.40（[16.64, 38.79]） | 3/30（10%） | 13/30（43%） | 37.4k | 1.13 | 16.07 | 343 s |
| Hard | L1_v2 Workflow | 30 | 95.71（[89.30, 99.52]） | 26/30（87%） | 29/30（97%） | 247.3k | 11.93 | 16.00 | 453 s |
| Extreme | L0 Atomic | 20 | 6.11（[1.11, 13.61]） | 0/20（0%） | 3/20（15%） | 47.3k | 1.20 | 5.25 | 453 s |
| Extreme | L1_v2 Workflow | 20 | 90.90（[84.59, 96.81]） | 13/20（65%） | 20/20（100%） | 392.5k | 13.80 | 16.40 | 1,567 s |
| **全部** | **L0 Atomic** | **100** | **40.21（[32.52, 48.67]）** | **23/100（23%）** | **49/100（49%）** | **29.8k** | **1.11** | **11.46** | **251 s** |
| **全部** | **L1_v2 Workflow** | **100** | **96.14（[93.74, 98.19]）** | **86/100（86%）** | **99/100（99%）** | **207.0k** | **9.56** | **11.52** | **492 s** |

> L0 的“工具/原子操作”统计为实际执行成功的 HTTP 原子步骤；L1_v2 的“工具调用”是 workflow 工具调用。二者均反映几何执行规模，但不是完全相同的计数口径。

## 解读

1. L0 的一次性计划在简单核心件上可得到可用结果，但随几何复杂度上升明显退化：Core、Hard、Extreme 的均分依次为 61.53、27.40、6.11。
2. L0 比 L1_v2 少用约 86% 的 token（29.8k 对 207.0k），但均分低 55.93 分，合格率低 63 个百分点；闭环观察和纠错是主要差异。
3. 100 次 L0 运行中有 21 次（21%）在 32k 输出预算内只产生 reasoning、没有正文计划。为维持与 `reasoning_effort=medium` L1 协议的一致性，这些运行没有切换到 non-thinking，也没有重发同一请求，均按 0 分记录。
4. L0 总计使用 2,978,080 token，累计运行时间约 6.96 小时；L1_v2 对照为 20,695,576 token。L0 并非总是更快：复杂单轮计划可能长时间推理后仍无法产生可执行正文。

## 复现命令

```powershell
$common = @(
  '--provider','qwenlocal',
  '--model','Qwen3.8-Flash-Next-FP8',
  '--reasoning-effort','medium',
  '--max-plan-tokens','32000',
  '--action-level','L0',
  '--lang','en',
  '--repeats','1',
  '--pause-every','3',
  '--sw-restarts','2',
  '--sw-max-private-gb','12',
  '--resume'
)

venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py @common `
  --split all --output eval\text_atomic_l0_qwen38flash_medium_core50_en_result.json
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py @common `
  --split hard --output eval\text_atomic_l0_qwen38flash_medium_hard30_en_result.json
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py @common `
  --split extreme --output eval\text_atomic_l0_qwen38flash_medium_extreme20_en_result.json
```

---

## 三视图 track：L0 Atomic 与 L1_v2 compact 对照（2026-10-16）

### 协议

在同一组 50 道三视图题上补跑完整 Atomic API。由于输入是图片，L0 先调用 Vision 把图纸转成 `ThreeViewIR`，再由 Plan 一次性生成 `SwModelingPlan`，随后顺序执行 `atomic_api_complete` / `part-api-v1` 原子操作；执行过程中仍然没有观察、修正、重试或回滚。因此这里的“一次性计划”包含两个阶段的模型调用，不应与纯文本 L0 的单次调用混为一谈。

- Vision 和 Plan 都使用自部署 `Qwen3.8-Flash-Next-FP8`，中文题面、温度 0、禁用缓存、每题 1 次；
- Vision 输出上限 8k；Plan 使用 `reasoning_effort=medium` 和 32k 输出上限；
- 模型在 32k 内只产生 reasoning、没有计划正文时按能力失败记 0 分，不降低推理强度或重发请求；
- Qwen、网络或 SolidWorks 外部故障才允许重跑。本轮外部故障为 0；
- L1_v2 对照为 `three_view_l1v2_qwen38_compact_v2_20261016`，题集、模型、题面语言和离线打分协议相同。

### 结果

| 指标 | L0 Atomic | L1_v2 compact | 差异（Atomic − compact） |
|---|---:|---:|---:|
| rubric 均分 | **61.4**（95% CI [51.2, 71.2]） | **96.4**（[93.6, 98.7]） | −35.0 |
| pass@1 | **18/50（0.36）** | **42/50（0.84）** | −24 题 |
| core_form | **41/50（0.82）** | **50/50（1.00）** | −9 题 |
| 显式条目通过 | 209/349 | 337/349 | −128 |
| 图像分均值 | **71.0**；16 题为 100 | **97.6**；41 题为 100 | −26.6 |
| 总 token / 每题平均 | **1,565,565 / 31.3k** | **6,835,709 / 136.7k** | −77.1% |
| LLM 调用 | **107**（50 Vision + 57 Plan） | **349** | −69.3% |
| 累计耗时 | **2.88 h** | **3.14 h** | −8.2% |

L0 按题型的结果：

| 类型 | 题数 | rubric | pass@1 | 图像分 |
|---|---:|---:|---:|---:|
| 基础零件 | 9 | 86.3 | 0.556 | 83.3 |
| 组合零件 | 4 | 82.5 | 0.750 | 83.1 |
| 投影基础零件 | 18 | 64.5 | 0.333 | 77.5 |
| 投影组合零件 | 19 | 42.2 | 0.211 | 56.5 |

### 计划生成和执行情况

- 47 题生成计划和 STEP；`d15_motor_mount`、`d19_hinge_leaf`、`d29_hanger_bracket` 在 32k Plan 预算内没有返回计划正文，3 题均记 0 分。
- 共 107 次 LLM 调用、1,565,565 token。Vision 固定 50 次、238,298 token；Plan 57 次、1,327,267 token。多出的 7 次 Plan 调用来自结构化输出解析重试，不是执行后的几何纠错。
- 模型计划了 787 个原子操作，其中实际执行 666 个：651 个成功、15 个失败；首个失败后有 121 个后续操作未执行。这里的“失败”和“未执行”分开统计，不能把 `计划数 − 成功数 = 136` 全部算作 API 调用失败。
- 32 题的已执行原子操作全部成功，但只有 18 题通过全部 rubric。这说明主要瓶颈不只是 Atomic API 执行错误，更是一次性计划对图纸几何、尺寸和特征关系的理解不够准确。

### 结论

三视图 L0 Atomic 将 token 降低 77.1%，但 rubric 均分下降 35.0 分，pass@1 下降 48 个百分点。并且耗时只降低 8.2%：复杂单轮计划的长时间 reasoning 和大量 SolidWorks 原子操作抵消了调用次数减少的优势。随着题目从基础件变为投影组合件，rubric 从 86.3 降到 42.2；执行后观察、截图核对和局部返工是 L1_v2 保持性能的关键。

### 复现与产物

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py `
  --action-level L0 --mode single_turn --provider qwenlocal `
  --plan-model Qwen3.8-Flash-Next-FP8 --vision-model Qwen3.8-Flash-Next-FP8 `
  --reasoning-effort medium --max-plan-tokens 32000 --lang zh --repeats 1 `
  --name three_view_atomic_l0_qwen38_medium_20261016 --resume `
  --sw-restarts 3 --sw-max-private-gb 12

venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view `
  --artifacts eval\artifacts\three_view_atomic_l0_qwen38_medium_20261016 `
  --output eval\artifacts\three_view_atomic_l0_qwen38_medium_20261016\scores.json

venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval\rubrics_three_view `
  summary eval\artifacts\three_view_atomic_l0_qwen38_medium_20261016\scores.json --repeats 1
```

完整结果位于 `eval/artifacts/three_view_atomic_l0_qwen38_medium_20261016/`：50 份 trace、47 份 STEP/SLDPRT、`runs.json`、`scores.json` 和 `summary.json`。缺失的 3 份 STEP 对应上面的模型输出失败，并非产物丢失。

---

## 装配 track：L0 Atomic 与 Workflow v3 对照（2026-10-17）

### 协议

在装配 benchmark 全部 50 个测例（7 个核心、20 个 bench、23 个 bench2）上运行
`gen × 1 repeat`：

- 模型为自部署 `Qwen3.8-Flash-Next-FP8`，中文题面，温度 0，
  `reasoning_effort=medium`，禁用缓存；
- 零件阶段直接复用 `atomic_api_complete` / `part-api-v1`，每个零件生成一份
  单轮 `SwModelingPlan`；
- 装配阶段使用 `assembly-api-v1`，只开放插入组件、设置单组件位姿、固定/解除固定、
  创建或删除单条 mate、保存装配体；不开放 `joint`、`mesh_gears`、
  `crank_slider`、`close_loop` 等聚合工具；
- 每个装配只有一个逻辑规划轮次，执行结果不反馈给模型。输出上限初始为 16k；
  与 workflow 截断协议一致，`finish_reason=length` 时放宽到 32k 重试一次；
- 评分与 Workflow v3 完全相同：零件接口 rubric、装配 rubric、运动评分、
  `structure_ok`、`motion_score_gated` 和 e2e 均沿用
  `eval/eval_assembly_scratch.py` 的现行实现。

结果文件为
`LLMPlanner/eval/assembly_atomic_l0_qwen38flash_all50.json`，产物在
`LLMPlanner/eval/artifacts/assembly_atomic_l0_qwen38flash_all50/`。
整批 50 例均完成评分，无 `infra_contaminated`。

### 结果

| 范围 | 测例 | 零件合格率 | 装配 rubric | 运动分 | 门控运动分 | 结构成立 | e2e |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全部 | 50 | 0.743 | 22.80 ± 19.19 | 4.11 ± 7.98 | 0.00 | 0/50 | 0/50 |
| 核心 | 7 | 0.720 | 23.43 | 2.38 | 0.00 | 0/7 | 0/7 |
| 扩展（bench） | 20 | 0.710 | 24.92 | 4.27 | 0.00 | 0/20 | 0/20 |
| 第二批（bench2） | 23 | 0.780 | 20.77 | 4.50 | 0.00 | 0/23 | 0/23 |

与相同模型的 Workflow v3 全 50 例结果对照：

| 指标 | L0 Atomic | Workflow v3 | Atomic − Workflow |
|---|---:|---:|---:|
| 零件合格率 | 0.743 | 0.998 | −0.255 |
| 装配 rubric | 22.80 | 90.93 | −68.13 |
| 运动分 | 4.11 | 73.83 | −69.72 |
| 门控运动分 | 0.00 | 70.72 | −70.72 |
| 结构成立 | 0/50 | 43/50 | −43 |
| e2e | 0/50 | 27/50 | −27 |
| 平均装配 LLM 调用 | 1.50 | 68.90 | −97.8% |
| 平均总 token / 例 | 139,981 | 3,030,434 | −95.4% |

Token 合计 **6,999,051**：零件阶段 5,715,101，装配阶段 1,283,950；
输入 4,550,585，输出 2,448,466。实际 API 请求共 536 次，其中零件 461 次、
装配 75 次。50 个装配中 25 个在 16k 内返回，25 个触发 32k 重试。
结果记录中的零件和装配累计耗时分别约 5.49 h 和 7.96 h。

### 失败分析

- 458 份零件中 341 份通过接口 rubric（74.3%）；393 次计划执行到结束，
  65 次原子执行失败。失败集中在切除拉伸 54 次、基础拉伸 8 次、圆周阵列 3 次。
- 44 个装配生成了可执行计划，但都在首个失败原子操作处停止：
  42 个为几何引用解析失败（主要是模型指定的圆柱面不存在），2 个为
  SolidWorks 特征/配合建立失败。因此没有计划执行到最后的 `save_assembly`。
- 另有 6 个装配在 32k 重试后仍因输出截断而没有合法计划：
  `radial_engine_4cyl`、`quick_return_shaper`、`jansen_leg`、
  `lambda_conveyor`、`planetary_mixer`、`scissor_lift_3stage`。
- 13/50 的原始运动分大于 0，但没有一例同时满足组件完整、连到机架且活动度正确，
  所以 `structure_ok` 全部为 false，门控运动分和 e2e 均为 0。

这说明装配比单零件更依赖闭环：一次错误的实体引用会让剩余整份计划无法执行，
而没有执行后状态时，模型也无法修正配合面、补齐缺失组件或调整活动度。
L0 将平均 token 降低 95.4%，但装配与运动指标均大幅下降；Workflow 的语义引用、
聚合关节、状态观察和局部返工是装配成功的关键。

### 复现命令

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_assembly_scratch.py `
  --action-level L0 `
  --cases gearbox,engine,desk_lamp,umbrella,drill_gearbox,sewing_machine,projector,bench,bench2 `
  --condition gen --repeats 1 `
  --provider qwenlocal --model Qwen3.8-Flash-Next-FP8 `
  --reasoning-effort medium --max-part-plan-tokens 16000 --asm-max-tokens 16000 `
  --output eval\assembly_atomic_l0_qwen38flash_all50.json `
  --output-dir eval\artifacts\assembly_atomic_l0_qwen38flash_all50 `
  --resume
```
