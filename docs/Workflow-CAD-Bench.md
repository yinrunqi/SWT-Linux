# Workflow-CAD-Bench：text-to-CAD track 的评测指南

Workflow-CAD-Bench 有三个 track：

| track | 输入 | 任务数 | 文档 |
|---|---|---|---|
| text-to-CAD | 自然语言设计要求 | 50（另有 30 道困难子集、20 道极难子集） | 本文第 1–8 节 |
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

测试会用 OpenCASCADE 直接构造四组参考实体和变体：
- text-to-CAD 的 50 个参考实体、12 个合法变体和 62 个错误变体；
- 困难子集的 30 个参考实体和 74 个变体；
- 极难子集的 20 个参考实体和 71 个错误变体（见第 8 节）；
- 三视图 track 的参考实体和错误变体（见 [`Workflow-CAD-Bench-3Views.md`](./Workflow-CAD-Bench-3Views.md) 第 4 节）。

它们分别用 rubric 和图像分打分，再加上检查器其余模块的单元测试，应该显示 `1116 passed`，全部跑完约 20 分钟。这一步通过，说明你机器上的检查器和我们的打分结果完全一致。

Linux 和 macOS 上把 `venv-occ\Scripts\python.exe` 换成 `venv-occ/bin/python` 即可，下文同理。以下命令都在 `LLMPlanner` 目录下执行。

---

## 1. 五分钟上手

```powershell
# 1) 环境已按第 0 节配好

# 2) 导出 50 个任务：每行一个 {"case", "kind", "prompt"}；默认英文题面，加 --lang zh 导出中文原文
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

任务的唯一来源是 `LLMPlanner/eval/rubrics/<case>.yaml`，每个文件的 `prompt` 字段就是交给建模系统的原文（中文）。`prompt_en` 字段是逐句对应的英文版本：数值、单位和歧义程度与中文一致，rubric 的 `ungraded` 项在英文里同样不写明，打分规则不变。50 道核心题、30 道困难题和 20 道极难题都有 `prompt_en`。前两组在 `eval_text_singleturn_qwen.py` 的 `PROMPTS_EN` 里有一份副本，`test/test_text_singleturn_qwen.py` 检查它与 YAML 逐字一致。极难题的中英文题面由脚本直接从 `eval/rubrics_extreme` 读取，没有副本。

从 2026-10-07 起，text-to-CAD 默认用英文题面：`geomcheck.bench tasks` 默认导出 `prompt_en`，`eval_text_singleturn_qwen.py` 默认 `--lang en`，缺省输出文件名带 `_en` 后缀。要用中文原文就加 `--lang zh`。下面第 6、7 节标为“中文题面”的结果都是在改默认值之前跑的，复现时要加 `--lang zh`。`--resume` 时，如果已有结果的 `prompt_lang` 与 `--lang` 不同，脚本会直接报错，以免把两种语言混在一个结果文件里。

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

脚本默认用英文题面；本节下面 Qwen3.5-397B-A17B 的参考结果是中文题面，复现时要加 `--lang zh`。评测脚本会自动把每次运行的 STEP 和 SLDPRT 存到 `eval/artifacts/<输出文件名>/`，并在结果 JSON 里记录 token、调用次数、失败操作数、耗时和费用。

L1 运行过程中，每次拿到 LLM 回复、每轮工具执行完，都会把完整对话、工具结果和原始回复写到同一目录下的 `<题号>_r<k>.checkpoint.json`。如果中途 SolidWorks 卡死或进程被结束，重启服务后在原命令后加 `--resume` 即可续跑：
- 已经跑完的运行直接保留；
- 中断的运行会先在新零件上按原顺序重放已执行的工具恢复模型，再接着原对话继续，已经拿到的回复不会重新请求；
- 请求异常和 LLM 接口出错的运行会重跑。

如果重放结果与记录不一致，这次运行会标记为 `resume_failed`，不会在错误的模型上继续建模。

**L1_v2 的截图工具 `view_model`**：`--action-level L1_v2` 在 L1 工具之外默认再提供 `view_model`。它用 SolidWorks 原生渲染截取当前零件，参数 `views` 最多选 4 个视向（默认 `isometric`、`front`、`top`、`right`，另可选 `back`、`bottom`、`left`、`trimetric`、`dimetric`）。截图时着色带边线、隐藏基准面和草图、关闭阴影，每个视向先缩放到全屏，截完恢复原来的显示设置和视角。各视向拼成两列的一张 PNG，每格下方标注视向和坐标轴方向（例如"前视：沿 -Z 看，右 = +X，上 = +Y"）。工具结果的文字部分是包围盒、体积和所截视向，图片作为下一条 user 消息发给模型，对话里只保留最新一张，旧图换成占位文字；checkpoint 和结果 JSON 不存图片，`--resume` 时也不重放截图。

- 骨干模型不支持图片输入时必须加 `--no-view-tool`，否则第一次截图就会报 `llm_error`。Web 接口对应 `/api/run` 的 `"view_tool": false`。
- 结果 JSON 记录 `view_tool`。加入这个工具之后的 L1_v2 结果不能直接和之前的 L1_v2 结果对比；要对比请用 `--no-view-tool` 重跑。
- 截图依赖 C# 服务新增的 `/api/view/capture`，更新代码后需要重新编译并重启服务。`eval/smoke_view_tools.py` 不调用 LLM，在 SolidWorks 里建一个零件和一个装配体，分别截图存到 `eval/artifacts/smoke_view_tools/` 供人工检查。

参考结果（Workflow-CAD L1 共 24 个工具，骨干模型 Qwen3.5-397B-A17B，温度 0，每题 2 次，共 100 次运行）：

| 零件类型 | 任务数 | 均分 | pass@1 | pass@2 | 平均 token | 平均 LLM 调用 |
|---|---|---|---|---|---|---|
| 日常物品 | 9 | 78.1 | 0.61 | 0.78 | 172.9k | 12.9 |
| 常见机加工件 | 19 | 90.5 | 0.82 | 0.89 | 103.7k | 9.0 |
| 多特征零件 | 8 | 73.9 | 0.56 | 0.75 | 282.0k | 16.7 |
| 特定操作零件 | 14 | 87.8 | 0.82 | 0.86 | 58.2k | 5.8 |
| **全部** | **50** | **84.9**（95% CI [79.3, 90.3]） | **0.74** | **0.84** | 131.9k | 10.0 |

核心形体率为 0.85。每次运行平均 91 s、0.18 元，折合每个合格零件 0.25 元。这些结果对应的打分文件是 `eval/rubric_*_tools24.json`。

### 6.1 GPT-6-Astra（英文题面，2026-10-02）

这一轮早于提交 7d73ae1 的两处 L1 工具修复（见 7.2 节），按那里的说明不要与 7.2 节的结果直接比较。

设置：

- Workflow-CAD L1，24 个工具，每次运行最多 40 次 LLM 调用；
- 模型 `gpt-6-astra`，`--reasoning-effort medium`，温度 0，经 TT Switch 网关轨道 B（`/native`），每次运行带独立的 `X-Session-Id` 以复用 prompt 缓存；
- 英文题面（`--lang en`），每题 1 次，50 道核心题和最初 8 道困难题各跑一轮。

命令：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --provider ttswitch --model gpt-6-astra `
    --action-level L1 --reasoning-effort medium --lang en --split all --repeats 1 `
    --output eval\text_workflow_l1_gpt6_astra_medium_all_en_result.json
```

最初 8 道困难题把 `--split all` 换成 `--split hard --ids quad_frame connecting_rod finned_housing sprocket handwheel camshaft planet_carrier impeller`（`hard` 现在共 30 题，见第 7 节）。结果文件是 `eval/text_workflow_l1_gpt6_astra_medium_{all,hard}_en_result.json`，rubric 打分是 `eval/rubric_gpt6_astra_medium_{all,hard}_en.json`。

