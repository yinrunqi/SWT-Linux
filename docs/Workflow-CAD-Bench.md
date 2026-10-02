# Workflow-CAD-Bench：零件建模 track 的评测指南

Workflow-CAD-Bench 有三个 track：

| track | 输入 | 任务数 | 文档 |
|---|---|---|---|
| text-to-CAD | 自然语言设计要求 | 50（另有 8 道困难子集） | 本文第 1–7 节 |
| 三视图生成 CAD | 工程图（三视图 PNG）+ 一句中性题面 | 50 | 本文第 8 节 |
| 复杂装配体生成 | 自然语言装配要求 | 见装配文档 | [`Workflow-CAD-Bench-Assembly.md`](./Workflow-CAD-Bench-Assembly.md) |

text-to-CAD track 共有 **50 个任务**。每个任务是一段自然语言设计要求，配有一份 **rubric**：它把题面里的每条要求写成对最终实体的确定性几何检查。三视图 track 用同一套检查器，rubric 只检查图纸上标注或画出的内容，另外附一个基于投影图像的相似度分。

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

测试会用 OpenCASCADE 直接构造三组参考实体和变体：text-to-CAD 的 50 个参考实体、12 个合法变体和 62 个错误变体，困难子集的 8 个参考实体和 24 个变体，以及三视图 track 的 50 个参考实体和 133 个错误变体（第 8 节）。它们分别用 rubric 和图像分打分，再加上检查器其余模块的单元测试，应该显示 `541 passed`，全部跑完约 6 分钟。这一步通过，说明你机器上的检查器和我们的打分结果完全一致。

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

## 7. 困难子集：8 个复杂综合件（showcase）

上面 50 题对当前系统已经偏简单（pass@1 = 0.74），所以另外准备了 8 道复杂零件。它们都是能直接当演示用的真实机械件，每题需要十几个特征，并且要把阵列相位、切线轮廓、径向孔、对称槽等关系全部做对。困难子集**不计入** 50 题的统计，rubric 单独放在 `LLMPlanner/eval/rubrics_hard/`。

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

另外，评测脚本等待 Web 后端的超时从 900 s 放宽到了 3600 s。复杂件用满 40 次调用可能超过 15 分钟，原来的超时会在服务端还在建模时就发起下一次运行。

---

## 8. 三视图 track：50 道看图建模题

### 8.1 任务与文件

共 50 道题，分两部分：B/C 组 13 道来自 Web Demo 的三视图样例；D 组 37 道是为 benchmark 新设计的零件（见下文）。

B/C 组 13 道题来自 Web Demo 的三视图样例（`datasets/manual_three_view/`）。原样例不适合直接用作 benchmark，原因有三点：
- C1–C3 的图纸只画了外框；
- B22 没有隐藏线和内腔尺寸，B07 缺少凹槽和孔的标注；
- 文字提示里写了 SolidWorks 建模步骤。

因此 benchmark 版本**重新生成了这 13 张图纸**：先按图纸尺寸用 OpenCASCADE 构造参考实体（`geomcheck/tests/refs_three_view.py`），再用 HLR 投影出三视图。可见边画粗实线，隐藏边画虚线，尺寸、引线、中心线和注释按每题 rubric 里的 `drawing` 清单绘制。原 demo 的图纸和数据保持不变。

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

| 文件 | 内容 |
|---|---|
| `eval/rubrics_three_view/<case>.yaml` | 题面（`prompt` / `prompt_en`）、图纸标注清单 `drawing`、rubric 条目 |
| `eval/three_view/drawings/<case>.png` | 交给建模系统的图纸（第三角画法：俯视图在主视图上方，右视图在主视图右方） |
| `eval/three_view/references/<case>.step` | 参考实体，图像分的比较对象 |

题面只有一句中性的话，例如“根据所附三视图（第三角画法，单位 mm）建模 L 型支架，单一实体。”，全部尺寸都从图纸读取。C3 和 D17 的题面另外说明零件由两个分开的实体组成。

