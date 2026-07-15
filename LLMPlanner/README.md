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

- `single_turn` 是缺省模式，保持原有协议：LLM 一次生成完整计划后顺序执行。
- `multi_turn` 每轮最多执行 5 个操作，随后读取特征树、包围盒和体积，再决定继续、
  调整、完成或阻塞；缺省最多 8 轮。
- Web Demo 的设置面板可以切换模式并调整最大轮数。
- Multi-turn MVP 目前仅支持纯文本，不支持图片、SSE 实时流、持久会话、截图和回滚。
- SolidWorks 是单实例执行器，Web 后端会串行执行并发建模请求。

`/api/run` 不传 `mode` 时响应结构与原 single-turn 完全一致。启用 Multi-turn：

```json
{
  "instruction": "创建一个 20mm 立方体并对所有边做 R2mm 圆角",
  "mode": "multi_turn",
  "max_turns": 8
}
```

响应包含 `multi_turn.turns`（每轮 Observe / Action / Result / Check）、
`final_observation` 以及 LLM 调用数和操作统计。

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

**解决**：Prompt 拆分为**文件驱动的 Skill 体系**，加物件 = 新增一个 Markdown 文件，零 Python 改动。

### 目录结构

```
planner/skills/
├── core/                         # 恒定注入：所有对话都会用到的通用规则
│   ├── 00_header.md              # 系统开场白
│   ├── 01_operations.md          # 21 个原子 operation 及其参数
│   ├── 02_units.md               # 单位换算（mm↔m）
│   ├── 03_rules.md               # 拉伸方向/坐标映射/多轮廓约束等核心规则 + 板凳/骰子等示例
│   └── 04_editing.md             # 编辑已有模型（modify_dimension / 面上加特征）
├── agent/
│   └── 05_agent_loop.md          # 仅 Multi-turn 动态追加，不改变 single-turn prompt
├── recipes/                      # 按用户指令关键词命中注入（当前示例都在 03_rules.md 内嵌）
└── input_modes/                  # 按输入模态命中注入
    └── three_view.md             # 三视图建模规则（视图↔基准面/枚举尺寸/L 形正确画法等）
```

### Skill 文件格式（带 YAML frontmatter 的 md）

```markdown
---
name: stool
description: 四脚板凳/桌子类 —— 凳面拉伸 + 四腿 + 圆角
triggers: [板凳, 凳子, 桌子, stool]
applies_to: [text, three_view]
---

# 正文（会作为 System Prompt 的一部分喂给 LLM）
...
```

frontmatter 极简 YAML 子集（**不引入 pyyaml 依赖**），支持 `key: value`、`key: [a, b, c]`、`key: true/false`。

### 运行时组装

`SkillRegistry` 启动时扫描 `planner/skills/**.md`；`SkillSelector` 按 triggers 关键词打分挑 top-K；`build_system_prompt` 拼装 `core 全量 + 命中的 recipes/input_modes`。

`SwLLMClient` 支持两种模式：

- `skill_mode="full"`（默认）：注入全部 core + 全部 recipes/input_modes。**与迁移前的硬编码 SYSTEM_PROMPT 字节级等价**，保证零回归。
- `skill_mode="routed"`：core 全量 + 仅命中 triggers 的 recipes/input_modes。省 token，多模态阶段 B 默认走此模式。

**缓存自动失效**：`_make_cache_key(model, system_prompt, user_prompt, image?)` 依赖组装后的 prompt 内容；skill 变了缓存 key 自动变，无需手动 `clear-cache`。

### 新增建模知识的流程

1. 在 `planner/skills/recipes/` 下新建 `my_part.md`
2. 写好 frontmatter（关键：`triggers` 关键词、`applies_to`）
3. 正文写建模步骤示例
4. 完了。下一次 LLM 请求命中相关关键词时会自动注入，无需改任何 Python 代码。

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

## 回归测试

```bash
# 结构化单元测试（不调 API，秒级完成）
venv\Scripts\python.exe -X utf8 test\test_skill_regression.py

# 端到端多模态测试（需 SolidWorks + API Key）
venv\Scripts\python.exe -X utf8 test\test_three_view_e2e.py
```

## TODO List
- [ ] 查看 json mode 是否报错
- [ ] 从 core/03_rules.md 拆出独立的 stool/dice/hex_nut/mug/wrench/vase/key/gear recipes，让 routed 模式真正省 token
- [ ] 引入 TriView2CAD / CAD-VGDrawing 数据集脚本，跑 Tier S/A benchmark


## demo 示例
+ 创建一个凸台，底为长 100 mm，宽 50 mm 的长方体，向上拉伸 30 mm
+ 将刚才的凸台再向上拉伸 50 mm
+ **[多模态]** 上传 B01 L 型支架三视图 PNG，AI 自动识别并建出 60×40×20mm 的 L 形零件