| 零件类型 | 任务数 | 均分 | pass@1 | 平均 token | 平均 LLM 调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 日常物品 | 9 | 100 | 1.0 | 129.8k | 12.9 | 126 s |
| 常见机加工件 | 19 | 100 | 1.0 | 55.9k | 6.8 | 54 s |
| 多特征零件 | 8 | 100 | 1.0 | 76.3k | 9.0 | 77 s |
| 特定操作零件 | 14 | 97.1 | 0.93 | 52.5k | 6.4 | 52 s |
| **全部 50 题** | **50** | **99.2**（95% CI [97.6, 100]） | **0.98** | 71.5k | 8.1 | 70 s |
| 困难子集（最初 8 题） | 8 | 100 | 1.0 | 166.4k | 16.2 | 153 s |

两轮的核心形体率都是 1.0。对照第 6、7 节的 Qwen3.5-397B-A17B：50 题均分 84.9、pass@1 0.74，困难子集均分 62.2、pass@1 0.125。两者不完全可比：Qwen 用中文题面、每题 2 次、推理强度为服务端默认。

- 58 次运行都由智能体自己 `finish`，最多用了 36 次调用，没有碰到 40 次的预算。
- 537 次 LLM 调用里没有截断（`finish_reason=length` 0 次）。单次输出最多 930 token；推理 token 中位数 0、最大 813；prompt 缓存命中率 50 题 86%、困难题 91%。
- 唯一不合格的是 `rect_to_circle`（60 分）。英文题面是 “transition tube”，模型放样后用 `shell` 抽成 2 mm 壁厚、两端开口，并在总结里写明“未指定的壁厚暂取 2 mm”；rubric 按实心放样体检查两个端面，底面、顶面两项失败。rubric 的 `ungraded` 已注明“过渡管：未给壁厚，按实心放样体理解”，所以这是题面歧义。同类的 `square_to_round`（“transition fitting”）做成了实心，通过。按“看过结果后不改 rubric”的约束没有调整。

建模时脚本打印的 `score` 是旧版近似分 `score_case_v2`（包围盒、体积区间、特征计数，期望值写在 `eval_text_singleturn_qwen.py` 的 `TextCase` 里），不是 rubric 分。这一轮它给出 50 题 97.61、困难题 93.75。除 `rect_to_circle` 两种分都判失败外，另有 15 题 rubric 满分而旧分扣分，原因都在旧分：

- 旧期望值写错：`quad_frame` 机臂沿对角线，包围盒应为 2 × (110·cos45° + 16) = 187.56，旧值按 252；`camshaft` 桃尖最大半径 20、四个凸轮相差 90°，包围盒应为 40×40，旧值 33.9；`lidless_housing` 精确体积是 100×60×50 − 90×50×45 = 97500，旧区间 100000–104000；`vase` 是空心件，旧体积区间 300k–500k 偏大；`wrench`、`phone_stand` 的两种合理读法 rubric 都接受，旧值只认一种。
- 只按特征类型计数：L1 的 `hole`、`shell`、`circular_pattern` 和轮廓内圆角不计入 cuts / circles / fillets，`flange`、`mug`、`storage_box`、`socket_bolt`、`soap_dish` 因此被判特征不足。
- 失败工具调用扣分：每次 2.5 分，即使已回滚并修正（`standard_die`、`flanged_elbow`、`connecting_rod`、`planet_carrier` 等）。

58 个 STEP 与运行记录一一对应，OCC 读出的体积与 SolidWorks 实测体积最大相对偏差 0.004%，困难题体积与参考实体一致（例如 `quad_frame` 36432.05 对参考 36432.1）。

### 6.2 自部署 Qwen3.8-Flash-Next-FP8 + L1_v2（2026-10-06）

设置：

- Workflow-CAD L1_v2（含 `view_model` 截图工具），每次运行最多 40 次 LLM 调用；
- 模型 `Qwen3.8-Flash-Next-FP8`，自部署 vLLM 的 OpenAI 兼容接口（`--provider qwenlocal`，地址和 key 读 `.env` 的 `QWENLOCAL_BASE_URL`、`QWENLOCAL_API_KEY`），`--reasoning-effort medium`，温度 0；
- 中文题面，每题 1 次；
- 代码版本为提交 d44ebf2。

服务端上下文为 73728 token，输入、推理和输出共用。agent 对这个模型的处理（`planner/workflow/agent.py` 的 `CONTEXT_LIMITS`）：
- 截断重试时输出上限从 8000 起翻倍，最多 32000；
- 如果服务端返回超长 400，就按报错里的输入 token 数把本轮 `max_tokens` 压到窗口剩余量，再试一次；
- 剩余不足 2048 时以 `context_exceeded` 结束。

这一轮没有触发过压缩，也没有 `context_exceeded`。

命令（困难 30 题把 `--split all` 换成 `--split hard`，结果见 7.5 节；修复 prompt 后用英文题面重跑的结果见 6.3、7.6 节）：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --split all --action-level L1_v2 `
    --provider qwenlocal --model Qwen3.8-Flash-Next-FP8 --reasoning-effort medium --max-llm-calls 40 --repeats 1 `
    --sw-restarts 2 --sw-max-private-gb 12 --resume --lang zh --output eval\text_workflow_l1v2_qwen38flash_all_result.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --artifacts eval\artifacts\text_workflow_l1v2_qwen38flash_all_result `
    --rubric-dir eval\rubrics --output eval\rubric_l1v2_qwen38flash_all.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench summary eval\rubric_l1v2_qwen38flash_all.json --repeats 1
```

结果文件是 `eval/text_workflow_l1v2_qwen38flash_all_result.json`，rubric 打分是 `eval/rubric_l1v2_qwen38flash_all.json`。离线重打的分数与结果 JSON 里 `rubric` 字段逐题一致。

| 零件类型 | 任务数 | 均分 | pass@1 | 平均 token | 平均 LLM 调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 日常物品 | 9 | 96.7 | 0.78 | 210.4k | 9.4 | 678 s |
| 常见机加工件 | 19 | 95.7 | 0.84 | 104.2k | 6.8 | 89 s |
| 多特征零件 | 8 | 100 | 1.0 | 157.8k | 10.0 | 132 s |
| 特定操作零件 | 14 | 100 | 1.0 | 81.2k | 5.6 | 63 s |
| **全部 50 题** | **50** | **97.8**（95% CI [95.4, 99.6]） | **0.90**（45/50） | 125.5k | 7.4 | 195 s |

核心形体率 1.0（50/50），共用 627 万 token，总耗时 2.7 小时。“日常物品”的平均 token 和耗时主要被 `phone_stand` 拉高（116 万 token、5297 s），其余 8 题平均 101 s。

对照：Qwen3.5-397B-A17B + L1 的 50 题均分 84.9、pass@1 0.74、核心形体率 0.85（每题 2 次）；GPT-6-Astra + L1 是 99.2、0.98、1.0（英文题面）。三者的工具集、题面语言和次数不完全相同。

不合格的 5 题：

| 任务 | rubric 分 | LLM 调用 | 未通过的条目 |
|---|---|---|---|
| `wrench` | 60 | 6 | handle, handle_centered, volume |
| `gear_disc` | 75 | 7 | slots |
| `mug` | 80 | 6 | handle_ring, cavity_clear |
| `socket_bolt` | 83.3 | 23 | socket |
| `phone_stand` | 90 | 37 | ribs |

运行情况：
- 50 题都由智能体自己 `finish`。5 次回复因长度截断，都放宽上限后自动重试。
- `phone_stand` 第 5 次 LLM 调用时连接被服务端断开（`RemoteProtocolError: peer closed connection`），单次请求约 260 s。原因很可能是服务端或代理的空闲超时：这个服务不流式返回推理内容，长时间思考期间连接上没有数据。两批跑完后用 `--resume` 从检查点续跑，前 4 次回复没有重新请求，最终共 37 次调用，得 90 分。
- 建模时脚本打印的 `score` 是旧版近似分（见 6.1 节），这一轮它给出 96.14、37/50 合格，不作为结果。

