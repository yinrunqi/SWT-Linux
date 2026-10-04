# Workflow-CAD-Bench：text-to-CAD track 的评测指南

Workflow-CAD-Bench 有三个 track：

| track | 输入 | 任务数 | 文档 |
|---|---|---|---|
| text-to-CAD | 自然语言设计要求 | 50（另有 30 道困难子集） | 本文第 1–7 节 |
| 三视图生成 CAD | 工程图（三视图 PNG）+ 一句中性题面 | 50 | [`Workflow-CAD-Bench-3Views.md`](./Workflow-CAD-Bench-3Views.md) |
| 复杂装配体生成 | 自然语言装配要求 | 见装配文档 | [`Workflow-CAD-Bench-Assembly.md`](./Workflow-CAD-Bench-Assembly.md) |

text-to-CAD track 共有 **50 个任务**。每个任务是一段自然语言设计要求，配有一份 **rubric**：它把题面里的每条要求写成对最终实体的确定性几何检查。三视图 track 用同一套检查器和本文第 0 节的环境，题目、打分和命令见 [`Workflow-CAD-Bench-3Views.md`](./Workflow-CAD-Bench-3Views.md)。

**评测与建模系统解耦。** 你只需要为每个任务交一个 STEP 文件。用什么模型、什么 CAD 软件（SolidWorks、Onshape、FreeCAD、CadQuery……）、什么方式建模都可以。打分只读 STEP 的几何，不看建模过程、特征树或系统自己的日志。我们自己的系统 Workflow-CAD（见 [`Workflow-CAD.md`](./Workflow-CAD.md)）只是其中一个被评测的系统。

---

## 0. 在一台新机器上配置评测环境

打分**不需要 SolidWorks，也不需要我们的建模系统**，只需要 Python 和 OpenCASCADE 的 Python 绑定。

### 0.1 需要安装的软件

| 软件 | 我们测试过的版本 | 说明 |
|---|---|---|
| Python | 3.14.3 | `cadquery-ocp==8.0.1.0.0` 需要有对应 Python 版本的 wheel，直接用 3.14 最省事 |
| Git | 任意 | 用来获取代码 |
| 操作系统 | Windows 11 x64 | 检查器是纯 Python 加 OCP。OCP 也提供 Linux 和 macOS 的 wheel，但我们只在 Windows 上测试过 |

### 0.2 获取代码

```bash
git clone https://github.com/fansunqi/SolidWorksTutorial.git     # 或 git@github.com:fansunqi/SolidWorksTutorial.git
cd SolidWorksTutorial/LLMPlanner
```

评测只用到仓库里的两部分：`LLMPlanner/geomcheck/`（检查器、汇总工具和测试）和 `LLMPlanner/eval/rubrics/`（50 个 rubric，题面也在里面）。其余代码属于我们的建模系统，评测时用不到。

### 0.3 创建检查器环境

Windows（PowerShell）：

```powershell
py -3.14 -m venv venv-occ
venv-occ\Scripts\python.exe -m pip install -r geomcheck\requirements.txt
```

Linux / macOS：

```bash
python3.14 -m venv venv-occ
venv-occ/bin/python -m pip install -r geomcheck/requirements.txt
```

`geomcheck/requirements.txt` 固定了五个依赖：`cadquery-ocp==8.0.1.0.0`、`numpy==2.5.3`、`PyYAML==6.0.3`、`pytest==9.1.1` 和 `pillow==12.3.0`（只在重新生成三视图图纸和输出差异图时用到）。

### 0.4 验证环境

```powershell
venv-occ\Scripts\python.exe -c "import OCP, numpy, yaml; print('occ ok')"
venv-occ\Scripts\python.exe -m pytest geomcheck/tests -q -p no:cacheprovider
```

测试会用 OpenCASCADE 直接构造三组参考实体和变体：text-to-CAD 的 50 个参考实体、12 个合法变体和 62 个错误变体，困难子集的 30 个参考实体和 74 个变体，以及三视图 track 的 50 个参考实体和 133 个错误变体（见 [`Workflow-CAD-Bench-3Views.md`](./Workflow-CAD-Bench-3Views.md) 第 4 节）。它们分别用 rubric 和图像分打分，再加上检查器其余模块的单元测试，应该显示 `612 passed`，全部跑完约 8 分钟。这一步通过，说明你机器上的检查器和我们的打分结果完全一致。

Linux 和 macOS 上把 `venv-occ\Scripts\python.exe` 换成 `venv-occ/bin/python` 即可，下文同理。以下命令都在 `LLMPlanner` 目录下执行。

---

## 1. 五分钟上手

