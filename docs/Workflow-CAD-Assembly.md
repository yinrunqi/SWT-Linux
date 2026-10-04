# Workflow-CAD-Assembly：从零生成零件并装配

零件用 L1 `WorkflowAgent` 生成，装配用另一套工程师级工具（`planner/assembly_workflow/`）。配合驱动运动，评测器自己做 Motion Study 采样和逐帧干涉检查。

当前试点是两个案例：双级减速箱、单缸曲柄滑块发动机。原来的 7 个「脚本造件 + 原子装配」案例没有改。

## 1. 流程

```
按零件拆好的文字规格
        │
        ▼
L1（可选 spur_gear）→ 接口 rubric
        │
        ▼
装配 Workflow Agent（语义配合 / 宏工具 / check_motion）
        │
        ▼
评测器：Motion Study 采样 → 干涉、传动比或行程、固定件、可选齿侧间隙
```

三种评测条件：

| 条件 | 含义 |
|---|---|
| `gold` | 确定性脚本造的标准零件 + Agent 装配 |
| `gen` | L1 生成零件 + Agent 装配 |
| `e2e` | 与 gen 相同，但零件接口必须全部合格才进入装配；端到端成功 = 零件全过且运动检查通过 |

## 2. 装配工具

观察与放置：`get_assembly_state`、`insert_component`、`ground_component`。

语义配合：`mate`，引用写成 `shaft1:cyl(d=10)`、`base:face(+Y, largest)`、`gear:plane(front)`。类型：重合、同心、平行、距离、角度、齿轮。

宏工具：`mount_on_shaft`、`revolute_joint`、`slider_joint`、`mesh_gears`、`crank_slider`。

自检：`check_interference`、`check_motion`。`finish` 要求已经保存，并且最近一次 `check_motion` 通过。

### 2.1 两套工具与配方（v1 / v2）

系统提示词两版完全相同，只换工具表和配方，评测器也相同（`--workflow v1|v2|both`）。

| | v1（原试点） | v2（按挖掘结果补齐） |
|---|---|---|
| 配方 | `assembly_ground_dof`、`assembly_gear_train`、`assembly_crank_slider`、`assembly_motion_check` | `assembly_topology`（G1 选地、G3 从地生长、G6 自由度预算）、`assembly_joint_mapping`（G2 关节→配合映射表）、`assembly_coupling`（G5）、`assembly_linkage`（G4 闭环）、`assembly_motion_check` |
| `mate` 类型 | 重合、同心、平行、距离、角度、齿轮 | 另加槽口、齿条小齿轮、螺旋、线性耦合、锁定；同心可 `lock_rotation`，距离可带上下限 |
| 引用 | 面、基准面 | 另加 `edge(line, +X, longest)`、`sphere`、`component` |
| 宏工具 | `mount_on_shaft`（轴上固定用 1:1 齿轮配合）、`revolute_joint`、`slider_joint`、`mesh_gears`、`crank_slider` | 同左，`mount_on_shaft` 改用同心锁定旋转；新增 `joint`（fixed/revolute/slider/cylindrical/planar/ball/pin_slot）、`close_loop`（先最小配合，过定义就回滚）、`rack_pinion`、`screw_pair`、`linear_coupler`、`check_dof`（Grübler 估算 + 浮动/冗余/未接地） |

v2 的规则都来自 AutoMate 与 Fusion 360 Gallery 的统计，不来自试点案例，见 [Workflow-CAD-Assembly-Mining.md](Workflow-CAD-Assembly-Mining.md)。离线单测：`venv\Scripts\python.exe -m pytest test\test_assembly_workflow_v2.py -q`。

v3（`--workflow v3`）在 v2 上改的是工具反馈和智能体循环，不加新的机构知识。改动来自对 20 个扩展测例 v2 结果的审查（用满调用预算、截断停滞、闭环最后一个铰过定义、层位压在同一 Z 上），不针对具体测例。配方多一份 `assembly_v3_tools`。