### 6.3 Qwen3.8-Flash-Next-FP8 + L1_v2，修复 prompt 后用英文题面重跑（2026-10-08）

和 6.2 节相比只改了两处，其余设置（模型、`--reasoning-effort medium`、40 次调用上限、每题 1 次、看门狗）都相同：

- 代码换成提交 5f0d673。针对 6.2、7.5 节不合格的 12 题，在 L1_v2 系统提示词和工具反馈里加了几条规则：不等径 hull 没有等宽直边、slot 端点是中心线端点、轮辐不用穿轴的宽 slot、回转体先锐边后圆角、杯壁环形把手、径向紧定螺钉孔深度、双盘 + 立柱的建模顺序、螺栓先杆后头；
- 题面用英文（`--lang en`，现在的默认值）。

困难 30 题的结果见 7.6 节。

运行方式：先单独重跑了 12 道不合格题，再跑其余 68 题；`phone_stand` 两批里都有，只计后一次。每题只计一次从头到尾没有中断的运行。下面几种运行都删掉检查点从头重跑，直到没有为止：

- LLM 接口错误：服务端返回空的流式响应，组装回复时报 ValidationError，记为 `llm_error`；
- Web 后端崩溃导致的连接错误；
- 从检查点续跑过的运行。

重跑过的有 `stepped_block`、`knob`、`pillow_block`、`phone_stand` 和困难集的 `quad_frame`。

命令（`--ids` 列出要跑的题；困难集把 `--split all` 换成 `--split hard`）：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --split all --action-level L1_v2 `
    --provider qwenlocal --model Qwen3.8-Flash-Next-FP8 --reasoning-effort medium --max-llm-calls 40 --repeats 1 `
    --sw-restarts 2 --sw-max-private-gb 12 --lang en --resume `
    --ids <题号...> --output eval\text_workflow_l1v2_qwen38flash_rest46_core_en_result.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --artifacts eval\artifacts\text_workflow_l1v2_qwen38flash_core_en_result `
    --rubric-dir eval\rubrics --output eval\rubric_l1v2_qwen38flash_core_en.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench summary eval\rubric_l1v2_qwen38flash_core_en.json --repeats 1
```

两批结果合并在 `eval/text_workflow_l1v2_qwen38flash_core_en_result.json` 里，`merged_from` 字段记录每题来自哪一批：

- 46 题来自 `..._rest46_core_en_result.json`；
- `wrench`、`gear_disc`、`mug`、`socket_bolt` 来自 `..._fail5_core_en_result.json`，其中的 `phone_stand` 是续跑过的，不用。

合并后的 STEP 复制到 `eval/artifacts/text_workflow_l1v2_qwen38flash_core_en_result/`，离线重打的分数与结果 JSON 里的 `rubric` 字段逐题一致。

| 零件类型 | 任务数 | 均分 | pass@1 | 平均 token | 平均 LLM 调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 日常物品 | 9 | 93.3 | 0.78 | 199.0k | 9.4 | 1289 s |
| 常见机加工件 | 19 | 97.8 | 0.89 | 78.1k | 5.2 | 208 s |
| 多特征零件 | 8 | 81.3 | 0.75 | 139.1k | 8.6 | 104 s |
| 特定操作零件 | 14 | 100 | 1.0 | 86.1k | 5.6 | 53 s |
| **全部 50 题** | **50** | **95.0**（95% CI [90.2, 98.9]） | **0.88**（44/50） | 111.8k | 6.6 | 343 s |

核心形体率 0.96（48/50），共用 559 万 token，总耗时 4.8 小时。这一轮服务端有一段时间明显变慢，单次调用 100–200 s（例如 `flange` 只用 4 次调用却花了 276 s），耗时不能和 6.2 节直接比。

不合格的 6 题：

| 任务 | rubric 分 | 6.2 节 | LLM 调用 | 未通过的条目 |
|---|---|---|---|---|
| `fork_end` | 22.2 | 100 | 12 | outer, shank, block, ears, ear_tips, pin_hole, volume（核心形体失败） |
| `twin_arm_rocker` | 28.6 | 100 | 8 | outer, hub, plates, profile, volume（核心形体失败） |
| `key` | 60 | 100 | 7 | ring_hole, teeth |
| `gear_disc` | 75 | 75 | 7 | slots |
| `phone_stand` | 80 | 90 | 28 | ribs, vents |
| `socket_bolt` | 83.3 | 83.3 | 7 | neck_fillet |

和 6.2 节相比，均分 97.8 → 95.0，合格 45 → 44：

- `wrench`（60 → 100）、`mug`（80 → 100）修好了；
- `gear_disc`、`socket_bolt` 分数没变。`socket_bolt` 未通过的条目从 socket 变成了 neck_fillet；
- `key`、`fork_end`、`twin_arm_rocker` 从满分变成不合格，`phone_stand` 从 90 降到 80。新失败题的原因分析见 7.6 节。

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

22 题共 217 个显式条目，配了 50 个针对性错误变体（相位转错、方向反了、少一级特征、孔不通等），每个都恰好在预期条目上失败。连同前 8 题，困难子集共 30 题、304 个条目、73 个错误变体。下面 Qwen 的参考结果只覆盖前 8 题；22 道新题上 claude-sonnet-5 的结果见 7.2 节，GPT-6-Astra 的结果见 7.3 节；claude-sonnet-5 + L1_v2 在全部 30 题上的结果见 7.4 节；自部署 Qwen3.8-Flash-Next-FP8 + L1_v2 的结果见 7.5 节，修复 prompt 后用英文题面重跑的结果见 7.6 节。

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

打分文件是 `eval/rubric_hard_tools24.json`，汇总在 `eval/rubric_hard_tools24_summary.json`。GPT-6-Astra 在这 8 题上的结果（英文题面，8/8 合格）见 6.1 节。

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
    --provider ttswitch --model claude-sonnet-5 --reasoning-effort medium --max-llm-calls 40 --repeats 1 --lang zh `
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

### 7.3 GPT-6-Astra 在加难 22 题上的结果

**评测方式**：与 7.2 节相同（L1、24 个工具、`reasoning_effort = medium`、每题最多 40 次 LLM 调用、每题 1 次、只看 rubric），代码版本为 7d73ae1 之后。区别是骨干模型换成 `gpt-6-astra`（经 TT Switch 网关轨道 B），并且用**英文题面**（`--lang en`），而 7.2 节是中文题面。

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --action-level L1 --split hard `
    --provider ttswitch --model gpt-6-astra --reasoning-effort medium --max-llm-calls 40 --repeats 1 --lang en `
    --sw-restarts 2 --sw-max-private-gb 12 `
    --ids crankshaft brake_disc wheel_rim geneva_wheel spiral_staircase engine_piston pipe_manifold `
          gearbox_housing heat_sink honeycomb_panel gear_pump_body cold_plate truss_beam turbine_disk `
          bt40_holder hydraulic_manifold rocket_fin_can ratchet_wheel injector_plate robot_arm_link `
          menger_sponge u_joint_yoke `
    --output eval\text_workflow_l1_gpt6_astra_medium_hard22_en_result.json
```

建模机器上 OCC 被 Windows 智能应用控制拦截，结果 JSON 里每题的 `rubric` 字段是报错；rubric 分是把 STEP 拷到另一台机器上用 `geomcheck.rubric --rubric-dir eval/rubrics_hard` 打的。结果文件 `eval/text_workflow_l1_gpt6_astra_medium_hard22_en_result.json`，打分文件 `eval/rubric_gpt6_astra_medium_hard22_en.json`。

**结果**（22 题）

| 均分 | pass@1 | 核心形体率 | 平均 token | 平均 LLM 调用 | 平均工具调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 95.9（95% CI [88.6, 100]） | 0.909（20/22） | 1.0（22/22） | 184k | 17.8（1 题用满 40 次） | 17.8 | 195 s |