```powershell
# 1) 环境已按第 0 节配好

# 2) 导出 50 个任务：每行一个 {"case", "kind", "prompt"}；加 --lang en 导出英文题面
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench tasks --output tasks.jsonl

# 3) 用你的系统逐题建模，把结果导出为 STEP：
#    my_run\<case>_r1.step, my_run\<case>_r2.step, ...

# 4) 打分：逐文件对照 eval/rubrics/<case>.yaml
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --artifacts my_run --output my_run_scores.json

# 5) 汇总：均分与 95% 置信区间、pass@k、核心形体率、按零件类型分解；缺失的运行记 0 分
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench summary my_run_scores.json --repeats 2 --output my_run_report.json
```

第 5 步的输出形如：

```
ALL  tasks=50 runs=100 score=... ci95=[..., ...] pass@1=... pass@2=... core_form=...
  Everyday objects           tasks= 9 score= ... pass@1=...
  Common machined parts      tasks=19 score= ... pass@1=...
  Multi-feature parts        tasks= 8 score= ... pass@1=...
  Operation-specific parts   tasks=14 score= ... pass@1=...
```

不想用我们的汇总脚本的话，`my_run_scores.json` 里已经有逐次运行的 `score`、`accepted`、`hard_ok` 和每个 rubric 条目的检查明细，可以自己统计。

---

## 2. 任务

### 2.1 来源与格式

任务的唯一来源是 `LLMPlanner/eval/rubrics/<case>.yaml`，每个文件的 `prompt` 字段就是交给建模系统的原文（中文）。`prompt_en` 字段是逐句对应的英文版本：数值、单位和歧义程度与中文一致，rubric 的 `ungraded` 项在英文里同样不写明，打分规则不变。`geomcheck.bench tasks` 只是把这些字段导出成 JSONL（`--lang en` 导出 `prompt_en`）；`eval_text_singleturn_qwen.py --lang en` 用英文题面跑评测，结果文件名带 `_en` 后缀。

题面只给目标尺寸和功能细节，不给 API 序列、坐标布局或建模步骤，例如：

> 创建一个顶部敞开的长方形肥皂盒，单一实体。外部尺寸 110×75×25mm（高 25mm），侧壁厚 3mm，底厚 3mm；外侧四条竖直棱边做 R8mm 圆角。底板上开 15 个直径 5mm 的贯穿排水孔，排成 3 行 × 5 列：沿 110mm 方向 5 列、列距 18mm，沿 75mm 方向 3 行、行距 18mm，孔阵中心与底面中心重合。

### 2.2 任务列表

括号中是显式 rubric 条目数（计入得分）；带 `+n` 的表示另有 n 个隐含条目，单独汇报，不计入得分。

| 类型 | 数量 | 任务（case id） |
|---|---|---|
| 日常物品 | 9 | 一体式手机支架 `phone_stand`（10+1）、四脚板凳 `stool`（6）、圆角板凳 `rounded_stool`（7）、收纳盒 `storage_box`（7）、标准骰子 `standard_die`（5）、马克杯 `mug`（5+2）、花瓶 `vase`（6）、钥匙 `key`（5）、排水肥皂盒 `soap_dish`（8） |
| 常见机加工件 | 19 | 六角螺母 `hex_nut`（5）、扳手 `wrench`（5+1）、齿轮盘 `gear_disc`（4）、法兰盘 `flange`（6）、皮带轮 `pulley`（6）、三段阶梯轴 `stepped_shaft`（5）、内六角螺栓 `socket_bolt`（6）、L 形角码 `l_bracket`（6）、隔套 `spacer_ring`（4）、安装板 `mounting_plate`（6）、台阶块 `stepped_block`（5）、旋钮 `knob`（6）、六角隔离柱 `hex_standoff`（4）、U 形槽 `u_channel`（5+1）、法兰衬套 `flanged_bushing`（5）、斜楔块 `wedge_block`（6）、减重轮盘 `spoked_wheel`（5）、端盖 `end_cap`（5）、连杆 `link_bar`（6） |
| 多特征零件 | 8 | 叉形接头 `fork_end`（9）、三耳法兰轴承座 `three_lug_housing`（9）、立式轴承座 `pillow_block`（8）、双臂摇臂 `twin_arm_rocker`（7）、双耳支座 `clevis_bracket`（9）、菱形法兰盘 `diamond_flange`（7）、轴支撑座 `support_seat`（7）、曲柄 `crank`（8） |
| 特定操作零件 | 14 | 带筋角码 `gusset_bracket`（6）、带筋支架 `corner_rib_stand`（6）、开口盒 `open_box`（6）、敞口壳体 `lidless_housing`（6）、沉头孔安装板 `counterbore_plate`（5）、沉头盖板 `csk_cover`（5）、侧凸台块 `side_pad`（4+1）、侧凸台座 `through_boss`（4+1）、钣金平板 `sheet_angle`（3）、钣金条 `sheet_channel`（3）、法兰弯管 `flanged_elbow`（7）、折弯接管 `bent_tube`（7）、方圆过渡接管 `square_to_round`（5+1）、矩形圆过渡管 `rect_to_circle`（5+1） |