- 状态：零件面列表按组件缓存；改结构的工具返回增量 `after`（活动度、冗余、过定义、悬空件）；`insert_component` 返回面摘要；`get_assembly_state` 默认精简，可用 `faces` / `components` 参数按需展开；对话里只保留最新一次完整状态，更早的用占位符替换。
- 关节：`joint` 先加主约束（同心或导向），再按秩判断轴向位置（滑块是止转）是否已被别的关节定住，定住了就不加并返回 `note`，所以闭环的最后一个铰不会冗余过定义；`close_loop` 与 `joint` 等价。新增 `at_z`：直接写 a 的零件原点装好后的 Z，换算成相对 b 的偏距。`errStatus=5` 时返回两条轴线当前的距离和排查提示。
- 诊断：`check_dof` 改为按 SolidWorks 里的真实配合求秩，给出三维活动度、冗余约束数、每个零件相对机架剩余的转动 / 平移自由度；`screw_pair` 建好后按约束零空间核对实际导程；同一对零件反复加删配合 3 次以上时提示；引用解析失败时列出候选圆柱面和平面。
- 验证：新增 `check_pose`（与评测器的多位姿模式同一实现），用于有限行程、多输入或直线输入的机构；`check_motion` 附 `interference_pairs`，单帧转角过大时自动加密采样；驱动件在相邻帧间跳转时返回 `sampling.driver_jump_deg` 和提示（多半是机构卡死或闭环没装上）。`finish` 要求已保存，并且 `check_motion` 通过，或 `check_pose` 通过且 `check_dof` 活动度符合。
- 智能体循环：回复因 `finish_reason=length` 截断且工具参数不完整时，丢弃这条回复、把 `max_tokens` 翻倍（最多 32000）重试，第一次截断时提示少写分析，不计入停滞；每轮最后一条工具结果附 `budget.remaining_llm_calls`，剩 5 次以内且未保存时提示先保存。

C# 端的 Animation 马达只支持旋转，所以直线输入由 `check_pose` 覆盖，C# 端不需要改。离线单测：`test\test_assembly_workflow_v3.py`（假 HTTP 层按零件位姿给出配合实体，秩法诊断能真实运行）。

### 2.2 C# 端

端点在 `/api/asmwf/*`（`SwCommands.AssemblyWorkflow.cs`）：按组件面、边序号或默认基准面选实体，加配合，Motion Study 驱动与逐帧采样。

机械配合实测（`eval/assembly_scratch/smoke_mechanical_mates.py`，结果在 `mechanical_mates_smoke.json`）：

| 配合 | 实测 | 期望 |
|---|---|---|
| 齿条小齿轮（节圆 36 mm） | 每转 113.097 mm | π·36 = 113.097 mm |
| 线性耦合（分子 1、分母 2） | b 的位移 / a 的位移 = 2.0 | 2.0 |
| 螺旋（导程 2 mm） | 每转 2.0 mm | 2.0 mm |
| 槽口、锁定、同心锁定旋转、球面同心、距离限位 | 全部建立，errorCode 0 | — |

实现上踩过的坑：

