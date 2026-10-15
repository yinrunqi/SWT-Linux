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
