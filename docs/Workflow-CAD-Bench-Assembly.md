# Workflow-CAD-Bench-Assembly：装配测例与评测方式

本文是装配评测的规格说明：有哪些测例、每个测例怎么打分、怎么新增测例。装配 workflow 本身（工具、配方、实现中踩过的坑、试点过程）见 [Workflow-CAD-Assembly.md](Workflow-CAD-Assembly.md)。

当前有 27 个测例：7 个核心测例，加 20 个扩展测例（见下文"扩展测例"）。核心测例中的后五个由 SolidWorksTutorial 旧示例（台灯、雨伞、电钻、缝纫机、放映机）转换而来：只保留核心运动链，去掉装饰件，每个 6–16 个组件。

| case_id | `--cases` 名 | 机构 | 组件数 | 目标活动度 | 运动检查 |
|---|---|---|---|---|---|
| `two_stage_gearbox` | `gearbox` | 双级外啮合圆柱齿轮减速箱 6:1 | 8 | 1 | 马达 |
| `single_cylinder_engine` | `engine` | 单缸曲柄滑块 + 2:1 正时齿轮 + 凸轮 | 12 | 1 | 马达 |
| `adjustable_desk_lamp` | `desk_lamp` | 三铰平面臂（底座–下臂–上臂–灯头） | 7 | 3 | 多位姿 |
| `folding_umbrella` | `umbrella` | 中棒 + 滑动下巢 + 两组伞骨/撑杆闭环 | 7 | 1 | 多位姿 |
| `drill_planetary_gearbox` | `drill_gearbox` | 两级行星（内齿圈固定，行星架输出）20:1 | 11 | 1 | 马达 |
| `sewing_machine_drive` | `sewing_machine` | 针杆曲柄滑块 + 2:1 旋梭齿轮 + 送布曲柄摇杆 | 15 | 1 | 马达 |
| `film_projector_drive` | `projector` | 惰轮带快门 1:1 + 输片 4:1 减速 | 12 | 1 | 马达 |

规格文件是 `eval/assembly_scratch/<--cases 名>.yaml`。下文路径都相对 `LLMPlanner/`。

转换时的取舍：

- 放映机旧示例的四槽槽轮（间歇运动）无法用配合表达，换成"惰轮带快门同步 + 小齿轮带输片减速"的齿轮系。
- 电钻每级只留一个行星轮。多个行星轮只分担载荷、不改变运动链；而且 SolidWorks 求解冗余的齿轮闭环时，单步就要几分钟，超过当时智能体每次工具调用 180 s 的超时（现为 900 s）。
- 台灯是 3 自由度，没有单一驱动件，雨伞的驱动是下巢的直线滑动。两者都用多位姿模式检查（4.4 节）。

零件工具集按版本标注（`planner/workflow/tools.py` 的 `EXTRA_TOOLSET_VERSION`，结果 JSON 里记为 `extra_toolset`）：

| 版本 | 额外零件工具 | 装配工具变化 |
|---|---|---|
| v1 | `spur_gear` | — |
| v2 | `spur_gear`、`internal_gear`（渐开线内齿圈） | `mesh_gears` 增加 `internal: true`（内啮合中心距与相位） |

6.1 节及更早的结果都是 v1 跑的。v2 只新增工具，原有工具的行为不变。

### 扩展测例（20 个）

全部由 `eval/assembly_scratch/bench_cases.py` 一个文件定义：零件（板、两端圆头长条、销、圆盘、直齿轮、内齿圈六种基本体）、角色、提示词、运动规格、装配 rubric 和参考装配的搭法都写在里面，`write` 子命令据此生成规格 yaml、零件 rubric 和装配 rubric。摆角、行程、位姿点位、初始插入角都由文件里的平面运动学解算，不手填。机构都是平面叠层结构：各零件装好后占的 z 范围写在装配提示里，转轴都沿 Z。

| case_id | `--cases` 名 | 机构 | 组件数 | 目标活动度 | 运动检查 |
|---|---|---|---|---|---|
| `crank_rocker_linkage` | `crank_rocker` | 曲柄摇杆四杆（20/70/50/80） | 8 | 1 | 马达：摇杆摆角 |
| `offset_slider_crank` | `offset_slider_crank` | 偏置曲柄滑块（15/60，偏距 10） | 7 | 1 | 马达：滑块行程 |
| `twin_cylinder_pump` | `twin_pump` | 双缸往复泵，两曲柄相差 180°，板前板后各一缸 | 12 | 1 | 马达：两缸行程、反相 |
| `pumpjack_drive` | `pumpjack` | 10/30 齿减速 + 曲柄摇杆游梁 | 11 | 1 | 马达：曲柄比、游梁摆角 |
| `windshield_wiper_linkage` | `wiper_linkage` | 曲柄摇杆 + 平行四边形带第二刮臂 | 13 | 1 | 马达：两刮臂摆角 |
| `beam_engine` | `beam_engine` | 曲柄摇杆游梁，另一端经活塞杆带活塞 | 12 | 1 | 马达：游梁摆角、活塞行程 |
| `clock_motion_works` | `clock_motion` | 钟表跨轮 10/30 + 8/32，时轮管套在分轮轴上 | 10 | 1 | 马达：跨轮 1/3 反向、时针 1/12 同向 |
| `hand_winch` | `hand_winch` | 摇把 12/36 齿减速带卷筒 | 8 | 1 | 马达：卷筒 1/3 反向 |
| `turntable_ring_drive` | `turntable` | 12 齿小齿轮内啮合 48 齿齿圈转台 | 6 | 1 | 马达：转台 1/4 同向 |
| `star_gear_reducer` | `star_reducer` | 星型轮系：中间轮轴固定、齿圈输出 | 8 | 1 | 马达：输出 1/4 反向 |
| `lead_screw_stage` | `lead_screw_stage` | 竖直丝杠滑台（导程 2 mm，导杆导向） | 6 | 1 | 马达：导程 |
| `gear_driven_slider_crank` | `geared_slider_crank` | 12/36 齿减速 + 对心曲柄滑块 | 10 | 1 | 马达：曲柄比、行程 |
| `epicyclic_carrier_output` | `planetary_carrier` | 单级行星：齿圈固定、行星架输出 | 8 | 1 | 马达：行星架 1/5 同向 |
| `excavator_arm` | `excavator_arm` | 动臂—油缸筒—活塞杆闭环 + 斗杆 + 铲斗 | 11 | 3 | 多位姿 |
| `scara_arm` | `scara_arm` | SCARA：两转动 + 升降杆圆柱副 | 6 | 4 | 多位姿 |
| `pantograph` | `pantograph` | 2 倍缩放仪 | 10 | 2 | 多位姿 |
| `ackermann_steering` | `ackermann_steering` | 阿克曼转向梯形 | 8 | 1 | 多位姿 |
| `toggle_clamp` | `toggle_clamp` | 肘节夹钳四杆 | 8 | 1 | 多位姿 |
| `parallel_gripper` | `parallel_gripper` | 两组平行四边形导向的夹爪 | 15 | 2 | 多位姿 |
| `double_wishbone` | `double_wishbone` | 双横臂悬架（平面简化） | 10 | 1 | 多位姿 |

设计时的约束：

- 每个测例最多 3 对齿轮啮合，且不出现冗余的齿轮闭环（多行星轮）。原因见电钻的说明。
- 不用齿条小齿轮、线性耦合和槽口：装配 rubric 的活动度不计它们（3.1 节）。
- 闭环机构的最后一个铰在 rubric 里允许 revolute 或 cylindrical，滑块允许 slider 或 planar，理由同发动机、缝纫机。
- 销都只连两个体，`flex` 允许它固定在任一侧。
- 多位姿测例的点位都取在杆件 X 轴（孔心连线）上，与零件正装、翻面无关；四杆类的装配模式（例如肘节夹钳的铰点 J 在 A、K 连线上方）在提示里写明，因为从一个模式到另一个模式要穿过奇异位形，角度配合改不过来。

### 第二批测例（23 个）

由 `eval/assembly_scratch/bench_cases2.py` 定义，基本体、零件规格格式、零件 rubric 与第一批相同，但每个测例改成声明式写法：

- `layout(s)`：给定马达转角或位姿名，返回每个角色的平面位姿 (x, y, 转角)，z 取自 `z` 表；
- `steps`：装配步骤，`ground` / `fix` / `rev` / `cyl`（闭环最后一个铰，只要同心）/ `slide` / `coinc` / `mesh` / `screw`。

参考装配体（`build_reference2`）、离线自检和解析锚点都从这两样派生。组件数 11–28，比第一批（6–15）复杂，多数带 2–3 对齿轮或多重闭环。