| 任务 | rubric 分 | 合格 | LLM 调用 | 7.2 节 Sonnet | 未通过的条目 |
|---|---|---|---|---|---|
| `bt40_holder` | 100 | ✓ | 13 | 70 | — |
| `cold_plate` | 100 | ✓ | 17 | 90 | — |
| `crankshaft` | 100 | ✓ | 26 | 64.3 | — |
| `engine_piston` | 100 | ✓ | 20 | 81.8 | — |
| `gear_pump_body` | 100 | ✓ | 19 | 30 | — |
| `gearbox_housing` | 100 | ✓ | 25 | 58.3 | — |
| `geneva_wheel` | 100 | ✓ | 14 | 44.4 | — |
| `heat_sink` | 100 | ✓ | 11 | 100 | — |
| `honeycomb_panel` | 100 | ✓ | 13 | 50 | — |
| `hydraulic_manifold` | 100 | ✓ | 13 | （未计入） | — |
| `injector_plate` | 100 | ✓ | 21 | 100 | — |
| `menger_sponge` | 100 | ✓ | 13 | （未计入） | — |
| `pipe_manifold` | 100 | ✓ | 30 | 27.3 | — |
| `ratchet_wheel` | 100 | ✓ | 8 | 62.5 | — |
| `robot_arm_link` | 100 | ✓ | 20 | 100 | — |
| `rocket_fin_can` | 100 | ✓ | 17 | （未计入） | — |
| `truss_beam` | 100 | ✓ | 16 | 100 | — |
| `turbine_disk` | 100 | ✓ | 11 | 90 | — |
| `u_joint_yoke` | 100 | ✓ | 15 | 100 | — |
| `wheel_rim` | 100 | ✓ | 16 | 100 | — |
| `brake_disc` | 80 | | 14 | 80 | rings, cross_holes |
| `spiral_staircase` | 30 | | 40 | 100 | base, column, steps_low, steps_high, step_shape, posts, base_holes |

在 7.2 节的同一组 19 题上，GPT-6-Astra 均分 95.3、17/19 合格，claude-sonnet-5 是 76.2、7/19；两者题面语言不同，只能粗略对照。

- 21 题由智能体自己 `finish`。没有截断（`finish_reason=length` 0 次），没有 LLM 接口错误，也没有触发看门狗重启或续跑。
- 392 次 LLM 调用，每次只发一个工具调用。推理 token 中位数 0、最大 1552；prompt 缓存命中率 91.9%。22 题共 405 万 token，总耗时约 1.2 小时。

两道不合格的题：

- `spiral_staircase`：模型把 12 级台阶和 12 根栏杆柱逐级建出（每级一个 `ref_plane`、一次扇形 `extrude`、一次栏杆 `extrude`，共 36 次调用），用满 40 次预算，底座上的进线孔和 4 个地脚螺栓孔都没开。已经做对的台阶、栏杆也一并判失败，原因是 rubric 用进线孔 `cable` 给零件坐标系定方位，孔缺失后依赖这个坐标系的 7 个条目都报“未找到实体 cable”。
- `brake_disc`：把模型的零件绕“轴线 + 螺栓孔”所在平面镜像后重新打分，10 项全部通过。题面的约束对这个平面对称：40 片筋每 9° 一片，5 个螺栓孔间隔 72° 且一孔正对一片筋，散热孔隔一个筋间隙排布。题面没有规定散热孔相对螺栓孔偏 +4.5° 还是 −4.5°，所以两种手性都符合题意，但 rubric 只接受一种。7.2 节 claude-sonnet-5 在这题上失败的条目相同、实测值完全一致，也是选了另一种手性。按“看过结果后不改 rubric”的约束这里没有调整；如果把镜像写法补进 `brake_disc.yaml`，两个模型这题都是 100，GPT-6-Astra 为 21/22、均分 96.8。

### 7.4 claude-sonnet-5 + L1_v2 在全部 30 题上的结果

L1_v2（`--action-level L1_v2`）是在 L1 失败分析基础上改进的工具集与系统提示词，L1 本身保持不变。改动见 `planner/workflow/tools_v2.py`：
- 参数容错，以及逐特征的范围、去除量反馈；
- 拒绝多实体，`get_model_state` 分组输出；
- 新增凸包、极坐标轮廓；sweep 支持空间折线和螺旋路径，loft 支持点截面和引导线；
- 真正的锥形沉头；多条边的圆角、倒角合并成一个特征；
- 重复调用拦截，提示剩余调用预算；
- 第 6 节的 `view_model` 截图工具。

这一节用 L1_v2 把困难子集 30 题完整跑了一遍。前 8 题以前只有 Qwen 和 GPT-6-Astra 的结果，加难 22 题中有 3 题在 7.2 节里只有不计入均分的试跑；这是 claude-sonnet-5 第一次在同一协议下跑满 30 题。

**评测方式**：除 `--action-level L1_v2` 外与 7.2 节相同：claude-sonnet-5，`reasoning_effort = medium`，中文题面，每题最多 40 次 LLM 调用，每题 1 次，只看 rubric。`view_model` 默认打开。代码版本为提交 439ec34（含 7fe624d 的 sweep/loft 扩展和 c4ae189 的沉头孔、多边圆角修复），再加 289209c 的逐步进度日志；日志不改变 agent 行为。

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --action-level L1_v2 --split hard `
    --provider ttswitch --model claude-sonnet-5 --reasoning-effort medium --max-llm-calls 40 --repeats 1 --lang zh `
    --sw-restarts 2 --sw-max-private-gb 12 --resume `
    --ids <题号...> --output eval\text_workflow_l1v2_mymodel_hard_result.json
```

30 题分两批跑：
- `eval/text_workflow_l1v2_sonnet5_medium_hard_low11_result.json`：7.2 节 L1 分数最低的 11 题。第 10 名并列，两题都跑了。
- `eval/text_workflow_l1v2_sonnet5_medium_hard_rest19_result.json`：其余 19 题。

每题的 rubric 结果在结果 JSON 的 `rubric` 字段里。Web 后端终端逐行打印每次 LLM 调用（第几次、累计 token、耗时）和每个工具调用的成败，便于监控。

**结果**（30 题）

| 均分 | pass@1 | 核心形体率 | 平均 token | 平均 LLM 调用 | 平均工具调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 89.2（95% CI [84.1, 93.9]） | 0.533（16/30） | 0.967（29/30） | 349k | 12.0（无一题用满 40 次） | 19.9 | 618 s |

均分的置信区间是对 30 题分数做 bootstrap 得到的。分组来看：
- 前 8 题：均分 89.5，4/8 合格，核心形体 7/8。Qwen3.5-397B-A17B + L1 在这 8 题上均分 62.2。
- 加难 22 题：均分 89.1，12/22 合格，核心形体 22/22。
- 7.2 节的同一组 19 题：L1_v2 均分 88.6、10/19 合格、核心形体 19/19。claude-sonnet-5 + L1 按现行 rubric 是 78.0、8/19 合格、核心形体 17/19。

