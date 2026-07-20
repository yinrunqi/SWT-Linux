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

## 通用化清理后的无答案 / 自然需求复测（2026-07-20）

本轮移除了生产 Prompt、Skill 和运行时中的 benchmark 答案，并用两种输入政策区分
“图纸本身足够”与“图纸未完整标注用户需求”的情况：

- B01–B03：`minimal_generic_hint`，只要求读取三视图，没有物体级提示。
- B04–B05：`natural_requirements_hint`，只提供用户可见的几何规格，不提供 API、
  操作顺序、射线坐标或 golden steps。B04 的 14×14 内轮廓容易被 Vision 误认为孔；
  B05 图中没有完整标注 12×12×3mm 盲槽，因此纯图片无法唯一确定 ground truth。
- 所有调用禁用缓存；每个 case 前调用 `/api/close-all-documents`。
- B02 验收新增切除草图圆数量检查，避免“错误方向的单孔恰好得到相同体积”误通过。

| Case | 输入政策 | 结果 | 轮数 | LLM calls | 操作 | Tokens | Duration |
|------|----------|------|------|-----------|------|--------|----------|
| B01 L 型支架 | minimal | ✅ | 1 | 2 | 4/4 | 16,532 | 80.23s |
| B02 带 2 孔平板 | minimal | ✅ | 2 | 3 | 7/7 | 21,554 | 155.47s |
| B03 T 型支架 | minimal | ✅ | 1 | 2 | 4/4 | 18,551 | 88.15s |
| B04 教学键帽 | natural requirements | ✅ | 1 | 2 | 4/4 | 16,293 | 84.17s |
| B05 完整键帽 | natural requirements | ✅ | 6 | 1 | 11/11 | 5,465 | 68.42s |

- 最终可复现结果：**5/5（100%）**，共 **78,395 tokens**，约 **¥0.116329**，
  总耗时 **476.44s**。
- B05 只调用一次 Vision；IR 已包含完整参数时，由通用 IR compiler 生成拔模棱柱、
  盲槽和圆角事务，不再支付冗余 Plan 调用。
- B01/B03 的 L/T 凹轮廓同样由尺寸关系生成连续 polyline；规则按几何类别参数化，
  不含测例 ID、固定坐标或 ground truth 查询。

迭代诊断期间，具有 usage 回执但未进入最终表格的失败/旧验收运行合计
**390,942 tokens，约 ¥0.620345**；另有主动中止的长重试没有可靠 usage 回执，
不计入该数字。主要浪费来自 Vision schema alias 重试、Vision/Plan 阶段 Prompt
未隔离，以及 schema-valid 但空步骤的 Plan。对应修复为：

- Vision 只接收图纸解析规则，不再注入 SolidWorks operation 手册。
- 归一化 provider 常见的顶层、尺寸、视图、特征、位置和 operation aliases。
- provider 一次不遵守 structured schema 后，本请求后续直接走 JSON mode。
- `through_all` 自动补齐 C# 传输层仍要求的正 depth。
- 对凹多边形、T 形轮廓和拔模棱柱使用 IR 参数化编译；无法确定的形状才调用
  Stage B Plan LLM。

## 参数化双级减速箱 Multi-turn 装配评测（2026-07-20）

评测脚本：`LLMPlanner/eval/eval_two_stage_gearbox_multiturn.py`

规格：`LLMPlanner/eval/two_stage_gearbox_spec.json`

模型：`Qwen3.5-397B-A17B`，禁用缓存。

对象由安装底板、输入 12 齿小齿轮、中间轴 24/12 齿复合齿轮和输出 36 齿
大齿轮共 5 个组件组成。两级减速比为 2:1 和 3:1，中间轴两个独立组件用
1:1 同向同步，总减速比 6:1。评测只把零件路径、装配位置和功能关系作为
用户可见需求提供给 Agent，不提供隐藏 golden steps。

