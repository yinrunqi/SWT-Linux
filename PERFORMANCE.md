# Multi-turn 账本 / Observer 评测记录

评测脚本：`LLMPlanner/eval/eval_modes_three.py`（`evaluation_version: 3`）  
结果文件：`LLMPlanner/eval/modes_three_result.json`  
Provider：AI Ping · 模型：`Qwen3.5-397B-A17B` · SolidWorks HTTP API：`localhost:5100`

## 结果摘要（2026-07-16）

| Case | Mode | Acceptance | Turns | LLM calls | Actions (ok) | Duration |
|------|------|------------|-------|-----------|--------------|----------|
| 简化骰子 | single_turn | ✅ | 1 | 1 | 11/11 | 4.1s |
| 简化骰子 | multi_turn | ✅ | 2 | 2 | — | 41.1s |
| 标准骰子 | single_turn | ✅ | 1 | 1 | — | 10.2s |
| 标准骰子 | multi_turn | ✅ | 11 | 11 | 38/38 | 171.5s |
| 收纳盒 | single_turn | ✅ | 1 | 1 | 11/11 | 5.0s |
| 收纳盒 | multi_turn | ✅ | 3 | 3 | — | 32.8s |

## 标准骰子 Multi-turn 硬验收

- Agent `status=completed`（由里程碑账本 + 确定性验收器裁定，非 LLM `finish`）
- 包围盒 20×20×20mm；主体拉伸 + 12 边 R1.5 圆角
- 六个语义面 `{+Y,-Y,+Z,-Z,+X,-X}`，圆数量集合恰为 `{1,2,3,4,5,6}`
- 六次切除深度均为 1.5mm；`ICE >= 6`；无失败操作
- 中间轮次允许对同一里程碑失败后换策略；最终 8 个里程碑验收均通过

## 实现要点

- 结构化观察：`GET /api/model-observation`（面 / 草图 / 特征关联 / fingerprint）
- 批量圆：`POST /api/sketch/circles`（`sketch_circles`）
- 账本：`milestone_ledger` + `compressed_memory` + `idempotency`
- Recipe：`planner/skills/agent/dice_multiturn.md`（不进入 SkillRegistry，避免污染 single-turn cache）
- Single-turn skill regression cache key 保持稳定（`20a0c35b…`）