- 进程外 COM 每次调用都会触发界面刷新。一个 291 面齿轮列面要 240 s；所有 asmwf 操作包在 `ISldWorks.CommandInProgress = true` 里之后降到 1.3 s。
- 锁定配合走 `CreateMateData` 会报 errorStatus=4，改走选择 + `AddMate5(swMateLOCK)`。
- `IRackPinionMateFeatureData.set_EntitiesToMate(role, entity)` 是按角色逐个设置单个实体，传数组会 InvalidCast。
- 槽口、螺旋设置 `EntitiesToMate` 总报 errorStatus=4，改用 Mark=1 预选。预选路径下 `CreateMate` 会忽略数据对象上的数值参数，所以建好后要用 `ModifyDefinition` 写回。螺旋导程连 `ModifyDefinition` 也写不进去，要直接改配合特征上的尺寸 `D1`。
- `/api/sketch/circle` 默认不关草图推理：圆心和圆周点会吸到已有圆的象限点或折线上。齿轮孔曾从 5 mm 变成 4.738 mm；连杆第二个孔变成 r 5.657 / 6.062；偏心凸轮的孔变成 r 4，外圆圆心也跑了。现在可以传 `noInference: true`，在 `AddToDB` 下画圆。标准零件全部用这个参数，默认行为不变，以免影响 L1 结果复现。
- `/api/sketch/slot` 退化成矩形，槽口配合认不出。测试件改为矩形切除加两端圆孔切除。
- 齿轮配合语义（用 Animation 量转角实测）：`AddMate5` 的分子:分母就是 ω_b/ω_a（a 为先选的实体），但 SolidWorks 的 `Reverse=false` 才是反向转。`IGearMateFeatureData` 读回的分子/分母与 `AddMate5` 相反，原样 `ModifyDefinition` 写回会把齿轮比颠倒。旧实现在要求 `reverse=true` 时走了这条路，所以此前所有 agent 的齿轮配合都是同向、比值颠倒。现在对外统一为「分子:分母 = a 齿数 : b 齿数，reverse=true 表示外啮合反向」，`mates` 读回也按这个约定。
- 距离配合不分正负，SolidWorks 按求解结果任选一侧。实测 `align=aligned` 总把 a 放在 b 面法向的负侧，于是 `mount_on_shaft`/`joint` 的 `offset` 把齿轮装到轴外 15 mm 处、把曲柄装在离曲轴端面 34 mm 的空中，运动学照样对、几何是错的。现在 `offset` 有符号，表示 a 面相对 b 面沿 b 面法向的距离。建好后用配合实体几何量一下，反了就带 `flip` 重建。
- `mesh_gears` 原来固定给从动轮转半个齿距，只在中心连线沿 X、主动轮未转时成立；发动机正时齿轮（连线斜 16°）因此有齿面干涉。现在按中心连线方向和主动轮当前转角计算从动轮相位（零件局部 0° 是齿）。
- `IMateEntity2.ReferenceType` 才是几何类型（0 点、1 线、2 圆、3 平面、4 圆柱、5 球、7 圆锥）；`ReferenceType2` 返回选择类型。`EntityParams` 是装配坐标下的点(3)、方向(3)、半径1、半径2（米）。
- Animation 能正确解齿轮系和曲柄滑块闭环（见 5.4），但旋转马达不按 rpm 转：10 rpm 实测 47–95 rpm。传动比、行程都按驱动件实际转角算；13 帧时相邻帧转角超过 180°，展开会混叠，所以运动学改用 121 帧密采样，干涉仍只在 13 帧上做。
- 偶尔在配合求解或删除 Motion Study 后，SolidWorks 单核满载几分钟，服务无响应（同一装配体一次 65 s、一次 7 min，不是确定性的）。批量重评分脚本遇到这种情况会自动重启 SolidWorks 和服务，并重试一次。

`spur_gear` 只在装配零件生成会话里通过 `WorkflowSession(extra_tools=["spur_gear"])` 打开，50 题和困难子集的 24 工具结果可以复现。

额外零件工具集 v2（`EXTRA_TOOLSET_VERSION`）增加了 `internal_gear`：渐开线内齿圈，齿槽轮廓就是外齿轮齿形把齿顶高、齿根高对调（齿顶圆 r − m，齿根圆 r + 1.15m，齿隙 0.05m），`phase_deg` 是某个齿圈轮齿中心的方位角。装配工具 `mesh_gears` 增加了 `internal: true`：b 是齿圈，中心距必须是 m(z_b − z_a)/2，只转动外齿轮 a 去对齐相位，默认 `reverse=false`（同向）。实测 SolidWorks 的齿轮配合能正确处理行星架：太阳 12 / 行星 18 / 齿圈 48 固定时，行星架转速比 0.2、同向、零干涉。v1 的结果（只有 `spur_gear`）不受影响，测例清单和工具集版本见 [Workflow-CAD-Bench-Assembly.md](Workflow-CAD-Bench-Assembly.md)。