| case_id | `--cases` 名 | 机构 | 组件数 | 目标活动度 | 运动检查 |
|---|---|---|---|---|---|
| `l_twin_engine` | `l_twin_engine` | L 型双缸发动机，两连杆共用曲柄销，16/32 正时齿轮带凸轮轴 | 15 | 1 | 马达：凸轮轴 1/2、两缸行程 30 |
| `radial_engine_4cyl` | `radial_engine` | 星形四缸，主连杆 + 三根副连杆铰在主连杆法兰上，带螺旋桨 | 21 | 1 | 马达：四缸行程 30 / 30.25 / 30.25 / 30 |
| `quick_return_shaper` | `quick_return_shaper` | 牛头刨急回：曲柄滑块导杆 + 连杆推刨头，摇把经 1:3 增速 | 15 | 1 | 马达：导杆摆角 49.25°、刨头行程 116.67 |
| `knuckle_press` | `knuckle_press` | 肘杆压力机，14/42 齿减速 | 15 | 1 | 马达：肘杆摆角 36.16°、滑块行程 12.49 |
| `oldham_coupling` | `oldham_coupling` | 十字滑块联轴器，两轴偏距 12 | 12 | 1 | 马达：输出 1:1 同向、十字块 X / Y 行程各 12 |
| `elliptical_trainer` | `elliptical_trainer` | 椭圆机，左右踏板反相 | 18 | 1 | 马达：摆杆摆角 29.06°、踏板行程、反相 |
| `jansen_leg` | `jansen_leg` | Jansen 八杆步行腿（Strandbeest 比例） | 22 | 1 | 马达：足端行程 135.8 × 44.9、摆角 |
| `lambda_conveyor` | `lambda_conveyor` | Chebyshev λ 机构步进输送，30/15 齿在板后 | 19 | 1 | 马达：齿比、足端行程（65% 行程近似直线） |
| `geared_five_bar` | `geared_five_bar` | 齿轮耦合五杆绘图机 | 13 | 1 | 马达：齿比、笔尖行程 |
| `planetary_mixer` | `planetary_mixer` | 行星搅拌机，固定太阳轮 + 双行星 + 齿轮驱动行星架 | 14 | 1 | 马达（3 圈）：行星架 1/3 反向、行星轮 2/3 反向、行星轮行程 96 |
| `spirograph` | `spirograph` | 万花尺：固定 60 齿内齿圈 + 两只 20 齿行星带笔 | 16 | 1 | 马达：行星架 1/3 反向、行星轮 2/3 同向 |
| `wankel_rotary` | `wankel_rotary` | Wankel 转子：偏心 12，转子 1/3 同向 | 11 | 1 | 马达：转子比、偏心行程 |
| `orrery_earth_moon` | `orrery` | 太阳系仪：固定太阳轮—惰轮—地球轮，三对啮合 | 16 | 1 | 马达（3 圈）：臂 1/3 反向、惰轮 1:1 反向、地球 1/6 同向、地球行程 128 |
| `oscillating_fan` | `oscillating_fan` | 摇头风扇：两级 12/36 减速 + 曲柄摇杆摇头 | 15 | 1 | 马达：中间轴 1/3、摇头摆角 60.14° |
| `gear_screw_lift` | `gear_screw_lift` | 齿轮带丝杠的升降台，导程 6 | 12 | 1 | 马达：齿比、每圈升 3 mm |
| `scissor_lift_3stage` | `scissor_lift` | 三级剪叉 + 底部滑块 + 顶部滑块平台 | 21 | 1 | 多位姿：3 个滑块偏距，平台与各级铰点 |
| `mechanical_iris_5blade` | `mechanical_iris` | 五叶机械光圈：驱动环 + 5 组四杆叶片，分 3 层交叠 | 28 | 1 | 多位姿：3 个驱动环转角，五个叶尖 |
| `peaucellier_linkage` | `peaucellier_linkage` | Peaucellier 精确直线机构 | 14 | 1 | 多位姿：3 个曲柄角，输出点在 x=93.75 的直线上 |
| `five_bar_robot` | `five_bar_robot` | 五杆并联机器人 | 11 | 2 | 多位姿：3 组双角度，两肘点与末端 |
| `palletizing_robot` | `palletizing_robot` | 码垛机器人：驱动平行四边形 + 两级调平平行四边形 | 18 | 2 | 多位姿：3 组双角度，肘点、腕点与腕部法兰姿态 |
| `gear_sync_gripper` | `gear_sync_gripper` | 15/15 齿轮同步的平行四边形夹爪 | 17 | 1 | 多位姿：张开 / 中间 / 夹紧（两侧各设角度），指尖开口 79.4 / 47.9 / 8.1 |
| `robot_finger_coupled` | `robot_finger` | 三节手指，两组反平行四边形耦合 | 13 | 1 | 多位姿：3 个近节角，中节末端与指尖 |
| `luffing_crane` | `luffing_crane` | 四杆臂架 + 变幅油缸的起重机 | 14 | 1 | 多位姿：3 个俯仰角，吊点、臂头、油缸铰点 |

在第一批约束之外，另有几条：

- 测例来源是教科书上的经典机构，尺寸自行设计，不取自 CAD-1K。
- 马达模式的检查项必须在 13 帧采样（驱动件每帧 30°）下可测：被检比值的输出件每帧转角不超过 180°，所以增速机构把驱动放在慢轴上，再检查马达轴的 `ratio: 1/3`。
- 马达起始角不取对称位置（用 45° / 30° 等）。评测器拿首帧、中间帧和末帧判断零件是否卡住，起始角对称时这几帧会恰好重合，正确的装配也会被误判成卡住（第 9 节）。
- 零件 id 的前缀不能与第一批重复，否则 `write` 会覆盖第一批的零件 rubric。
- 多位姿的 `offset` 项带符号：a 的基准面原点在 b 的基准面法向一侧为正，与参考装配体的距离配合一致。

#### 不用 SolidWorks 的自检

`python -m eval.assembly_scratch.bench2_check [name ...]` 对每个测例跑四项检查，全部通过才算可用：

1. 关节与层位：每个 `rev` / `fix` / `cyl` 的两个孔在所有采样帧上同心，销覆盖所连零件的 z 范围；`mesh` 的中心距等于 m(z1±z2)/2，两轮按齿数比滚动。
2. 穿模：按零件轮廓（板、长条、圆盘、齿顶圆）在每一帧做同层二维相交，销半径缩 3%，啮合与丝杠副跳过。
3. 合成配合打 rubric：按 `steps` 生成配合列表，交给 `geomcheck.assembly_rubric.evaluate`，必须 100 分（活动度、关节类型、刚体组、锚点都对）。
4. 合成帧打运动分：按 `layout` 生成 13 帧变换（多位姿是各位姿的变换），交给 `geomcheck.assembly_motion`，必须 100 分；多位姿还逐项核对角度、点位和带符号偏距。

阴性对照：故意改错一个铰点位置、一个层位或一个齿数，相应检查会失败。零件 rubric 的阳性 / 阴性对照见 `geomcheck/tests/test_bench_parts.py`：按规格用 OCC 生成两批全部 403 个零件，rubric 都判合格；另外改一个孔位、销长或齿数的 6 个变体都在对应条目上失败。第二批的 257 个零件另外走过一遍 OCC 导出 STEP → `_grade_step` 读回打分的流程，全部合格。

关节锚点：第一批的锚点由参考装配体导出（`joint_anchors.json`）。第二批的参考装配体还没搭，`write` 先从 `layout` 解析出锚点写到 `joint_anchors_analytic.json`，`load_assembly_rubric` 在前者没有该 case 时用它。

#### SolidWorks 可达性验证（oracle 轨迹）

阳性对照只说明评测器认得正确的装配体；要说明智能体有可能拿满分，还得证明只用 v3 公开工具就能装出来。`eval/assembly_scratch/oracle_replay.py` 按 `steps` 为每个测例写出一条智能体可以照抄的工具调用序列，不调用模型：

- `insert_component` 放在 `layout(s0)` 的位姿；`ground_component`；
- `fix` / `rev` → `joint fixed` / `revolute`，`face_a` / `face_b` 取两件的 `plane(front)`，`offset` 为层位差；`cyl` → `joint cylindrical`；
- `slide` / `coinc` 偏距为 0 时 → `mate coincident`；偏距非零而两件各有一个正好贴合的外表面时 → 两面 `coincident`（`align: anti`），否则 → `mate distance`；
- `mesh` → `mesh_gears`（齿圈加 `internal`），`screw` → `screw_pair`；
- 收尾 `check_dof` → `check_motion` 或 `check_pose`（参数取自运动规格）→ `save_assembly` → `finish`。

会话按智能体的方式创建（`workflow=v3`、只开放 v3 工具），保存后再用评测器的 `score_assembly_rubric` 和 `_motion` 打分。`oracle_supervise.py` 逐例起子进程、断线自动重启 SolidWorks 并续跑，结果在 `eval/artifacts/bench2_oracle/<name>/`（`trajectory.json`、`result.json`、`progress.json`）。

结果：23 个测例都有一条公开工具轨迹拿到装配 rubric 100、运动分 100，`finish` 通过。工具调用数 27–65（光圈最多，65 次），单例重放 4–35 分钟。

验证中发现并修正的问题：

| 测例 | 现象 | 原因 | 修正 |
|---|---|---|---|
| `quick_return_shaper` | 刨头导向的 `mate distance 95` 报过定义，活动度 3 | 距离配合不分正负，SolidWorks 把刨头放到了 −95 一侧；当时公开 `mate` 不能翻转 | 床身板上沿改到 y=87，刨头下沿面与它贴合；`mate` 开放 `flip`（见下） |
| `oldham_coupling` | 每帧都有约 6560 mm³ 干涉 | 同上，十字块的距离配合落在错误一侧 | 十字块底面与输入半联轴器端面贴合 |
| `planetary_mixer`、`orrery` | 运动分 91.67，行程实测 96 / 128，期望 48 / 64 | Animation 马达实际比 rpm 快约 9.5 倍，“一圈”实际转了好几圈；行程却按驱动一圈算，而行星架三圈才转一周 | 扫 1080°（3 圈）、密采样 361 帧，期望行程按整周 |
| `gear_sync_gripper` | 左指到位、右指停在张开位 | 临时角度配合后的静态重建不传递齿轮配合，带锁转的同轴也会被放松，且不报错 | 两侧主动摆杆各设一个角度；齿轮同步由装配 rubric 的齿轮耦合和活动度 1 考核 |
| `lambda_conveyor`、`spirograph` | 轨迹全部通过，评分时运动分为 0 | 评测器运动采样的 HTTP 超时写死 180 s，重型装配一次 121 帧采样要更久 | `geomcheck.assembly_motion.HTTP_TIMEOUT_S = 900` |

同时调整了智能体侧：

- `AssemblySession` 默认 HTTP 超时由 180 s 改为 900 s（与参考装配构建相同）。实测这些装配体在 SolidWorks 里单条配合 150–170 s、列组件最长 364 s、保存最长 354 s，180 s 会把正常操作判成 `SW_UNAVAILABLE`。
- `mate` 增加 `flip`（只对 distance 有意义，把 a 换到 b 的另一侧），distance 配合返回 `side_mm`（a 在 b 法向上的带符号距离）；distance 报过定义时提示加 / 去 `flip`。SolidWorks 实测：同一条 95 mm 距离配合，不带 `flip` 落在 −95，带 `flip` 落在 +95。

剩余的 SolidWorks 步骤：`build_reference_assemblies.py` 搭参考装配体、`derive_joint_anchors.py` 导出锚点替换解析锚点、渲染展示图。oracle 轨迹保存的 `assembly.SLDASM` 已可代替参考装配体做阳性对照。

## 1. 评测流程

一次评测对应一个 (测例, 条件, 工作流版本, 重复序号)，由 `eval/eval_assembly_scratch.py` 驱动：

1. 准备零件。
   - `gold`：直接用 `eval/assembly_scratch/gold_parts/<--cases 名>/` 里的标准零件（`build_standard_parts.py` 生成）。
   - `gen`：智能体按 `parts[].prompt` 逐个从零建零件，用零件接口 rubric 判合格。每个零件只开放它 `extra_tools` 里列出的额外工具。
   - `e2e`：同 `gen`，但只要有零件不合格就不进入装配，整条记 0 分。
2. 装配。装配智能体拿到 `assembly.prompt`、配方和零件路径列表，在 50 次 LLM 调用预算内装配并保存。插入坐标不算最终位姿，必须靠配合定位。
3. 评分。智能体结束后，评测器自己打开装配体评分，不采用智能体自报的结果：
   - 装配 rubric（第 3 节），只读配合与位姿；
   - 运动检查（第 4 节），由评测器加马达、采样。