全部 50 个任务共有 295 个显式条目（每题 3–10 个，平均 5.9 个）、9 个隐含条目和 586 个几何检查。“特定操作零件”每一对任务需要一种超出拉伸和切除的操作：筋、抽壳、沉头孔或锥形沉头孔、拉伸到面、钣金、扫描、放样。不过打分只看几何，不看用的是什么操作。

---

## 3. 提交要求

- **一个任务一次运行对应一个 STEP 文件**，命名为 `<case>_r<k>.step`，k 从 1 开始。所有文件放在同一个目录里。
- **单位是毫米。** STEP 文件自带单位，OpenCASCADE 读入时会换算成毫米。
- **单一实体。** 全部 50 个 rubric 都要求恰好一个有效的封闭实体。多个分离的实体或只有曲面，都会在这一项失败。
- **位置和朝向不限。** 检查在零件自身的坐标系里进行：按尺寸排序的主轴坐标系、沿选定圆柱轴线的坐标系、以底座为基准的坐标系等，所以平移、旋转零件都不影响得分。只有题面明确给出世界坐标的任务才检查绝对位置（例如 `side_pad` 写明“50 沿 X、底面在 Y=0”，`hex_nut` 要求“厚度方向关于原点居中”）。
- **建法不限。** 题面里“用抽壳完成”“用放样连接”之类的建法指令不单独打分，只检查它们的几何结果。
- **缺失即 0 分。** 没有交 STEP 的运行记 0 分、不合格，建模失败也按这个处理。

推荐的评测协议（论文采用）：每题独立运行 `n = 2` 次；关闭所有缓存；两次运行之间清空 CAD 会话；温度设为 0；同时记录 token、模型调用次数、耗时和费用。**不要针对具体任务调优**提示词、工具或代码。如果开发过程中看过部分任务的结果，请在报告里说明。

---

## 4. 打分规则

### 4.1 条目与得分

每个 rubric 由若干**条目**组成，每个条目对应题面里的一条要求，并记录它出自题面的哪句话（`source`）。每个条目包含一个或多个检查，所有检查都通过，这个条目才通过。

- **显式条目**（`basis: explicit`，默认）：题面直接写明的要求，计入得分。
- **隐含条目**（`basis: implied`）：由物体名称带来的常识要求，比如马克杯的杯腔不能被把手堵住。单独汇报，不计入得分。
- **核心条目**（`hard: true`）：有效实体和主体形状。两者都通过的比例记为**核心形体率**。
- **不打分**（`ungraded`）：题面没有确定的内容，比如花瓶的壁厚、内角是尖角还是圆角，会写明原因，不作猜测。

任务得分：

\[ S = 100 \cdot \frac{\sum_i w_i p_i}{\sum_i w_i} \]

其中 \(i\) 取遍显式条目，\(w_i\) 是权重（默认为 1），\(p_i \in \{0, 1\}\) 表示该条目是否通过。**所有显式条目都通过**才算这次运行合格（accepted），pass@k 就按这个标准计算。默认容差为长度 0.1 mm、角度 0.5°、体积 1%。

### 4.2 多种合理读法

如果题面有多种合理的理解，rubric 会全部接受，并写明原因。例如：立板“高 36mm”可以从底板上表面量起，也可以从底面量起；开口边的圆角可以做在外圈、内圈或两圈；外棱倒圆的盒子内角可以是尖角，也可以是同心圆角；侧面凸台的位置没给时，可以放在该面的任意位置。体积因此可能是一个区间，推导过程写在对应条目的 `derivation` 字段里。

### 4.3 检查类型

检查器共实现 61 种确定性检查，都直接作用在 B-rep 上，分为五类：

1. **拓扑**：单一有效实体、把手的亏格等。
2. **外形包络**：排序后的包围盒、零件坐标系中的盒子、底面轮廓、总高度。
3. **特征与关系**：实体（按直径和内外侧选出的圆柱、圆锥、圆环、球、棱柱凹槽、平面）的存在与数量，以及同轴、平行、轴线位置与间距、等角分布、通孔还是盲孔及深度、沉头结构、倒角尺寸、指定边附近的圆角面。
4. **材料探针**：沿直线、半径或射线测出精确的材料区间，用来检查壁厚、底厚、平齐和有没有多余材料；沿圆周扫描统计开口数量和尺寸；截面面积；用对称差体积检查镜像和旋转对称。
5. **体积**：精确的解析体积；题面允许多种合法构造时用区间。

### 4.4 一个 rubric 的样子

以下是 `eval/rubrics/soap_dish.yaml` 的节选：