`insert_component` 的 `rotate_deg` 修正（2026-10-02，扩展测例试点之前）：C# 的 `set-component-pose` 按行向量约定，物理转向与 `angleDeg` 相反。`mesh_gears` 早就做了取负补偿，`insert_component` 却直接透传，所以 `rotate_deg: 30` 实际是绕 Z 转 −30°，与工具说明"绕 +Z"相反。齿轮相位只差正负号时影响不大，但闭环连杆按说明给初始角时，SolidWorks 会解到镜像装配模式（如肘节夹钳的铰点落到另一侧），角度配合再也拨不回来。现已在 `insert_component` 里同样取负，实测 `rotate_deg: 30` 读回 30°。此前的试点结果都是旧行为下跑的。

`insert_component` 的 `rotate_deg` 修正（2026-10-02，扩展测例试点之前）：C# 的 `set-component-pose` 按行向量约定，物理转向与 `angleDeg` 相反。`mesh_gears` 早就做了取负补偿，`insert_component` 却直接透传，所以 `rotate_deg: 30` 实际是绕 Z 转 −30°，与工具说明"绕 +Z"相反。齿轮相位只差正负号时影响不大，但闭环连杆按说明给初始角时，SolidWorks 会解到镜像装配模式（如肘节夹钳的铰点落到另一侧），角度配合再也拨不回来。现已在 `insert_component` 里同样取负，实测 `rotate_deg: 30` 读回 30°。此前的试点结果都是旧行为下跑的。

`insert_component` 的 `rotate_deg` 修正（2026-10-02，扩展测例试点之前）：C# 的 `set-component-pose` 按行向量约定，物理转向与 `angleDeg` 相反。`mesh_gears` 早就做了取负补偿，`insert_component` 却直接透传，所以 `rotate_deg: 30` 实际是绕 Z 转 −30°，与工具说明"绕 +Z"相反。齿轮相位只差正负号时影响不大，但闭环连杆按说明给初始角时，SolidWorks 会解到镜像装配模式（如肘节夹钳的铰点落到另一侧），角度配合再也拨不回来。现已在 `insert_component` 里同样取负，实测 `rotate_deg: 30` 读回 30°。此前的试点结果都是旧行为下跑的。

## 3. 评测协议

测例与评分规格的完整说明（含新增测例的流程）见 [Workflow-CAD-Bench-Assembly.md](Workflow-CAD-Bench-Assembly.md)。本节是摘要。

规格：`LLMPlanner/eval/assembly_scratch/{gearbox,engine}.yaml`。

零件接口 rubric：`eval/assembly_scratch/rubrics/`。

装配 rubric（`geomcheck/assembly_rubric.py`，规格在 `eval/assembly_scratch/assembly_rubrics/{case_id}.yaml`），替代原来的静态分（原规则：保存 50 + 每个组件 20，基本只看「存没存」）。只读配合定义和组件位姿，不跑 Motion Study：

- 每个配合按实体几何（`ReferenceType` + `EntityParams`）写成两零件相对旋量 (v, ω) 上的线性约束行：面–面、同心（`lockRotation` 再加一行）、线/点–面、平行、垂直/角度、锁定、螺旋；同轴 1:1 同向齿轮视为锁转；限位距离不减少瞬时自由度。
- 刚体组：两两约束的零空间维数为 0 的零件并成一组，再与 rubric 的 `bodies` 对照（`flex` 里的零件，比如销，可以归任一侧）。权重 20。
- 关节类型：两组之间所有直接配合合起来求零空间，分成 fixed / slider / revolute / screw / cylindrical / planar / ball，和 `joints` 逐对比较。权重 25。
- 活动度：所有非固定件拼成全局约束矩阵，再加齿轮耦合行 num·(u_a·ω_a) − den·(u_b·ω_b)，求瞬时活动度 6·N − rank，与目标值比较（等价于 `check_dof`，但不依赖 agent 调用它）。权重 15。
- 接地与浮动件：要求的零件固定；从固定件出发、沿非耦合配合到不了的零件算浮动件。权重 10。
- 过定义：组件约束状态为过定义类，或配合 `errorCode` ≠ 0。权重 10。
- 齿轮耦合：每对齿轮各 1/4 分，分别看两组之间有没有齿轮配合、按选择顺序的齿数比、外啮合反向，以及按位姿算的中心距 |Δt⊥u| 是否等于 m(z1+z2)/2（容差 0.2 mm）且轴线平行。权重 20。