4. 汇总。结果写到 `eval/assembly_scratch_result.json`，装配体和零件存在 `eval/artifacts/assembly_scratch/<workflow>/<case>/<condition>_r<repeat>/`。智能体结束后评测器先把当前装配体存成检查点再评分，所以即使智能体没调用 `save_assembly`，评分用的也是它最后的状态；同目录的 `rubric_inputs.json` 存下 rubric 的原始输入（组件、配合、别名），以后改 rubric 可以离线重评。

基础设施故障自动识别（`infra_flags`）：装配阶段的工具报错或运动评测错误里出现 SolidWorks 不可用、RPC 服务器不可用、活动文档丢失、请求超时等标记时，该行记 `infra_contaminated`。评测器会重启 SolidWorks 和服务，只重跑装配阶段（零件不重建），最多 `--infra-retries` 次（默认 1）；重试后仍被污染的行保留在 `all_rows` 里，但不进 `summary`，`n_infra_contaminated` 单独报告。

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
| 其他齿轮 | 不进两两关节，只在全局活动度里加一行节点无滑滚动约束（见下） |
| 限位距离 | 不减少瞬时自由度 |
| 槽口 | 未建模；齿条小齿轮、线性耦合不计入活动度（在 `skipped` 里列出） |

齿轮耦合行按节点 p 处两齿面切向速度相等来写：行向量为 [t, p×t]，t = u_a × u（u 是两轴连心方向），作用在两齿轮的相对旋量上。

- 外啮合时 p 在两轴之间，按半径比 k = r_a/r_b 分点；内啮合时 p 在外分点上。
- 两轴同轴、或者内啮合且 k = 1 时退化，改用绝对角速度行 num·(u_a·ω_a) − den·(u_b·ω_b)。
- 固定轴齿轮系的两种写法秩相同，所以减速箱、发动机的分数不变。
- 行星架转动时，只有滚动写法才正确：绝对角速度写法会把行星轮的公转也算进齿数比，活动度对不上。

### 3.2 六项检查

先算完整度 `completeness`：rubric 里出现的角色中，满足下面三条的比例。

- 已插入；
- 从固定件出发沿非耦合配合能走到（齿轮、齿条、线性耦合不算连接）；
- 没有被直接固定在空间里。只有机架所在组的角色（以及 `flex` 允许落在机架组的角色）可以是固定件，其他角色被固定时记在 `wrongly_fixed`。

各项的计分都以“角色已连上”为前提，未连上的角色不能靠插入位置碰巧对而得分：

| 项 | 权重 | 计分 |
|---|---|---|
| `rigid_groups` 刚体分组 | 20 | 两两约束零空间维数为 0 的零件用并查集合成刚体组，固定件也并在一起。`bodies` 里每个角色都要落在本组、不和别的组合并，并且已连上；`flex` 里的角色恰好落在允许的一组。按通过比例计分 |
| `joints` 关节类型 | 25 | 每对组之间的全部直接配合合起来求零空间，分为 fixed / slider / revolute / screw / cylindrical / planar / ball / free 等，看是否在允许列表里，且两组都已连上。规格可选 `lead_mm`（螺旋副导程，误差 ≤ 5%）和 `anchors`（关节轴线在某角色局部坐标下的点和方向，见下文），给了就一并检查。按通过比例计分 |
| `mobility` 活动度 | 15 | 非固定件拼成全局约束矩阵，每个齿轮配合加一行滚动约束（3.1 节），瞬时活动度 m = 6·N − rank。有缺件或悬空件时 0 分，否则得 max(0, 1 − \|m − t\| / max(t, 1))，t 是规格的 `mobility` |
| `grounded` 接地 | 10 | `ground` 角色必须固定，否则 0 分；否则等于完整度 |
| `not_over_defined` 过定义 | 10 | 任一组件约束状态属于过定义类，或任一配合 `errorCode` ≠ 0，就是 0 分；否则等于完整度（只插入不配合的装配体不会过定义，不能白拿这 10 分） |
| `couplings` 齿轮耦合 | 20 | 每对齿轮 4 小项各占 1/4，各对取平均（见 3.3）；中心距小项要求两个齿轮都已连上 |

总分 = 100 × Σ(权重 × 得分) / Σ(适用项的权重)。规格里没有齿轮时 `couplings` 不适用，没有关节时 `joints` 不适用，依此类推；不适用的项不进分母，也不出现在 `parts` 里。结果里每一项都列出具体是哪个角色、哪一对组、哪个配合出的问题。

`anchors` 由 `eval/assembly_scratch/derive_joint_anchors.py` 从参考装配体自动导出，存在 `assembly_rubrics/joint_anchors.json`（按 rubric 里 `joints` 的序号），加载 rubric 时合并进去。只给转轴唯一的关节（转动、圆柱、螺旋）导出。检查方法：

1. 按该关节的约束行求出瞬时旋转轴（点 = ω×v/\|ω\|²）。
2. 同一角色上有多个带锚点的关节时，两两比较转轴之间的距离和夹角，与参考装配体的值比，距离容差 `anchor_tol_mm`（默认 0.5 mm）。
3. 一个关节与超过一半的同伴对不上才判错，所以一个关节装错不会连累同一零件上装对的关节。

只比相对关系，是因为零件接口 rubric 按包围盒量孔位，生成的零件原点、朝向可以和标准零件不同，但同一零件上孔的间距不变。它抓的是“关节类型对、但装在错的孔上”。对称零件左右调换使用时间距不变，不扣分。导出脚本会确认合并锚点后参考装配体仍是 100 分，否则报错退出。

层位（零件沿 Z 的高度）不单独检查：层位错了通常表现为干涉或位姿点位不对，运动检查已经覆盖。

旧口径保留为 `score_v1`：总是按 100 分满权重计、不看完整度、活动度全对或全错、`grounded` 按非浮动组件比例计。新旧口径的差别主要在不完整的装配体上。阴性对照（离线单测）：只插入不配合、所有零件都固定、只插入一半零件且不配合，三种情况新口径都 ≤ 10 分，旧口径是 22–41 分；减速箱装对输入轴和中间轴、输出轴整组没装时，新口径落在 30–80 分之间。

### 3.3 齿轮耦合四小项

1. 两齿轮所在的刚体组之间存在一个非 1:1 同向的齿轮配合。
2. 齿数比正确。约定 ω(第二个实体) = ω(第一个实体)·分子/分母，实体顺序就是选择顺序。第一个实体属于 a 组时，期望比是 z_a/z_b，否则是 z_b/z_a。
3. 转向正确：`reverse == external`（外啮合反向，内啮合同向）。这一项只在第 1 项成立时才算。
4. 中心距：由两齿轮的位姿算 |Δt⊥u|，外啮合要等于 m(z1+z2)/2，内啮合（rubric 里 `external: false`）要等于 m|z1−z2|/2，误差不超过 `center_distance_tol_mm`（0.2 mm），并且两轴平行。

齿轮配合语义按实测结果统一，见 Workflow-CAD-Assembly.md §2.2：SolidWorks 的 `Reverse=false` 才是反向转，读回的分子、分母与 `AddMate5` 相反。C# 的 `mates` 读回时已经换算成上面的约定。

### 3.4 各测例的 rubric

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

台灯（`adjustable_desk_lamp.yaml`）：

- `ground: base`，`mobility: 3`；
- 刚体组：base、lower_arm、upper_arm、shade 各自一组；
- `flex`：三根铰销各自固定在相邻两组中的任一组；
- 关节：lower_arm–base、upper_arm–lower_arm、shade–upper_arm 为 revolute。

雨伞（`folding_umbrella.yaml`）：

- `ground: shaft`，`mobility: 1`；
- 刚体组：shaft = {shaft, hub}，runner、rib_a、rib_b、stretcher_a、stretcher_b 各自一组；
- 关节：
  - runner–shaft 允许 cylindrical 或 slider：只套在中棒上是圆柱副，它绕轴的转动由两组撑杆闭环锁住；
  - 伞骨对上巢、撑杆对下巢都是 revolute；
  - 撑杆远端–伞骨中孔是闭环的最后一个铰，允许 revolute 或 cylindrical。

电钻（`drill_planetary_gearbox.yaml`）：

- `ground: housing`，`mobility: 1`；
- 刚体组：frame = {housing, ring1, ring2}、input = {input_shaft, sun1}、planet1、carrier1 = {carrier1, sun2}、planet2、output = {carrier2, output_shaft}；
- 关节：input、carrier1、output 对 frame，planet1 对 carrier1，planet2 对 output，全部 revolute；
- 耦合：模数 1.2，中心距都是 18 mm，共四对：
  - sun1–planet1 12/18，外啮合；
  - planet1–ring1 18/48，内啮合；
  - sun2–planet2 15/15，外啮合；
  - planet2–ring2 15/45，内啮合。

缝纫机（`sewing_machine_drive.yaml`）：

- `ground: frame`，`mobility: 1`；
- 刚体组：frame = {frame, rocker_pin}、main = {main_shaft, crank, main_gear, feed_crank}、needle_rod、needle_bar、hook = {hook_shaft, hook_gear, hook}、feed_coupler、feed_rocker；
- `flex`：crank_pin ∈ {main, rod}，needle_pin ∈ {needle, rod}；
- 关节：
  - needle–frame 允许 slider 或 planar（理由同发动机的活塞）；
  - coupler–rocker 允许 revolute 或 cylindrical（四杆闭环的最后一个铰）；
  - 其余为 revolute；
- 耦合：main_gear–hook_gear 40/20，模数 1.5，外啮合，中心距 45 mm。

放映机（`film_projector_drive.yaml`）：

- `ground: frame`，`mobility: 1`；
- 刚体组：drive = {drive_shaft, drive_gear, drive_pinion}、idler = {idler_shaft, idler_gear}、shutter = {shutter_shaft, shutter_gear, shutter}、sprocket = {sprocket_shaft, sprocket_gear, sprocket}；
- 关节：四组对 frame 都是 revolute；
- 耦合：模数 1.5，都是外啮合：
  - drive_gear–idler_gear 24/18，中心距 31.5 mm；
  - idler_gear–shutter_gear 18/24，中心距 31.5 mm；
  - drive_pinion–sprocket_gear 16/64，中心距 60 mm。

扩展测例的 rubric 由 `bench_cases.py` 生成，写法与上面一致，几个要点：