| 运行 | 结果 | 可核算 Tokens | 主要发现 / 修复 |
|------|------|---------------|-----------------|
| 协议基线 | blocked | 34,800 | `act` 缺省 actions 被 Pydantic 默认值漏检 |
| 严格协议重试 | error | 未可靠回执 | 模型长分析耗尽 4k 输出；固定 JSON 键顺序并限制摘要 |
| 首次真实插入 | blocked | 50,992 | `filePath`/`partPath` 别名与 3 轮恢复预算不足 |
| 精确位置前 | blocked | 79,382 | `AddComponent5` 坐标是包围盒近似值，误用重合配合修位置 |
| Mate 类型验收 | blocked | 125,575 | 已有两级 Gear Mate，只缺中间轴 1:1 同步 |
| 最终冷启动 | ✅ completed | 94,433 | 7 轮完成全部结构、传动和保存验收 |

最终运行：

- Prompt **90,469** + Completion **3,964** = **94,433 tokens**，7 次 LLM 调用。
- Agent 用时 **128.4s**；含准备检查和窗口清理总耗时 **131.0s**。
- 零件首次参数化生成耗时 **33.6s**，不调用 LLM；正式运行复用已生成零件。
- 5/5 组件精确定位；3 个 Gear Mate 的归一化比值为 `{1, 2, 3}`；
  2 个 Coincident Mate；原生干涉检测为 **0**。
- 保存后重新打开 `.SLDASM` 冷后验：组件 5、配合 5，齿轮比分别
  `12:24`、`12:36`、`1:1`，全部未压制。
- 完成后 `/api/close-inactive-documents` 关闭 5 个预加载零件窗口，只保留最终
  装配体，验证了测试期间不累积无用零件/装配窗口的稳定性原则。

本轮可核算的失败迭代共 **290,749 tokens**；严格协议重试因旧评测脚本在异常时
未保存 usage，不并入该数。后续脚本已保证 error 状态也写入 token_usage。

### Web Demo Gear Mate 与安全缓存修复

首次 Web 手工运行暴露了两个通用问题：LLM 需要猜中心孔射线，连续选面失败后
在 `drive_relations` 阻塞；同时失败 AgentTurn 在执行前就被缓存。该次运行结果为
`blocked`，9 轮、8/15 操作成功，消耗 **139,029 tokens**。

修复后：

- Gear Mate 只接收组件名和传动比；C# 根据组件 Transform 自动选中心孔圆柱面。
- AgentTurn 仅在所有操作执行成功后提交缓存；失败执行会删除对应缓存。
- 缓存 observation 使用语义状态：忽略 COM 矩阵微小波动，并归一化等价齿轮比
  （如 `1:2` 与 `12:24`），但 SolidWorks 操作和机器验收仍逐次执行。
- 装配操作兼容 provider 的扁平参数格式，并在执行前检查必要参数。

| 运行 | 结果 | 轮数 | 操作 | Tokens | Duration |
|------|------|------|------|--------|----------|
| 修复后冷启动 | ✅ completed | 4 | 14/14 | 50,842 | 90.1s |
| 相同测例自然缓存 | ✅ completed | 4 | 14/14 | **0** | 39.9s |

两次均通过最终验收：组件 5、Mate 5、Gear 比 `{1,2,3}`、干涉 0。另用纯
SolidWorks API 单独验证了不带任何射线参数创建 3 个 Gear Mate，返回比例
`12:24`、`12:36`、`1:1`。

## 单缸曲柄滑块正时发动机 Multi-turn 评测（2026-07-20）

评测脚本：`LLMPlanner/eval/eval_single_cylinder_engine_multiturn.py`
规格：`LLMPlanner/eval/single_cylinder_engine_spec.json`

测例由机架、缸体、活塞、连杆、曲柄、飞轮、两只正时齿轮、凸轮轴和凸轮共
10 个组件组成。曲柄半径 25mm、连杆中心距 90mm、理论行程 50mm；16/32 齿
正时传动使凸轮反向半速。生产 Skill 只描述可复用的曲柄滑块和同步传动规则，
具体尺寸、组件路径和验收采样点仅存在于评测层。

实现与验收：

