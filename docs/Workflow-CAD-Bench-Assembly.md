# Workflow-CAD-Bench-Assembly：装配测例与评测方式

本文是装配评测的规格说明：有哪些测例、每个测例怎么打分、怎么新增测例。装配 workflow 本身（工具、配方、实现中踩过的坑、试点过程）见 [Workflow-CAD-Assembly.md](Workflow-CAD-Assembly.md)。

当前有两个测例：

| case_id | 规格 | 机构 | 零件数 | 目标活动度 |
|---|---|---|---|---|
| `two_stage_gearbox` | `eval/assembly_scratch/gearbox.yaml` | 双级外啮合圆柱齿轮减速箱 6:1 | 8 | 1 |
| `single_cylinder_engine` | `eval/assembly_scratch/engine.yaml` | 单缸曲柄滑块 + 2:1 正时齿轮 + 凸轮 | 12 | 1 |

下文路径都相对 `LLMPlanner/`。

## 1. 评测流程

一次评测对应一个 (测例, 条件, 工作流版本, 重复序号)，由 `eval/eval_assembly_scratch.py` 驱动：

1. 准备零件。
   - `gold`：直接用 `eval/assembly_scratch/gold_parts/{gearbox,engine}/` 里的标准零件（`build_standard_parts.py` 生成）。
   - `gen`：智能体按 `parts[].prompt` 逐个从零建零件，用零件接口 rubric 判合格。
   - `e2e`：同 `gen`，但只要有零件不合格就不进入装配，整条记 0 分。
2. 装配。装配智能体拿到 `assembly.prompt`、配方和零件路径列表，在 50 次 LLM 调用预算内装配并保存。插入坐标不算最终位姿，必须靠配合定位。
3. 评分。智能体结束后，评测器自己打开装配体评分，不采用智能体自报的结果：
   - 装配 rubric（第 3 节），只读配合与位姿；
   - 运动检查（第 4 节），由评测器加马达、采样。
4. 汇总。结果写到 `eval/assembly_scratch_result.json`，装配体和零件存在 `eval/artifacts/assembly_scratch/<workflow>/<case>/<condition>_r<repeat>/`。

角色映射：规格里的 `gold_roles` 把装配角色（如 `lay_shaft`）映射到零件 id（如 `shaft`）。同一个零件可以出现在多个角色上，例如三根轴、三根销。智能体插入组件时给的名字记在 `aliases`（角色 → SolidWorks 组件名），评分全部按角色进行。

## 2. 零件接口 rubric（gen / e2e 条件）

规格在 `eval/assembly_scratch/rubrics/<part_id>.yaml`，用 OCC 对导出的 STEP 检查装配要用到的接口：孔径、孔位、轴径、长度、齿数、模数等。`accepted` 为真才算这个零件合格。gold 条件下零件合格率恒为 1。

## 3. 装配 rubric（替代旧静态分）

实现：`geomcheck/assembly_rubric.py`。规格：`eval/assembly_scratch/assembly_rubrics/<case_id>.yaml`。输入只有两项：

- `asmwf/components`：组件名、是否固定、约束状态、位姿 Transform；
- `asmwf/mates`：配合类型、两端组件、每个实体的 `ReferenceType` 和 `EntityParams`（装配坐标下的点、方向、半径）、齿轮比与转向、`errorCode`、是否压缩。

rubric 不跑 Motion Study，所以不受求解器和采样影响，同一个装配体每次评分结果都相同。

### 3.1 配合 → 约束

每个未压缩、`errorCode` 为 0 的配合，按实体几何写成两零件相对旋量 (v, ω) 上的线性约束行：