- 刚体组：同轴固连的轴、齿轮、曲柄、卷筒、手轮、指针等合成一组；固定在机架上的立轴、导杆、齿圈并入机架组。
- 铰销一律写进 `flex`，允许固定在相邻两组中的任一组。
- 双缸泵的两个活塞、偏置 / 对心曲柄滑块的滑块、梁机的活塞，对机架都允许 slider 或 planar。
- 丝杠滑台：carriage–frame 允许 cylindrical 或 slider（套在导杆上），carriage–screw 必须是 screw。
- SCARA 的升降杆对小臂是 cylindrical（可升降也可自转），整机活动度 4。
- 挖掘机的活塞杆对缸筒允许 slider 或 planar。
- 钟表的时轮组对分轮组是 revolute（时轮管套在分轮轴上），星型轮系与行星架输出的输出组对机架是 revolute（与主轴承孔同轴）。

## 4. 运动检查

实现：`geomcheck/assembly_motion.py`。参数来自规格里的 `motion` 段，有两种模式：

- 马达模式（默认）：`sample_motion` + `analyze_frames`，见 4.1–4.3 节；
- 多位姿模式（`mode: poses`）：`run_poses` + `analyze_poses`，见 4.4 节。

### 4.1 采样

- 在 `driver` 角色上加 Animation 旋转马达（`rpm` 10），按 `sweep_deg` 360 算时长。
- Animation 马达的实际转速是名义值的 5–10 倍。所以运动学用 121 帧（`dense_frames`）密采样、不做干涉，逐帧展开转角；干涉只在 13 帧（`frames`）上做 SolidWorks 原生检测。13 帧时相邻帧转角会超过 180°，展开后比值是假的，这是旧版运动分卡在 35 的原因之一。
- 比值和行程都按驱动件的实际转角算，不按名义 rpm 算。

### 4.2 计分

单帧转角超过 60° 时展开会混叠（相邻帧转角接近 180° 时，比值、导程都会算错）。`refine_aliased` 按实测单帧转角加密一次运动学采样，最多 481 帧，分批请求，每批不超过 121 帧：一次请求上千帧时 SolidWorks 会超时甚至崩溃。加密后单帧转角没有明显变小（仍大于原来的 75%），说明是位形跳变（机构卡死时求解器在两个解之间跳），不是混叠，这时保留原帧，在 `sampling.driver_jump_deg` 里标出。智能体侧 v3 的 `check_motion` 用同一个函数。

运动分改为正向计分：每个适用的检查项给 [0, 1] 的得分，按权重平均（`CHECK_WEIGHTS`）。

| 检查项 | 权重 | 得分 |
|---|---|---|
| `kinematics` 运动学 | 2 | 下表中比值、导程、行程、正时、摆角、反相各小项的通过比例 |
| `interference` 干涉 | 2 | 无干涉帧的比例 × 该动件实际在动的比例 |
| `moving` 该动的动了 | 1 | `expect_moving` 里没有卡住的比例 |
| `fixed` 固定件不漂 | 1 | `expect_fixed` 里没漂移的比例 × 该动件实际在动的比例 |
规格里的 `clearance_pairs`（齿侧间隙）只记录到 `motion.clearance`，不计分、也不影响 `passed`：`planner/gears.py` 的外啮合直齿轮不留侧隙（齿厚正好半个齿距），参考减速箱两对啮合实测只有 0.009 / 0.006 mm，低于 0.02 mm 下限，这一项过不了阳性对照。

“无干涉”“固定件不漂”只有在机构真的动起来时才有意义，所以乘上该动件在动的比例：完全卡死的机构得 0 分（旧口径是 65 分）。

`passed` 的条件不变：下表的扣分项全部不触发。旧的扣分口径保留为 `score_v1`，从 100 分起扣，最低 0：

| 条件 | 扣分 | 判定 |
|---|---|---|
| 干涉 | −40 | 任一干涉帧的干涉数大于 `max_interference`（0） |
| 卡住 | −20 | `expect_moving` 里有零件在中间帧和末帧都相对首帧没动 |
| 固定件漂移 | −15 | `expect_fixed` 里有零件位姿变化超过 1e-4 |
| 传动比 | −15 | 有 `ratios` 列表时：每项 `role` 的转角 / 驱动转角与 1/`ratio` 的相对误差超过 8%，或转向与 `same_direction` 不符；任一项不对就扣。没有 `ratios` 时沿用旧写法：末个 `expect_moving` 零件的比值与 1/`expect_ratio` 相差超过 0.08，`expect_same_direction` 时还要求同向 |
| 行程 | −15 | `stroke_role`（默认 piston_pin）在 `stroke_axis`（默认 y）上坐标的极差，与 `expect_stroke_mm` 相差超过 2 mm |
| 正时 | −15 | `timing_driven` 转角 / 驱动转角，与 1/`timing_ratio` 相差超过 0.08；`timing_opposite` 时还要求反向 |
| 导程 | 并入传动比项 | `leads`：每项 `role` 沿 `axis` 的位移 / 驱动圈数与 `mm_per_rev` 的相对误差超过 8%（默认不比符号，`signed: true` 时比） |
| 多件行程、摆角、反相 | 并入行程项 | `strokes`：每项 `role` 原点沿 `axis` 的极差与 `mm` 相差超过 2 mm；`swings`：每项 `role` 绕 Z 转角（展开后）的极差与 `deg` 相差超过 2°；`antiphase`：`roles` 两件沿 `axis` 的位置序列相关系数不小于 −0.9 |

扣分项全部不触发才算 `passed`。如果规格要求的角色在装配体里不存在，或者采样出错，运动分记 0，并在 `error` 里写明原因。

"卡住"只比较中间帧和末帧。如果某零件在采样时长内恰好转回首帧位置，会被误判为卡住；目前各测例的转速比都避开了这种情况。

### 4.3 马达模式各测例的运动规格

| | 驱动件 | 固定件 | 应运动 | 运动学 | 齿隙 |
|---|---|---|---|---|---|
| 减速箱 | input_shaft | housing | input_shaft, output_shaft, input_pinion, output_wheel | 输出/输入 = 1/6，同向 | 两对啮合齿轮 0.02–2.0 mm |
| 发动机 | crank_shaft | frame | crank, piston_pin, camshaft | 活塞行程 50 mm；凸轮轴/曲轴 = 1/2，反向 | — |
| 电钻 | input_shaft | housing | sun1, planet1, carrier1, planet2, carrier2 | carrier1 = 1/5、carrier2 = 1/20，都同向 | — |
| 缝纫机 | main_shaft | frame | crank, needle_bar, hook, feed_rocker | needle_pin 沿 Y 行程 36 mm；hook = 2 倍速，反向 | — |
| 放映机 | drive_shaft | frame | idler_gear, shutter, sprocket | shutter = 1:1 同向；sprocket = 1/4，反向 | — |

### 4.4 多位姿模式

用于没有单一转动驱动件的机构：台灯有 3 个自由度，雨伞的输入是下巢的直线滑动。评测器把装配体依次摆到 `poses` 里的每个位姿，检查关键点的位置。

每个位姿的流程：

1. 按运动链顺序逐项加临时配合（`set`）：
   - `angle`：两个面之间的角度；
   - `offset`：带符号的面距（a 面落在 b 面法向正侧多少 mm）；
   - `distance`：不带符号的距离。
2. 角度配合的方向有歧义，评测器按顺序试四种变体：不翻转、翻转、反向对齐、翻转 + 反向对齐。每试一种，就用该项所附的 `points` 量一次，取第一个通过的；都不通过时取误差最小的那个。没选中的变体先删掉，再试下一种，免得前后两种互相冲突。
3. 所有项加完后，统一判定全部点位。点位的写法：
   - 在 `frame` 角色的局部坐标系里量；
   - `local` 是该零件局部坐标系里的点；
   - `expect` 可以写 x、y、z，也可以写离轴距离 r_xy、r_xz、r_yz；
   - `abs` 里列出的坐标按绝对值比较，用来容许整机左右镜像摆放；
   - 误差不超过 `tolerance_mm`（1.5 mm）才算对。
4. 做一次静态干涉检查，并检查 `expect_fixed` 是否漂移。最后删掉本位姿的全部临时配合。

一个位姿要满足四个条件才算通过：临时配合全部建成、点位全对、干涉数不超过 `max_interference`、固定件没有漂移。

每个位姿按 4.2 节同一组权重计分：`kinematics`（点位全对）2、`interference` 2、`fixed` 1，后两项只在点位全对时给分；临时配合建不成时该位姿 0 分。运动分是各位姿得分的平均。这样“摆对了但有干涉”的位姿得 60 分，而不是和“完全摆不出来”一样记 0。旧口径（100 × 通过的位姿数 / 位姿总数）保留为 `score_v1`。

在雨伞这样的闭环里，SolidWorks 有时会把不带 flip 的距离配合直接判为过定义，哪怕距离就等于当前值。评测器遇到这种情况会改用 flip 重建，再核对落在哪一侧。这个回退只在评测器里，智能体用的 `joint` 工具不变。

| | 台灯 | 雨伞 |
|---|---|---|
| 参考系 | base | shaft |
| 位姿 | 3 个：(肩, 肘, 腕) = (30°, 35°, 15°)、(60°, 35°, 30°)、(82°, 50°, 45°) | 3 个：下巢距中棒底面 209.65、121.42、65.08 mm（张开、半开、收拢） |
| 检查点 | 两臂两孔中点、灯头末端；x 取绝对值 | 两根伞骨尖端 (300, 0, 2)，量轴向高度 z 和离轴距离 r_xy |

## 5. 指标

| 字段 | 含义 |
|---|---|
| `part_pass_rate` | 零件接口 rubric 合格比例（gold 恒为 1） |
| `assembly_score` | 装配 rubric 总分（结果里的 `static_score` 沿用旧名，实际就是这个分） |
| `motion_score` | 运动分 |
| `e2e` | 零件全部合格且运动检查 `passed` |
| `structure_ok` | 结构成立：完整度为 1 且活动度正确 |
| `motion_score_gated` | 结构不成立时记 0，否则等于 `motion_score`。缺件的装配体可能恰好让剩下的零件转得对，这个指标排除这种情况 |
| `assembly_score_v1` / `motion_score_v1` | 旧口径，便于和以前的结果对比 |
| `infra_contaminated` | 见第 1 节 |

`summarize` 只用未被污染的行，按工作流版本和测例分别取平均，并给出标准差、`finish_rate`（智能体正常 finish 的比例）、`saved_by_agent_rate`、`budget_exhausted_rate`、`llm_calls_mean`、`structure_ok_rate`。

调用预算：默认每个测例 50 次 LLM 调用（`--max-asm-calls`）。`--asm-calls-per-role N` 大于 0 时改为 max(`--max-asm-calls`, N × 角色数)，零件多的测例预算更多；报告里记录这两个参数。