```yaml
case: soap_dish
prompt: 创建一个顶部敞开的长方形肥皂盒……
tolerance: {length_mm: 0.1, angle_deg: 0.5, volume_rel: 0.01}
entities:
  frame:   {principal_frame: {up: open_end}}          # 0=高 1=宽 2=长，原点在包围盒角点
  holes:   {cylinder: {d: 5, side: inner}, count: 15}
rubrics:
  - id: solid
    source: 单一实体
    hard: true
    check: [single_valid_solid: {}]
  - id: bottom
    source: 底厚 3mm
    check:
      - ray_thickness: {frame: frame, from: [0.5, 0.62, 0.581818], dirs: ["-0"], value: 3}
  - id: drain_holes
    source: 15 个直径 5mm 的贯穿排水孔……孔阵中心与底面中心重合
    check:
      - exists: holes
      - through: {holes: holes}
      - axis_positions: {frame: frame, items: holes, axes: [1, 2], expect: [[19.5, 19], ...]}
  - id: volume
    derivation: 外廓 204876.5；腔体 157872（尖内角）至 157399.9（R5 内角）；孔 883.6
    check: [volume_range: [46120.9, 46593.0]]
ungraded:
  - source: 腔体内侧的四条竖直角
    reason: 文字只要求外侧棱边圆角
```

所有坐标系的约定写在 `geomcheck/rubric.py` 开头的文档字符串里。

### 4.5 rubric 本身是怎么验证的

每个 rubric 都经过测试（`geomcheck/tests/`）：

- **参考实体**：根据题面直接用 OpenCASCADE 构造，不依赖任何建模系统，必须得 100 分，而且体积与 rubric 推导值的差距不超过 0.05%。
- **合法变体**（12 个）：实现其他合理读法，也必须得 100 分。
- **针对性错误变体**（62 个）：每个都违反一条或几条要求，比如孔偏移 5 mm、底厚 4 mm、孔距 20 mm。测试断言**恰好**是预期的那几个条目失败，所以检查过严或过松都会被发现。

运行测试：

```powershell
venv-occ\Scripts\python.exe -m pytest geomcheck/tests -q -p no:cacheprovider
```

---

## 5. 常用命令

```powershell
# 给单个 STEP 打分，并打印每个条目和检查的结果
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --step part.step --rubric eval/rubrics/soap_dish.yaml

# 给整个目录打分（按文件名 <case>_r<k> 自动匹配 rubric）
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --artifacts my_run --output my_run_scores.json

# 多个打分文件一起汇总（例如分批跑的结果）
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench summary a.json b.json --repeats 2
```

`scores.json` 中每次运行的字段如下：

| 字段 | 含义 |
|---|---|
| `step` | STEP 文件路径 |
| `case` | 任务 id |
| `score` | 0–100，按上面的公式计算 |
| `accepted` | 所有显式条目是否都通过 |
| `hard_ok` | 核心条目是否都通过 |
| `passed` / `total` | 通过的显式条目数 / 显式条目总数 |
| `implied_passed` / `implied_total` | 隐含条目的对应数字 |
| `rubrics` | 每个条目的 `id`、`ok`、`basis`、`hard`，以及每个检查的实测值 |

---

## 6. 用 Workflow-CAD 跑这套 benchmark（可选）

如果你想复现我们自己系统的结果，需要一台装有 SolidWorks 的 Windows 机器。先按 [`Workflow-CAD.md`](./Workflow-CAD.md) 第 2 节配好环境（C# 服务、两个 Python 环境、`.env`），再按第 3 节启动 SolidWorks、C# 服务和 Web 后端，并完成端到端自检，然后执行：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --action-level L1 --split all --repeats 2 `
    --output eval\text_workflow_l1_mymodel_all_result.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric `
    --artifacts eval\artifacts\text_workflow_l1_mymodel_all_result --output eval\rubric_mymodel_l1.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench summary eval\rubric_mymodel_l1.json --repeats 2
```

评测脚本会自动把每次运行的 STEP 和 SLDPRT 存到 `eval/artifacts/<输出文件名>/`，并在结果 JSON 里记录 token、调用次数、失败操作数、耗时和费用。

L1 运行过程中，每次拿到 LLM 回复、每轮工具执行完，都会把完整对话、工具结果和原始回复写到同一目录下的 `<题号>_r<k>.checkpoint.json`。如果中途 SolidWorks 卡死或进程被结束，重启服务后在原命令后加 `--resume` 即可续跑：
- 已经跑完的运行直接保留；
- 中断的运行会先在新零件上按原顺序重放已执行的工具恢复模型，再接着原对话继续，已经拿到的回复不会重新请求；
- 请求异常和 LLM 接口出错的运行会重跑。

如果重放结果与记录不一致，这次运行会标记为 `resume_failed`，不会在错误的模型上继续建模。

