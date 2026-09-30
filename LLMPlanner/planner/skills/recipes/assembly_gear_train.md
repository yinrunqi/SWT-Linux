---
name: assembly_gear_train
description: 圆柱齿轮系：先轴系，再套齿轮，最后啮合与运动检查
triggers: [齿轮, 减速, gear, pinion, 啮合, 传动比]
applies_to: [assembly]
---

## 齿轮系装配顺序

1. 插入机体/底板，`ground_component`。不要依赖插入坐标当最终位置。
2. 每根轴：`mount_on_shaft` 或 `revolute_joint` 与对应轴承孔同心，并做轴向定位。轴应还能绕自身轴线转动。
3. 把齿轮套到轴上（`mount_on_shaft`，需要与轴一起转时 `lock_rotation=true`）。
4. 同层啮合用 `mesh_gears`。它会检查中心距是否等于 m(z1+z2)/2，并旋转从动轮到啮合相位。外啮合默认 `reverse=true`。
5. 多级减速分别啮合每一对；同轴的两个齿轮必须 1:1 同向锁在一起。
6. `check_motion` 驱动输入轴至少一个齿周期。输出转角应等于输入 / 总传动比，转向符合外啮合次数。