## 6. 阳性对照与重评分

改评测器之前，先保证参考装配体是满分。`eval/assembly_scratch/build_reference_assemblies.py` 用宏工具按规格手工给每个测例搭了一个参考装配体：`gold_parts/reference_<--cases 名>.SLDASM`，这些文件不入库。当前结果（`eval/assembly_reference_rescore.json`，2026-10-02，滚动约束 rubric）：

| 参考装配体 | 装配 rubric | 运动分 | 关键测量 |
|---|---|---|---|
| 减速箱 | 100 | 100 | 输出 +0.1667；中心距 54 / 72 mm；零干涉 |
| 发动机 | 100 | 100 | 行程 49.998 mm；凸轮轴 0.5（反向）；中心距 72 mm；零干涉 |
| 台灯 | 100 | 100 | 活动度 3；3 个位姿点位最大误差 0.004 mm；零干涉 |
| 雨伞 | 100 | 100 | 3 个位姿伞骨尖端最大误差 0.012 mm；零干涉 |
| 电钻 | 100 | 100 | carrier1 0.2、carrier2 0.05，都同向；四对齿轮中心距都是 18 mm；零干涉 |
| 缝纫机 | 100 | 100 | 针杆行程 35.998 mm；旋梭 2 倍速反向；中心距 45 mm；零干涉 |
| 放映机 | 100 | 100 | 快门 1.0 同向；输片 0.25 反向；中心距 31.5 / 31.5 / 60 mm；零干涉 |

扩展测例（全部按修正后的 `rotate_deg` 重建，缝纫机也重建复核，仍是 100 / 100）：

| 参考装配体 | 装配 rubric | 运动分 | 关键测量（括号内为解算值） |
|---|---|---|---|
| 曲柄摇杆 | 100 | 100 | 摇杆摆角 47.385°（47.39°）；零干涉 |
| 偏置曲柄滑块 | 100 | 100 | 行程 30.455 mm（30.46）；零干涉 |
| 双缸泵 | 100 | 100 | 两缸行程 30.0 / 29.999 mm；位置相关系数 −0.992；零干涉 |
| 抽油机 | 100 | 100 | 曲柄 0.3333 反向；游梁摆角 47.691°（47.71°）；中心距 50 mm；零干涉 |
| 雨刮 | 100 | 100 | 两刮臂摆角都是 44.185°（44.19°）；零干涉 |
| 梁机 | 100 | 100 | 游梁摆角 40.001°（40.01°）；活塞行程 40.932 mm（40.94）；零干涉 |
| 钟表跨轮 | 100 | 100 | 跨轮 0.3333 反向；时针 0.0833 同向；中心距 30 / 30 mm；零干涉 |
| 手摇绞盘 | 100 | 100 | 卷筒 0.3333 反向；中心距 48 mm；零干涉 |
| 内齿圈转台 | 100 | 100 | 转台 0.25 同向；内啮合中心距 36 mm；零干涉 |
| 星型轮系 | 100 | 100 | 输出 0.25 反向；中心距 30 / 30 mm；零干涉 |
| 丝杠滑台 | 100 | 100 | 导程 2.0 mm/转；零干涉 |
| 齿轮曲柄滑块 | 100 | 100 | 曲柄 0.3333 反向；行程 39.997 mm；中心距 48 mm；零干涉 |
| 行星架输出 | 100 | 100 | 行星架 0.2 同向；中心距 30 / 30 mm；零干涉 |
| 挖掘机臂 | 100 | 100 | 活动度 3；3 个位姿点位最大误差 0.004 mm；零干涉 |
| SCARA | 100 | 100 | 活动度 4；3 个位姿（含升降 z）最大误差 0.004 mm；零干涉 |
| 缩放仪 | 100 | 100 | 活动度 2；3 个位姿最大误差 0.005 mm；零干涉 |
| 阿克曼转向 | 100 | 100 | 3 个转角下两侧转向节铰点最大误差 0.007 mm；零干涉 |
| 肘节夹钳 | 100 | 100 | 3 个手柄角最大误差 0.004 mm；零干涉 |
| 平行夹爪 | 100 | 100 | 活动度 2；对称、不对称共 3 个位姿最大误差 0.004 mm；零干涉 |
| 双横臂悬架 | 100 | 100 | 3 个跳动位置最大误差 0.005 mm；零干涉 |

扩展测例的 146 个标准零件在各自的零件接口 rubric 上全部 100 分。

第一次搭扩展测例的参考装配时，缩放仪、阿克曼转向、肘节夹钳的位姿分是 0：机构全部落在关于 X 轴的镜像装配模式。原因是 `insert_component` 的 `rotate_deg` 实际是顺时针（见 Workflow-CAD-Assembly.md §2.2 末尾的修正说明），修正后重建即恢复 100。

如果参考装配体不是满分，先查评测器或标准零件，不要先怀疑智能体。重新生成标准零件后，参考装配体也要重建：它引用的零件变了，旧的配合可能还在，但运动已经不对。这次发动机的阳性对照就是这样掉到 65 分的（曲柄、连杆、凸轮在参考装配体之后重建过），重建参考装配体后恢复 100。

搭这五个参考装配体时，评测器和标准零件做了这些修正：

- 电钻第二级（15/15/45 齿）：行星轮要转 12° 才能与齿圈啮合，这时朝太阳轮一侧正好是齿，所以太阳轮二插入时就要转好 12°。
- 缝纫机：送布连杆会扫过主轴轴线，最近距离只有 0.07 mm。所以轴从 40 mm 缩短为 32 mm（主轴 z∈[-18,14]，不伸进连杆那一层），旋梭盘移到 [-20,-12]，并在装配提示里写明各层的 z 范围。
- 雨伞 -Y 侧的伞骨、撑杆：同轴配合会把杆件的 +Z 转到 +Y，杆件前视面再与 -Y 面同向重合就会过定义。参考装配体里改用反向距离配合；智能体可以用别的面写出等价的约束。

`eval/assembly_scratch/rescore_assemblies.py` 不重跑智能体，只用当前评测器给已保存的装配体重新打分。遇到 SolidWorks 无响应时，它会自动重启 SolidWorks 和服务并重试一次；`--out` 里已有的行会跳过。

### 6.1 gen 条件结果（claude-sonnet-5，workflow v2，零件工具集 v1，2026-10-01）

结果在 `eval/assembly_gen_sonnet5_v2.json`，每个测例跑 2 次。这一轮早于五个新测例，也早于滚动约束 rubric；固定轴齿轮系在两种 rubric 下分数相同，所以表中数字仍然有效。

| 测例 | 轮次 | 零件合格率 | 装配 rubric | 运动分 | e2e | 装配智能体 |
|---|---|---|---|---|---|---|
| 减速箱 | r1 | 6/6 | 100 | 90 | 否 | 用满 50 次调用，未保存 |
| 减速箱 | r2 | 6/6 | 100 | 75 | 否 | 33 次调用后 finish |
| 发动机 | r1 | 10/10 | 18.1 | 0 | 否 | 9 次调用后停滞 |
| 发动机 | r2 | 10/10 | 46.67 | 0 | 否 | 26 次调用后停滞 |

- 零件阶段 32/32 全部合格，瓶颈在装配。
- 减速箱两次装配 rubric 都是满分。r1 的传动比实测 0.1667，与期望一致；扣的 10 分来自两对啮合齿轮的间隙测量为 0 mm。（2026-10-02 更正：这个 0 mm 是 `clearance_mm` 的缺陷造成的。它把以 m 为单位的平移直接作用在以 mm 读入的 STEP 上，旋转也取反了，所以任何齿轮对都量成 0。参考装配体旁边没有零件 STEP，齿隙检查被跳过，因此没有暴露。修正后由 `geomcheck/tests/test_clearance.py` 覆盖。）r2 的输出轴几乎不转（实测比 0.0009，方向也不对），扣掉传动比分和间隙分。
- 发动机两次都只插入 7 个零件，凸轮轴、凸轮、凸轮齿轮、飞轮和曲轴齿轮都没装上。凸轮轴缺失时运动检查直接报 `'camshaft'`，记 0 分。
- 停滞的原因：每次 LLM 调用的 `finish_reason` 现在记在结果的 `llm_calls_detail` 里，被判停滞的调用全部是 `finish_reason=length`，输出正好 8000 token，可见文字和工具调用都是 0。也就是说，模型把 `max_tokens=8000` 全部花在了隐藏的思考上，答案还没写出来就被截断，智能体把它当成"没有调用工具"。重跑结果在 `eval/assembly_gen_sonnet5_v2_engine_trace.json`：r1 是 11 次调用、最后 3 次截断；r2 是 35 次调用、其中 11 次截断，装配 rubric 分别为 18.1 和 46.67，与第一次一致。
- 发动机 r2 的另一个问题出在模型自身：它反复按 `cyl(d=24)` 去找机体上的缸孔，但机体是一块平板，没有这个孔，活塞的滑动副应该用平面来约束。
- 现在每行结果里有装配阶段的工具调用记录 `tool_calls`、智能体的文字回复 `agent_texts` 和每次调用的 `llm_calls_detail`；完整对话保存在产物目录的 `agent_messages.json`，每次调用的原始响应保存在同目录的 `agent_llm_responses.json`，包括 `reasoning_content`、`finish_reason` 和逐次 usage。`gen` / `e2e` 条件下零件生成阶段的完整记录保存在 `parts/<id>.agent.json`，内容有对话、原始响应、工具调用和最终状态，结果里的 `parts[].trace` 指向这个文件。
- 为排除截断，又把装配阶段的 `max_tokens` 放大到 32000（`--asm-max-tokens 32000`）重跑发动机，产物在 `eval/artifacts/assembly_gen_sonnet5_tok32k/`。r1 第 31 次工具调用（`joint`）时 SolidWorks 卡死，HTTP 等满 180 s 超时，之后活动装配文档丢失，剩下约 25 次调用全部报"没有活动的装配体文档"，保存时脚本崩溃，r2 没有跑。这是基础设施故障，不是模型结果；harness 已改为记 `infra_error` 后继续，需要在 SolidWorks 空闲时重跑。
- 同期用 TT Switch 跑 gemini-3.5-flash / gemini-3.1-pro 时，多轮工具调用的请求会间歇性返回 `400 model_param_invalid`，有时几分钟内全部被拒。这是网关上游的问题，所以没有 Gemini 的结果。

### 6.2 gen 条件结果：27 个测例（claude-sonnet-5，workflow v2，零件工具集 v2，2026-10-02 / 03）