参考结果（Workflow-CAD L1 共 24 个工具，骨干模型 Qwen3.5-397B-A17B，温度 0，每题 2 次，共 100 次运行）：

| 零件类型 | 任务数 | 均分 | pass@1 | pass@2 | 平均 token | 平均 LLM 调用 |
|---|---|---|---|---|---|---|
| 日常物品 | 9 | 78.1 | 0.61 | 0.78 | 172.9k | 12.9 |
| 常见机加工件 | 19 | 90.5 | 0.82 | 0.89 | 103.7k | 9.0 |
| 多特征零件 | 8 | 73.9 | 0.56 | 0.75 | 282.0k | 16.7 |
| 特定操作零件 | 14 | 87.8 | 0.82 | 0.86 | 58.2k | 5.8 |
| **全部** | **50** | **84.9**（95% CI [79.3, 90.3]） | **0.74** | **0.84** | 131.9k | 10.0 |

核心形体率为 0.85。每次运行平均 91 s、0.18 元，折合每个合格零件 0.25 元。这些结果对应的打分文件是 `eval/rubric_*_tools24.json`。

---

## 7. 困难子集：30 个复杂综合件（showcase）

上面 50 题对当前系统已经偏简单（pass@1 = 0.74），所以另外准备了复杂零件：最初 8 道，后来又加了 22 道更难的（见 7.1 节）。下表是最初的 8 道。它们都是能直接当演示用的真实机械件，每题需要十几个特征，并且要把阵列相位、切线轮廓、径向孔、对称槽等关系全部做对。困难子集**不计入** 50 题的统计，rubric 单独放在 `LLMPlanner/eval/rubrics_hard/`。

| 任务（case id） | 显式条目 | 主要难点 |
|---|---|---|
| X 型四旋翼机架 `quad_frame` | 12 | 沿对角线的机臂、16 个电机孔与 4 个飞控孔的位置换算、8 个内角 R5 圆角 |
| 发动机连杆 `connecting_rod` | 12 | 两圆外公切线杆身、正反两面梯形减重槽、沿中线的油孔、轴承孔两端倒角 |
| 带散热鳍片的电机壳 `finned_housing` | 12 | 12 片鳍片的相位与长度、带圆角的方形法兰、底板孔与法兰孔同方位 |
| 18 齿滚子链链轮 `sprocket` | 11 | 节圆上的齿槽阵列、键槽、减重孔 / 齿槽 / 键槽三者对齐、径向紧定螺钉孔 |
| 带摇把的五辐手轮 `handwheel` | 9 | 圆环轮缘、中面内的径向轮辐、六角孔、带球头的摇把 |
| 四缸凸轮轴 `camshaft` | 10 | 基圆 + 桃尖圆 + 公切线的凸轮轮廓、四个凸轮顺时针 90° 递进的相位、定位销孔与键槽 |
| 行星齿轮架 `planet_carrier` | 11 | 双盘 + 连接柱、行星轮轴孔与连接柱交错、输出轴螺纹底孔、4 条外圆倒角 |
| 后弯叶片离心叶轮 `impeller` | 10 | 给定圆心和半径的圆弧叶片、45° 阵列、叶片高度与外端截止半径、轮毂倒角 |

8 题共 87 个显式条目。rubric 的验证方式与 50 题相同：参考实体满分、1 个合法变体（叶轮叶片外端改为径向端面）满分、23 个针对性错误变体恰好在预期条目上失败（`geomcheck/tests/test_refs_hard.py`）。

### 7.1 加难的 22 题

在上面 8 题的基础上，又按同样的写法加了 22 道更难的综合件。和前 8 题相比，它们的特征更多，并且大量依赖“阵列 + 相位 + 与另一组特征的相对方位”这类必须整体做对的关系。它们和前 8 题放在同一个目录、同一个 `split="hard"` 里；`bench.py` 中单列为 `Showcase parts (hard)` 一类。参考实体在 `geomcheck/tests/refs_hard2.py`。