| 配合 | 约束行 |
|---|---|
| 面–面 重合/距离 | 沿法向平移 1 行 + 绕两条面内轴转动 2 行 |
| 圆柱–圆柱 同心 | 垂直轴线平移 2 行 + 绕垂直轴线转动 2 行；`lockRotation` 再加绕轴转动 1 行 |
| 轴线–平面、点–平面、点–点、点–轴线 | 对应的平移/转动行 |
| 平行、垂直、角度 | 转动行 |
| 锁定 | 6 行 |
| 螺旋 | 同心 4 行 + 平移与转动按导程耦合 1 行 |
| 同轴 1:1 同向齿轮 | 绕轴转动 1 行（等价于键连接，兼容 v1 的写法） |
| 其他齿轮 | 不进两两关节，只在全局活动度里加耦合行 |
| 限位距离 | 不减少瞬时自由度 |
| 槽口 | 未建模；齿条小齿轮、线性耦合不计入活动度（在 `skipped` 里列出） |

### 3.2 六项检查

| 项 | 权重 | 计分 |
|---|---|---|
| `rigid_groups` 刚体分组 | 20 | 两两约束零空间维数为 0 的零件用并查集合成刚体组，固定件也并在一起。`bodies` 里每个角色都要落在本组、且不和别的组合并；`flex` 里的角色恰好落在允许的一组。按通过比例计分 |
| `joints` 关节类型 | 25 | 每对组之间的全部直接配合合起来求零空间，分为 fixed / slider / revolute / screw / cylindrical / planar / ball / free 等，看是否在允许列表里。按通过比例计分 |
| `mobility` 活动度 | 15 | 非固定件拼成全局约束矩阵，齿轮配合加耦合行 num·(u_a·ω_a) − den·(u_b·ω_b)，瞬时活动度 = 6·N − rank，必须等于 `mobility`。全对或全错 |
| `grounded` 接地与浮动件 | 10 | `ground` 角色必须固定，否则 0 分。从固定件出发、沿非耦合配合走不到的组件算浮动件，按非浮动组件的比例计分 |
| `not_over_defined` 过定义 | 10 | 任一组件约束状态属于过定义类，或任一配合 `errorCode` ≠ 0，就是 0 分 |
| `couplings` 齿轮耦合 | 20 | 每对齿轮 4 小项各占 1/4，各对取平均（见 3.3） |

总分 = Σ 权重 × 该项得分，满分 100。结果里每一项都列出具体是哪个角色、哪一对组、哪个配合出的问题。

### 3.3 齿轮耦合四小项

1. 两齿轮所在的刚体组之间存在一个非 1:1 同向的齿轮配合。
2. 齿数比正确。约定 ω(第二个实体) = ω(第一个实体)·分子/分母，实体顺序就是选择顺序。第一个实体属于 a 组时，期望比是 z_a/z_b，否则是 z_b/z_a。
3. 转向正确：`reverse == external`（外啮合反向）。这一项只在第 1 项成立时才算。
4. 中心距：由两齿轮的位姿算 |Δt⊥u|，要等于 m(z1+z2)/2，误差不超过 `center_distance_tol_mm`（0.2 mm），并且两轴平行。

齿轮配合语义按实测结果统一，见 Workflow-CAD-Assembly.md §2.2：SolidWorks 的 `Reverse=false` 才是反向转，读回的分子、分母与 `AddMate5` 相反。C# 的 `mates` 读回时已经换算成上面的约定。

### 3.4 两个测例的 rubric

减速箱（`two_stage_gearbox.yaml`）：

- `ground: housing`，`mobility: 1`；
- 刚体组：`housing` / `input` = {input_shaft, input_pinion} / `lay` = {lay_shaft, lay_wheel, lay_pinion} / `output` = {output_shaft, output_wheel}；
- 关节：input、lay、output 各自对 housing 为 revolute；
- 耦合：input_pinion–lay_wheel 12/24，lay_pinion–output_wheel 12/36，模数 3，外啮合。中心距期望 54、72 mm。

发动机（`single_cylinder_engine.yaml`）：

- `ground: frame`，`mobility: 1`；
- 刚体组：`frame` / `crank` = {crank_shaft, crank, flywheel, crank_gear} / `rod` / `piston` / `cam` = {camshaft, cam_gear, cam}；
- `flex`：crank_pin ∈ {crank, rod}，piston_pin ∈ {piston, rod}。销必须固定在一端；两端都转的浮动销会多出一个绕自身轴线的空转，活动度对不上；
- 关节：crank–frame、rod–crank、piston–rod、cam–frame 为 revolute。piston–frame 允许 slider 或 planar：它是闭环的最后一个关节，活塞的 z 和倾斜已由连杆链确定，只写一个面重合时单看这一对是平面副，整体等效滑块；
- 耦合：crank_gear–cam_gear 16/32，模数 3，外啮合，中心距 72 mm。