运动检查（`geomcheck/assembly_motion.py`）由评测器驱动，不采用 Agent 自称的结果：

- 在规格指定的主动件上加旋转马达（Animation），密采 121 帧算运动学，另采 13 帧做 SolidWorks 原生干涉检测；
- 核对传动比或活塞行程、固定件是否移动、本该联动的件是否在动；
- 发动机另查正时：凸轮轴/曲轴转角比 0.5 且反向（`timing_driven`、`timing_opposite`），不过扣 15；
- 可选：用 OCC 算啮合齿轮的最小距离（有间隙且不相交）。

两个参考装配体（`build_reference_assemblies.py`，用宏工具按规格搭的阳性对照）的装配 rubric 和运动分都是 100。评测器改动以它们满分为前提。

指标：零件合格率、装配 rubric 分（`static_score` 字段沿用旧名）、运动分、端到端成功率。

## 4. 命令

先启动 SolidWorks 和 C# 服务（`run_service.bat`）。

```powershell
cd LLMPlanner

# 技术验证（配合驱动齿轮系 / 曲柄滑块）
venv\Scripts\python.exe -X utf8 assembly_smoke\spike_mate_driven_motion.py gears
venv\Scripts\python.exe -X utf8 assembly_smoke\spike_mate_driven_motion.py crank

# 建造标准零件
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\build_standard_parts.py all

# 评测（gold / gen / e2e / all；每条件 repeats 次）
venv\Scripts\python.exe -X utf8 eval\eval_assembly_scratch.py --condition gold --cases gearbox --repeats 1

# 参考装配体（阳性对照）与不重跑 agent 的重评分
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\build_reference_assemblies.py all
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_assemblies.py --reference --out eval\assembly_reference_rescore.json
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_assemblies.py --out eval\assembly_workflow_rescore.json `
    eval\assembly_workflow_compare.json=eval\artifacts\assembly_scratch `
    eval\assembly_workflow_compare_engine.json=eval\artifacts\assembly_scratch `
    eval\assembly_workflow_compare_gearbox_rerun.json=eval\artifacts\assembly_scratch_rerun