| 任务（case id） | 显式条目 | 主要难点 |
|---|---|---|
| 双拐曲轴 `crankshaft` | 14 | 两拐相差 180°、长圆 + 扇形平衡重的曲柄臂轮廓、穿过曲柄销且垂直于曲拐方向的油孔、键槽与定位销孔的方位 |
| 通风刹车盘 `brake_disc` | 10 | 双摩擦环之间 40 片径向通风筋、20 个散热孔隔一个筋间隙排布、盘帽与轮毂螺栓孔对筋 |
| 五辐轮毂 `wheel_rim` | 12 | 扇环形减重窗的相位、20 条窗角 R8 圆角、轮辐正对螺栓孔、与减重窗同方位的径向气门嘴孔 |
| 六槽槽轮 `geneva_wheel` | 9 | 6 条径向槽与 6 段内凹锁止圆弧交错、减重孔在角平分线上、凸台上沿槽方向的径向螺钉孔 |
| 旋转楼梯 `spiral_staircase` | 10 | 12 级逆时针递升的扇形台阶、每级角平分线上的栏杆柱、半球柱顶 |
| 发动机活塞 `engine_piston` | 11 | 三道环槽、内腔中的销座与贯穿销孔、垂直于销孔的两个气门避让坑、油环槽底的径向回油孔 |
| 三支管法兰歧管 `pipe_manifold` | 11 | 主管与三根支管内孔贯通、端法兰螺栓孔错开 22.5°、支管法兰孔在 45° 斜线上、底部排放凸台 |
| 减速器下箱体 `gearbox_housing` | 12 | 薄壁开口箱体、两侧各两个不同孔径的轴承座凸台、凸台下的加强筋、法兰与底脚孔 |
| 针状散热器 `heat_sink` | 9 | 9×9 针阵省去 4 角（77 根）、角上带沉孔的安装孔、底面导热台 |
| 蜂窝减重板 `honeycomb_panel` | 10 | 5 行交错排列的 43 个正六边形盲槽、六边形朝向、3mm 筋宽、角部圆角与安装孔同心 |
| 齿轮泵体 `gear_pump_body` | 10 | “8”字形双圆齿轮腔、主动轴通孔与从动轴盲孔、两侧不同孔径的油口凸台、对角定位销孔 |
| 蛇形流道液冷板 `cold_plate` | 10 | 5 段直线 + 4 个半圈的蛇形流道、侧面进出液孔正好通到流道端头、圆角矩形密封槽 |
| 华伦桁架梁 `truss_beam` | 8 | 9 个朝向交替的三角孔、27 个内角 R4 圆角、两端不同孔径的凸台、长边加强筋 |
| 燕尾榫槽涡轮盘 `turbine_disk` | 10 | 30 个外窄内宽的燕尾榫槽、减重孔在两槽角平分线上、辐板与轮毂/轮缘之间的环形圆角 |
| BT40 刀柄 `bt40_holder` | 10 | 7:24 锥柄、60° V 形槽、两个驱动键槽与定向孔相差 90°、两端孔用中心冷却孔贯通 |
| 液压阀块 `hydraulic_manifold` | 9 | 四个方向钻入、彼此必须相交但不能多穿的油道（P/A/T 口、主油道、横孔）、堵头沉孔 |
| 火箭尾段 `rocket_fin_can` | 9 | 拉瓦尔喷管薄壳、4 片后掠梯形尾翼的平面形状与方位、尾翼与通气孔错开 45° |
| 棘轮 `ratchet_wheel` | 8 | 24 个单向锯齿（齿背方向）、正六边形孔的朝向与安装孔对齐 |
| 喷注器面板 `injector_plate` | 9 | 背面 4 条集液槽、80 个喷孔分 4 圈且各圈起始角不同、定位销孔与螺栓孔 |
| 机械臂连杆 `robot_arm_link` | 10 | 两圆公切线臂身、正反两面减重槽、肩部螺纹孔阵列、肘部开口缝与横穿的夹紧螺钉孔 |
| 二级门格海绵 `menger_sponge` | 7 | 两级分形挖孔（共 27 个方孔）必须全部贯穿且位置准确 |
| 万向节叉 `u_joint_yoke` | 9 | 双叉臂与同轴十字轴承孔、半圆叉头、孔内键槽与径向紧定螺钉孔、叉臂外侧的轴承盖螺孔 |

22 题共 217 个显式条目，配了 50 个针对性错误变体（相位转错、方向反了、少一级特征、孔不通等），每个都恰好在预期条目上失败。连同前 8 题，困难子集共 30 题、304 个条目、73 个错误变体。下面 Qwen 的参考结果只覆盖前 8 题；22 道新题上 claude-sonnet-5 的结果见 7.2 节。

除了 OCC 参考实体，22 道新题还在 SolidWorks 里用 L1 的 24 个工具逐题建了一遍参考解（`LLMPlanner/eval/hard_l1_reference.py`，只调用模型能拿到的同一套工具），22 题全部 100 分，说明每道题在这套工具下都是可解的。其中最绕的是火箭尾段：`ref_plane` 只能建平行偏移面，45° 方位的尾翼要先阵列 8 片、再切掉 4 片。

打分和汇总时用 `--rubric-dir` 指向这个目录：

```powershell
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_hard tasks --output tasks_hard.jsonl
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --artifacts my_hard_run --rubric-dir eval/rubrics_hard --output my_hard_scores.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_hard summary my_hard_scores.json --repeats 2
```

