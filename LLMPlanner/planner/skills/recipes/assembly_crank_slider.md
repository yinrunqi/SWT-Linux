---
name: assembly_crank_slider
description: 曲柄滑块：先固定机体，再曲柄、连杆、活塞
triggers: [曲柄, 连杆, 活塞, 滑块, crank, slider, piston, 发动机]
applies_to: [assembly]
---

## 曲柄滑块装配顺序

1. 插入机体并固定。机体上的主轴承孔是曲柄的旋转中心，缸孔（或导向平面）是活塞的移动方向。
2. 曲柄与主轴承孔做成转动副（`revolute_joint` 或同心 + 端面重合）。
3. 连杆大头套曲柄销、小头套活塞销；两处都是转动副，端面不要贴死到过定义。
4. 活塞/滑块与缸孔同心，再用一个平面副限制它只沿缸孔轴线移动（`slider_joint`）。
5. 可用 `crank_slider` 一次建这三处连接，失败时再拆成单步 `mate`。
6. `check_motion` 转曲柄一整圈：活塞应往复，行程约等于 2×曲柄半径；连杆应摆动；机体不动。