```

装配 rubric 与 workflow 的离线单测：`venv\Scripts\python.exe -m pytest test\test_assembly_rubric.py test\test_assembly_workflow_v2.py -q`。

离线单测（不占 SolidWorks）：

```powershell
venv-occ\Scripts\python.exe -m pytest geomcheck/tests/test_assembly_refs.py geomcheck/tests/test_assembly_parts.py -q
```

## 5. 试点结果

### 5.1 技术验证（2026-09-30）

`assembly_smoke/spike_mate_driven_motion.py gears`：

- 三根轴 + 四齿轮全部用同心/距离/齿轮配合装上，静态干涉为 0。
- 插入时给从动轮加了半齿相位（24 齿 −7.5°，36 齿 −5°），说明「先转相位再加配合」可行。
- Motion Study 采了 13 帧、耗时 35.7 s，每帧都能读到全部 Transform 和原生干涉。
- 马达转起来之后各齿轮都在动，但 Animation 求解器没有保持 2:1 / 3:1 的齿数比，第 2 帧起出现齿面干涉。简单的两齿轮组（`assembly_smoke/capture_gear_animation.py`）此前已经 25 帧零干涉。复杂轴系在 Animation（不是 Motion Analysis）里不能当作严格的配合求解。
- 评测器仍然逐帧读干涉和转角；传动比不达标就判失败。需要严格啮合时，可以用评测器按齿轮比推算从动轮转角，再逐帧做干涉，并在结果里标明。

`spike_mate_driven_motion.py crank`：配合都能加上。Motion Study 转动曲柄时，活塞销的 Y 坐标一直是 0，连杆也不摆。Animation 求解器解不了这个闭环。发动机评测按计划退回：由评测器按曲柄滑块公式写位姿，再逐帧做干涉检查，并在结果里标明。详细帧数据在 `LLMPlanner/log/spike_motion/`。

> 更正（2026-10-01）：本节两条「Animation 解不了」的结论不成立，见 5.4。齿轮系保持不了齿数比，是齿轮配合语义写错（同向、比值颠倒）加上 13 帧角度混叠造成的。用宏工具搭的曲柄滑块参考装配体在 Animation 下行程和正时都精确。这里的 spike 当时为什么不动，没有再复查。

### 5.2 LLM 试点

以 `eval/assembly_scratch_result.json` 为准。标准零件用 `build_standard_parts.py` 生成；完整 `--condition all --repeats 2` 按第 4 节命令跑。

### 5.3 v1 / v2 前后对比（gold 条件，2026-09-30）

配方和工具先冻结，再跑两试点 × 2 次 × v1/v2。系统提示词、评测器、调用预算（装配 50 次 LLM 调用）都相同，v1、v2 在每次重复里交替运行。原始结果在 `eval/assembly_workflow_compare*.json`，汇总在 `eval/assembly_workflow_compare_summary.json`（`eval/assembly_scratch/summarize_workflow_compare.py` 生成）。

减速箱第 2 次重复时，两个版本的运动采样都遇到 SolidWorks `RPC_E_SERVERFAULT`，随后 SolidWorks 整个 COM 断开。这属于评测基础设施故障，不计入平均；重启 SolidWorks 后补跑了一次。

| 案例 / 版本 | 有效次数 | 运动通过 | 平均运动分 | 平均静态分 | 已保存 | 用满调用预算 |
|---|---|---|---|---|---|---|
| 减速箱 v1 | 2 | 0 | 35.0 | 20.0 | 0 | 2 |
| 减速箱 v2 | 2 | 0 | 35.0 | 35.0 | 1 | 2 |
| 发动机 v1 | 2 | 0 | 12.5 | 20.0 | 0 | 2 |
| 发动机 v2 | 2 | 0 | 12.5 | 20.0 | 0 | 2 |

结论：在这个预算下，v2 没有带来可测的提升。

- 减速箱：四次有效运行的运动测量完全相同：驱动件扫过 −882°，输出传动比 1.103（期望 0.167），每帧都有齿面干涉。当时判断瓶颈在 Animation 求解器，这个判断是错的，见 5.4。v2 有一次在预算内保存了装配（静态分 50）。
- 发动机：两个版本各有一次运动分 25、一次 0 分。0 分的两次都是没来得及插入凸轮轴，评测器找不到该零件。差别在性质上：v2 第 1 次的活塞真的动了，行程 47.95 mm（期望 50，容差 2 mm，差 0.05 mm 未过）；v1 第 2 次曲柄和活塞销都卡住，行程 0。样本太少，不能据此下结论。
- 八次运行全部用满 50 次调用。v2 的工具调用更多（50–72 次对 50–52 次），失败调用也更多（6–17 次对 2–12 次）。v2 用到了 `joint`、`check_dof`，发动机上还用过一次 `close_loop`。

对比中发现的工具问题（为保持结果可复现，没有在对比后修改）：

- `mesh_gears` 不给 `module` 时，把中心距校验算成了 0.02 mm，报 FEATURE_NO_EFFECT。应改为从齿轮几何推断模数，或者明确要求必填。
- 评测器在缺少规格中某个零件时直接抛 KeyError，运动分记为 0。应改为把缺件单独列出。

### 5.4 装配 rubric 与「运动分卡在 35」的原因（2026-10-01）

减速箱四次运行都卡在 35 分，原因不在 Animation，而是工具和评测器自身的问题。按影响排序：

1. 齿轮配合写反（工具）：见 2.2。要求外啮合时，旧实现走 `ModifyDefinition` 把齿轮比颠倒，转向也变成同向。agent 拿到的运动反馈因此一直是错的。重评分时，10 个装配体里只有 3 个最后还留着齿轮配合（其中 2 个比值写反）。
2. 13 帧混叠（评测器与 `check_motion`）：马达实际转速是名义值的 5–10 倍，相邻帧转角超过 180°，展开后的比值是假的。不同运行的配合写法不同，测出的却是同一个 1.103，就是混叠所致。`check_motion` 回给 agent 的 `sample_ratio` 也因此没有意义。现在改为 121 帧密采样，按帧展开转角，回给 agent 的是每个零件相对驱动件的比值。
3. 标准零件本身有缺陷：连杆、曲柄的第二个孔和凸轮孔被草图推理画偏（见 2.2），之前所有发动机 gold 运行都用的这批零件。已用 `noInference` 重建。
4. `offset` 的方向和 `mesh_gears` 的相位（工具）：见 2.2。这两项不影响运动学比值，但会造成悬空零件和齿面干涉。

修正后，两个参考装配体的 Animation 测量都是精确的。减速箱：中间轴 −0.5000，输出 +0.1667。发动机：行程 49.998 mm（期望 50），凸轮轴 −0.5000（反向），13 帧零干涉。装配 rubric 和运动分都是 100（`eval/assembly_reference_rescore.json`）。

用新评测器给 5.3 里保存下来的 10 个 agent 装配体重评分（不重跑 agent，`eval/assembly_workflow_rescore.json`）。减速箱多了补跑那一组，第 2 次重复当时遇到的基础设施故障也不影响重评分。

| 案例 / 版本 | 次数 | 平均装配 rubric | 平均运动分 | 主要扣分 |
|---|---|---|---|---|
| 减速箱 v1 | 3 | 65.2 | 38.3 | 齿轮与轴不成刚体（键连接配合缺失），缺齿轮配合，活动度 5–15 |
| 减速箱 v2 | 3 | 61.7 | 30.0 | 缺齿轮配合或比值写反，活动度 2–7 |
| 发动机 v1 | 2 | 38.2 | 5.0 | 连杆/活塞关节缺失，凸轮轴组浮动，正时比 2.0（写反） |
| 发动机 v2 | 2 | 39.8 | 12.5 | 一次凸轮轴组浮动、无正时齿轮配合；一次曲柄组整体浮动 |

装配 rubric 能直接指出错在哪一对零件、哪个关节或哪个耦合，不再是「存没存」的二值信号。不同运行之间也能拉开差距：减速箱从统一的 20 分变成 57.5–68，发动机变成 18.1–61.5。

注意：这 10 个装配体是用旧工具搭的（齿轮配合写反、offset 方向、相位、`check_motion` 混叠）。现在的分数只说明当时的装配哪里错，不能用来比较修正后的 v1 / v2。要公平对比，需要在冻结的新工具上重跑 gold 试点。

## 6. 已知局限

- Animation 的马达转速和名义 rpm 不一致，评测必须密采样。Motion Analysis 需要 SOLIDWORKS Motion 插件，本机没有加载；Basic Motion 不驱动配合。
- 装配 rubric 的约束行只覆盖常见低副和齿轮；槽口未建模，齿条小齿轮/线性耦合不计入活动度（结果里列在 `skipped`）。
- 齿轮配合仍要求零件上有可识别的中心孔圆柱面。
- 默认基准面名按中文 SolidWorks（前视/上视/右视）。
- 不针对某个试点案例改提示词或工具实现。