## 4. 运动检查

实现：`geomcheck/assembly_motion.py`（`sample_motion` + `analyze_frames`）。参数来自规格里的 `motion` 段。

### 4.1 采样

- 在 `driver` 角色上加 Animation 旋转马达（`rpm` 10），按 `sweep_deg` 360 算时长。
- Animation 马达的实际转速是名义值的 5–10 倍。所以运动学用 121 帧（`dense_frames`）密采样、不做干涉，逐帧展开转角；干涉只在 13 帧（`frames`）上做 SolidWorks 原生检测。13 帧时相邻帧转角会超过 180°，展开后比值是假的，这是旧版运动分卡在 35 的原因之一。
- 比值和行程都按驱动件的实际转角算，不按名义 rpm 算。

### 4.2 扣分

从 100 分起扣，最低 0：

| 条件 | 扣分 | 判定 |
|---|---|---|
| 干涉 | −40 | 任一干涉帧的干涉数大于 `max_interference`（0） |
| 卡住 | −20 | `expect_moving` 里有零件在中间帧和末帧都相对首帧没动 |
| 固定件漂移 | −15 | `expect_fixed` 里有零件位姿变化超过 1e-4 |
| 传动比 | −15 | 末个 `expect_moving` 零件的转角 / 驱动转角，与 1/`expect_ratio` 相差超过 0.08；`expect_same_direction` 时还要求同向 |
| 行程 | −15 | 活塞销 Y 坐标的极差，与 `expect_stroke_mm` 相差超过 2 mm |
| 正时 | −15 | `timing_driven` 转角 / 驱动转角，与 1/`timing_ratio` 相差超过 0.08；`timing_opposite` 时还要求反向 |
| 齿隙（可选） | −10 | `clearance_pairs` 里各对零件在首帧的 OCC 最小距离不在 `clearance_mm` 范围内 |

扣分项全部不触发才算 `passed`。如果规格要求的角色在装配体里不存在，或者采样出错，运动分记 0，并在 `error` 里写明原因。

### 4.3 两个测例的运动规格

| | 减速箱 | 发动机 |
|---|---|---|
| 驱动件 | input_shaft | crank_shaft |
| 固定件 | housing | frame |
| 应运动 | input_shaft, output_shaft, input_pinion, output_wheel | crank, piston_pin, camshaft |
| 运动学 | 输出/输入 = 1/6，同向 | 活塞行程 50 mm；凸轮轴/曲轴 = 1/2，反向 |
| 齿隙 | 两对啮合齿轮 0.02–2.0 mm | — |

## 5. 指标

| 字段 | 含义 |
|---|---|
| `part_pass_rate` | 零件接口 rubric 合格比例（gold 恒为 1） |
| `assembly_score` | 装配 rubric 总分（结果里的 `static_score` 沿用旧名，实际就是这个分） |
| `motion_score` | 运动分 |
| `e2e` | 零件全部合格且运动检查 `passed` |

`summarize` 按工作流版本和测例分别取平均。

## 6. 阳性对照与重评分

改评测器之前，先保证参考装配体是满分。`eval/assembly_scratch/build_reference_assemblies.py` 用宏工具按规格手工搭了两个参考装配体：`gold_parts/reference_{gearbox,engine}.SLDASM`，这两个文件不入库。当前结果（`eval/assembly_reference_rescore.json`）：

| 参考装配体 | 装配 rubric | 运动分 | 关键测量 |
|---|---|---|---|
| 减速箱 | 100 | 100 | 中间轴 −0.5000，输出 +0.1667；中心距 54 / 72 mm；零干涉 |
| 发动机 | 100 | 100 | 行程 49.998 mm；凸轮轴 −0.5000（反向）；中心距 72 mm；零干涉 |

