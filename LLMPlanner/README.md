# LLM Planner — SolidWorks 自然语言自动建模

将自然语言建模指令（如"创建一个 100mm x 50mm 的长方体"）通过 LLM 解析为 SolidWorks 操作序列，自动调用 C# HTTP API 完成建模。

## 架构

```
用户自然语言指令
       ↓
   LLM Client (planner/llm_client.py)
   - OpenAI 兼容 API (结构化输出)
   - diskcache 磁盘缓存
   - tenacity 自动重试
       ↓
   SwModelingPlan (planner/schemas.py)
   - Pydantic 结构化输出模型
   - 有序操作步骤列表
       ↓
   Executor (planner/executor.py)
   - HTTP 请求发送到 C# 服务
   - 逐步执行 + 错误处理
       ↓
   SolidWorks C# HTTP API (localhost:5100)
```

## 文件结构

```
LLMPlanner/
├── planner/
│   ├── __init__.py        # 包入口
│   ├── schemas.py         # Pydantic 结构化输出模型
│   ├── llm_client.py      # LLM 客户端（缓存+重试+结构化输出）
│   ├── executor.py        # HTTP API 执行器
│   └── main.py            # 命令行交互入口
├── test/
│   └── test_create_box.py # 直接调用 HTTP API 的测试
├── requirements.txt       # Python 依赖
├── venv/                  # Python 虚拟环境
└── README.md              # 本文件
```

## 快速开始

### 1. 激活虚拟环境并安装依赖

```bash
# Windows
C:\codes\sw\SolidWorksTutorial\LLMPlanner\venv\Scripts\activate.bat

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
# 设置 OpenAI API Key（必须）
export OPENAI_API_KEY=your_api_key_here

# 设置自定义 API 地址（可选，用于兼容第三方 API 如 DeepSeek 等）
export OPENAI_BASE_URL=https://api.openai.com/v1
```

Windows CMD:
```cmd
set OPENAI_API_KEY=your_api_key_here
set OPENAI_BASE_URL=https://api.openai.com/v1
```

### 3. 确保 SolidWorks C# 服务已启动

```bash
# 在另一个终端启动 C# HTTP API 服务
.\Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe
```

### 4. 运行 LLM Planner

```bash
# 从 LLMPlanner 目录运行
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

# 交互模式
python -m planner.main

# 单次执行
python -m planner.main "创建一个 100mm x 50mm x 30mm 的长方体"

# 模拟执行（不调用 SolidWorks，仅查看计划）
python -m planner.main --dry-run "画一个半径 20mm 的圆柱"

# 跳过确认直接执行
python -m planner.main -y "创建一个正方体，边长 5cm"

# 指定模型
python -m planner.main --model gpt-4o "创建一个复杂零件"

# 禁用缓存
python -m planner.main --no-cache "创建一个长方体"
```

## 交互模式命令

| 命令 | 说明 |
|---|---|
| 任意文字 | 作为建模指令发送给 LLM |
| `quit` / `exit` / `q` | 退出程序 |
| `clear-cache` | 清空 LLM 缓存 |
| `dry-run` | 切换模拟执行模式 |

## 缓存机制

- 使用 `diskcache` 库进行磁盘缓存
- 缓存文件存放在 `planner/.llm_cache/` 目录
- 对 (模型名, system_prompt, 用户指令) 进行 SHA256 哈希作为缓存 key
- 相同指令不会重复调用 API，节省费用
- Multi-turn 额外缓存 Vision IR、初始全局蓝图，以及由语义 observation、
  里程碑账本、轮次和 Skill Prompt 确定的 Agent 决策。Agent 操作执行成功后
  才提交缓存，失败会删除对应条目。重复测例仍会重新执行 SolidWorks 操作和
  机器验收，只跳过状态语义等价时的 LLM API 调用。
- 可通过 `--no-cache` 禁用，或在交互模式中输入 `clear-cache` 清空

## 模型选择

默认使用 `gpt-4o-mini`，性价比高且支持结构化输出。可通过 `--model` 参数切换：

