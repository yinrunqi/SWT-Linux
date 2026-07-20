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

## 三视图前 5 例 Multi-turn 评测（2026-07-18）

评测脚本：`LLMPlanner/eval/eval_three_view_multiturn.py`  
结果文件：`LLMPlanner/eval/three_view_multiturn_result.json`  
Web Demo 默认模型：Vision `GLM-4.6V`，Plan `Qwen3.5-397B-A17B`

| Case | 结果 | 轮数 | 操作 | Tokens | 说明 |
|------|------|------|------|--------|------|
| B01 L 型支架 | ❌ blocked | 1 | 1/2 | 198,874 | Vision 重试 6 次；生成蓝图执行失败，未形成实体 |
| B02 带 2 孔平板 | ✅ completed | 2 | 8/8 | 55,854 | 尺寸、体积均通过 |
| B03 T 型支架 | ❌ blocked | 3 | 11/11 | 89,648 | 包围盒正确，体积 31,500 vs 22,500mm³ |
| B06 门锁扣板 | ✅ completed | 3 | 13/13 | 86,253 | 尺寸、体积均在容差内 |
| B07 86 型开关面板 | ❌ error | — | — | 未返回 | Plan 输出连续不合 schema；单独重试又超时 |

- 通过率：**2/5（40%）**
- API 响应中可核算 token（B01/B02/B03/B06）：prompt **408,122** +
  completion **22,507** = **430,629 tokens**
- B07 两次失败均未返回 usage，因此总 token 只能记为 **至少 430,629**。
- 正式 5 例批次 AI Ping 账单延迟结算后约 **¥0.680836**；额外 B07
  统计重试约 **¥0.561272**。这两个金额包含 API 已计费但未返回 token usage
  的失败请求。

## Multi-turn 迭代架构修复后复测（2026-07-20）

根因并非“多轮天然更弱”，而是旧实现让 LLM 每轮重新局部规划，丢失 Single-turn
的全局几何一致性；5-action 边界会切断复杂草图；蓝图一旦失败即终止而不修复；
粗粒度 bbox/feature-count 又可能把错误拓扑误判为完成。B07 另有
`overall_size_mm=[86,86,10]` 未兼容导致 Vision 重试问题。

修复：

- 先生成完整全局蓝图，再按“选择 → 草图 → 特征”事务拆成里程碑。
- 连续直线/圆自动压缩为 `sketch_polyline` / `sketch_circles`，复杂轮廓不再跨轮。
- 每个事务执行后读取模型并验收；失败时强制 Agent 保留实体原位修复，
  而不是重新 `new_part` 或提前 `finish`。
- 最终里程碑额外检查 bbox、体积及有效实体；Vision 尺寸数组自动规范化。

| Case | 修复后结果 | 轮数 | 操作 | Tokens |
|------|------------|------|------|--------|
| B01 L 型支架 | ✅ | 1 | 4/4 | 84,068 |
| B02 带 2 孔平板 | ✅ | 3 | 7/8 | 123,980 |
| B03 T 型支架 | ✅ | 1 | 4/4 | 92,143 |
| B06 门锁扣板 | ✅ | 5 | 12/12 | 85,720 |
| B07 86 型开关面板 | ✅ | 7 | 14/14 | 85,336 |

- 分项复测汇总：**5/5（100%）**，可核算 **471,247 tokens**。
- B02 首次事务有 1 个操作失败，但随后原位修复并通过最终尺寸/体积验收，
  这是修复后 Multi-turn 相比纯顺序 Single-turn 的直接收益。