如果参考装配体不是满分，先查评测器或标准零件，不要先怀疑智能体。

`eval/assembly_scratch/rescore_assemblies.py` 不重跑智能体，只用当前评测器给已保存的装配体重新打分。遇到 SolidWorks 无响应时，它会自动重启 SolidWorks 和服务并重试一次；`--out` 里已有的行会跳过。

## 7. 命令

先启动 SolidWorks 和 C# 服务（`run_service.bat`）。

```powershell
cd LLMPlanner

# 标准零件与参考装配体
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\build_standard_parts.py all
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\build_reference_assemblies.py all

# 阳性对照
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_assemblies.py --reference --out eval\assembly_reference_rescore.json

# 正式评测（--condition gold|gen|e2e|all，--workflow v1|v2|both）
venv\Scripts\python.exe -X utf8 eval\eval_assembly_scratch.py --cases gearbox,engine --condition gold --workflow both --repeats 2

# 重评分已保存的装配体：结果 JSON=artifacts 目录，可给多组
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_assemblies.py --out eval\assembly_rescore.json `
    eval\assembly_scratch_result.json=eval\artifacts\assembly_scratch

# 离线单测（不占 SolidWorks）
venv\Scripts\python.exe -m pytest test\test_assembly_rubric.py test\test_assembly_workflow_v2.py -q
```

## 8. 新增测例

每个测例需要准备：

1. 规格 `eval/assembly_scratch/<name>.yaml`：
   - `case_id`、`title`、关键尺寸；
   - `parts`：每个零件的 `id`、`prompt`、`extra_tools`；
   - `gold_roles`：角色 → 零件 id；
   - `assembly.prompt`：只描述功能和约束关系，不给最终坐标；
   - `motion`：`driver`、`expect_fixed`、`expect_moving`，再按机构选用 `expect_ratio`/`expect_same_direction`、`expect_stroke_mm`、`timing_*`、`clearance_pairs`。
2. 零件接口 rubric `eval/assembly_scratch/rubrics/<part_id>.yaml`，每个零件 id 一份。
3. 装配 rubric `eval/assembly_scratch/assembly_rubrics/<case_id>.yaml`：`ground`、`mobility`、`bodies`、可选的 `flex`、`joints`（type 可以是列表）、`couplings`、`center_distance_tol_mm`。
4. 标准零件：在 `build_standard_parts.py` 里加生成函数。画圆一律带 `noInference: true`，生成后用 OCC 核对孔径和孔位。
5. 参考装配体：在 `build_reference_assemblies.py` 里用宏工具搭一份，要求装配 rubric 和运动分都是 100、静态和逐帧都零干涉、零件在几何上真的装在一起（检查位姿，不能只看运动学）。
6. 登记：`eval_assembly_scratch.py` 的 `--cases` 名称映射和 gold 零件目录，`rescore_assemblies.py` 的 `CASE_FILES`。

约束：

- 测例来源不能是 CAD-1K 的原题。CAD-1K 只做统计和挖掘，不用作测试集。
- 先冻结配方和工具，再跑试点对比。试点结果不能反过来调提示词、工具或评测代码；发现的问题记下来，下一轮冻结前统一修。
- rubric 只写机构必须满足的结构，允许几种等价写法时用 type 列表或 `flex` 表达，不按某个智能体的写法定制。

## 9. 已知局限

- 活动度是瞬时值。奇异位形（比如曲柄滑块的死点）下会偏高；参考装配体都避开了死点。
- 槽口配合未建模；齿条小齿轮、线性耦合只出现在 `skipped` 里，不计入活动度。
- 运动检查依赖 Animation，Motion Analysis 插件本机未加载。目前只支持旋转马达驱动，驱动件的转角按绕 Z 轴的转角读取，所以所有测例的转动轴都要沿装配 Z 轴。
- 中心距和齿相位按齿轮零件的原点和局部 Z 轴计算，要求齿轮零件原点在轴线上。
- SolidWorks 偶尔会满载数分钟、不响应。正式评测里出现这种情况会记成采样错误，需要人工识别为基础设施故障后补跑。