- `gpt-4o-mini` — 默认，快速且便宜
- `gpt-4o` — 更强的推理能力，适合复杂建模
- 其他兼容 OpenAI 接口的模型（通过 OPENAI_BASE_URL 配置）

## 退出虚拟环境

```bash
deactivate
```

## Web Demo（可视化界面）

除命令行外，还提供了一个 ChatGPT 风格的 Web 界面用于演示。

### 安装额外依赖

```bash
pip install flask flask-cors
```

### 启动

```bash
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

# 启动 Web Demo（默认端口 5000）
python -m web.app

# 指定端口
python -m web.app --port 8080

# 指定模型
python -m web.app --model gpt-4o
```

打开浏览器访问 `http://localhost:5000`，在输入框输入自然语言建模指令即可。

### Single-turn / Multi-turn

- `single_turn` 是缺省模式，保持原有协议：LLM 一次生成完整计划后顺序执行；不加载 Agent Skill，不改变 cache key / 请求 / 响应结构。
- `multi_turn` 采用“全局计划 + 迭代执行”架构：先生成与 Single-turn 同等完整的全局蓝图，再按选择/草图/特征原子事务拆成里程碑；每轮执行后观察并做确定性验收，失败时保留现有模型并由 Agent 原位修改，全部通过才返回 `completed`。
- 连续 `sketch_line` / `sketch_circle` 会自动压缩成 `sketch_polyline` / `sketch_circles`，避免 5-action 轮次边界留下未闭合草图。
- 观察源优先使用聚合 `GET /api/model-observation`（面拓扑、草图摘要、特征-草图关联）；失败时回退到 feature-tree / bbox / volume。
- 纯文本装配任务支持新建/打开装配体、精确插入组件、固定/浮动、重合/同心/
  齿轮配合、重建、保存和干涉检查。`GET /api/assembly/observation` 返回组件路径、
  Transform、固定/压制状态、Mate 类型和齿轮比，供里程碑确定性验收。
- `assembly_mate_gear` 无需 LLM 提供射线；C# 根据组件 Transform 自动选择中心孔
  圆柱面，避免复杂齿面上的射线误选和慢速遍历。
- 装配测试完成后使用 `/api/close-inactive-documents` 保留最终装配体并关闭预加载
  零件窗口；用例之间仍使用 `/api/close-all-documents` 清场。
- Web Demo“装配体相关”区域提供“Multi-turn 构建双级减速箱”一键入口：首次运行
  生成并缓存决策，重复运行按完整状态命中缓存，同时重新执行装配和最终验收。
- 已生成的双级减速箱可从四个齿轮中选择任意一个作为 Motion Study 马达输入，
  再播放或停止 5 圈联动；这条演示路径只调用确定性的 SolidWorks API，不调用 LLM。
- 通用机构操作 `assembly_set_crank_slider_pose` 根据曲柄半径、连杆长度、滑动轴
  和旋转轴解析设置曲柄/连杆/滑块姿态，并可同步飞轮、正时齿轮、轴和凸轮。
  Web Demo 的单缸正时发动机测例使用 10 个组件和 2:1 正时传动，并在完整周期
  采样验证 50mm 行程；曲柄角滑块是纯 SolidWorks API 的确定性演示。
- 通用 `assembly_set_indexed_rotary_pose` 将连续输入转换为啮合区内定角推进、
  其余区间停歇锁止的旋转输出，并支持连续/分度两组同步件。Web Demo 的 15 组件
  16mm 放映机用四槽机构完成间歇送片，0°–1440°拨盘控制不调用 LLM。
- 轮数缺省按蓝图阶段和每阶段恢复预算动态计算；请求里传 `max_turns` 时作为硬上限。
- Web Demo 时间线展示里程碑状态、机器验收证据和失败后换策略；Single-turn UI 不变。
- “复杂单体零件模式对比”提供同一份一体式手机支架规格的 Single-turn 与
  Multi-turn 直接运行按钮；配套 eval 独立给出 0–100 完成度，不要求两种模式
  都完整成功。
