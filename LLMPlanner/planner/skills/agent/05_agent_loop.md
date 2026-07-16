## Multi-turn 增量建模规则

- 系统会提供 `current_milestone` 和完整账本；每轮只服务该里程碑，
  `milestone_id` 必须与其 id 完全一致。
- 每轮最多输出 5 个原子操作；禁止一次输出整个任务的全部步骤。
- 首轮必须从 `new_part` 开始。
- 后续轮次基于当前半成品继续，禁止再次 `new_part`，除非当前状态已无法恢复且
  `progress_summary` 明确说明要从头重建。
- 先阅读账本、`forbidden_actions`、`last_execution`、`errors`、faces、sketches、
  featureSketchLinks、包围盒和体积，再决定下一步。
- 操作失败后必须改变参数、选面/选边方式或建模策略，禁止原样重试。
- 已完成里程碑禁止重做；不要根据 ICE 总数猜测哪个面已完成。
- 一组草图操作和对应特征操作应放在同一轮，不能让草图长时间处于未完成状态。
- 多圆草图优先使用一次 `sketch_circles`。
- `decision="finish"` 时 `actions` 必须为空；最终完成由机器验收器决定，
  不要在账本仍有待办里程碑时输出 finish。
- `decision="blocked"` 只用于缺少必要能力或连续替代策略仍失败的情况。
- `progress_summary` 只写简短、可展示的判断依据，不输出内部思维过程。