27 个测例各跑 1 次，装配阶段 `max_tokens` 为 8000。配方、工具和提示词在阳性对照通过后冻结，跑完后没有改动。原始结果：扩展测例在 `eval/assembly_gen_sonnet5_v2_bench.json`；核心测例在 `eval/assembly_gen_sonnet5_v2_core.json`（由 `_core_part1.json`、`_core_part2.json` 合并）；放映机另见下文。

两张表评的是同一批保存下来的装配体（`eval/assembly_v2_rescore_new.json`）。旧口径用旧公式重算（`score_v1`），新口径用第 3、4 节的现行公式。表由 `eval/assembly_scratch/bench_table.py` 生成。

**旧口径**

| 测例 | 组 | 零件合格 | 装配 rubric | 运动分 | e2e | 装配智能体 |
|---|---|---|---|---|---|---|
| 减速箱 | 核心 | 6/6 | 100.0 | 100.0 | 是 | finish（16 次 LLM 调用） |
| 发动机 | 核心 | 10/10 | 18.1 | 0.0 | 否 | 停滞（9 次 LLM 调用） |
| 台灯 | 核心 | 5/5 | 100.0 | 100.0 | 是 | finish（40 次 LLM 调用） |
| 雨伞 | 核心 | 5/5 | 48.6 | 0.0 | 否 | 停滞（9 次 LLM 调用） |
| 电钻 | 核心 | 8/8 | 18.6 | 0.0 | 否 | 停滞（7 次 LLM 调用） |
| 缝纫机 | 核心 | 13/13 | 32.9 | 0.0 | 否 | 用满调用（50 次 LLM 调用） |
| 放映机 | 核心 | 8/8 | 52.5 | 25.0 | 否 | LLM 报错（15 次 LLM 调用） |
| 曲柄摇杆 | 扩展 | 7/7 | 60.0 | 25.0 | 否 | 用满调用（50 次 LLM 调用） |
| 偏置曲柄滑块 | 扩展 | 6/6 | 72.5 | 60.0 | 否 | 用满调用（50 次 LLM 调用） |
| 双缸泵 | 扩展 | 6/6 | 77.9 | 60.0 | 否 | 停滞（50 次 LLM 调用） |
| 抽油机 | 扩展 | 10/10 | 80.0 | 25.0 | 否 | 用满调用（50 次 LLM 调用） |
| 雨刮 | 扩展 | 8/9 | 35.6 | 0.0 | 否 | 停滞（10 次 LLM 调用） |
| 梁机 | 扩展 | 9/9 | 77.0 | 25.0 | 否 | 用满调用（50 次 LLM 调用） |
| 钟表跨轮 | 扩展 | 9/10 | 98.0 | 60.0 | 否 | 用满调用（50 次 LLM 调用） |
| 手摇绞盘 | 扩展 | 8/8 | 66.2 | 60.0 | 否 | 用满调用（50 次 LLM 调用） |
| 内齿圈转台 | 扩展 | 6/6 | 100.0 | 100.0 | 是 | finish（26 次 LLM 调用） |
| 星型轮系 | 扩展 | 7/8 | 61.7 | 0.0 | 否 | 用满调用（50 次 LLM 调用） |
| 丝杠滑台 | 扩展 | 6/6 | 100.0 | 100.0 | 是 | finish（33 次 LLM 调用） |
| 齿轮曲柄滑块 | 扩展 | 9/9 | 65.0 | 10.0 | 否 | 用满调用（50 次 LLM 调用） |
| 行星架输出 | 扩展 | 7/8 | 77.5 | 45.0 | 否 | 用满调用（50 次 LLM 调用） |
| 挖掘机臂 | 扩展 | 9/9 | 95.8 | 0.0 | 否 | 用满调用（50 次 LLM 调用） |
| SCARA | 扩展 | 6/6 | 100.0 | 100.0 | 是 | 停滞（31 次 LLM 调用） |
| 缩放仪 | 扩展 | 5/5 | 57.0 | 0.0 | 否 | 停滞（41 次 LLM 调用） |
| 阿克曼转向 | 扩展 | 5/5 | 100.0 | 100.0 | 是 | 用满调用（50 次 LLM 调用） |
| 肘节夹钳 | 扩展 | 7/7 | 48.5 | 0.0 | 否 | 停滞（11 次 LLM 调用） |
| 平行夹爪 | 扩展 | 5/5 | 100.0 | 100.0 | 是 | 用满调用（50 次 LLM 调用） |
| 双横臂悬架 | 扩展 | 7/7 | 100.0 | 100.0 | 是 | finish（40 次 LLM 调用） |
| **平均** | | **197/201** | **72.0** | **44.3** | **8/27** | |

**新口径**

| 测例 | 组 | 零件合格 | 装配 rubric | 结构成立 | 运动分 | 运动分（门控） | e2e |
|---|---|---|---|---|---|---|---|
| 减速箱 | 核心 | 6/6 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| 发动机 | 核心 | 10/10 | 3.3 | 否 | 0.0 | 0.0 | 否 |
| 台灯 | 核心 | 5/5 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| 雨伞 | 核心 | 5/5 | 7.1 | 否 | 0.0 | 0.0 | 否 |
| 电钻 | 核心 | 8/8 | 3.6 | 否 | 0.0 | 0.0 | 否 |
| 缝纫机 | 核心 | 13/13 | 22.2 | 否 | 0.0 | 0.0 | 否 |
| 放映机 | 核心 | 8/8 | 41.7 | 否 | 0.0 | 0.0 | 否 |
| 曲柄摇杆 | 扩展 | 7/7 | 45.3 | 否 | 39.3 | 0.0 | 否 |
| 偏置曲柄滑块 | 扩展 | 6/6 | 65.6 | 否 | 66.7 | 0.0 | 否 |
| 双缸泵 | 扩展 | 6/6 | 72.3 | 否 | 66.7 | 0.0 | 否 |
| 抽油机 | 扩展 | 10/10 | 80.0 | 否 | 41.7 | 0.0 | 否 |
| 雨刮 | 扩展 | 8/9 | 3.9 | 否 | 0.0 | 0.0 | 否 |
| 梁机 | 扩展 | 9/9 | 68.2 | 否 | 41.7 | 0.0 | 否 |
| 钟表跨轮 | 扩展 | 9/10 | 81.0 | 否 | 66.7 | 0.0 | 否 |
| 手摇绞盘 | 扩展 | 8/8 | 60.0 | 否 | 66.7 | 0.0 | 否 |
| 内齿圈转台 | 扩展 | 6/6 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| 星型轮系 | 扩展 | 7/8 | 57.5 | 否 | 0.0 | 0.0 | 否 |
| 丝杠滑台 | 扩展 | 6/6 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| 齿轮曲柄滑块 | 扩展 | 9/9 | 65.0 | 否 | 8.3 | 0.0 | 否 |
| 行星架输出 | 扩展 | 7/8 | 77.5 | 否 | 33.3 | 0.0 | 否 |
| 挖掘机臂 | 扩展 | 9/9 | 94.8 | 是 | 60.0 | 60.0 | 否 |
| SCARA | 扩展 | 6/6 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| 缩放仪 | 扩展 | 5/5 | 46.2 | 否 | 0.0 | 0.0 | 否 |
| 阿克曼转向 | 扩展 | 5/5 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| 肘节夹钳 | 扩展 | 7/7 | 18.8 | 否 | 0.0 | 0.0 | 否 |
| 平行夹爪 | 扩展 | 5/5 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| 双横臂悬架 | 扩展 | 7/7 | 100.0 | 是 | 100.0 | 100.0 | 是 |
| **平均** | | **197/201** | **63.5** | **9/27** | **47.8** | **31.9** | **8/27** |

核心 7 例：旧口径 rubric 53.0 / 运动 32.1；新口径 rubric 39.7 / 运动 28.6 / 门控 28.6；结构成立 2，e2e 2

扩展 20 例：旧口径 rubric 78.6 / 运动 48.5；新口径 rubric 71.8 / 运动 54.5 / 门控 33.0；结构成立 7，e2e 6

两种口径的区别：
- 装配 rubric：新口径只在适用项上归一化；"未过约束""已固定"两项乘完整度（角色装上并连到机架的比例）；活动度改为连续计分。所以零件没装全的行分数下降最多，例如发动机 18.1 → 3.3、雨伞 48.6 → 7.1、雨刮 35.6 → 3.9。
- 运动分：新口径按权重给部分分，旧口径是扣分制；多位姿模式下运动学精度和干涉分开计。挖掘机臂就是例子：三个位姿的点位误差都不超过 0.004 mm，但每个位姿有 2 处静态干涉，旧口径记 0，新口径记 60。
- 运动分（门控）：结构不成立（角色没装全、没连到机架或活动度不对）时运动分记 0。27 例中结构成立的只有 9 例。
- e2e 两种口径定义相同：零件全部合格，且运动检查通过。
- 齿侧间隙两种口径都不计分，只记录在 `motion.clearance`。原因是标准齿轮按零侧隙建模，参考减速箱两对啮合实测只有 0.009 / 0.006 mm，低于 0.02 mm 下限，这一项过不了阳性对照。（早先记录的 0 mm 来自 `clearance_mm` 的坐标换算 bug：平移的米没换成毫米、旋转取成了转置，已修正。）

与原始运行记录的分数相比，旧口径只有两处不同：
- 减速箱：运动分 90 → 100，e2e 否 → 是，原因是不再扣间隙分。
- 丝杠滑台：运动分 85 → 100。原始运行只采 13 帧，丝杠每帧转约 172°，转角展开出错，导程被算成 0.011 mm/转。重评用 121 帧密采样，测得导程 2 mm/转，与期望一致。