- Multi-turn 支持纯文本；带三视图图片时先走 Vision 抽 IR，再生成全局蓝图并进入同一套执行/检查/修复循环。暂不支持 SSE 实时流、持久会话、截图和回滚。
- SolidWorks 是单实例执行器，Web 后端会串行执行并发建模请求。

`/api/run` 不传 `mode` 时响应结构与原 single-turn 完全一致。启用 Multi-turn：

```json
{
  "instruction": "创建一个 20mm 立方体并对所有边做 R2mm 圆角",
  "mode": "multi_turn"
}
```

响应包含 `multi_turn.turns`（每轮 Observe / Action / Result / Check）、
`multi_turn.milestones`（账本与验收证据）、`final_observation` 以及 LLM 调用数和操作统计。

### Web Demo 文件结构

```
LLMPlanner/
└── web/
    ├── app.py              # Flask 后端（复用 planner 模块）
    └── static/
        ├── index.html      # 前端页面
        ├── style.css       # 样式
        └── app.js          # 交互逻辑
```

## Skill 动态加载架构（Prompt 优雅化）

**问题**：过去 `SYSTEM_PROMPT` 直接硬编码在 `llm_client.py` 里（约 400 行）。每加一类物体（骰子/板凳/钥匙…）就往里塞一段"⭐ 完整示例"，改完还要清 diskcache——臃肿且难维护。

**解决**：Prompt 拆分为**文件驱动的 Skill 体系**，用于维护可迁移的建模知识、输入模态规则和 Agent 行为，而不是为每个物件保存标准答案。

### Generalization-first 原则

- 提升自动建模能力时，禁止把评测 case 的 ground truth、完整操作答案或只针对某个样例的步骤直接写进 Prompt / Skill / 路由代码。
- 不让 LLM 按物体名称死记 recipe；优先增强通用几何推理、拓扑约束、操作语义、观察、机器验收、编辑和失败恢复能力。
- 示例只能讲解可复用技术，不能复现待评测样例的答案；改动必须同时在失败样例、未见样例和回归集上验证迁移效果。
- 评测器可以按 case 设计输入、ground truth、期望尺寸/体积/拓扑、操作检查和验收标准，以便精确定位能力缺口。
- 运行时建模代码不得根据 sample ID、图片哈希、benchmark 名称或 fixture 专属信息改变策略；评测答案只能用于打分和诊断，不能作为隐藏提示回灌给 LLM 或 planner。
- 如果现有原子操作难以稳定表达某类几何，应优先改进通用 API / 高层操作，而不是向 Prompt 填入完成模型的固定步骤。

### 目录结构

```
planner/skills/
├── core/                         # 恒定注入：所有对话都会用到的通用规则
│   ├── 00_header.md              # 系统开场白
│   ├── 01_operations.md          # 零件原子 operation 及其参数
│   ├── 02_units.md               # 单位换算（mm↔m）
│   ├── 03_rules.md               # 拉伸方向/坐标映射/多轮廓约束等通用规则
│   └── 04_editing.md             # 编辑已有模型（modify_dimension / 面上加特征）
├── agent/
│   └── 05_agent_loop.md          # 仅 Multi-turn 动态追加，不改变 single-turn prompt
├── recipes/                      # 可复用能力模式，按能力关键词命中（不得存放物体答案）
└── input_modes/                  # 按输入模态命中注入
    └── three_view.md             # 三视图建模规则（视图↔基准面/枚举尺寸/L 形正确画法等）
```

### Skill 文件格式（带 YAML frontmatter 的 md）

```markdown
---
name: cavity_by_cut
description: 无 Shell API 时用盲切形成参数化内腔
triggers: [内腔, 壁厚, 顶部敞开, cavity]
applies_to: [text, three_view]
---

# 正文只描述可迁移能力，例如连续闭合轮廓、面上切除或失败恢复；
# 不包含某个评测零件的完整答案步骤。
```

frontmatter 极简 YAML 子集（**不引入 pyyaml 依赖**），支持 `key: value`、`key: [a, b, c]`、`key: true/false`。

### 运行时组装

