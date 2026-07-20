---
name: assembly_workflow
description: 通用装配体创建、组件插入、配合和机器验收
triggers: [装配, 装配体, assembly, component, mate, 配合, 齿轮传动]
applies_to: [text]
---

## 通用装配工作流

装配任务必须分为可观察的阶段，不要在组件实例名尚未知时猜测名称。

1. 每个零件先在独立零件文档中建模并 `save_part_as`；保存路径必须是绝对路径。
2. `assembly_new` 创建装配体，随后用 `assembly_add_component` 插入零件。
   插入位置单位为米。每批插入后读取 observation 中返回的真实组件实例名。
3. 第一个组件通常自动固定。需要移动或旋转时先 `assembly_unfix_component`；
   基准组件需要保持定位时用 `assembly_fix_component`。
4. 使用 `assembly_mate_coincident` 对齐组件基准面。圆柱同轴关系用
   `assembly_mate_concentric`，射线起点应放在目标中心孔内部并沿径向指向孔壁。
5. 齿轮传动用 `assembly_mate_gear`；分子、分母必须来自参数化传动比，
   不要根据组件文件名臆测。多级传动要分别建立每一对啮合关系；同轴复合齿轮
   若由两个独立组件组成，除同轴定位外还必须建立 1:1 同向同步关系；当前通用
   API 用第三个 `assembly_mate_gear` 表达这一刚性旋转同步，不能只做 concentric。
6. 结构变化后 `assembly_rebuild`，再检查 components、mates 和 transforms。
   完成前调用 `assembly_check_interference` 和 `assembly_save`。

配合失败时先检查组件真实名称、射线是否命中目标圆柱面、组件是否被固定以及
是否形成过定义；每次只改变一个可验证因素。
