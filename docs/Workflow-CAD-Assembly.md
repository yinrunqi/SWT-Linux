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
- `/api/sketch/circle` 不关草图推理。圆画在齿廓折线之后，圆周点会吸到齿廓上，孔径从 5 mm 变成 4.738 mm。标准零件改为先画孔（没有全局改动，以免影响 L1 结果复现）。
- `/api/sketch/slot` 退化成矩形，槽口配合认不出。测试件改为矩形切除加两端圆孔切除。
- Animation 的旋转马达不严格按 rpm 转：10 rpm 实测约 47 rpm，1 rpm 实测约 6.9 rpm。传动比、行程都要按驱动件实际转角来算（评测器本来就这样算），帧要足够密，才能无歧义地展开角度。

`spur_gear` 只在装配零件生成会话里通过 `WorkflowSession(extra_tools=["spur_gear"])` 打开，50 题和困难子集的 24 工具结果可以复现。

## 3. 评测协议

规格：`LLMPlanner/eval/assembly_scratch/{gearbox,engine}.yaml`。

零件接口 rubric：`eval/assembly_scratch/rubrics/`。

运动检查（`geomcheck/assembly_motion.py`）由评测器驱动，不采用 Agent 自称的结果：

- 在规格指定的主动件上加旋转马达，采一整圈；
- 每一帧做 SolidWorks 原生干涉检测；
- 核对传动比或活塞行程、固定件是否移动、本该联动的件是否在动；
- 可选：用 OCC 算啮合齿轮的最小距离（有间隙且不相交）。

指标：零件合格率、装配静态分、运动分、端到端成功率。

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
```

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

- 减速箱：四次有效运行的运动测量完全相同：驱动件扫过 −882°，输出传动比 1.103（期望 0.167），每帧都有齿面干涉。v2 把轴上固定从 1:1 齿轮配合换成同心锁定旋转，只剩两个真正的齿轮配合，Animation 仍然保持不了齿数比。第 5.1 节的判断成立：瓶颈在 Animation 求解器，不在配合写法上。v2 有一次在预算内保存了装配（静态分 50）。
- 发动机：两个版本各有一次运动分 25、一次 0 分。0 分的两次都是没来得及插入凸轮轴，评测器找不到该零件。差别在性质上：v2 第 1 次的活塞真的动了，行程 47.95 mm（期望 50，容差 2 mm，差 0.05 mm 未过）；v1 第 2 次曲柄和活塞销都卡住，行程 0。样本太少，不能据此下结论。
- 八次运行全部用满 50 次调用。v2 的工具调用更多（50–72 次对 50–52 次），失败调用也更多（6–17 次对 2–12 次）。v2 用到了 `joint`、`check_dof`，发动机上还用过一次 `close_loop`。

对比中发现的工具问题（为保持结果可复现，没有在对比后修改）：

- `mesh_gears` 不给 `module` 时，把中心距校验算成了 0.02 mm，报 FEATURE_NO_EFFECT。应改为从齿轮几何推断模数，或者明确要求必填。
- 评测器在缺少规格中某个零件时直接抛 KeyError，运动分记为 0。应改为把缺件单独列出。

## 6. 已知局限

- Motion Study 的动画求解器对闭环机构可能比开式齿轮系更脆。若曲柄滑块 spike 失败，发动机会退回「按配合关系推算位姿 + 逐帧干涉」，并在结果里标明。
- 齿轮配合仍要求零件上有可识别的中心孔圆柱面。
- 默认基准面名按中文 SolidWorks（前视/上视/右视）。
- 不针对某个试点案例改提示词或工具实现。