`SkillRegistry` 启动时扫描 `planner/skills/**.md`；`SkillSelector` 按 triggers 关键词打分挑 top-K；`build_system_prompt` 拼装 `core 全量 + 命中的 recipes/input_modes`。

`SwLLMClient` 支持两种模式：

- `skill_mode="routed"`（默认）：core 全量 + 仅命中当前输入模态和能力的 Skill。
- `skill_mode="full"`：仅用于兼容性测试，注入全部 recipes/input_modes，不用于生产建模。

**缓存自动失效**：`_make_cache_key(model, system_prompt, user_prompt, image?)` 依赖组装后的 prompt 内容；skill 变了缓存 key 自动变，无需手动 `clear-cache`。

### 新增建模知识的流程

1. 先把失败归类为通用能力缺口，例如轮廓表达、坐标映射、选面、尺寸修改或恢复策略。
2. 优先修改通用 operation、观察器、验收器或 Agent 控制循环；只有确属 Prompt 知识缺口时才增加 Skill。
3. Skill 以能力命名并描述抽象方法，不以 benchmark 样例命名，不写入其完整答案。
4. 使用至少一个未参与设计的 held-out case 和现有回归集验证，再记录整体通过率与代价。

## 多模态：从三视图图纸建模（第一期）

支持上传**带尺寸标注的三视图 PNG**，自动识别几何 + 生成 SolidWorks 建模计划。

### 两阶段流水线

```
用户上传三视图 PNG + 可选文字说明
    ↓
阶段 A（Vision）: 图片 + core + input_modes/three_view → ThreeViewIR (结构化 IR)
    ↓
阶段 B（Text）  : IR + core + input_modes/three_view → SwModelingPlan
    ↓
Executor 执行 → SolidWorks 建出零件
```

**中间表示（`schemas.py::ThreeViewIR`）**：

- `part_type`：零件顶层类型（l_bracket / extrude_prism / flange / revolve …）
- `base_view`：主视图对应基准面（front→前视 0 / top→上视 1 / right→右视 2）
- `overall_size_mm`：总体包围盒尺寸
- `profiles[]`：各视图轮廓的中文描述
- `dimensions[]`：**逐条枚举图纸上所有可读到的尺寸标注**（阶段 A 的核心产出）
- `features[]`：孔/圆角/倒角/槽/凹坑等附加特征
- `assumptions`：缺失尺寸的合理假设说明

### 使用方式

**Web 界面**（推荐）：
1. 打开 `http://localhost:5000`
2. 点击底部 **📎** 按钮上传三视图图片，或直接**拖入**图片文件
3. 输入框输入文字说明（可选），点发送
4. AI 会输出计划卡片 + 执行日志 + 完成摘要

也可点击欢迎区的 **📐 三视图建模 (L 型支架)** 示例按钮，一键加载 B01 样例。

**Python API**：

```python
import base64
from planner.llm_client import SwLLMClient

img_b64 = base64.b64encode(open("drawing.png", "rb").read()).decode()

client = SwLLMClient(use_cache=True, skill_mode="routed")
plan = client.plan("这是一张 L 型支架的三视图……", image_base64=img_b64)
# plan 是 SwModelingPlan，走标准 executor.execute_plan(plan) 建模
```

### 端到端验证

`test/test_three_view_e2e.py` 演示了完整流程（B01 L 型支架三视图 PNG → 建模 → 包围盒/体积验证）。实测 9/9 步通过，包围盒 60×40×20mm、体积 36000 mm³ 与理论完全吻合。

### 测例数据集金字塔（规划中）

- **Tier S**：TriView2CAD（20 万 PNG+DXF+STEP，含尺寸标注，MIT）——公开数据集主力 benchmark
- **Tier A**：CAD-VGDrawing（15 万 SVG，MIT）——SVG 直读路径评测
- **Tier B**：手工标注三视图（`datasets/manual_three_view/`）——中文场景 demo 与 Vision 能力验证
- **Tier C**：合成三视图（脚本生成，CI 快跑）

详见 `docs/multimodal-plan.md`。

## 通用多关节装配能力

