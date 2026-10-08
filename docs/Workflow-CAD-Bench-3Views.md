# Workflow-CAD-Bench：三视图 track 的评测指南

三视图 track 共有 **50 道看图建模题**。每道题交给建模系统一张工程图（三视图 PNG）和一句中性题面，要求按图建出实体并导出 STEP。打分用的检查器和 text-to-CAD track 是同一套；区别是这里的 rubric 只检查图纸上标注或画出的内容，另外附一个基于投影图像的相似度分。

其他两个 track 的文档：text-to-CAD 见 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md)，复杂装配体见 [`Workflow-CAD-Bench-Assembly.md`](./Workflow-CAD-Bench-Assembly.md)。

**评测与建模系统解耦。** 每道题交一个 STEP 文件即可，用什么模型、什么 CAD 软件、什么方式建模都可以。打分只读 STEP 的几何。

---

## 0. 环境

打分不需要 SolidWorks，只需要 Python 和 OpenCASCADE 的 Python 绑定。安装与验证步骤同 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md) 第 0 节。依赖里的 `pillow` 只在重新生成图纸和输出差异图时用到。以下命令都在 `LLMPlanner` 目录下执行。

本 track 用到的代码和数据：

| 路径 | 内容 |
|---|---|
| `eval/rubrics_three_view/<case>.yaml` | 题面（`prompt` / `prompt_en`）、图纸标注清单 `drawing`、rubric 条目 |
| `eval/three_view/drawings/<case>.png` | 交给建模系统的图纸（第三角画法：俯视图在主视图上方，右视图在主视图右方） |
| `eval/three_view/references/<case>.step` | 参考实体，图像分的比较对象 |
| `geomcheck/three_view.py` | 打分入口：rubric + 图像分 |
| `geomcheck/views.py` | 图像分 |
| `geomcheck/drawing.py` | 由参考实体投影出图纸 |
| `geomcheck/tests/refs_three_view.py` | 参考实体的构造函数；直接运行会重新生成图纸和参考 STEP |
| `geomcheck/tests/test_refs_three_view.py` | rubric 与图像分的验证测试 |

---

## 1. 任务

共 50 道题，分两部分：B/C 组 13 道来自 Web Demo 的三视图样例；D 组 37 道是为 benchmark 新设计的零件。

### 1.1 B/C 组：Web Demo 样例

这 13 道题来自 Web Demo 的三视图样例（`datasets/manual_three_view/`）。原样例不适合直接用作 benchmark，原因有三点：
- C1–C3 的图纸只画了外框；
- B22 没有隐藏线和内腔尺寸，B07 缺少凹槽和孔的标注；
- 文字提示里写了 SolidWorks 建模步骤。

因此 benchmark 版本**重新生成了这 13 张图纸**：先按图纸尺寸用 OpenCASCADE 构造参考实体，再用 HLR 投影出三视图。可见边画粗实线，隐藏边画虚线，尺寸、引线、中心线和注释按每题 rubric 里的 `drawing` 清单绘制。原 demo 的图纸和数据保持不变。

与原 demo 相比，有三处几何改动：
- B06 的 R3 圆角从“3 mm 厚板的长边”改到板的四个竖直角。原来的圆角半径等于板厚，是退化几何。
- C3 的矩形齿嵌入齿根圆 1 mm，避免齿与轮盘只在一条线上相切。
- 体积一律取 OCC 参考实体的精确值。原 GT 忽略了圆角，圆也用多边形近似。

### 1.2 D 组：自行设计的零件

D 组 37 道题全部是自行设计的常见机械零件，参考实体、图纸和 rubric 的生成流程与 B/C 组相同。没有使用任何外部数据集的图纸或模型，这样一来不涉及许可问题，题目也不会出现在模型的训练数据里。设计时让每道题覆盖不同的建模操作组合：
- 拉伸截面：V 形块、燕尾槽、T 形槽、U 形槽、导轨；
- 回转：阶梯轴、V 带轮、带头销轴（退刀槽、倒角）、法兰轴套、立柱底座；
- 孔：阵列孔、沉孔、沉头孔、盲孔、交叉孔、径向孔、只穿一侧壁的侧孔；
- 筋板与圆角，斜切面；
- 长圆孔、键槽、开口槽（开口轴环）；
- 抽壳与腔体：圆杯、壳体罩、阀体内孔、夹具板凹槽；
- 公切线轮廓（连杆）；
- 等距阵列（散热片、齿条）；
- 两实体装配位（管夹）。