- 新增通用 `assembly_set_component_pose` 和
  `assembly_set_crank_slider_pose`；后者解析求解任意给定滑动轴/旋转轴的平面
  曲柄滑块，并支持多个按比例同步的旋转组件。
- 组件轴向层偏移和同步组件缺省位置从当前 Transform 推导；兼容
  `name/componentName`、snake_case 和 position 数组，避免 LLM 必须重复抄写
  易错坐标。
- 10 个零件确定性生成耗时 **29.9s**，不调用 LLM；每个零件保存后立即关闭。
- 静态验收通过：10 个组件、5 个未压制 Gear Mate，归一化齿比
  `[1,1,1,1,2]`，装配体已保存。
- 运动学验收在 `0°/90°/180°/270°/360°` 采样，活塞位置分别为
  `115/86.458082/65/86.458082/115mm`，每点误差 **0mm**；实际行程
  **50mm**，周期闭合，凸轮系角度为曲柄的 `-1/2`。

性能记录：

- 首次协议运行在机构姿态里程碑阻塞：10 次 provider 调用，
  **164,692 tokens**，323.97s。根因为同步组件输出 `name` 且省略位置，
  同时未提供连杆/活塞轴向层偏移；该失败直接推动了上述通用参数推导修复。
- 修复后运行完成：2 次未缓存 provider 调用，Prompt **33,033** +
  Completion **2,144** = **35,177 tokens**，244.68s；其余决策命中此前的
  成功缓存。
- 完全相同的再次冷重建：6 轮、20/20 操作成功，provider 调用 **0**、
  **0 tokens**；Agent 用时 **200.41s**，含零件复用检查和窗口清理总耗时
  **207.10s**。SolidWorks 操作、observation、五角度运动学验收和保存仍全部
  重新执行。
- 机构姿态更新通过单次组件枚举、禁用中间图形刷新和跳过无意义 Mate Rebuild，
  单角度 API 耗时由约 **30s** 降至约 **11s**。

## 复古 16mm 电影放映机 Multi-turn 评测（2026-07-20）

评测脚本：`LLMPlanner/eval/eval_film_projector_multiturn.py`
规格：`LLMPlanner/eval/film_projector_spec.json`

测例共 15 个组件和 5 个 Gear Mate。连续主动拨盘在 120°–240°输入相位内
平滑推动四槽分度轮，其余区间锁止；每个输入周期输出 90°，四周期完成一圈。
快门和风扇属于连续同步件，送片轮与双卷盘属于分度同步件。

实现与验收：

- 新增通用 `assembly_set_indexed_rotary_pose`，支持任意槽数、啮合区间、
  连续同步件和分度同步件；绝对输入角可超过 360°，不会丢失已完成工位。
- 10 个输入角全部达到 **0°角误差**；停歇锁止、啮合状态、快门相位、
  风扇两倍速、卷盘四分之一速和 1440°输入后的 360°输出均通过。
- 15 个零件确定性生成耗时 **44.96s**；每个零件保存后关闭。
- Web 确定性姿态实测：输入 180°，输出 **45°**，`engaged=true`。
- 保存装配体的独立 0-token 复验通过，耗时 **71.25s**。

协议修复与性能记录：

- 首次探索运行完成组件和 Mate 后，分度 operation 使用未覆盖的组件名别名，
  8 次 provider 调用、**137,759 tokens**、186.28s 后协议校验终止。
- 补充通用组件名、position 数组和同步组件别名后，模型仍曾返回
  `decision=act` 但省略 `actions`；该次 2 调用、**38,763 tokens**、55.80s。
- 将盲目的 temperature=0 协议重试改为携带“原始无效 JSON + 精确 Pydantic
  错误”的纠错请求后运行完成：6 次 provider 调用，Prompt **112,352** +
  Completion **3,878** = **116,230 tokens**，158.90s。
- 完全相同的再次重建和验收：所有成功决策命中语义状态缓存，provider 调用
  **0**、**0 tokens**，Agent 用时 **96.68s**。SolidWorks 的 15 组件插入、
  5 个 Mate、10 点分度采样、保存和关闭 15 个非活动窗口仍全部重新执行。