其余分数与原始记录一致。下面几行需要单独说明：
- 手摇绞盘、行星架输出、阿克曼转向、平行夹爪、钟表跨轮：旧评测器在运动评测之后才保存装配体，而运动评测时 SolidWorks 压缩了部分配合，所以存下的文件和智能体结束时的状态不同。这几行的装配 rubric 用重评时导出的 `rubric_inputs.json`，把被压缩的配合当作有效配合离线重算；运动分用原始运行的运动记录，按新公式离线重算（`rescore_unsuppress.py` 的 `motion_from_record`）。用同一个函数重算另外 16 行能在线重评的结果，与在线结果完全一致。
- 放映机：表中是 10-03 第二次运行的分数，结果在 `eval/assembly_gen_sonnet5_v2_core_projector_llm400.json`，零件、装配 rubric 和运动分都是当时实时评的。这次装配以 LLM 报错结束，智能体没有保存，装配 rubric 按评测时 SolidWorks 里的装配体状态计算。
  - 第一次运行：前两对齿轮啮合后，`mount_on_shaft`（把齿轮装到轴上并锁定转动）让 SolidWorks 的配合求解长时间占满 CPU，之后的请求都等满 180 s 超时或被重置连接，共 9 次 `SW_UNAVAILABLE`。重试后仍被污染，按第 1 节剔除。
  - 第二次运行：模型一次工具调用的参数不是合法 JSON。智能体返回了 `TOOL_ARGS`，但把这条非法参数原样留在对话历史里；网关把历史转成 Anthropic 的 `tool_use.input` 时解析失败，之后每次请求都返回 `400 Invalid request parameters`。这是 harness 的通用 bug，已修复：解析失败时把历史里的 `arguments` 换成 `{}`，原始参数的前 300 个字符放进错误信息；评测侧遇到 400 只重试一次。其余 26 例的结果里都没有出现非法 JSON 参数。
  - 第三次运行（修复后）：零件全部合格，装配阶段又在同一类配合上让 SolidWorks 求解器卡死 30 分钟以上，于是中止。第一次和第三次都卡在 12 个组件、4 对齿轮啮合的同一段配合链上，是 SolidWorks 的求解性能问题。

观察：
- 零件阶段基本不是瓶颈：201 个零件中 197 个合格。不合格的 4 个分别在雨刮、钟表跨轮、星型轮系、行星架输出，各 1 个。
- 失分主要在装配阶段。27 次中 13 次用满 50 次调用，8 次停滞（连续 3 次没有工具调用），5 次正常 finish，1 次 LLM 报错。停滞的 8 次里每次都有 3–6 次调用 `finish_reason=length`：模型把 8000 token 全部用在隐藏思考上，没写出答案，和 6.1 节发动机的现象相同。
- 装配 rubric 较高但运动分低的几例：
  - 梁机：游梁摆角 39.49°，在 2° 容差内，但活塞卡住不动（行程 0），13 个采样帧全部干涉；
  - 星型轮系：评测器在驱动件上找不到圆柱面，加不上马达；
  - 雨刮：缺 `link1` 角色。

扩展测例批次中 SolidWorks 崩溃了 3 次（RPC 服务器不可用、进程退出）。每次都结束评测进程、重启 SolidWorks，再从未完成的测例续跑，分批结果最后合并。偏置曲柄滑块、挖掘机臂、缩放仪首次运行时，装配中途丢失了活动装配文档，结果带 `infra_error` 且分数为 0；按第 1 节的约定剔除，改用补跑结果，补跑产物在 `eval/artifacts/assembly_gen_sonnet5_bench_rerun/`。梁机的智能体运行正常，但评测器首次运动采样时 SolidWorks 请求超时（180 s），运动分记为 0。这属于评测侧故障，所以没有重跑智能体，只对保存的装配体重新采样。

### 6.3 gen 条件结果（GPT-6-Astra，workflow v2，零件工具集 v2，2026-10-03）

> 本节分数由合并前的评测器算出：装配 rubric 与运动分是旧口径（第 3、4 节改版之前的公式），并且齿隙仍按“不在 [0.02, 2] mm 内扣 10 分”计分。现在齿隙只记录不计分（见 4.2 节），所以减速箱的运动分在现行旧口径下应为 100。产物目录不在本机，还没有按新口径重评，不能和 6.2 节的新口径表直接比较。

设置：

- 27 个测例各跑 1 次，`--condition gen --workflow v2`；
- 模型 `gpt-6-astra`，`--reasoning-effort medium`，经 TT Switch 网关轨道 B（`/native`），每个智能体运行带独立的 `X-Session-Id`；
- 题面是中文：规格里只有中文的零件提示和装配提示；
- 装配预算 50 次调用，`max_tokens` 8000；零件预算 40 次调用。

建模机器上 OCC 不可用（Windows 智能应用控制拦截了 cadquery-ocp 的 DLL），所以零件接口 rubric 和减速箱齿隙是在另一台机器上用 `regrade_offline.py` 从零件 STEP 补算的；装配 rubric 和其余运动检查在建模时就已算好。原始结果在 `eval/assembly_gen_gpt6_astra_medium_v2.json`，以补算后的 `eval/assembly_gen_gpt6_astra_medium_v2_regraded.json` 为准。整批没有 `infra_error`。

| 范围 | 测例 | 零件合格率 | 装配 rubric | 运动分 | e2e |
|---|---|---|---|---|---|
| 全部 | 27 | 201/201 | 87.4 | 60.3 | 11/27 = 0.41 |
| 核心测例 | 7 | 55/55 | 80.6 | 34.3 | 1/7 |
| 扩展测例 | 20 | 146/146 | 89.8 | 69.3 | 10/20 |

| 测例 | 装入角色 | 装配 rubric | 运动分 | e2e | 装配调用 | 主要问题 |
|---|---|---|---|---|---|---|
| 双级圆柱齿轮减速箱 `two_stage_gearbox` | 8/8 | 100 | 90 | 否 | 50（用满） | 两对齿轮齿隙 0.009 / 0.006 mm，低于 0.02 mm 下限（见下文） |
| 单缸曲柄滑块发动机 `single_cylinder_engine` | 9/12 | 70 | 0 | 否 | 50（用满） | 没插入凸轮轴、凸轮、凸轮齿轮；正时耦合 0 |
| 可调台灯 `adjustable_desk_lamp` | 7/7 | 100 | 100 | 是 | 50（finish） | — |
| 折叠伞骨架 `folding_umbrella` | 7/7 | 100 | 0 | 否 | 50（用满） | 装配 rubric 满分，3 个位姿都没过 |
| 电钻两级行星减速器 `drill_planetary_gearbox` | 11/11 | 73.75 | 25 | 否 | 50（用满） | 活动度不对，耦合 0.44；13 帧全干涉，carrier1 / planet2 / carrier2 卡住 |
| 缝纫机主传动 `sewing_machine_drive` | 10/15 | 45.38 | 0 | 否 | 50（用满） | 没插入曲柄销、针杆连杆、针杆销、送布连杆、送布摇杆 |
| 电影放映机传动 `film_projector_drive` | 12/12 | 75 | 25 | 否 | 50（用满） | 活动度不对，耦合 0.5；13 帧全干涉，快门与输片轮卡住 |
| 曲柄摇杆四杆机构 `crank_rocker_linkage` | 8/8 | 100 | 100 | 是 | 44（finish） | — |
| 偏置曲柄滑块机构 `offset_slider_crank` | 7/7 | 93.75 | 80 | 否 | 50（用满） | 关节 0.75；滑块卡住 |
| 双缸 180° 曲柄往复泵 `twin_cylinder_pump` | 11/12 | 72.62 | 0 | 否 | 50（用满） | 没插入第二个活塞 |
| 游梁式抽油机传动 `pumpjack_drive` | 11/11 | 100 | 100 | 是 | 50（用满） | — |
| 双刮臂雨刮联动 `windshield_wiper_linkage` | 12/13 | 79.89 | 65 | 否 | 50（用满） | 少一根销；第二刮臂卡住 |
| 蒸汽梁机 `beam_engine` | 10/12 | 69.29 | 25 | 否 | 50（用满） | 没插入活塞杆和一根销；13 帧干涉 |
| 钟表跨轮 `clock_motion_works` | 10/10 | 77.5 | 60 | 否 | 50（用满） | 活动度不对，耦合 0.62；2 帧干涉 |
| 手摇绞盘 `hand_winch` | 8/8 | 100 | 100 | 是 | 46（finish） | — |
| 内齿圈转台 `turntable_ring_drive` | 6/6 | 100 | 100 | 是 | 39（finish） | — |
| 星型齿轮减速器 `star_gear_reducer` | 8/8 | 100 | 100 | 是 | 50（用满） | — |
| 竖直丝杠滑台 `lead_screw_stage` | 6/6 | 100 | 65 | 否 | 46（finish） | 手轮与滑台卡住，导程不对 |
| 齿轮驱动曲柄滑块 `gear_driven_slider_crank` | 10/10 | 100 | 100 | 是 | 50（用满） | — |
| 单级行星减速器 `epicyclic_carrier_output` | 8/8 | 66.25 | 25 | 否 | 50（用满） | 耦合 0.25；13 帧全干涉，行星轮与行星架卡住 |
| 挖掘机工作臂 `excavator_arm` | 9/11 | 67.75 | 0 | 否 | 50（用满） | 没插入铲斗和铲斗销；3 个位姿都没过 |
| SCARA 机械臂 `scara_arm` | 6/6 | 100 | 100 | 是 | 50（用满） | — |
| 2 倍缩放仪 `pantograph` | 10/10 | 100 | 66.67 | 否 | 50（用满） | 3 个位姿过 2 个 |
| 阿克曼转向梯形 `ackermann_steering` | 8/8 | 100 | 100 | 是 | 50（用满） | — |
| 肘节夹钳 `toggle_clamp` | 8/8 | 100 | 100 | 是 | 50（用满） | — |
| 平行四边形夹爪 `parallel_gripper` | 13/15 | 69.83 | 0 | 否 | 50（用满） | 没插入左右两个夹爪 |
| 双横臂悬架 `double_wishbone` | 10/10 | 100 | 100 | 是 | 50（用满） | — |

“装入角色”是规格 `gold_roles` 里被智能体插入的角色数。装配 rubric 未满分时，“主要问题”里的关节、耦合是 3.2 节对应项的得分比例。

分析：

- 零件阶段不是瓶颈。201 个零件全部 100 分，平均每个 4.1 次 LLM 调用，最多 9 次。
- 瓶颈在装配的调用预算。22 个测例用满了 50 次，只有 5 个自己调用了 `finish`；有 13 个测例一次 `check_motion` 都没调，是在自检之前被截停的。
  - 1325 次装配调用里有 1322 次只发一个工具调用，没有并行。
  - 查询类调用占了近一半：`get_assembly_state` 354 次、`check_dof` 281 次，而 `insert_component` 只有 243 次。
  - 7 个测例因此没把零件插完（发动机、缝纫机、双缸泵、雨刮、梁机、挖掘机、夹爪）。运动检查要求的角色不存在时运动分记 0，所以这几个测例的运动分大多是 0。
- 行星和多级齿轮系是第二类失败。电钻、放映机、行星架输出都插齐了零件，但耦合项只有 0.25–0.5，活动度不对，13 帧全部干涉，输出件卡住，说明啮合相位或中心距没做对。钟表跨轮是同一类问题，只有 2 帧干涉。
- 没有截断：`finish_reason=length` 出现 0 次。推理 token 中位数 0、p95 59、最大 629。prompt 缓存命中率 96.4%。
- token：零件阶段 6.4M，装配阶段 32.8M。耗时：零件 1.7 h，装配 3.4 h。
- 与 6.1 节 Sonnet 5 相比只有减速箱和发动机两个测例可对照，而且 rubric 和零件工具集版本不同：减速箱 100 / 90（Sonnet 两次为 100 / 90 与 100 / 75），发动机 70 / 0（Sonnet 两次为 18.1 / 0 与 46.67 / 0）。

