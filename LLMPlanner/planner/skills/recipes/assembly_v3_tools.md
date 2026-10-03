# 配方：v3 工具的用法差异

本配方补充前面几份配方，凡与之冲突处以本配方为准。

1. **不用每步都看状态**。改结构的工具成功后会返回 `after`，包括 `mobility`、`redundant`、`over_defined`、`floating`。`insert_component` 会返回该零件的面摘要（零件局部坐标）。需要再看面时，用 `get_assembly_state(components=[...])`。
2. **关节会自动处理闭环**。`joint(type=revolute | pin_slot | slider)` 先加主约束，再判断轴向位置（滑块则是止转）是否已被其他关节定住：
   - 已定住（闭环）：不再加，返回 `note`。
   - 没定住：按 `face_a`/`face_b`、`offset` 或 `at_z` 补上。
   - 因此 `close_loop` 不再必要，与 `joint` 等价。
3. **层位要写明**。分层叠放的连杆、齿轮按题目给的 Z 范围，用 `at_z`（a 的零件原点装好后的 Z）或 `offset` 指定。不要依赖默认的前视面贴合，否则几层会压到同一个 Z 上，造成穿模或过定义。
4. **自由度诊断看 `check_dof`**。
   - `mobility` 是按全部真实配合求秩得到的三维活动度，替代 v2 的 `mobility_planar`。
   - `free` 列出每个零件相对机架还剩几个自由度。活动度偏大时，从这里找多出自由度的零件。
   - `redundant` 是冗余约束数。平面闭环有冗余是正常的，只要没有过定义。
5. **验证方式按机构选**：
   - 输入件能整周回转：`check_motion`。
   - 输入件只能在有限范围内转动或移动、机构有多个输入，或输入是直线运动：`check_pose`。用 `angle` / `offset` 临时配合把输入摆到两三个典型位形，在 `points` 里写题目给出的关键点，再调用 `check_dof(expected_mobility=设计活动度)`。
   - `finish` 接受这两种验证中的任一种。验证之后又改了结构的话，要重新验证。
6. **干涉看 `interference_pairs`**。
   - 第 0 帧就有的干涉，多半是层位或孔位错了。
   - 转到某个角度才出现的干涉，多半是相位不对，或杆件的摆动范围不对。
7. **预算**。每条工具结果都带 `budget.remaining_llm_calls`。剩余不多时，先 `save_assembly` 保存，再继续修改。