## AI Ping 三模型复杂装配体 Multi-turn 对比（2026-07-20）

运行器：`LLMPlanner/eval/eval_assembly_model_benchmark.py`

从 `https://aiping.cn/api/v1/models` 的实时可用列表选择三个不同模型家族：
`Qwen3.5-397B-A17B`、`GLM-5`、`MiniMax-M2.5`。三个模型分别运行双级
减速箱、单缸发动机和 16mm 放映机，共 9 次。

公平性约束：

- 所有 LLM 决策禁用缓存；温度为 0，每个“模型 × 测例”只运行一次。
- 参数化零件文件确定性复用，但装配体、Mate、observation 和机器验收全部重做。
- 每个测例开始前调用 `/api/close-all-documents`；单测例硬超时为 900s。
- 质量分不包含速度或 token：静态机器验收 40 分，机构/功能验收 40 分，
  执行完成度与可靠性 20 分。协议错误后若最终 SolidWorks 产物仍可观察，
  允许补做确定性机器验收，但错误状态不变且执行分为 0。

| 模型 | 测例 | 分数 | 最终通过 | 状态 | Tokens | Agent耗时 |
|---|---|---:|:---:|---|---:|---:|
| `Qwen3.5-397B-A17B` | 双级减速箱 | **78.08** | 否 | blocked | 313,183 | 766.92s |
| `Qwen3.5-397B-A17B` | 单缸发动机 | **100.00** | 是 | completed | 136,198 | 212.24s |
| `Qwen3.5-397B-A17B` | 16mm 放映机 | **100.00** | 是 | completed | 142,328 | 210.77s |
| `GLM-5` | 双级减速箱 | **100.00** | 是 | completed | 156,921 | 379.24s |
| `GLM-5` | 单缸发动机 | **100.00** | 是 | completed | 234,517 | 469.33s |
| `GLM-5` | 16mm 放映机 | **0.00** | 否 | 900s timeout | 未回传 | 902.41s |
| `MiniMax-M2.5` | 双级减速箱 | **0.00** | 否 | protocol error | 40,492 | 41.91s |
| `MiniMax-M2.5` | 单缸发动机 | **0.00** | 否 | protocol error | 45,888 | 49.53s |
| `MiniMax-M2.5` | 16mm 放映机 | **80.00** | 否 | protocol error | 255,409 | 478.91s |

| 模型 | 平均分 | 正式通过数 | 已知总 Tokens | 总耗时 |
|---|---:|---:|---:|---:|
| `Qwen3.5-397B-A17B` | **92.69** | 2/3 | 591,709 | 1,189.93s |
| `GLM-5` | **66.67** | 2/3 | ≥391,438（1 项未回传） | 1,750.98s |
| `MiniMax-M2.5` | **26.67** | 0/3 | 341,789 | 570.36s |

结果解读：

- Qwen 的减速箱生成了 4 个 Gear Mate，比值为 `[1,1,2,3]`，比期望的
  `[1,2,3]` 多出一个重复 1:1 同步关系；其余结构、位置、干涉和保存验收通过。
- GLM-5 完整通过减速箱和发动机；放映机在超时前已建立组件与传动关系，但未
  返回最终结果文件。该项 token 是“未知”，不能误读为零消耗。
- MiniMax 在减速箱和发动机上分别使用 `translation_z`、`translationZ`
  等未被协议归一化的字段，结构化重试后终止。放映机已形成可通过全部静态和
  10 点分度验收的最终产物，获得 80 分产品完成度；但 Agent 在最终分度
  operation 再次违反 schema，因此正式状态仍为 error、执行分为 0。
- 该排名衡量当前系统的端到端能力，包括模型推理、结构化输出兼容性、修复策略
  和 SolidWorks 执行结果，不应解释为模型本身的纯推理能力排名。

按 AI Ping 模型列表价格区间估算，已回传 usage 的费用约为：Qwen
**0.629–0.944**、GLM-5 至少 **1.885–2.714**（放映机 usage 未回传）、
MiniMax **0.868**；实际计费以 AI Ping 账单及所选供应商为准。