| 任务 | rubric 分 | 合格 | LLM 调用 | Sonnet + L1 | GPT-6-Astra + L1 | 未通过的条目 |
|---|---|---|---|---|---|---|
| `geneva_wheel` | 100 | ✓ | 19 | 44.4 | 100 | — |
| `gearbox_housing` | 100 | ✓ | 20 | 58.3 | 100 | — |
| `honeycomb_panel` | 100 | ✓ | 10 | 50 | 100 | — |
| `cold_plate` | 100 | ✓ | 7 | 90 | 100 | — |
| `ratchet_wheel` | 100 | ✓ | 11 | 75 | 100 | — |
| `finned_housing` | 100 | ✓ | 8 | — | 100 | — |
| `sprocket` | 100 | ✓ | 10 | — | 100 | — |
| `camshaft` | 100 | ✓ | 11 | — | 100 | — |
| `impeller` | 100 | ✓ | 8 | — | 100 | — |
| `heat_sink` | 100 | ✓ | 5 | 100 | 100 | — |
| `truss_beam` | 100 | ✓ | 17 | 100 | 100 | — |
| `hydraulic_manifold` | 100 | ✓ | 4 | （未计入） | 100 | — |
| `injector_plate` | 100 | ✓ | 6 | 100 | 100 | — |
| `robot_arm_link` | 100 | ✓ | 10 | 100 | 100 | — |
| `menger_sponge` | 100 | ✓ | 5 | （未计入） | 100 | — |
| `u_joint_yoke` | 100 | ✓ | 15 | 100 | 100 | — |
| `connecting_rod` | 91.7 | | 10 | — | 100 | chamfers |
| `wheel_rim` | 91.7 | | 12 | 100 | 100 | window_fillets |
| `engine_piston` | 90.9 | | 18 | 81.8 | 100 | pin_bosses |
| `planet_carrier` | 90.9 | | 11 | — | 100 | chamfers |
| `crankshaft` | 85.7 | | 5 | 64.3 | 100 | bolt_holes, volume |
| `spiral_staircase` | 80 | | 9 | 100 | 30 | column, base_holes |
| `rocket_fin_can` | 77.8 | | 20 | （未计入） | 100 | fins, fin_planform |
| `gear_pump_body` | 70 | | 14 | 30 | 100 | ports, port_bosses, volume |
| `turbine_disk` | 70 | | 15 | 90 | 100 | web, fillets, volume |
| `bt40_holder` | 70 | | 14 | 70 | 100 | flange, v_groove, volume |
| `brake_disc` | 70 | | 14 | 100 | 80 | rings, hat, volume |
| `quad_frame` | 66.7 | | 15 | — | 100 | plate, center_plate, fillets, volume |
| `handwheel` | 66.7 | | 18 | — | 100 | hub, hex_bore, knob |
| `pipe_manifold` | 54.5 | | 19 | 27.3 | 100 | main_pipe, branches, branch_bores, drain, volume |

对照列的说明：
- “Sonnet + L1”取自 `eval/rubric_hard22_sonnet5_medium.json`，已按提交 2e65989 放宽后的现行 rubric 重新打分：`brake_disc` 两种手性都接受，`ratchet_wheel` 在零件坐标系里量尺寸。所以 `brake_disc` 是 100、`ratchet_wheel` 是 75，与 7.2 节表格不同。
- “GPT-6-Astra + L1”取自 7.3 节和 6.1 节的打分文件，用的是放宽前的 rubric，题面为英文。按 7.3 节的镜像分析，它的 `brake_disc` 在现行 rubric 下是 100。前 8 题那一轮早于 7d73ae1 的两处工具修复。
- “—”表示该模型没有用 claude-sonnet-5 跑过这题；“（未计入）”的 3 题见 7.2 节末尾的试跑说明。

观察：
- L1 分数最低的 11 题里，L1_v2 有 9 题提高、1 题持平。这 11 题均分从 61.9 提高到 85.6，合格从 0 题增加到 5 题。
- 槽轮、减速器箱体、蜂窝板从 44–58 分直接到满分；`pipe_manifold` 和 `gear_pump_body` 的核心形体也由失败变为通过。
- 退步的有 4 题：`brake_disc`（100 → 70）、`spiral_staircase`（100 → 80）、`wheel_rim`（100 → 91.7）、`turbine_disk`（90 → 70）。每题只跑 1 次，单题的涨跌包含随机波动。
- 没合格的 14 题里，有 4 题只差一个条目，其中 3 题是倒角或圆角：`connecting_rod`、`planet_carrier` 差倒角，`wheel_rim` 差窗角圆角。另一题是 `engine_piston` 差销座。
- 平均 LLM 调用和工具调用都比 7.2 节的 L1 少：LLM 调用 12.0 对 21.9，工具调用 19.9 对 35.2，没有一题用满预算。

运行情况：
- 30 题都由智能体自己 `finish`，没有 LLM 接口错误；4 次回复因长度截断，都放宽上限后自动重试。
- SolidWorks 私有内存超过 12 GB，看门狗在题间自动重启了 3 次，都没有影响正在跑的题。
- `crankshaft` 为了加日志手动停过一次，用 `--resume` 从检查点续跑，已经拿到的回复没有重新请求。
- 30 题共用 1047 万 token，总耗时约 5.2 小时。最慢的是 `menger_sponge`（2752 s，后期每次切除约 1 分钟）和 `rocket_fin_can`（2648 s）。

### 7.5 自部署 Qwen3.8-Flash-Next-FP8 + L1_v2 在全部 30 题上的结果

设置和代码版本与 6.2 节相同，命令把 `--split all` 换成 `--split hard`。除骨干模型外，协议与 7.4 节一致：L1_v2、`view_model` 打开、中文题面、每题最多 40 次 LLM 调用、每题 1 次、只看 rubric。结果文件是 `eval/text_workflow_l1v2_qwen38flash_hard_result.json`，rubric 打分是 `eval/rubric_l1v2_qwen38flash_hard.json`（`--rubric-dir eval\rubrics_hard`）。

`geomcheck.bench summary` 按 50 道核心题汇总，不适用于困难子集。下表的均分和置信区间直接由 30 题的 rubric 分算出，置信区间是对题目做 bootstrap（10000 次）。

| 均分 | pass@1 | 核心形体率 | 平均 token | 平均 LLM 调用 | 平均工具调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 95.1（95% CI [91.4, 98.3]） | 0.767（23/30） | 0.967（29/30） | 311k | 14.7（无一题用满 40 次） | 18.1 | 673 s |

分组来看：
- 前 8 题：均分 93.6，6/8 合格，核心形体 8/8。
- 加难 22 题：均分 95.7，17/22 合格，核心形体 21/22。
- 对照 7.4 节的 claude-sonnet-5 + L1_v2（89.2、16/30、核心形体 29/30）：9 题更高、18 题持平、3 题更低。

| 任务 | rubric 分 | 合格 | LLM 调用 | token | 耗时 (s) | Sonnet + L1_v2 | 未通过的条目 |
|---|---|---|---|---|---|---|---|
| `brake_disc` | 100 | ✓ | 9 | 171k | 785 | 70 | — |
| `bt40_holder` | 100 | ✓ | 10 | 187k | 575 | 70 | — |
| `camshaft` | 100 | ✓ | 5 | 87k | 209 | 100 | — |
| `cold_plate` | 100 | ✓ | 7 | 130k | 336 | 100 | — |
| `connecting_rod` | 100 | ✓ | 8 | 138k | 246 | 91.7 | — |
| `engine_piston` | 100 | ✓ | 22 | 477k | 941 | 90.9 | — |
| `finned_housing` | 100 | ✓ | 9 | 155k | 205 | 100 | — |
| `gear_pump_body` | 100 | ✓ | 14 | 248k | 261 | 70 | — |
| `gearbox_housing` | 100 | ✓ | 20 | 385k | 504 | 100 | — |
| `heat_sink` | 100 | ✓ | 5 | 83k | 225 | 100 | — |
| `honeycomb_panel` | 100 | ✓ | 14 | 384k | 966 | 100 | — |
| `hydraulic_manifold` | 100 | ✓ | 6 | 100k | 225 | 100 | — |
| `impeller` | 100 | ✓ | 9 | 155k | 280 | 100 | — |
| `injector_plate` | 100 | ✓ | 18 | 351k | 318 | 100 | — |
| `menger_sponge` | 100 | ✓ | 8 | 133k | 265 | 100 | — |
| `pipe_manifold` | 100 | ✓ | 10 | 210k | 452 | 54.5 | — |
| `quad_frame` | 100 | ✓ | 26 | 728k | 2230 | 66.7 | — |
| `ratchet_wheel` | 100 | ✓ | 8 | 134k | 178 | 100 | — |
| `robot_arm_link` | 100 | ✓ | 12 | 212k | 204 | 100 | — |
| `sprocket` | 100 | ✓ | 13 | 235k | 342 | 100 | — |
| `truss_beam` | 100 | ✓ | 11 | 186k | 272 | 100 | — |
| `u_joint_yoke` | 100 | ✓ | 10 | 184k | 369 | 100 | — |
| `wheel_rim` | 100 | ✓ | 18 | 351k | 629 | 91.7 | — |
| `spiral_staircase` | 90 | | 12 | 287k | 850 | 80 | column |
| `geneva_wheel` | 88.9 | | 12 | 205k | 270 | 100 | set_screw |
| `planet_carrier` | 81.8 | | 17 | 301k | 246 | 90.9 | discs, volume |
| `crankshaft` | 78.6 | | 35 | 808k | 968 | 85.7 | phasing, counterweights, volume |
| `rocket_fin_can` | 77.8 | | 34 | 931k | 3559 | 77.8 | outer, fin_planform（核心形体失败） |
| `turbine_disk` | 70 | | 38 | 906k | 2720 | 70 | web, fillets, volume |
| `handwheel` | 66.7 | | 22 | 459k | 559 | 66.7 | hex_bore, spokes, volume |

