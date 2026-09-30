---
name: assembly_joint_mapping
description: 一对零件一个关节：关节语义到 SolidWorks 配合的映射
triggers: [配合, 关节, 转动, 滑动, 铰链, 固定, 键, joint, mate]
applies_to: [assembly]
---

## 关节映射

依据：91% 的零件对之间只有一条配合或一个关节；多条配合叠加时，常出现冗余组合（例如平面+滑动），在 SolidWorks 里容易过定义。

1. **每对零件只选一个关节语义**，用 `joint(type=…)` 一次展开，不要逐条堆 `mate`。映射如下：

   | 关节 | 展开 | 典型接口 |
   |---|---|---|
   | fixed | 圆柱同心（锁转）+ 端面贴合；只给组件名时整体 lock | 孔对轴、面对面 |
   | revolute | 圆柱同心 + 端面贴合（或 `offset` 轴向距离） | 孔对轴 |
   | cylindrical | 只同心 | 可转且可沿轴滑的套 |
   | slider | 圆柱同心或平面贴合 + 一个止转面 `guide_a/guide_b` | 轴在套里、导轨 |
   | planar | 两平面贴合 | 平面滑块 |
   | ball | 两球面同心 | 球头 |
   | pin_slot | 槽口 + 端面贴合 | 销在槽里 |

2. **接口优先取“孔/轴圆柱面 + 端面”**。
   - 旋转副最常见的引用是两条圆边的圆心对齐（Fusion 中占 35%），在 SolidWorks 里等价于柱面同心加端面重合。
   - 用 `cyl(d=…)` 按直径选孔或轴，端面用 `face(±轴, outermost)` 或默认基准面。
3. **随轴一起转的零件用 fixed**。齿轮、飞轮、凸轮、曲柄等要与轴一起转时，用 `mount_on_shaft(lock_rotation=true)` 或 `joint(type=fixed)`，不要只做同心。
   - 否则零件会在轴上空转，运动检查里表现为 `stuck`。
4. **滑动副默认加行程限位**：给 `limit_min/limit_max` 和两个垂直于运动方向的面 `travel_a/travel_b`。依据：Fusion 中 31% 的滑动副启用了限位，旋转副只有 9%。
5. **零件外形不能决定关节类型**。按外形分类后，各类零件对的配合分布几乎相同，关节要由需求和接口决定。
   - 需求没说能动的连接，一律按 fixed 处理。
   - 需求说能转、能滑的连接，才建运动副。
6. 每个 `joint` 返回后看 `get_assembly_state` 的 `dof` 与 `joints`：
   - 出现 `over` 时删掉这组配合，换一个约束更少的关节（例如 revolute 换成 cylindrical）；
   - 不要在过定义的状态上继续叠加配合。