| 类型 | 任务（case id，括号内为显式条目数） |
|---|---|
| 基础零件（9） | L 型支架 `b01_l_bracket`（5）、带孔平板 `b02_plate_with_holes`（5）、T 型支架 `b03_t_bracket`（6）、键帽 `b04_keycap`（6）、带圆角和底槽的键帽 `b05_keycap_full`（6）、门锁扣板 `b06_lock_striker`（6）、86 型开关面板 `b07_switch_panel`（6）、齿轮盘 `b11_gear_disc`（5）、圆碗 `b22_round_bowl`（4） |
| 组合零件（4） | 迷你机械键盘 `c1_mech_keyboard`（7，65 个键帽阵列）、齿轮减速器外壳 `c2_gearbox`（8）、齿轮组 `c3_gear_pair`（7，两个实体）、一体式桌面笔筒 `c4_desktop_pen_holder`（10） |
| 投影基础零件（18） | V 形块 `d01_v_block`（7）、法兰盖 `d04_flange_cover`（8）、阶梯轴 `d05_stepped_shaft`（6）、U 形叉头 `d06_u_clevis`（7）、偏心轮 `d07_eccentric_cam`（7）、角钢支架 `d09_angle_bracket`（7）、沉孔压板 `d10_clamp_plate`（7）、燕尾滑块 `d11_dovetail_slide`（6）、切角支座 `d12_cut_block`（7）、齿条 `d18_gear_rack`（7）、T 形槽块 `d21_t_slot_block`（7）、法兰轴套 `d22_flanged_bushing`（8）、十字连接板 `d23_cross_plate`（7）、U 形槽座 `d24_u_channel`（7）、带头销轴 `d25_step_pin`（8）、方形法兰座 `d26_square_flange`（8）、导轨 `d27_guide_rail`（6）、开口轴环 `d35_shaft_collar`（6） |
| 投影组合零件（19） | 轴承座 `d02_bearing_block`（10）、带筋支架 `d03_rib_bracket`（9）、法兰三通 `d08_pipe_tee`（7）、双槽 V 带轮 `d13_v_pulley`（9）、连杆 `d14_link_rod`（7）、电机安装座 `d15_motor_mount`（8）、散热器 `d16_heat_sink`（6）、管夹 `d17_pipe_clamp`（7，两个实体）、合页片 `d19_hinge_leaf`（6）、带筋底座 `d20_ribbed_base`（7）、带缘圆杯 `d28_round_cup`（7）、吊架 `d29_hanger_bracket`（7）、阀体 `d30_valve_body`（7）、摇臂 `d31_lever_arm`（7）、刀具座 `d32_tool_holder`（7）、壳体罩 `d33_shell_housing`（8）、夹具底板 `d34_fixture_plate`（7）、双法兰短管 `d36_flanged_tube`（7）、立柱底座 `d37_column_base`（9） |

50 题共 349 个显式条目（B/C 组 81 个，D 组 268 个）。

B/C 组与原 demo 相比，有三处几何改动：
- B06 的 R3 圆角从“3 mm 厚板的长边”改到板的四个竖直角。原来的圆角半径等于板厚，是退化几何。
- C3 的矩形齿嵌入齿根圆 1 mm，避免齿与轮盘只在一条线上相切。
- 体积一律取 OCC 参考实体的精确值。原 GT 忽略了圆角，圆也用多边形近似。

### 8.2 打分

**主分是 rubric**，规则与第 4 节相同：每个条目只检查图纸上标注或画出的内容，`source` 写明出自哪个视图的哪个标注或注释。例如“主视图：孔心距左端 20、孔距 40、距底边 20”。检查都在零件自身的坐标系里进行，摆放方向不限。