用 Workflow-CAD 跑时把 `--split all` 换成 `--split hard`。参考结果（协议同第 6 节：L1、24 个工具、Qwen3.5-397B-A17B、每题 2 次，共 16 次运行）：

| 均分 | pass@1 | pass@2 | 核心形体率 | 平均 token | 平均 LLM 调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 62.2（95% CI [50.6, 74.6]） | 0.125 | 0.25 | 0.56 | 737k | 31.4（6 次用满 40 次预算） | 440 s |

16 次运行共花费 15.7 元。只有行星齿轮架和凸轮轴各有一次合格。常见的失败有：
- 手轮的轮辐穿过中心六角孔；
- 链轮轮盘漏开中心孔；
- 鳍片按半厚度建模；
- 叶片伸出后盘，或者根本没有做成圆弧；
- 连杆的油孔或减重槽把实体拆成多块；
- 机架只做了 1 个内角圆角。

打分文件是 `eval/rubric_hard_tools24.json`，汇总在 `eval/rubric_hard_tools24_summary.json`。

需要说明两处在看过首批结果后对 rubric 的修正，两处都只放宽了与题意无关的判定：
- 凸轮轴：模型按切点坐标画弧时，同一段基圆被拆成了圆心相差几微米的两张面，被数成了 5 个基圆。现在不再要求基圆和桃尖圆的数量恰好为 4，四个凸轮是否齐全改由逐个凸轮的探测保证。
- 叶轮：后盘和轮毂的探测原本依赖叶片圆柱面来定方位。叶片缺失时，这两项会被连带判失败。现在它们改用与方位无关的坐标系。

另外，评测脚本等待 Web 后端的超时从 900 s 先放宽到 3600 s，后来又放宽到 7200 s。复杂件用满 40 次调用可能超过 15 分钟；推理模型（如 claude-sonnet-5）单次回复要几分钟，门格海绵一题就用了约 53 分钟。超时太短时，评测端会在服务端还在建模时就发起下一次运行。

对推理模型，L1 agent 还做了两处处理，都与具体题目无关：
- 回复因长度上限被截断、又没有完整的工具调用时，不再算作停滞，而是把输出上限翻倍（最多 32000）后重新请求。
- 请求改为流式，避免长回复在网关处读超时。

### 7.2 claude-sonnet-5 在加难 22 题上的结果

**评测方式**

- 系统：Workflow-CAD L1（24 个工具，闭环 agent，每轮工具执行后把结果和模型状态反馈给模型）。
- 骨干模型：claude-sonnet-5（经 TT Switch 网关），`reasoning_effort = medium`，中文题面。
- 预算：每题最多 40 次 LLM 调用；每题 1 次运行（pass@1）。
- 打分：只看 rubric（`eval/rubrics_hard/<case>.yaml`）。题目分数是通过的显式条目占比；所有显式条目都通过才算合格（`accepted`）；`hard_ok` 表示核心条目（有效实体和主体形状）都通过。
- 代码版本：提交 7d73ae1 及之后。这一版修了两个会让正确操作失败的工具 bug：一是先做过旋转、又建过参考面之后，在 top_plane 上画的圆会被 SolidWorks 自动捕捉偏移，导致切除失败；二是很短的旋转轴中心线会被吞掉。更早的 L1 结果都受这两个 bug 影响，不要和这里的结果直接比较。
- 结果 JSON 里的 `score` 是旧的 v2 分（包围盒、体积、特征计数），比 rubric 宽松得多，**不作为本节的结果**。`status` 只表示每次工具调用是否都成功；L1 里被 agent 自己纠正的失败调用也会让它变成 `failed`，agent 是否正常收尾要看 `workflow_status`。

复现命令（SolidWorks、C# 服务和 Web 后端已按第 6 节启动；Web 后端需要在拉取新代码后重启）：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --action-level L1 --split hard `
    --provider ttswitch --model claude-sonnet-5 --reasoning-effort medium --max-llm-calls 40 --repeats 1 `
    --sw-restarts 2 --sw-max-private-gb 12 `
    --ids crankshaft brake_disc wheel_rim geneva_wheel spiral_staircase engine_piston pipe_manifold `
          gearbox_housing heat_sink honeycomb_panel gear_pump_body cold_plate truss_beam turbine_disk `
          bt40_holder hydraulic_manifold rocket_fin_can ratchet_wheel injector_plate robot_arm_link `
          menger_sponge u_joint_yoke `
    --output eval\text_workflow_l1_mymodel_hard22_result.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --rubric-dir eval\rubrics_hard `
    --artifacts eval\artifacts\text_workflow_l1_mymodel_hard22_result --output eval\rubric_mymodel_hard22.json
```

