# Workflow-CAD-Bench：text-to-CAD 部分的评测指南

Workflow-CAD-Bench 的 text-to-CAD 部分共有 **50 个任务**。每个任务是一段自然语言设计要求，配有一份 **rubric**：它把题面里的每条要求写成对最终实体的确定性几何检查。

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

`geomcheck/requirements.txt` 固定了四个依赖：`cadquery-ocp==8.0.1.0.0`、`numpy==2.5.3`、`PyYAML==6.0.3` 和 `pytest==9.1.1`。

### 0.4 验证环境

```powershell
venv-occ\Scripts\python.exe -c "import OCP, numpy, yaml; print('occ ok')"
venv-occ\Scripts\python.exe -m pytest geomcheck/tests -q -p no:cacheprovider
```

测试会用 OpenCASCADE 直接构造 50 个参考实体、12 个合法变体和 62 个错误变体，分别用 rubric 打分，应该显示 `125 passed`。这一步通过，说明你机器上的检查器和我们的打分结果完全一致。

Linux 和 macOS 上把 `venv-occ\Scripts\python.exe` 换成 `venv-occ/bin/python` 即可，下文同理。以下命令都在 `LLMPlanner` 目录下执行。

---

## 1. 五分钟上手

```powershell
# 1) 环境已按第 0 节配好

# 2) 导出 50 个任务：每行一个 {"case", "kind", "prompt"}
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

任务的唯一来源是 `LLMPlanner/eval/rubrics/<case>.yaml`，每个文件的 `prompt` 字段就是交给建模系统的原文（中文）。`geomcheck.bench tasks` 只是把这些字段导出成 JSONL。

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

## 7. 已知局限

- rubric 是人工编写的。rubric 没写到的要求不会被检查，标为 `ungraded` 的内容也不打分。
- 目前只有 OpenCASCADE 一个检查器。一个通过 SolidWorks API 评估同一套 rubric 的检查器正在开发，用来交叉验证。
- 题面是中文。如果翻译成其他语言使用，请以 YAML 中的原文为准，并在报告里说明。
- 旧的评分方式（包围盒、体积区间、特征树计数）已经被 rubric 取代，只在论文里用来对比说明两者的差别。