**辅助指标是图像分**（`geomcheck/views.py`），把提交实体和参考实体沿三个正交方向投影后逐像素比较：
- 每个方向生成四张图，像素边长取参考件最大尺寸的 1/400：轮廓；近侧深度和远侧深度（对应正、背两个视图）；沿视线的材料厚度（类似 X 光片，能看到盲腔、背面凹槽和内部空腔）。
- 一个像素算“一致”，需要两件在该像素都有材料，且三种深度之差都不超过参考件最大尺寸的 0.5%。允许 1 个像素的位置误差。
- 某方向的得分是一致像素数除以两件轮廓并集的像素数；图像分是 100 乘以三个方向得分的平均。
- 提交件可以任意摆放：比较前在 24 种正交旋转中取最好的一种。**镜像不算旋转**，所以镜像件得不到满分。

图像分对图纸上任何可见差异都敏感，但不区分差异是否重要，所以只作为辅助指标单独报告，合格与否只看 rubric。

### 8.3 rubric 与图像分的验证

`geomcheck/tests/test_refs_three_view.py` 共 368 个测试：
- 从参考 STEP 读回的实体得 100 分，体积与 rubric 一致。
- 133 个针对性错误变体（B/C 组 28 个，D 组 105 个）恰好在预期条目上失败。例如：孔距 36、凹槽深 5、去掉充电线孔、齿轮中心距 72、12 列键帽；D 组如燕尾角 50°、6 片散热片、去掉油孔、沉孔深 4、管夹无间隙、退刀槽移位、开口加宽、去掉键槽、刀孔少一个。少数变体会连带另一个条目失败，原因是两处几何确实相互关联（例如齿条改成 11 个齿后，沉孔上方正好是齿）；这些情况在测试里逐条注明。
- 参考实体在原位和旋转后的图像分都是 100（旋转后要求不低于 99.9）。
- 133 个错误变体和 C4 的镜像件，图像分都低于 99.5。

### 8.4 命令

```powershell
# 导出题目：每行 {"case", "kind", "prompt", "image"}，image 是相对 LLMPlanner 的图纸路径
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view tasks --output tasks_three_view.jsonl

# 打分：rubric + 图像分；--diff-dir 输出差异图（灰 = 一致，红 = 深度不一致，蓝 = 多出的材料，绿 = 缺少的材料）
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.three_view --artifacts my_tv_run --output my_tv_scores.json --diff-dir my_tv_diffs

# 汇总：在第 1 节的指标之外附带平均图像分（缺失的运行记 0）
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_three_view summary my_tv_scores.json --repeats 2

# 修改标注清单或参考实体后，重新生成图纸 PNG 和参考 STEP
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.tests.refs_three_view
```

提交要求同第 3 节。区别是 C3 和 D17 要求恰好两个有效实体，其余 48 题要求单一实体。`scores.json` 的每条记录比 `geomcheck.rubric` 多一个 `image` 字段，内容包括 `image_score`、`silhouette_iou`、三个视图各自的得分，以及选中的旋转。

用 Workflow-CAD 跑时用 `eval/eval_three_view_bench.py`。它把图纸和题面发给 `/api/run`，并把每次运行的 STEP 存到 `eval/artifacts/<name>/<case>_r<k>.step`，之后按上面的命令离线打分。这个 track 目前还没有参考结果。

---

## 9. 已知局限

- rubric 是人工编写的。rubric 没写到的要求不会被检查，标为 `ungraded` 的内容也不打分。
- 目前只有 OpenCASCADE 一个检查器。一个通过 SolidWorks API 评估同一套 rubric 的检查器正在开发，用来交叉验证。
- 题面是中文。如果翻译成其他语言使用，请以 YAML 中的原文为准，并在报告里说明。
- 旧的评分方式（包围盒、体积区间、特征树计数）已经被 rubric 取代，只在论文里用来对比说明两者的差别。
- 三视图 track 的 rubric 坐标系允许左手系，所以对称性足够强的零件，其镜像件可能通过 rubric。镜像件的图像分低于满分，这种情况由图像分发现。