说明：
- 评测脚本在每题导出 STEP 后会自动调用 `venv-occ` 打 rubric 分，写进结果 JSON 的 `rubric` 字段，并在控制台打印未通过的条目；上面第二条命令用来生成单独的打分文件。
- `--sw-restarts` 打开 SolidWorks 看门狗。SolidWorks 在长时间运行中有内存泄漏，大约每 5 道复杂件就会超过 12 GB：
  - 每题开始前，如果 SolidWorks 进程不在、C# 服务无响应，或私有内存超过 `--sw-max-private-gb`，就重启 SolidWorks 和 C# 服务；
  - 运行中丢失 SolidWorks 的题会在重启后从检查点续跑，每题最多续跑这么多次。
  - SolidWorks 不在默认路径时，用环境变量 `SW_EXE` 指定 `SLDWORKS.exe`。
- 在别的模型或系统上跑时，只要把每题导出成 `<case>_r1.step` 放进一个目录，用第二条命令打分即可，不必用 Workflow-CAD。

**结果**（19 题，均为上面的协议）

| 均分 | pass@1 | 核心形体率 | 平均 token | 平均 LLM 调用 | 平均工具调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 76.2（95% CI [64.9, 86.6]） | 0.368（7/19） | 0.84（16/19） | 648k | 21.9（1 题用满 40 次） | 35.2 | 753 s |

| 任务 | rubric 分 | 合格 | LLM 调用 | 未通过的条目 |
|---|---|---|---|---|
| `wheel_rim` | 100 | ✓ | 26 | — |
| `spiral_staircase` | 100 | ✓ | 16 | — |
| `heat_sink` | 100 | ✓ | 14 | — |
| `truss_beam` | 100 | ✓ | 13 | — |
| `injector_plate` | 100 | ✓ | 15 | — |
| `robot_arm_link` | 100 | ✓ | 15 | — |
| `u_joint_yoke` | 100 | ✓ | 18 | — |
| `cold_plate` | 90 | | 27 | channel_depth |
| `turbine_disk` | 90 | | 9 | hub |
| `engine_piston` | 81.8 | | 20 | pin_bore, volume |
| `brake_disc` | 80 | | 39 | rings, cross_holes |
| `bt40_holder` | 70 | | 16 | flange, v_groove, volume |
| `crankshaft` | 64.3 | | 39 | crankpins, phasing, web_profile, oil_holes, volume |
| `ratchet_wheel` | 62.5 | | 11 | outer, hex_bore, volume |
| `gearbox_housing` | 58.3 | | 23 | bearing_bores, bosses, ribs, drain_and_sight, volume |
| `honeycomb_panel` | 50 | | 13 | middle_row, offset_rows, outer_rows, hex_orientation, volume |
| `geneva_wheel` | 44.4 | | 33 | disc_and_hub, slots, slot_tips, locking_arcs, lightening_holes |
| `gear_pump_body` | 30 | | 40 | outer, chamber, ports, port_bosses, tapped_holes, dowels, volume |
| `pipe_manifold` | 27.3 | | 29 | solid, outer, branches, branch_bores, branch_flanges, branch_bolts, drain, volume |

19 题共用 1231 万 token（416 次 LLM 调用），总耗时约 4 小时。打分文件是 `eval/rubric_hard22_sonnet5_medium.json`，完整结果（含每次 LLM 回复、工具调用与返回、token 和耗时）是 `eval/text_workflow_l1_sonnet5_medium_hard19_result.json`。

另外 3 题（`hydraulic_manifold`、`menger_sponge`、`rocket_fin_can`）是在上面两个工具 bug 修复之前试跑的，协议也不一致：前两题没有设置 `reasoning_effort`（用网关默认值），火箭尾段中途中断后续跑过。所以它们**不计入上面的均分**，仅供参考：液压阀块 100、门格海绵 100、火箭尾段 55.6（尾翼方位和平面形状不对）。

在用 SolidWorks 参考解验证可解性时发现了两处 rubric 问题，已在跑上面 19 题之前修正。两处都只放宽了与题意无关的判定，并在 OCC 参考实体和 SolidWorks 参考解上重新验证过：
- 材料区间探测忽略零长度区间。探测线恰好擦过一条棱时会得到零长度区间，它不代表材料。
- 齿轮泵体：油口凸台从侧面起建，还是略嵌入块体（参考实体从 y=±44 起），题面没有规定，两种都接受。

---

## 8. 已知局限

- rubric 是人工编写的。rubric 没写到的要求不会被检查，标为 `ungraded` 的内容也不打分。
- 目前只有 OpenCASCADE 一个检查器。一个通过 SolidWorks API 评估同一套 rubric 的检查器正在开发，用来交叉验证。
- 题面是中文。如果翻译成其他语言使用，请以 YAML 中的原文为准，并在报告里说明。
- 旧的评分方式（包围盒、体积区间、特征树计数）已经被 rubric 取代，只在论文里用来对比说明两者的差别。