### 1.3 题面与任务列表

题面只有一句中性的话，例如“根据所附三视图（第三角画法，单位 mm）建模 L 型支架，单一实体。”，全部尺寸都从图纸读取。C3 和 D17 的题面另外说明零件由两个分开的实体组成。

| 类型 | 任务（case id，括号内为显式条目数） |
|---|---|
| 基础零件（9） | L 型支架 `b01_l_bracket`（5）、带孔平板 `b02_plate_with_holes`（5）、T 型支架 `b03_t_bracket`（6）、键帽 `b04_keycap`（6）、带圆角和底槽的键帽 `b05_keycap_full`（6）、门锁扣板 `b06_lock_striker`（6）、86 型开关面板 `b07_switch_panel`（6）、齿轮盘 `b11_gear_disc`（5）、圆碗 `b22_round_bowl`（4） |
| 组合零件（4） | 迷你机械键盘 `c1_mech_keyboard`（7，65 个键帽阵列）、齿轮减速器外壳 `c2_gearbox`（8）、齿轮组 `c3_gear_pair`（7，两个实体）、一体式桌面笔筒 `c4_desktop_pen_holder`（10） |
| 投影基础零件（18） | V 形块 `d01_v_block`（7）、法兰盖 `d04_flange_cover`（8）、阶梯轴 `d05_stepped_shaft`（6）、U 形叉头 `d06_u_clevis`（7）、偏心轮 `d07_eccentric_cam`（7）、角钢支架 `d09_angle_bracket`（7）、沉孔压板 `d10_clamp_plate`（7）、燕尾滑块 `d11_dovetail_slide`（6）、切角支座 `d12_cut_block`（7）、齿条 `d18_gear_rack`（7）、T 形槽块 `d21_t_slot_block`（7）、法兰轴套 `d22_flanged_bushing`（8）、十字连接板 `d23_cross_plate`（7）、U 形槽座 `d24_u_channel`（7）、带头销轴 `d25_step_pin`（8）、方形法兰座 `d26_square_flange`（8）、导轨 `d27_guide_rail`（6）、开口轴环 `d35_shaft_collar`（6） |
| 投影组合零件（19） | 轴承座 `d02_bearing_block`（10）、带筋支架 `d03_rib_bracket`（9）、法兰三通 `d08_pipe_tee`（7）、双槽 V 带轮 `d13_v_pulley`（9）、连杆 `d14_link_rod`（7）、电机安装座 `d15_motor_mount`（8）、散热器 `d16_heat_sink`（6）、管夹 `d17_pipe_clamp`（7，两个实体）、合页片 `d19_hinge_leaf`（6）、带筋底座 `d20_ribbed_base`（7）、带缘圆杯 `d28_round_cup`（7）、吊架 `d29_hanger_bracket`（7）、阀体 `d30_valve_body`（7）、摇臂 `d31_lever_arm`（7）、刀具座 `d32_tool_holder`（7）、壳体罩 `d33_shell_housing`（8）、夹具底板 `d34_fixture_plate`（7）、双法兰短管 `d36_flanged_tube`（7）、立柱底座 `d37_column_base`（9） |

50 题共 349 个显式条目（B/C 组 81 个，D 组 268 个）。

---

## 2. 提交要求

- 一道题一次运行对应一个 STEP 文件，命名为 `<case>_r<k>.step`，k 从 1 开始，放在同一个目录里。
- 单位是毫米，位置和朝向不限，建法不限，缺失的运行记 0 分。这几条与 text-to-CAD track 相同，详见 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md) 第 3 节。
- C3 和 D17 要求恰好两个有效实体，其余 48 题要求单一实体。

---

## 3. 打分