除齿轮、曲柄滑块和间歇分度机构外，系统支持
`assembly_set_planar_chain_pose`：按相对关节角解析求解任意刚性平面串联链，
并可让多根平行杆共享同一段运动。求解结果返回全部关节点、每段中点/末点和
末端执行件姿态，可用于机械臂、折叠支架、双节台灯等装配体的机器验收。

Web Demo“装配体相关”包含 18 组件双节可调台灯。Multi-turn 首次完成装配后，
高度滑块可在低位、工作和升高姿态之间确定性插值；滑块只调用 SolidWorks API，
不产生 LLM token。对应评测和规格位于：

- `eval/eval_adjustable_desk_lamp_multiturn.py`
- `eval/adjustable_desk_lamp_spec.json`
- `test/test_adjustable_desk_lamp.py`

系统还支持 `assembly_set_radial_deployable_pose`：用中心滑套、主肋连接距离和
撑杆长度解析求解任意数量的等间隔径向连杆，可用于折叠伞、可展开天线和径向
支架。API 返回每个方位的铰点、末端、实际实体方向及端点误差。

Web Demo 中的 22 组件八骨折叠伞使用该能力，并提供 0%–100% 确定性开合滑块。
对应规格、评分和回归位于：

- `eval/eval_radial_folding_umbrella_multiturn.py`
- `eval/radial_folding_umbrella_spec.json`
- `test/test_radial_folding_umbrella.py`

面向同轴传动和夹紧机构，系统新增：

- `assembly_set_planetary_train_pose`：解析求解任意级固定齿圈行星轮系，包含
  太阳轮输入、行星架输出、行星轮自转/公转和同步输出组件；
- `assembly_set_radial_sliders_pose`：同步控制多爪夹头、径向夹具和同心滑块，
  返回每个滑块的真实方向、径向距离和内端半径。

Web Demo 中的 27 组件充电式手电钻组合了 20:1 双级行星减速器与三爪自定心
夹头，并提供电机角和夹头开度控制。对应文件：

- `eval/eval_cordless_drill_planetary_multiturn.py`
- `eval/cordless_drill_planetary_spec.json`
- `test/test_cordless_drill_planetary.py`

面向更多闭环与相位耦合机构，系统进一步提供：

- `assembly_set_synchronized_rotary_pose`：由共同输入角驱动任意数量、任意比例、
  方向和相位的旋转组件；
- `assembly_set_four_bar_pose`：解析求解开式或交叉式平面四连杆闭环，并返回
  关节、杆角、闭合误差和任意耦合点位置；
- `assembly_set_cam_follower_pose`：按谐波或偏心轮廓求解旋转凸轮和直动从动件
  的升程与实体姿态。

Web Demo 中的 40 组件家用机械缝纫机组合针杆曲柄滑块、旋梭同步转动、送布
四连杆和压脚凸轮，并提供 0°–720° 主轴确定性姿态控制。产品尺寸和验收轨迹
只保留在 eval 层，对应文件：

- `eval/eval_household_sewing_machine_multiturn.py`
- `eval/household_sewing_machine_spec.json`
- `test/test_household_sewing_machine.py`

## 回归测试

```bash
# 结构化单元测试（不调 API，秒级完成）
venv\Scripts\python.exe -X utf8 test\test_skill_regression.py

# 端到端多模态测试（需 SolidWorks + API Key）
venv\Scripts\python.exe -X utf8 test\test_three_view_e2e.py
```

## TODO List
- [ ] 查看 json mode 是否报错
- [ ] 按 held-out 失败类型补充参数化能力 Skill；禁止按物体名或 benchmark case 添加 recipe
- [ ] 引入 TriView2CAD / CAD-VGDrawing 数据集脚本，跑 Tier S/A benchmark


## demo 示例
+ 创建一个凸台，底为长 100 mm，宽 50 mm 的长方体，向上拉伸 30 mm
+ 将刚才的凸台再向上拉伸 50 mm
+ **[多模态]** 上传 B01 L 型支架三视图 PNG，AI 自动识别并建出 60×40×20mm 的 L 形零件