观察：
- 7.4 节里 claude-sonnet-5 没合格的 14 题中，Qwen3.8 合格了 8 题，包括 `pipe_manifold`（54.5 → 100）、`quad_frame`（66.7 → 100），以及 `brake_disc`、`bt40_holder`、`gear_pump_body`（都是 70 → 100）。7.4 节里只差一个倒角、圆角或销座的 `connecting_rod`、`wheel_rim`、`engine_piston` 也都满分。
- 两个模型都没合格的有 6 题：`spiral_staircase`、`planet_carrier`、`crankshaft`、`rocket_fin_can`、`turbine_disk`、`handwheel`。其中后三题两者分数完全相同，`turbine_disk` 未通过的条目也相同（web, fillets, volume），`rocket_fin_can` 都失败在 fin_planform，`handwheel` 都失败在 hex_bore。
- 更低的 3 题：`geneva_wheel`（100 → 88.9）、`planet_carrier`（90.9 → 81.8）、`crankshaft`（85.7 → 78.6）。每题只跑 1 次，单题的涨跌包含随机波动。
- 和 claude-sonnet-5 相比，调用更多、耗时更长，但 token 更少：平均 LLM 调用 14.7 对 12.0，平均耗时 673 s 对 618 s，平均 token 311k 对 349k。最慢的三题 `rocket_fin_can`（3559 s）、`turbine_disk`（2720 s）和 `quad_frame`（2230 s）都用了 26 次以上调用，单次调用常超过 1 分钟。

运行情况：
- 30 题都由智能体自己 `finish`，没有 LLM 接口错误和 `context_exceeded`；8 次回复因长度截断，都放宽上限后自动重试。
- SolidWorks 私有内存超过 12 GB，看门狗在 `wheel_rim` 和 `bt40_holder` 开始前各重启了一次，没有影响正在跑的题。
- 30 题共用 932 万 token，总耗时约 5.6 小时。
- 脚本打印的旧版近似分是 96.25、26/30 合格，不作为结果。

把 6.2 节的 50 题和这 30 题合在一起，80 题 rubric 均分 96.8，68/80 合格，核心形体 79/80，共 1559 万 token。

### 7.6 修复 prompt 后用英文题面重跑全部 30 题（2026-10-08）

设置、代码版本（5f0d673）、英文题面和“每题只计一次没有中断的运行”的规则都与 6.3 节相同。

两批结果合并在 `eval/text_workflow_l1v2_qwen38flash_hard_en_result.json`：

- 23 题来自 `..._rest23_hard_en_result.json`；
- 7.5 节不合格的 7 题来自 `..._fail7_hard_en_result.json`。

rubric 打分是 `eval/rubric_l1v2_qwen38flash_hard_en.json`（`--rubric-dir eval\rubrics_hard`），均分和置信区间的算法同 7.5 节。

| 均分 | pass@1 | 核心形体率 | 平均 token | 平均 LLM 调用 | 平均工具调用 | 平均耗时 |
|---|---|---|---|---|---|---|
| 96.1（95% CI [92.7, 98.9]） | 0.80（24/30） | 1.0（30/30） | 288k | 13.7（无一题用满 40 次） | 16.7 | 558 s |

分组来看：

- 前 8 题：均分 95.1，6/8 合格；
- 加难 22 题：均分 96.5，18/22 合格。

不合格的 6 题：

| 任务 | rubric 分 | 7.5 节 | LLM 调用 | 未通过的条目 |
|---|---|---|---|---|
| `pipe_manifold` | 63.6 | 100 | 18 | branches, branch_bores, drain, volume |
| `handwheel` | 77.8 | 66.7 | 17 | spokes, volume |
| `crankshaft` | 78.6 | 78.6 | 30 | phasing, counterweights, volume |
| `connecting_rod` | 83.3 | 100 | 8 | centerline, oil_hole |
| `spiral_staircase` | 90 | 90 | 10 | column |
| `turbine_disk` | 90 | 70 | 9 | dovetail |

和 7.5 节相比，均分 95.1 → 96.1，合格 23 → 24：

- 修好了 3 题：`geneva_wheel`（88.9 → 100）、`planet_carrier`（81.8 → 100）、`rocket_fin_can`（77.8 → 100）；
- 提高但仍不合格的 2 题：`turbine_disk`（70 → 90）、`handwheel`（66.7 → 77.8）；
- 新失败的 2 题：`connecting_rod`、`pipe_manifold`（都从 100 降下来）。

运行情况：

- 30 题都由智能体自己 `finish`；12 次回复因长度截断，都放宽上限后自动重试；
- 看门狗在 `gearbox_housing` 开始前重启过一次 SolidWorks；
- 30 题共用 865 万 token，总耗时约 4.7 小时。

**80 题合计**（6.3 节 50 题 + 本节 30 题）：

- rubric 均分 95.4（95% CI [92.1, 98.0]），68/80 合格，核心形体 78/80；
- 平均 9.3 次 LLM 调用，共 1424 万 token；
- 对照 6.2 + 7.5 节：均分 96.8，68/80 合格，核心形体 79/80。

修好的 7 题和新失败的 5 题正好抵消，所以合格数不变。

**新失败的 5 题是否由 prompt 修改引起。** 这一轮的推理内容没有被服务端返回，只能从工具调用序列判断。每题只跑了 1 次，而且同时换了代码和题面语言，所以下面的判断不能区分 prompt 修改、语言切换和随机波动。

| 任务 | 错在哪里 | 与新规则的关系 |
|---|---|---|
| `twin_arm_rocker` | 先拉伸 40 mm 轮毂，再把两块板贴在轮毂两端外侧（Z = −8~0 和 40~48），总长变成 56 mm。题面要求板的外侧面与轮毂端面齐平，即板在轮毂长度之内 | 可能有关。做法和新增的“双盘 + 立柱：先做贯通两盘间距的立柱，再贴合拉伸第二盘”一致，这条规则可能被套用到了“轮毂 + 两块板”上 |
| `fork_end` | 叉根块画在 Y = 25~35，嵌进了杆顶，没有接在杆顶 Y = 35~45 上；总高少 10 mm，后面的特征全部跟着下移 | 无关，没有涉及新规则的特征 |
| `key` | 把 10×5 环孔作为头部拉伸的内轮廓，随后从 x = 0 拉伸 42 mm 的杆，盖住了半个环孔；齿槽检查也随之失败 | 无关 |
| `connecting_rod` | 油孔先切 5.5 mm，刚好通到销孔，这是对的；随后模型自己把深度改成 25.5 mm，穿过销孔，在对面孔壁上又切出 0.5 mm 的盲坑 | 关系不大。第一次的深度和新增的紧定螺钉孔规则一致，出错的是之后没有依据的修改 |
| `pipe_manifold` | 支管从 Z = 0 起按空心管拉伸，排水凸台从 Z = 0 按实心拉伸，主管内腔里留下了多余材料，体积多 5.6%。7.5 节是先做完所有实体，最后统一开孔 | 无关，属于建模顺序错误 |

