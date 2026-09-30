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

配方在 `LLMPlanner/planner/skills/recipes/`：`assembly_gear_train.md`、`assembly_crank_slider.md`、`assembly_ground_dof.md`、`assembly_motion_check.md`。

C# 端点在 `/api/asmwf/*`（`SwCommands.AssemblyWorkflow.cs`）：按组件面序号或默认基准面选实体、加配合、Motion Study 驱动与逐帧采样。

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

## 6. 已知局限

- Motion Study 的动画求解器对闭环机构可能比开式齿轮系更脆。若曲柄滑块 spike 失败，发动机会退回「按配合关系推算位姿 + 逐帧干涉」，并在结果里标明。
- 齿轮配合仍要求零件上有可识别的中心孔圆柱面。
- 默认基准面名按中文 SolidWorks（前视/上视/右视）。
- 不针对某个试点案例改提示词或工具实现。
