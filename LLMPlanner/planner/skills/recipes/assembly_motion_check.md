---
name: assembly_motion_check
description: 配合驱动的运动验证与穿模排查
triggers: [运动, 干涉, 穿模, motion, interference]
applies_to: [assembly]
---

## 运动验证

1. 结构配合完成后先 `check_interference`。静态就干涉，先改中心距、相位或轴向错层，不要进入运动。
2. `check_motion(driver, sweep_deg=360, frames≥13)`。主动件必须是真正能被马达带动的轴或曲柄，并给出圆柱面引用。
3. 看返回值：
   - `interference_frames` 非空：记下哪两个组件、哪一帧。齿轮穿模 → 相位或中心距；连杆穿机体 → 轴向分层不够。
   - `stuck`：本该联动的件没动 → 缺少齿轮配合或键连接（1:1 gear）。
   - `drifted_fixed`：固定件在动 → 固定失败或配合连到了机体不该动的方向。
4. 改完删掉错误配合再查，不要在已经过定义的装配上叠新配合。
5. 连续两帧无干涉、预期件都在动，才 `save_assembly` 和 `finish`。