**主分是 rubric**，条目、得分公式、合格标准和容差与 text-to-CAD track 相同（[`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md) 第 4 节）。区别在于每个条目只检查图纸上标注或画出的内容，`source` 写明出自哪个视图的哪个标注或注释，例如“主视图：孔心距左端 20、孔距 40、距底边 20”。检查都在零件自身的坐标系里进行，摆放方向不限。

**辅助指标是图像分**（`geomcheck/views.py`），把提交实体和参考实体沿三个正交方向投影后逐像素比较：
- 每个方向生成四张图，像素边长取参考件最大尺寸的 1/400：轮廓；近侧深度和远侧深度（对应正、背两个视图）；沿视线的材料厚度（类似 X 光片，能看到盲腔、背面凹槽和内部空腔）。
- 一个像素算“一致”，需要两件在该像素都有材料，且三种深度之差都不超过参考件最大尺寸的 0.5%。允许 1 个像素的位置误差。
- 某方向的得分是一致像素数除以两件轮廓并集的像素数；图像分是 100 乘以三个方向得分的平均。
- 提交件可以任意摆放：比较前在 24 种正交旋转中取最好的一种。**镜像不算旋转**，所以镜像件得不到满分。

图像分对图纸上任何可见差异都敏感，但不区分差异是否重要，所以只作为辅助指标单独报告，合格与否只看 rubric。

`scores.json` 的每条记录比 `geomcheck.rubric` 的输出多一个 `image` 字段，内容包括 `image_score`、`silhouette_iou`、三个视图各自的得分，以及选中的旋转。

---

## 4. rubric 与图像分的验证

`geomcheck/tests/test_refs_three_view.py` 共 368 个测试：
- 从参考 STEP 读回的实体得 100 分，体积与 rubric 一致。
- 133 个针对性错误变体（B/C 组 28 个，D 组 105 个）恰好在预期条目上失败。例如：孔距 36、凹槽深 5、去掉充电线孔、齿轮中心距 72、12 列键帽；D 组如燕尾角 50°、6 片散热片、去掉油孔、沉孔深 4、管夹无间隙、退刀槽移位、开口加宽、去掉键槽、刀孔少一个。少数变体会连带另一个条目失败，原因是两处几何确实相互关联（例如齿条改成 11 个齿后，沉孔上方正好是齿）；这些情况在测试里逐条注明。
- 参考实体在原位和旋转后的图像分都是 100（旋转后要求不低于 99.9）。
- 133 个错误变体和 C4 的镜像件，图像分都低于 99.5。

只跑本 track 的测试：

```powershell
venv-occ\Scripts\python.exe -m pytest geomcheck/tests/test_refs_three_view.py -q -p no:cacheprovider
```

### 4.1 L1 workflow 的可解性：oracle 调用轨迹

上面验证的是“存在一个满分的几何形状”。这里再验证另一件事：用 L1 的 24 个工具（与 text-to-CAD 共用、也就是 LLM 实际拿到的那套工具）能把它建出来。`eval/three_view_l1_reference.py` 逐题按图纸尺寸依次调用工具（不调用 LLM），几何与 `geomcheck/tests/refs_three_view.py` 的参考实体一致、摆放也相同。每题的调用轨迹（工具名、参数、执行后体积）存在 `eval/three_view/oracle_l1/<case>.json`，汇总在同目录的 `summary.json`。

```powershell
venv\Scripts\python.exe -X utf8 -u eval\three_view_l1_reference.py [case ...]     # 需要 SolidWorks 与 C# 服务
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view --artifacts eval\artifacts\three_view_l1_reference
```

结果（2026-10-05，SolidWorks 2025）：

- 50 题 rubric 全部 100 分，全部合格；所有工具调用一次成功，没有失败后回滚的调用。
- 图像分 49 题为 100，`b05_keycap_full` 为 90.55（原因见下文第 2 点）。

轨迹长度。按每轮 LLM 调用发一个工具调用、最后再调一次 `finish` 计算，这是不做任何状态查询和返工时的下限：

| 最少 | 中位数 | 平均 | p90 | 最多 |
|---|---|---|---|---|
| 2 | 6 | 6.9 | 11 | 17 |

- 按组平均：B 组 3.6 次，C 组 8.0 次，D 组 7.5 次。
- 最长的几题：`d19_hinge_leaf` 17 次，`d06_u_clevis` 15 次，`c4_desktop_pen_holder` 14 次，`d08_pipe_tee` 13 次。
- 50 条轨迹共 344 次工具调用：`extrude` 90 次，`ref_plane` 82 次，`hole` 70 次，`cut` 38 次，另有少量 `revolve`、`fillet`、`counterbore`、`linear_pattern`。`ref_plane` 占 24%，因为每个偏离基准面的草图都要先单独建一个平行基准面。
- 实际运行的智能体还要调用 `get_model_state`、`render_views` 核对，并修正读图错误，调用数会明显高于这个下限。最长的 oracle 是 17 次，沿用 text-to-CAD 的 40 次预算约有 2.3 倍余量。

构造过程中发现的两个工具问题：

1. **`countersink` 做不出锥形沉头。** 它和 `counterbore` 用的是同一个实现（先切圆柱沉头、再打通孔），工具说明却写“沉头锥孔”。`d19_hinge_leaf` 的图纸要求 Ø9×90° 锥形沉头，按说明调用 `countersink` 只能拿到 66.67 分：没有 45° 锥面，`holes` 和 `volume` 两项失败。oracle 改为在 X = 40 的基准面上画锥面截面，用 `revolve(cut=true)` 旋转切出，再 `linear_pattern` 复制到另外两个孔。建议在 C# 端实现真正的锥形沉头，或者改正工具说明；否则模型照说明调用这个工具，这道题必然失败。
2. **`fillet` 一次只能选一条边，并且默认沿相切边传播。** `b05_keycap_full` 要给顶面四条棱倒 R1.5 圆角。逐条倒圆时，前两条正常；第 3、4 条会顺着前两条圆角端部的圆弧（与它们相切）传播到四条斜棱上，结果多出 4 条斜棱圆角和 4 个球面角，体积是 1598.72（参考 1612.22）。rubric 仍然满分，因为体积差 0.84%，在 1% 容差内；但图像分只有 90.55。现有工具既不能一次选多条边，也不能关闭相切传播，所以这道题的体积离容差边界很近，严格一些的检查会把它判错。

这两个问题在 L1_v2 中已修复（`planner/workflow/tools_v2.py`，`--action-level L1_v2`）。L1 的行为保持不变，上面的 oracle 轨迹与结论仍然对应 L1：

- `countersink` 改为真正的锥形沉头。先用带拔模角的盲切在表面切出口部直径 `head_diameter`、锥角 `angle_deg`（默认 90°）的锥台，深度由两个直径和锥角算出；再打 `through_diameter` 的孔。只给 `head_depth` 时，由它反推锥角。结果里返回 `cone_removed_mm3` 和锥台理论体积 `cone_expected_mm3`，可以核对。真机上在顶面、底面各验证过一次，锥面去除量与锥台公式一致（Ø9→Ø4.5、90°：每孔 83.50 mm³）。
- `fillet` / `chamfer` 的射线数组改为一次选中全部棱，在同一个特征里倒完；`fillet` 另加 `propagate=false`，用来关闭相切传播。用 b05 的步骤真机重放：一次给四条射线，最终体积 1612.23；逐条倒圆但设 `propagate=false`，也是 1612.23。L1 逐条倒圆仍是 1598.72。参考体积为 1612.22。

---

## 5. 命令

```powershell
# 导出题目：每行 {"case", "kind", "prompt", "image"}，image 是相对 LLMPlanner 的图纸路径
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view tasks --output tasks_three_view.jsonl

# 打分：rubric + 图像分；--diff-dir 输出差异图（灰 = 一致，红 = 深度不一致，蓝 = 多出的材料，绿 = 缺少的材料）
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view --artifacts my_tv_run --output my_tv_scores.json --diff-dir my_tv_diffs

# 汇总：在 text-to-CAD 的指标之外附带平均图像分（缺失的运行记 0）
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view summary my_tv_scores.json --repeats 2

# 修改标注清单或参考实体后，重新生成图纸 PNG 和参考 STEP（可在末尾列出 case id，只重新生成这几题）
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.tests.refs_three_view
```

### 5.1 用 Workflow-CAD 跑

工作方式、环境要求和完整参数见 [`Workflow-CAD-3Views.md`](./Workflow-CAD-3Views.md)，这里只列要点。用 `eval/eval_three_view_bench.py`。它把图纸和题面发给 `/api/run`，把每次运行的 STEP 存到 `eval/artifacts/<name>/<case>_r<k>.step`，再按上面的命令离线打分。默认走自部署 `Qwen3.8-Flash-Next-FP8` 的 **L1_v2 端到端**，`provider=qwenlocal`、`reasoning_effort=medium`：

- 与 text-to-CAD 共用同一个 L1 Agent、同一套 24 个工具和同一份系统提示词。区别只有输入：图纸作为图片放在首条用户消息里，Agent 每次调用模型都能看到它。另附一段固定说明，交代图纸的视图方位与模型坐标的对应关系，所有题目都一样。
- `--action-level L1_v2` 使用与 text-to-CAD 完全相同的 v2 workflow，并默认开放 `view_model`；`--no-view-tool` 可关闭它。`render_views` 是三视图 track 额外提供的图纸对照工具，二者互不替代。
- 没有单独的读图阶段，`--plan-model` 必须是支持图片输入的多模态模型。
- 带图时 Agent 额外获得一个 `render_views` 工具：把当前零件导出为 STEP，在 `venv-occ` 里投影成不带标注的三视图（版式与输入图纸相同），以图片形式返回，并附包围盒和体积，方便模型逐个视图对照。上下文里只保留最新一张投影图。投影只用当前零件，不接触参考实体。`--no-views-tool` 关掉这个工具，用于消融。
- 后端所在环境没有 OCP，投影通过子进程调用 `venv-occ\Scripts\python.exe`；放在别处时用环境变量 `GEOMCHECK_PYTHON` 指定。

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py --repeats 2 --name three_view_l1v2_qwen38
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view --artifacts eval\artifacts\three_view_l1v2_qwen38 --output eval\three_view_l1v2_qwen38_scores.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view summary eval\three_view_l1v2_qwen38_scores.json --repeats 2
```

自部署 Qwen3.8-Flash-Next-FP8 使用与 text-to-CAD 相同的 provider、推理强度和上下文配置：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_three_view_bench.py `
    --provider qwenlocal --plan-model Qwen3.8-Flash-Next-FP8 `
    --action-level L1_v2 --reasoning-effort medium --max-llm-calls 40 `
    --ids b01_l_bracket --repeats 1 --name three_view_l1v2_qwen38_smoke
```

Agent 会按模型名自动采用 73,728 token 上下文上限，并使用与 text-to-CAD 相同的超长处理逻辑。该模型部署还必须支持图片输入；先跑上述单题 smoke，确认无误后再去掉 `--ids`。

`runs.json` 里每次运行另记 LLM 调用数、工具调用数、失败的建模调用数和 `render_views` 调用数。LLM 的完整输出存在 `<case>_r<k>.trace.json`，包括整段对话和每次调用的原始响应。运行过程中每次 LLM 回复、每轮工具后还会写 `<case>_r<k>.checkpoint.json`；中断后在原命令后加 `--resume`，已完成的运行保留，中断的运行从 checkpoint 续跑（与 text-to-CAD 相同），`--sw-restarts N` 可在 SolidWorks 崩溃时自动重启后续跑。`--action-level L0` 仍可使用旧流程：先由 `--vision-model` 把图纸读成结构化中间表示，再生成蓝图交给 L0 多轮 Agent。

这个 track 目前还没有参考结果。

---

## 6. 已知局限

- rubric 是人工编写的，图纸上没有被 rubric 覆盖的内容不会被检查。
- rubric 的坐标系允许左手系，所以对称性足够强的零件，其镜像件可能通过 rubric。镜像件的图像分低于满分，这种情况由图像分发现。
- 图纸由程序投影生成，标注风格统一，与真实工厂图纸相比缺少剖视图、局部放大图和公差标注。