新加的工具反馈在这 5 题里没有起误导作用：

- “不等径 hull”提示在 80 题中触发了 7 题，模型都继续用 hull，这些题本来就应该用 hull；
- “双盘 + 立柱”提示触发了 5 题，都不在这 5 道新失败题里。

要区分这几种原因，可以在同一代码下用 `--lang zh` 重跑这 5 题，或在 d44ebf2 代码下用 `--lang en` 重跑这 5 题，每题多跑几次。

**不合格的 12 题有没有受外部因素影响。** 都没有：

- 全部由智能体自己 `finish`，没有 LLM 接口错误、`context_exceeded` 或续跑；
- LLM 调用最多 30 次，没有用满 40 次；
- 工具返回里没有 SolidWorks 连接或 COM 异常；
- 两次看门狗重启都发生在题目开始前（`phone_stand` 和 `gearbox_housing`）。

截断的回复（`phone_stand` 2 次，`pipe_manifold`、`crankshaft`、`turbine_disk` 各 1 次）都放宽上限后自动重试了。`fork_end` 有一次工具参数不是合法 JSON，`pipe_manifold` 有一次 cut 漏了 `loops`，都被工具拒绝后由模型改正了参数。这 12 题的失败都是模型自身的建模错误。

---

## 8. 极难子集：20 道最难题（extreme）

第 7 节的困难子集上，GPT-6-Astra 和自部署 Qwen3.8 都已经接近饱和（pass@1 分别为 0.91 和 0.77），所以又准备了 20 道更难的题。和困难子集相比，这 20 道题的难点不在特征多，而在下面三类：
- **精确曲线**：渐开线、NACA 翼型、凸轮的升程规律与滚子包络、圆渐开线涡旋、变螺距螺旋线。
- **需要先求解的工程量**：法面参数换算成端面参数、钣金 K 因子展开、两个投影角合成的空间孔方向、平衡孔直径（由质心条件决定）、英制图纸标注和字母钻、公切线的切点。
- **SolidWorks 里也不好做的操作**：变半径圆角、拔模、等厚抽壳，以及沿整条相贯线的马鞍圆角。

极难子集**不计入** 50 题和困难子集的统计。rubric 在 `LLMPlanner/eval/rubrics_extreme/`，每个 YAML 都有中文 `prompt` 和逐句对应的英文 `prompt_en`。`geomcheck/bench.py` 里把它们单列为 `Extreme parts` 一类，`eval_text_singleturn_qwen.py` 里对应 `--split extreme`。

### 8.1 任务列表

“oracle 调用”是 8.3 节里 SolidWorks 实机参考解的工具调用数；“LLM 调用”等于工具调用数加 1（最后一次 `finish`），含义见 8.3 节。

| 任务（case id） | 显式条目 | 主要难点 | oracle 工具调用 | LLM 调用 |
|---|---|---|---|---|
| 双头左旋蜗杆轴 `worm_shaft` | 12 | ZA 型直线齿廓的双头左旋螺旋槽、z = 55 截面上的螺纹相位、螺纹在台阶处直接截止、卡簧槽、键槽、带钻尖的中心孔 | 4 | 5 |
| 复合角钻孔块 `compound_drill_block` | 11 | 由两个投影角确定的空间斜孔；垂直于 35° 斜面、且轴线与孔 A 相交的孔 B（入口位置要求解）；118° 钻尖；沉孔 | 7 | 8 |
| 变半径圆角 L 形支架 `variable_fillet_bracket` | 11 | 半径 3 → 9 线性变化的圆角、圆角之后再加筋、不等边倒角、锥形沉头孔、腰形孔 | 5 | 6 |
| 滚子从动件盘形凸轮 `roller_cam` | 9 | 摆线升程 + 3-4-5 多项式回程的理论廓线、沿法向内移 12 的滚子包络、以键槽为基准的相位、径向紧定孔 | 4 | 5 |
| 斜齿内齿圈 `helical_ring_gear` | 10 | 法面模数和压力角换算到端面、渐开线内齿、15° 右旋、z = 10 截面上的齿相位、只在一端倒角 | 5 | 6 |
| NACA 4412 翼肋 `naca_wing_rib` | 8 | 沿中弧线法向叠加厚度的翼型、闭合尖后缘、直径随 yt(x) 变化的减重孔、翼梁缺口 | 2 | 3 |
| 涡旋压缩机静涡旋盘 `scroll_wrap_plate` | 8 | 两侧壁都是圆渐开线的涡旋壁、两端沿发生线截平、顶部密封槽 | 3 | 4 |
| 左旋变螺距压缩弹簧 `variable_pitch_spring` | 9 | 分段线性变螺距（中心线高度是螺距对圈数的积分）、左旋、两端磨平 | 7 | 8 |
| 三维弯管管路 `pipe_route_3d` | 9 | 由交点（PI 点）和弯曲半径确定的空间中心线、两端法兰孔的方位、内孔连通的支管 | 12 | 13 |
| K 因子钣金支架 `sheet_metal_kfactor` | 9 | 按 K 因子把展开图上的孔位换算到折弯后、90° 和 45° 两道折弯 | 4 | 5 |
| 铸造三通歧管 `cast_manifold_fillets` | 8 | 沿整条相贯线的 R5 马鞍圆角、法兰内侧圆角、法兰孔的 45° 方位、贯通内孔 | 10 | 11 |
| 压铸盖 `die_cast_cover` | 10 | 内外侧壁都以分型面为中性面拔模 3°、反向拔模的螺钉柱、两侧拔模的十字筋、拔模凸台和通孔 | 8 | 9 |
| 静平衡飞轮 `balanced_flywheel` | 9 | 平衡孔直径由质心落在轴线上的条件求出（要计入键槽的偏心）、窗口竖直棱边 R5、腹板圆角 | 2 | 3 |
| 英制图纸标注板 `inch_callout_plate` | 9 | 英寸换算、字母钻 H、21 号钻（10-32 UNF 螺纹底孔）、沉孔与 82° 锥形沉孔、螺栓圆上孔的相位 | 7 | 8 |
| 六角孔阵裁切板 `clipped_hex_perforation` | 9 | 六边形点阵按“6 个顶点全部在界内”筛选、尖顶朝 ±Y、切平的圆板 | 2 | 3 |
| 相切三孔连杆 `tangent_link_lever` | 9 | 外公切线和两段与圆相切的凹圆弧组成的外轮廓（切点要求解）、键槽、圆弧槽 | 4 | 5 |
| 圆角抽壳外壳 `filleted_shell_enclosure` | 9 | 先圆角再等厚抽壳、止口、切过顶部圆角的通风槽、螺钉柱 | 7 | 8 |
| 三圆柱交体节点 `tricylinder_node` | 7 | 三根圆柱的公共部分（Steinmetz 体）、三向管和在节点内相互贯通的盲孔、空间斜撑与球头 | 11 | 12 |
| 扭转槽旋钮 `twisted_flute_knob` | 8 | 6 条匀速左旋扭转的凹槽（每个水平截面上是圆）、凹槽在 z = 30 处平截、D 形孔与紧定孔 | 8 | 9 |
| 球形封头接管 `spherical_dome_ports` | 9 | 按纬度、经度给出的径向接管、不过球心的水平接管、法兰孔相位、连通的内腔 | 11 | 12 |

20 题共 183 个显式条目（每题 7–12 个），没有隐含条目，共 361 个几何检查。

### 8.2 评测方式