齿隙下限的问题：修正 `clearance_mm` 后，减速箱两对齿轮实测间隙为 0.009 mm 和 0.006 mm，规格 `clearance_mm: [0.02, 2.0]` 不满足，运动分扣 10、e2e 判否。`planner/gears.py` 里外啮合直齿轮的齿厚正好是半个齿距，不加侧隙（只有内齿圈加 0.05m），所以中心距和相位都对的一对齿轮理论间隙就是 0，几微米来自渐开线的折线近似。也就是说，现在的齿轮模型几乎达不到 0.02 mm 的下限。这个问题以前没有暴露，是因为旧的 `clearance_mm` 总是量成 0（见 6.1 节更正）。按“看过结果后不改评测”的约束，这里没有改阈值；如果把下限改成 0、只用它检出干涉，减速箱运动分为 100，e2e 为 12/27 = 0.44。是否调整由后续统一决定。

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
venv\Scripts\python.exe -X utf8 eval\eval_assembly_scratch.py --cases desk_lamp,umbrella,drill_gearbox,sewing_machine,projector --condition gen --workflow v2
# 扩展测例：--cases bench 表示全部 20 个
venv\Scripts\python.exe -X utf8 eval\eval_assembly_scratch.py --cases bench --condition gen --workflow v2 --repeats 1 --model claude-sonnet-5

# 扩展测例的规格、零件 rubric、装配 rubric 由 bench_cases.py 生成；report 打印解算出的摆角 / 行程 / 传动角
venv\Scripts\python.exe -X utf8 -m eval.assembly_scratch.bench_cases write
venv\Scripts\python.exe -X utf8 -m eval.assembly_scratch.bench_cases report

# 第二批 23 个：--cases bench2；write 另外写解析锚点；bench2_check 是不用 SolidWorks 的四项自检
venv\Scripts\python.exe -X utf8 -m eval.assembly_scratch.bench_cases2 write
venv\Scripts\python.exe -X utf8 -m eval.assembly_scratch.bench_cases2 report
venv-occ\Scripts\python.exe -X utf8 -m eval.assembly_scratch.bench2_check
# 第二批的可达性验证：只用 v3 公开工具重放 oracle 轨迹并按评测器打分；supervise 断线自动重启、按 progress.json 续跑
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\oracle_supervise.py [name ...]
venv\Scripts\python.exe -X utf8 eval\eval_assembly_scratch.py --cases bench2 --condition gen --workflow v2 --repeats 1 --model claude-sonnet-5

# 只重评部分测例的阳性对照（参数是 case_id）
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_assemblies.py --reference --cases folding_umbrella --out eval\assembly_reference_rescore.json

# 重评分已保存的装配体：结果 JSON=artifacts 目录，可给多组
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_assemblies.py --out eval\assembly_rescore.json `
    eval\assembly_scratch_result.json=eval\artifacts\assembly_scratch

# 从参考装配体导出关节锚点（改了参考装配体或 rubric 的 joints 后重跑）
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\derive_joint_anchors.py

# 重评分结果整理成旧口径 / 新口径并排的表
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_table.py eval\assembly_rescore.json
# 旧评测压缩过配合的行：rubric 解除压缩离线重算，运动分由原始运动记录按新公式重算
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\rescore_unsuppress.py eval\assembly_v2_rescore_new.json
# 6.2 节的旧口径 / 新口径两张大表（--extra 给出没有可重评装配体的行）
venv\Scripts\python.exe -X utf8 eval\assembly_scratch\bench_table.py eval\assembly_v2_rescore_new.json --extra eval\assembly_gen_sonnet5_v2_core_projector_llm400.json
# 建模机器上 OCC 不可用时（零件全判不合格、齿隙为 None），把结果 JSON 和 --output-dir 拷到能跑 OCC 的机器上离线补算
# 零件接口 rubric、齿隙、运动分与 e2e（只需检查器环境）
venv-occ/bin/python -X utf8 eval/assembly_scratch/regrade_offline.py eval/assembly_gen_xxx.json `
    --artifacts eval/artifacts/assembly_gen_xxx --output eval/assembly_gen_xxx_regraded.json

# 离线单测（不占 SolidWorks）
venv\Scripts\python.exe -m pytest test\test_assembly_rubric.py test\test_assembly_motion.py test\test_assembly_workflow_v2.py test\test_assembly_workflow_v3.py -q
# 两批扩展测例的零件 rubric 阳性 / 阴性对照（OCC，约 3 分钟）
venv-occ\Scripts\python.exe -X utf8 -m pytest geomcheck\tests\test_bench_parts.py -q
```

## 8. 新增测例

每个测例需要准备：

1. 规格 `eval/assembly_scratch/<name>.yaml`：
   - `case_id`、`title`、关键尺寸；
   - `parts`：每个零件的 `id`、`prompt`、`extra_tools`；
   - `gold_roles`：角色 → 零件 id；
   - `assembly.prompt`：只描述功能和约束关系，不给最终坐标；
   - `motion`：马达模式写 `driver`、`expect_fixed`、`expect_moving`，再按机构选用 `ratios`（或旧的 `expect_ratio`/`expect_same_direction`）、`expect_stroke_mm` + `stroke_role`/`stroke_axis`、`timing_*`、`clearance_pairs`；多位姿模式写 `mode: poses`、`frame`、`expect_fixed`、`tolerance_mm`、`poses`。
   - 可选 `gold_dir`：标准零件目录名，默认等于规格文件名。
2. 零件接口 rubric `eval/assembly_scratch/rubrics/<part_id>.yaml`，每个零件 id 一份。
3. 装配 rubric `eval/assembly_scratch/assembly_rubrics/<case_id>.yaml`：`ground`、`mobility`、`bodies`、可选的 `flex`、`joints`（type 可以是列表）、`couplings`、`center_distance_tol_mm`。
4. 标准零件：在 `build_standard_parts.py` 里加生成函数。画圆一律带 `noInference: true`，生成后用 OCC 核对孔径和孔位。
5. 参考装配体：在 `build_reference_assemblies.py` 里用宏工具搭一份，要求装配 rubric 和运动分都是 100、静态和逐帧都零干涉、零件在几何上真的装在一起（检查位姿，不能只看运动学）。同一零件用在多个角色上时，组件名不是 `<角色>-1`，`_finish` 会把角色 → 组件名另存为 `reference_<name>.aliases.json`，`rescore_assemblies.py --reference` 优先读它。
6. 登记：只需在 `eval_assembly_scratch.py` 的 `CASE_FILES`（规格文件名 → case_id）里加一行，`rescore_assemblies.py` 直接复用。

由基本体拼成的平面机构，更省事的做法是在 `bench_cases.py` 里加一个 `@case` 函数：给出零件、角色、提示词、运动规格、rubric 和参考装配的搭法（`Ref` 记录每个角色的绝对 z，关节偏距自动算），然后运行 `write`。上面 1–6 步都由它完成：`build_standard_parts.py`、`build_reference_assemblies.py`、`CASE_FILES` 会自动登记 `bench_cases.CASES` 里的全部测例。闭环多、组件多的机构用 `bench_cases2.py` 的声明式写法（`layout` + `steps`），可以先用 `bench2_check` 离线验证，再上 SolidWorks。

约束：

- 测例来源不能是 CAD-1K 的原题。CAD-1K 只做统计和挖掘，不用作测试集。
- 先冻结配方和工具，再跑试点对比。试点结果不能反过来调提示词、工具或评测代码；发现的问题记下来，下一轮冻结前统一修。
- rubric 只写机构必须满足的结构，允许几种等价写法时用 type 列表或 `flex` 表达，不按某个智能体的写法定制。

## 9. 已知局限

- 活动度是瞬时值。奇异位形（比如曲柄滑块的死点）下会偏高；参考装配体都避开了死点。
- 槽口配合未建模；齿条小齿轮、线性耦合只出现在 `skipped` 里，不计入活动度。
- 马达模式依赖 Animation，Motion Analysis 插件本机未加载。目前只支持旋转马达驱动，驱动件的转角按绕 Z 轴的转角读取，所以马达模式测例的转动轴都要沿装配 Z 轴。直线输入或多自由度的机构改用多位姿模式。
- 中心距和齿相位按齿轮零件的原点和局部 Z 轴计算，要求齿轮零件原点在轴线上。
- SolidWorks 偶尔会满载数分钟、不响应。正式评测里出现这种情况会记成采样错误，需要人工识别为基础设施故障后补跑。
- 齿轮多的装配体建起来很慢。搭参考装配体时，电钻用了约 14 分钟，缝纫机约 19 分钟，大部分时间是请求在等 SolidWorks 主线程，单次最长停了约 6 分钟；而同一个装配体重新打开后评分只要 1 分钟左右。智能体每次工具调用的 HTTP 超时已由 180 s 放大到 900 s；2026-10-05 之前的 gen/e2e 结果按 180 s 跑，要留意其中的 `infra_error` 和超时。
- 多位姿模式靠临时角度配合加静态重建来摆位姿。静态重建不传递齿轮配合，带锁转的同轴也可能被放松而不报错，所以位姿规格里每条被齿轮隔开的支链都要单独设角度（`gear_sync_gripper`）。
- Animation 马达的实际转速约为名义 rpm 的 9.5 倍，`sweep_deg: 360` 实际转了约 9.5 圈。周期长于驱动一圈的构件，行程期望要按它的整周期写，并把 `sweep_deg` 设到覆盖一个整周期，离线合成检查才与 SolidWorks 一致。
- 渐开线齿形用折线近似：每个齿约 22 段，48 齿的内齿圈约有 1060 个面，取面列表一次要 1–4 s。
- 内啮合的齿相位：`mesh_gears` 只转动外齿轮那一侧。如果它与刚性固连在别的行星架上的太阳轮相啮合，事后转动不会带动太阳轮，需要在插入时就把太阳轮转到正确相位（参考装配体里第二级太阳轮转了 12°）。
- 多位姿模式只检查 `poses` 里列出的离散位姿，不检查位姿之间的连续运动。
- 马达模式判断“卡住”时只比首帧、中间帧和末帧。如果机构在这几帧上恰好回到同一位形（比如对称的双缸、起始角 0° 的往复件），正确的装配也会被判成卡住。第二批测例都避开了这类起始角，评测器本身没改。
