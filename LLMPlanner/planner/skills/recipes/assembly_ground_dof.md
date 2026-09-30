---
name: assembly_ground_dof
description: 固定基座并检查每个组件剩余自由度
triggers: [固定, ground, 自由度, 过定义, 欠约束]
applies_to: [assembly]
---

## 固定与自由度

1. 第一个结构件（机体、底板）插入后立刻 `ground_component`。其余零件不要固定，用配合约束。
2. 每加一个配合后 `get_assembly_state`，看 `dof`：
   - `under`：该件还能动，继续加需要的转动副/移动副/啮合。
   - `fully`：该件已完全约束。齿轮、曲柄此时通常不应完全锁死。
   - `over`：过定义，`delete_mate` 刚加上的那个，换一种对齐或少加一个距离配合。
3. 机构里故意留下的自由度：轴的自转、活塞的往复、连杆的平面摆动。不要为了“全部 fully”而把运动副锁死。