打分方式与前面各节完全相同：只读 STEP 的几何，对照 `eval/rubrics_extreme/<case>.yaml` 打分；题目分数是通过的显式条目占比，所有显式条目都通过才算合格。容差也一样（长度 0.1 mm、角度 0.5°、体积 1%）。提交要求见第 3 节：每题每次运行一个 `<case>_r<k>.step`，单一实体，单位 mm。

**用任意系统评测**（只需要第 0 节的 `venv-occ`，不需要 SolidWorks）：

```powershell
# 1) 导出 20 道题；默认英文题面，加 --lang zh 导出中文原文
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_extreme tasks --output tasks_extreme.jsonl

# 2) 用你的系统逐题建模，导出为 my_extreme_run\<case>_r1.step ...

# 3) 打分与汇总
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --artifacts my_extreme_run --rubric-dir eval/rubrics_extreme --output my_extreme_scores.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.bench --rubric-dir eval/rubrics_extreme summary my_extreme_scores.json --repeats 1
```

汇总输出的第一行是全部 20 题的均分、95% 置信区间、pass@k 和核心形体率，第二行是 `Extreme parts` 一类（就是这 20 题）。用 8.3 节的 20 个 oracle STEP 走一遍这三步，得到 `score=100.0 pass@1=1.0 core_form=1.0`。

**用 Workflow-CAD 评测**（环境与启动方式见第 6 节）：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --action-level L1_v2 --split extreme `
    --provider <provider> --model <model> --reasoning-effort medium --max-llm-calls 40 --repeats 1 --lang en `
    --sw-restarts 2 --sw-max-private-gb 12 --resume --output eval\text_workflow_l1v2_mymodel_extreme_en_result.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --rubric-dir eval\rubrics_extreme `
    --artifacts eval\artifacts\text_workflow_l1v2_mymodel_extreme_en_result --output eval\rubric_mymodel_extreme_en.json
```

脚本在每题导出 STEP 后会自动用 `eval/rubrics_extreme` 打 rubric 分，写进结果 JSON 的 `rubric` 字段。建议协议与 7.4 节一致：L1_v2、每题最多 40 次 LLM 调用、温度 0、只看 rubric；报告里写明题面语言和每题次数。目前还没有任何模型在这 20 题上的结果。

### 8.3 SolidWorks 实机可解性与 oracle 轨迹的 LLM 调用次数

为了确认每道题在 Workflow-CAD 的工具集下确实做得出来，我们在 SolidWorks 里给每题手写了一条 **oracle workflow 轨迹**（`LLMPlanner/eval/extreme_l1v2_reference.py`）。它只调用模型能拿到的那套 L1_v2 工具（`planner/workflow/tools_v2.py`），参数也是模型能填的同一套，导出 STEP 后用上面的 rubric 打分。20 题在同一次运行里**全部 100 分**，没有失败的工具调用。20 题共约 48 分钟，最慢的是弹簧（692 s）和内齿圈（385 s）。

**LLM 调用次数的口径**：oracle 本身不调用 LLM。统计时假设智能体每轮发一个工具调用、每次都成功，最后再用一轮调用 `finish`，所以“LLM 调用 = 工具调用数 + 1”。这是智能体在这套工具下完成每道题所需 LLM 调用数的一个参考下限：没有算查询模型状态、截图检查和出错重试。

| 最少 | 中位数 | 平均 | p90 | 最多 |
|---|---|---|---|---|
| 3 | 7 | 7.15 | 12 | 13 |

每题的工具调用数和 LLM 调用数见 8.1 节的表。完整轨迹（每次工具调用的名称和参数）在 `LLMPlanner/eval/extreme/oracle_l1v2/<case>.json`，汇总在同目录的 `summary.json`。字段 `tool_calls` 是工具调用数，`llm_calls_one_tool_per_turn` 是 LLM 调用数，另外还有 `rubric_score`、`volume_mm3`、`bbox_mm` 和 `seconds`。重放某几题（需要 SolidWorks、C# 服务和 Web 后端，不调用 LLM）：

```powershell
venv\Scripts\python.exe -X utf8 eval\extreme_l1v2_reference.py worm_shaft helical_ring_gear   # 不带题号则跑全部 20 题
```

需要注意三点：
- **调用次数少，不代表题目简单。** oracle 里的精确曲线和需要求解的量都是先用 Python 离线算好，再以多段线坐标或数值直接填进工具参数的，例如渐开线、NACA 翼型点、凸轮包络、平衡孔直径和切点。智能体必须自己算出这些数，难点主要在这里。
- **蜗杆有一处工具层面的已知问题。** SolidWorks 沿螺旋线扫掠非圆截面时，会把截面绕起点切向多转约 2.67°。这个偏差在每个方位角上都一样，加密螺旋线采样点也消除不了。不补偿的话，齿侧面偏离理论直线约 0.034 + 0.0475·(r − 15) mm：半径 13 处 −0.06 mm，半径 17 处 +0.13 mm，超出 0.1 mm 的容差，`flank` 条目失败。oracle 是把截面预先反向转了 2.67° 才拿到满分的。智能体不知道要做这个补偿，蜗杆的 `flank` 条目很可能失败；在修好 `sweep` 的螺旋路径之前，分析结果时要考虑这一点。
- **组合方式很多。** 有几题的 oracle 用了不显然的组合：
  - 复合角斜孔：先建临时块，旋转切出种子孔，再用圆周阵列转到斜向，最后切掉临时块；
  - 压铸盖：外侧拔模用拉伸自带的拔模角，内腔用壁厚为 5·cos3° 的抽壳；
  - 内齿圈：三个按螺旋角转过的端面截面放样切出一个齿槽，再圆周阵列 54 个。
  
  这些只是一种可行解；rubric 只看几何，用别的建法同样可以满分。

### 8.4 rubric 本身是怎么验证的

和前几组一样，每题都有一个直接用 OpenCASCADE 按题面构造的参考实体（`geomcheck/tests/refs_extreme.py`），必须得 100 分，并且体积与 rubric 推导值相差不超过 0.05%。另有 71 个针对性错误变体，测试断言**恰好**是预期的那几个条目失败（`geomcheck/tests/test_refs_extreme.py`）。这些变体包括：旋向反了、单头当双头、法面模数当成端面模数、平衡孔不计键槽、字母钻 H 当成 1/4 in、K 因子取 0.5、六角孔按孔心而不是顶点筛选、变半径圆角做成等半径等。

```powershell
venv-occ\Scripts\python.exe -m pytest geomcheck/tests/test_refs_extreme.py -q -p no:cacheprovider
```

在 SolidWorks 上做 oracle 时发现并修正了几处问题，都在 OCC 参考实体和 oracle STEP 上重新验证过：
- **翼肋的 rubric 体积**：平面被长 B 样条围住时，OpenCASCADE 的体积积分会明显失准，原来的 17119 是错的，正确值是 16546.43。现在检查器在精确积分和网格体积相差超过 0.5% 时改用网格体积，rubric 也改成了正确值。
- **变半径圆角支架**：腰形孔如果切穿整个零件，会在加强筋上开出缺口，原来的 rubric 查不出来，现在加了沿筋的材料探针。
- **弹簧的端面判定**：SolidWorks 导出的磨平端面上，采样点会超出端面平面 0.002–0.003 mm，原来按 0.001 mm 的容差会判成“端部没有平面”。现在端面容差是 0.01 mm。另外修正了主轴坐标系在细圆环面上误选方向的问题。

---

## 9. 已知局限

- rubric 是人工编写的。rubric 没写到的要求不会被检查，标为 `ungraded` 的内容也不打分。
- 目前只有 OpenCASCADE 一个检查器。一个通过 SolidWorks API 评估同一套 rubric 的检查器正在开发，用来交叉验证。
- 题面原文是中文，英文版是逐句翻译。两种语言的结果不能直接比较，报告里要写明用的是哪一种。翻译成其他语言时，以 YAML 中的中文原文为准。
- 旧的评分方式（包围盒、体积区间、特征树计数）已经被 rubric 取代，只在论文里用来对比说明两者的差别。
