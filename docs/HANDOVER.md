# SolidWorksTutorial 项目交接文档

> **交接时间**：2026-07-14
> **交接前开发环境**：CodeBuddy IDE + Claude-Sonnet-5（Anthropic 家族）
> **交接后开发环境**：Cursor + GPT-5.6 Sonnet
> **仓库**：https://github.com/fansunqi/SolidWorksTutorial （分支 `main`）
> **本地路径**：`c:\codes\sw\SolidWorksTutorial`

---

## 0. 一句话总结

**用 LLM（GPT/Claude）把自然语言或三视图图纸自动翻译成 SolidWorks 建模步骤，通过 C# HTTP 服务远程驱动 SolidWorks 完成建模；已支持零件、装配体、Gear Mate 联动以及 Web 控制的 Motion Study 齿轮动画。**

---

## 1. 项目目标

### 1.1 核心愿景
把 SolidWorks 从"专业 CAD 工程师才能用"变成"自然语言描述或上传三视图图纸就能自动建模"的 AI 助手工具。三个用户角色：
1. **产品经理**：用自然语言"我要一个 20mm 的骰子" → 得到 .SLDPRT 文件
2. **画图工程师**：上传"三视图带尺寸标注的图纸" → 自动建出对应零件
3. **教学场景**：Web demo 一键演示"AI 自动建模"效果，用于团队/学生 demo

### 1.2 当前已支持的输入
- ✅ **自然语言（文字建模）**：骰子、板凳、齿轮、六角螺母、钥匙、马克杯、花瓶、扳手、储物盒 等
- ✅ **三视图图纸（多模态建模）**：12 个手工数据集样例 B01~C3（L 支架、平板、T 支架、门锁扣板、开关面板、键盘键帽、齿轮盘、圆碗、机械键盘、齿轮减速器外壳、齿轮啮合组等）
- ⏳ **手绘草图 / 效果图**：架构已预留 `skills/input_modes/` 扩展位，未实现

### 1.3 当前已支持的输出
- ✅ **零件建模（.SLDPRT）**：单实体 + 多实体（同一 sldprt 里多个不相连实体）
- ✅ **装配体（.SLDASM）**：多个 .SLDPRT 组合，含 Coincident/Concentric/**Gear Mate（齿轮联动）** 配合
- ✅ **动画驱动**：Motion Study 旋转马达可驱动小齿轮，并通过 Gear Mate 带动大齿轮按 2:1 联动；Web demo 支持加载、播放和停止

---

## 2. 系统架构（高层）

```
┌──────────────────────────────────────────────────────────────────┐
│                   用户输入（自然语言 OR 三视图 PNG）                    │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  LLMPlanner (Python)                                              │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ SwLLMClient (planner/llm_client.py)                       │  │
│  │  ├─ 文字建模: plan(user_text) → SwModelingPlan            │  │
│  │  └─ 多模态: 阶段 A (Vision→IR) + 阶段 B (IR→Plan)          │  │
│  │  ├─ Skill 动态加载 (skill_registry.py, routed/full 模式)  │  │
│  │  └─ 端到端 cache (跳过 Vision+Plan, image_hash 稳定命中)   │  │
│  └────────────────────────────────────────────────────────────┘  │
│                              │                                     │
│                              ▼ SwModelingPlan (JSON)              │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ SwPlanExecutor (planner/executor.py)                       │  │
│  │  逐条 op 转 HTTP POST 请求 → localhost:5100/api/...        │  │
│  └────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬──────────────────────────────────┘
                                │ HTTP JSON
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  Chapter1.ConnectToSw (C# .NET Framework 4.7.2, EXE, HTTP 服务)   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Program.cs (HttpListener 监听 5100)                        │  │
│  │  路由分发 → 40+ 个 handler                                  │  │
│  └────────────────────────────────────────────────────────────┘  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ SwCommands.cs (SolidWorks COM 封装, ~2900 行)              │  │
│  │  零件建模: NewPart / SelectPlane / SketchXxx /              │  │
│  │            FeatureExtrusion3 / FeatureCut4 / Fillet / ...  │  │
│  │  装配体:   NewAssembly / AddComponent / MateGear / ...      │  │
│  └────────────────────────────────────────────────────────────┘  │
└───────────────────────────────┬──────────────────────────────────┘
                                │ COM Interop
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│  SolidWorks 2025 中文版 (已启动的桌面进程)                          │
└──────────────────────────────────────────────────────────────────┘
```

**关键设计约束**：
- **一切通过 HTTP**：Python 和 C# 完全解耦，可独立测试和演化
- **C# 服务需在 SolidWorks 已启动时运行**（COM 需要连接现有进程）
- **单位统一为米**（SolidWorks COM 规定，10mm = 0.01）
- **权限必须一致**：SolidWorks 和 C# 服务必须**同权限**（都普通 or 都管理员），否则 COM 拒绝连接

---

## 3. 目录结构

```
c:\codes\sw\SolidWorksTutorial\
├── CODEBUDDY.md                 # 🌟 项目最重要的上下文文档（AI 会话开头必读）
├── TEST.md                      # 🌟 测试清单 + Definition of Done 强约束
├── README.md                    # 用户文档（较简单）
├── SolidWorksTutorial.slnx      # 新版 slnx 解决方案文件（不是 sln）
├── build_service.bat            # 构建 C# 服务（含 msbuild 路径特殊处理）
├── run_service.bat              # 启动 C# 服务
├── .gitignore                   # 默认忽略一切，白名单跟踪
│
├── Chapter1.ConnectToSw/        # C# 服务项目 (.NET Framework 4.7.2)
│   ├── Program.cs               # HTTP 服务入口 + 40+ handlers (~1400 行)
│   ├── SwCommands.cs            # SolidWorks COM 封装核心 (~2900 行)
│   ├── TutorialExamples.cs      # 早期学习示例（较少用）
│   ├── Chapter1.ConnectToSw.csproj  # 传统 csproj (非 SDK 风格) + packages.config
│   ├── packages.config          # NuGet 依赖清单
│   ├── App.config
│   └── resources/               # 运行时资源（构建时自动复制到 bin/Debug/）
│       └── empty_assembly_template.SLDASM   # 装配体空模板 (57KB, 用户手工建的)
│
├── LLMPlanner/                  # Python LLM 规划器
│   ├── requirements.txt         # openai / pydantic / diskcache / flask 等
│   ├── venv/                    # Python 虚拟环境（不入库）
│   │
│   ├── planner/                 # 核心规划逻辑
│   │   ├── llm_client.py        # 🌟 SwLLMClient：调 LLM 生成 plan
│   │   ├── executor.py          # 逐 op 转 HTTP 请求
│   │   ├── schemas.py           # SwModelingPlan Pydantic 定义
│   │   ├── skill_registry.py    # Skill 动态加载（routed / full 模式）
│   │   ├── main.py              # 命令行入口
│   │   └── skills/              # Skill 系统（Markdown 驱动）
│   │       ├── core/            # 核心规则（每次都加载）
│   │       │   ├── 00_role.md
│   │       │   ├── 01_operations.md  # 🌟 所有 op 的详细规则（含齿轮/板凳 示例）
│   │       │   └── ...
│   │       ├── recipes/         # 特定物体的建模菜谱（按需加载）
│   │       │   ├── dice.md
│   │       │   ├── stool.md
│   │       │   └── ...
│   │       └── input_modes/     # 输入模式特有指引
│   │           ├── text.md
│   │           └── three_view.md   # 🌟 三视图建模的阶段 A/B 详细规则
│   │
│   ├── test/                    # 端到端测试
│   │   ├── objects/             # 文字建模测试（每物体一个测试文件）
│   │   │   ├── test_dice_llm.py
│   │   │   ├── test_gear_llm.py
│   │   │   ├── test_stool.py    # 板凳（LLM 版）
│   │   │   ├── test_vase_llm.py
│   │   │   ├── test_hex_nut_llm.py
│   │   │   ├── test_key_llm.py
│   │   │   ├── test_mug_llm.py
│   │   │   ├── test_wrench_llm.py
│   │   │   ├── test_storage_box.py
│   │   │   ├── _common.py       # 测试公共工具
│   │   │   └── _TEMPLATE_new_object.py   # 🌟 新增物体测试的参考模板
│   │   ├── test_three_view_e2e.py  # 🌟 三视图端到端测试
│   │   ├── test_skill_regression.py # Skill 回归测试
│   │   └── test_llm_api.py
│   │
│   ├── datasets/
│   │   └── manual_three_view/
│   │       ├── samples.json     # 🌟🌟🌟 三视图样例单一数据源！
│   │       │                    # 12 个样例 (B01~C3)，改这一个 JSON 三处自动同步
│   │       ├── build_all.py     # 从 samples.json 生成 drawing.png + gt.json
│   │       ├── B01_l_bracket/
│   │       │   ├── drawing.png     # 三视图 PNG
│   │       │   └── groundtruth.json  # 期望的包围盒/体积
│   │       └── ... (B02~C3 同结构)
│   │
│   ├── web/                     # Flask Web demo
│   │   ├── app.py               # 端点：/api/plan-and-execute、/api/three-view-samples
│   │   └── static/
│   │       ├── index.html       # 欢迎页 + 示例按钮 + 输入区
│   │       └── app.js           # 前端逻辑（PNG base64 处理、cache 命中日志）
│   │
│   ├── assembly_smoke/          # 🌟 装配体 API 冒烟测试目录
│   │   ├── README.md            # 🌟 装配体 API 详细文档 + 已知问题排查过程
│   │   ├── phase1_build_two_gears.py  # 用 API 建齿轮零件
│   │   ├── phase3_via_csharp_api.py   # 端到端搭齿轮啮合装配体
│   │   ├── gear_small_involute_bored.SLDPRT # 渐开线小齿轮（10 齿）
│   │   ├── gear_big_involute_bored.SLDPRT   # 渐开线大齿轮（20 齿，相位 +9°）
│   │   └── gear_pair_assembly_v4.SLDASM  # 渐开线啮合装配体（外啮合 -2:1）
│   │
│   └── log/                     # 测试日志（不入库）
│
├── docs/                        # 项目文档
│   ├── HANDOVER.md              # 本文档
│   ├── multimodal-plan.md       # 三视图 + Skill 架构规划
│   └── SolidWorks-Copilot-功能扩展规划.md
│
└── packages/                    # NuGet 缓存（含 Xarial.XCad 三个 dll）
```

---

## 4. 关键代码入口点（按开发场景）

| 你想改什么 | 改哪 |
|---|---|
| 新增一个 SolidWorks 原子操作（如"倒角"）| `SwCommands.cs` 加方法 + `Program.cs` 加 case+handler + `schemas.py` 加 op 枚举 |
| 新增一个自然语言样例（如"茶壶"）| `test/objects/test_teapot_llm.py`（参考 `_TEMPLATE_new_object.py`）+ 更新 `TEST.md` + 加 web demo 按钮 |
| 新增一个三视图样例 | **只改** `datasets/manual_three_view/samples.json`（三处自动同步），跑 `build_all.py`，然后跑 `test_three_view_e2e.py <part_id>` |
| 调整 LLM 的建模规则 | 改 `planner/skills/core/*.md` 或 `recipes/*.md` **无需改 Python 代码** |
| Web demo UI 改动 | `web/static/index.html` + `app.js`（前端）；`web/app.py`（后端）|
| 装配体新功能 | `SwCommands.cs` 装配体块（第 2359 行开始，`#region 装配体扩展`）|

**新增建模物体的 Definition of Done（强约束，写在 CODEBUDDY.md 和 TEST.md）**：

**文字建模**（自然语言 → CAD）：
1. 写 `test/objects/test_<name>_llm.py`（含几何断言）
2. 执行 + 日志留在 `log/`
3. 更新 `TEST.md` 表格 + 完整 Prompt
4. 加到 Web demo（`index.html` 按钮 + `app.js` fill 函数）
5. `git commit + push`

**三视图样例**（图纸 → CAD）：
1. 追加 `datasets/manual_three_view/samples.json` 一行（**只改这一处**）
2. 跑 `datasets\manual_three_view\build_all.py` 生成 PNG + gt
3. 清缓存 + 跑 `test\test_three_view_e2e.py <part_id>`
4. 更新 `TEST.md` §12.2 + §12.3
5. `git commit + push`（Web demo 按钮由 samples.json 自动驱动）

---

## 5. 构建与运行

### 5.1 前置条件（必须都满足）
- **SolidWorks 2025 中文版**已安装且**已启动**（有 SLDWORKS.exe 进程在跑）
- **SolidWorks 权限 = 命令行权限**（两者必须一致，见 §6.1）
- **MSBuild** 存在于 `C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe`（注意 VS 版本目录是 `18` 不是 `2022`）
- **Python 3.10+** + venv（`LLMPlanner\venv\`）
- **NuGet 包已还原**（`packages/Xarial.XCad.SolidWorks.0.8.3` 等目录存在）

### 5.2 首次运行流程

```powershell
# 1. 构建 C# 服务
cd c:\codes\sw\SolidWorksTutorial
.\build_service.bat        # 输出: bin/Debug/Chapter1.ConnectToSw.exe

# 2. 启动 SolidWorks 桌面软件（必须！）

# 3. 启动 C# HTTP 服务（在 SolidWorks 已启动后）
.\run_service.bat          # 监听 http://localhost:5100/

# 4. 验证
curl.exe http://localhost:5100/api/ping
# 期望: {"success":true,"message":"SolidWorks API 服务运行中"}

# 5. 激活 Python 环境 + 设 API key
cd LLMPlanner
venv\Scripts\activate
set OPENAI_API_KEY=<your-key>
set OPENAI_BASE_URL=https://api.juheai.top/v1
set PLANNER_MODEL=basic/claude-sonnet-5
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

# 6. 跑一个文字建模测试（会真调 LLM）
python -X utf8 test\objects\test_dice_llm.py

# 7. 或跑三视图测试
python -X utf8 test\test_three_view_e2e.py B01

# 8. 或启动 Web demo
python -X utf8 -m web.app --port 5000
# 浏览器打开 http://localhost:5000
```

### 5.3 API Keys 说明
- 用 **juheai.top 中转站**（`OPENAI_BASE_URL=https://api.juheai.top/v1`）
- 主力模型：`basic/claude-sonnet-5`（GPT-5.6 也可试）
- **cache 层已实现，同图同 prompt 秒回零花费**（见 §7）

---

## 6. 已经踩过的重要坑（一定要知道！）

### 6.1 【必读】权限一致性
**症状**：C# 服务连不上 SolidWorks，报 `Cannot access SOLIDWORKS application at process <PID>`

**根因**：Windows COM 跨进程访问要求两端**完整性级别一致**。若 SolidWorks 以管理员权限运行而命令行是普通权限，COM 拒绝。

**排查**：
```powershell
# 看是否 RUNASADMIN 兼容性标志导致 SolidWorks 强制以管理员启动
Get-ItemProperty 'HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers' -ErrorAction SilentlyContinue
```
若值含 `RUNASADMIN` → 用 `Remove-ItemProperty` 删掉再重新打开 SolidWorks。

**关键原则**：AI 无法自行提权（需交互式 UAC），也不应接收用户 PIN；两端必须同权限（都普通 or 都管理员）。

### 6.2 【必读】LLM 相关的 5 个坑

1. **`temperature=0` 必须显式设**（否则 Sonnet 5 长 plan 会截断/畸形）
2. **system prompt 要防"依次类推"**（会被 pydantic 校验拒绝，触发重试）
3. **对称件用 `midPlane=true`**（let 拉伸中心在 depth/2，坐标最不易错）
4. **切凹坑/孔用 `flipDirection=false`**（True 会切掉整个面外侧环形区域）
5. **矩形中心+宽高转对角线易算错**（Sonnet 5 会写 `x2 = cx + w` 而非 `x2 = cx + w/2`，已在 skill 加正例反例约束）

### 6.3 【必读】SolidWorks COM API 陷阱

1. **FeatureChamfer 用 3 参版 `IModelDoc2.FeatureChamfer(Width, Angle, Flip)`，不要用 8 参 `InsertFeatureChamfer`**（后者已知不稳定）
2. **CutExtrusion 退出草图必须 `IModelDoc2.InsertSketch2(true)`**，不能用 `SketchManager.InsertSketch(true)`（后者会导致特征失败）
3. **FeatureCut4 参数 `NormalCut=false, UseFeatScope=true, UseAutoSelect=true`**（NormalCut=true 是钣金专用）
4. **旋转特征绝不能用圆做轮廓**（`sketch_circle` + revolve = 甜甜圈，不是圆柱）
5. **旋转轮廓不能与旋转轴重合**（SW 会自动封闭，画了反而失败）
6. **多刀切除时锁定 `_activeSketchName`**（否则 SW 自动选择可能误指向立方体本体的 20×20 草图）
7. **射线选面加"平面优先 + 方向过滤"**（圆角后曲面多，会误命中）
8. **CircularPattern 多实体零件下的轴选择**：LLM 未传 `axisOrigin` 时，服务端自动从"源特征几何中心"选最近的圆柱面（v2 修复，见 SwCommands.cs `CircularPattern` 方法）

### 6.4 【必读】装配体 API 的独特坑

1. **SW2025 中文版没有 .asmdot 装配体模板文件**（Phase 2 pywin32 冒烟发现）
   - `GetUserPreferenceStringValue` 只返回内部占位符 `~BLANK_ASSY_TEMPLATE.asmdot`
   - 硬盘上根本没有真实 asmdot 文件
   - **解法**：项目内置 `Chapter1.ConnectToSw/resources/empty_assembly_template.SLDASM`（57KB，用户手工建的空装配体），`NewAssembly` 通过 `OpenDoc6` 打开 + `SaveAs2` 到目标路径

2. **AddComponent5 加进来的第一个组件默认 Fixed**（Transform 修改会被忽略）
   - 若要旋转/移动它，先 `UnfixComponent`

3. **SelectByID2 装配体上下文的组件成员名字格式**
   - `'前视基准面@gear_small_involute_bored-1@gear_pair_assembly_v4'`
   - 中文 SolidWorks 默认基准面名：**前视基准面 / 上视基准面 / 右视基准面**

4. **AddMate5 参数是 15 个的巨型签名**（见 SwCommands.cs 里的详细注释）

### 6.5 【必读】Cache 与 Web demo 陷阱

1. **前端样例图片不能走 canvas JPEG 压缩**
   - `canvas.toDataURL("image/jpeg", ...)` 在浏览器里**不完全确定性**（同一 PNG 每次转出的 JPEG bytes 有几字节差异）
   - 破坏 image_hash 稳定性 → cache 永远 miss → 用户每次点样例都花 API 钱
   - **解法**：`web/static/app.js` 的 `loadRawPngAsImage()` 直接 fetch PNG bytes 转 base64（不走 canvas 重编码）

2. **Web demo 与命令行 test 的 `skill_mode` 必须一致**
   - 命令行 test 用 `skill_mode="routed"`（只灌相关 skill，system_prompt 小）
   - Web demo 必须也用 `"routed"`，否则两边 cache_key 完全不同
   - 已在 `web/app.py` 的 `_get_llm_client()` 修复

3. **Vision IR 非确定性 → Plan cache 永远 miss**
   - Sonnet 5 对同一张图每次输出的 IR 结构不同（3333 vs 3111 vs 3119 字符）
   - Plan cache_key = f(IR_json) → IR 变化 → cache miss
   - **解法**：`_plan_multimodal` 增加"端到端 cache 层"，key = f(model, text_hint, image_hash)，完全不依赖 IR
   - 效果：同图同 prompt → 秒回同一个 plan，零 API 花费

### 6.6 【必读】编码 & 环境陷阱

1. **Windows 控制台默认 GBK 编码**，运行含 emoji 的 Python 会 UnicodeEncodeError
   - **必须设** `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`，用 `python -X utf8` 运行
   - 或重定向到日志文件再 `type` 读

2. **cmd 里 `set KEY=value && ...` 会把 && 前空格并进变量值**
   - 用 `set "KEY=value"`（引号包裹）

3. **重建 C# 前必须先杀掉服务且确认端口释放**
   - `Get-Process Chapter1.ConnectToSw | Stop-Process -Force`
   - 否则新实例启动失败而旧实例继续服务旧代码 → "改了没生效"假象

4. **Start-Process -RedirectStandardOutput 的相对路径是相对 PowerShell 当前目录**（不是进程 WorkingDirectory）
   - 用绝对路径避坑

---

## 7. Cache 机制（每次点样例是否花钱的关键）

**三层 cache**（`planner/.llm_cache/`）：

```
┌─ 端到端 cache（最外层，优先级最高）
│    key = SHA256(plan_model + "e2e_multimodal" + text_hint + image_hash)
│    命中 → 直接返回 SwModelingPlan，零 API 调用
│    ↓ miss
├─ 阶段 A cache（Vision 阶段）
│    key = SHA256(vision_model + sys_A + text_hint + image_hash)
│    命中 → 返回 ThreeViewIR（结构化 JSON）
│    ↓ miss
└─ 阶段 B cache（Plan 阶段）
     key = SHA256(plan_model + sys_B + text_hint + IR_json)
     命中 → 返回 SwModelingPlan
```

**关键点**：
- Cache 只用于复用同一用户输入自然产生的结果，不能手工预置 benchmark 的正确 plan。
- 性能评测应区分冷启动与自然 cache 命中；不能把预置答案的命中计作建模能力。
- Prompt/Skill 变化会进入 cache key，确保通用规则更新后不会命中过时计划。

---

## 8. 现有测试样例清单

### 8.1 文字建模（自然语言 → CAD）
`test/objects/` 目录下每个物体一个测试文件：

| 物体 | 测试文件 | Web demo 按钮 | 状态 |
|---|---|---|---|
| 骰子（20mm 立方体 21 点） | test_dice_llm.py | 🎲 骰子 | ✅ 38 步 |
| 板凳（4 脚，圆角） | test_stool.py | 🪑 板凳 | ✅ 14 步 |
| 齿轮 | test_gear_llm.py | ⚙️ 齿轮 | ✅ |
| 花瓶（旋转体+shell）| test_vase_llm.py | 🏺 花瓶 | ✅ |
| 六角螺母 | test_hex_nut_llm.py | 🔩 螺母 | ✅ |
| 钥匙 | test_key_llm.py | 🗝️ 钥匙 | ✅ |
| 马克杯 | test_mug_llm.py | ☕ 马克杯 | ✅ |
| 扳手 | test_wrench_llm.py | 🔧 扳手 | ✅ |
| 储物盒（8mm 圆角）| test_storage_box.py | 📦 储物盒 | ✅ |

### 8.2 三视图建模（图纸 → CAD）
`datasets/manual_three_view/samples.json` 单一数据源：

| ID | 名称 | 特征 | 端到端步数 |
|---|---|---|---|
| B01 | L 型支架 | 60×40 外接矩形右上切 30×20 缺口 | 9 |
| B02 | 带 2 孔平板 | 双圆孔 | 8 |
| B03 | T 型支架 | T 形轮廓 | 11 |
| B04 | 键盘键帽（简化）| 拔模拉伸首例 | 4 |
| B05 | 完整版键盘键帽 | 复杂拔模 | |
| B06 | 门锁扣板 | 圆孔+方孔 | |
| B07 | 86 型开关面板 | 标准家用面板 | |
| B11 | 齿轮盘 | 圆周阵列首例 | |
| B22 | 圆碗 | Shell 替代法首例 | |
| C1 | 迷你机械键盘 | 65 键线性阵列 | |
| C2 | 齿轮减速器外壳 | 法兰+箱体+内腔+轴承孔+4安装孔 | |
| C3 | 齿轮啮合组 | 10 齿小 + 20 齿大, 2:1 减速比 | 15 |

### 8.3 装配体（**首个装配体样例，Stage B 新增**）
`assembly_smoke/`：
- `gear_pair_assembly_v4.SLDASM` — 渐开线齿轮装配体，小齿轮 → 大齿轮按 -2:1 反向联动 ✅

---

## 9. HTTP API 端点全清单

见 `CODEBUDDY.md` 表格（那里最权威），这里只列高层分类：

### 9.1 零件建模（30+ 端点）
- 文档：`/api/new-part`
- 草图：`/api/sketch/{line,rectangle,circle,centerline,arc,ellipse,polygon,slot}`
- 特征：`/api/feature/{extrusion,revolve,cut-extrusion,fillet,chamfer,circular-pattern,linear-pattern}`
- 选择：`/api/select-plane`、`/api/select-face-and-sketch`
- 查询：`/api/feature-tree`、`/api/body-bounding-box`、`/api/body-volume`
- 组合：`/api/create-box`
- 其他：`/api/modify-dimension`、`/api/close-sketch`

### 9.2 装配体（13 个端点，Stage B 新增）
- 建模：`/api/assembly/{new,open,save,rebuild}`
- 组件：`/api/assembly/add-component`、`/api/assembly/unfix-component`
- Mate：`/api/assembly/mate-{coincident,concentric,gear}`
- 零件另存：`/api/save-part-as`
- 调试：`/api/assembly/get-component-transform`
- ⚠️ 有问题：`/api/assembly/rotate-component`、`/api/assembly/rotate-component-by-drag`（见 §10）
- ✅ Motion Study：`/api/assembly/motion/{probe,create-gear-motor,play,stop,status}`

---

## 10. 【重要】未完成 & 已知问题

### 10.1 ✅ Motion Study 编程驱动齿轮旋转（2026-07-14 已打通）

以下 3 种直接修改组件 Transform 的旧路径仍失败（API 返回 True 但 Transform 不变）：
- `IComponent2.SetTransformAndSolve3(newTransform, false)` → 返回 True，Transform 不变
- `component.Transform2 = newTransform` （setter） → 无异常，Transform 不变
- `IDragOperator.{AddComponent + BeginDrag + Drag(xform) + EndDrag}` → 每步返回 True，Transform 不变

**已采用的有效方案**：`IModelDocExtension.GetMotionStudyManager()` → Animation Study → `swFmAEMRotationalMotor` → `PlayFromStart()`。无需 Motion Analysis 许可，本机支持类型位掩码为 7。

齿轮已从矩形凸齿升级为同模数渐开线齿形：模数 4mm、压力角 30°、齿数 10:20、理论中心距 60mm，并带 ⌀10mm 中心孔。30° 压力角用于避免 10 齿小齿轮根切；大齿轮错相 9°。纯几何测试在一个完整齿周期采样 73 帧，无轮廓交叉。

2026-07-14 进一步修复 Gear Mate 方向：`AddMate5` 的 `AlignFromEnum` 不控制齿轮旋向，必须创建后通过 `IGearMateFeatureData.Reverse` 显式设置。实测小齿轮转角 -64.458°、大齿轮 +32.229°，满足外啮合 `-2:1`。`capture_gear_animation.py` 导出完整齿周期 25 帧 PNG/BMP/GIF，并逐帧通过 `IInterferenceDetectionMgr` 检查，25/25 零干涉。测试：`assembly_smoke/test_gear_geometry.py`、`assembly_smoke/test_motion_study.py`。

Web demo 已增加“加载装配体 / 播放 5 圈 / 停止”控件；直接 Transform 路线仅保留用于历史诊断。

### 10.2 🟡 CircularPattern 在少数情况下几何未完全生效
CODEBUDDY.md 里的表格已注明"本地 SW 上 API 调用成功但几何未完全生效，暂不推荐依赖"。多实体零件下已加"自动从源特征中心选圆柱面"逻辑，但**极端 case** 可能还有边缘问题，需要观察。

2026-07-14 修复 C3 稳定回归：圆周阵列创建参考轴后，`SelectByID2("草图4","SKETCH")` 返回 false，导致大齿轮种子齿拉伸失败。`Extrusion()` 现会回退到遍历特征树并调用 `IFeature.Select2`；C3 连续 3 次 15/15 通过，包围盒 138×88×15mm。

### 10.3 🟡 长 sketch/slot 在本地 SW 上退化为矩形近似
`SketchSlot` 已知问题：本地 SW 上 `CreateSlot4` 有兼容性问题，服务端降级为"中心矩形近似"。理想情况下应该是长圆孔（Slot），但目前是矩形。

### 10.4 🟢 Planner 端还未支持装配体
- **现状**：装配体 API（C# 服务端）已全部打通，但 Python 端 `SwModelingPlan` schema 和 `SkillRegistry` 还没有为装配体扩展。
- **待做**：
  1. `planner/schemas.py` 加装配体 op 枚举（`new_assembly`、`add_component`、`mate_gear` 等）
  2. `planner/executor.py` 加装配体 op 的 HTTP 转发
  3. 新建 `planner/skills/input_modes/assembly.md` — 装配体建模流程指引
  4. 新建 `planner/skills/recipes/gear_pair_assembly.md` — 齿轮组装配范式
  5. samples.json 加 D 系列样例（装配体样例）
  6. Web demo 按钮自动出现（samples.json 驱动）

### 10.5 三视图 cache 与评测隔离
禁止为三视图 benchmark 手工写入正确 plan。Web Demo 和评测应从空 cache 或自然请求 cache
开始，并分别记录冷启动、自然命中和 token 成本。

### 10.6 🟢 手绘草图 / 效果图输入模式
架构已预留 `skills/input_modes/` 扩展位，还未实现。可以先做 CAD-VGDrawing 数据集（SVG 直读，比 PNG OCR 简单）。

### 10.7 ✅ Motion Study / 齿轮动画演示
Web demo 已可加载预置齿轮装配体并播放/停止 5 圈 Motion Study 动画。后续仍可把该能力扩展成由 Planner 自动创建装配体的 D1 样例。

---

## 11. 现有 memory（AI 助手的持久记忆）

以下是之前 AI 会话中沉淀下来的关键经验，Cursor 里可以人工同步：

### 11.1 SolidWorks API 创建倒角特征的关键要点
- 必须用 `IModelDoc2.FeatureChamfer(Width, Angle, Flip)` 3参数版
- 8参版 `InsertFeatureChamfer` 不稳定（3dswym 论坛报告特征在树里但无实际效果）
- 各类型参数映射：等距 `Angle=π/4`；不等距 `Angle=atan(d2/d1)`；距离-角度直接传

### 11.2 SolidWorks API 中执行特征操作的关键要点
- **退出草图**：`IModelDoc2.InsertSketch2(true)` NOT `SketchManager.InsertSketch(true)`
- **FeatureCut4**：`NormalCut=false, UseFeatScope=true, UseAutoSelect=true`
- **旋转特征**：不能用圆做轮廓、轮廓线不能与旋转轴重合、球体正确做法是中心线+半圆弧

### 11.3 骰子建模（LLM 端到端）的关键经验
- 温度=0 必设（Sonnet 5 默认 temp=1 长 plan 会截断到 10/25 步）
- system prompt 补"禁止省略"规则（防止"依次类推"）
- 系统 prompt 补"拉伸中心=depth/2"规则（对称件用 midPlane=true 最不易错）
- 系统 prompt 补"面内轴 vs 法向轴"映射规则
- SwCommands 抽出 `FindFaceByRay` 辅助方法，**平面命中优先于曲面**
- 曲面分支必须加**射线方向过滤**（`forwardDot >= 0`）

### 11.4 SolidWorks API 在实体面上切除小凹坑
- **FeatureCut4 第 2 参 Flip 实为"翻转切除侧"**，不是"翻转方向"
- 切凹坑用 `flipDirection=False`（切内部，正确）
- 切孔通穿用 `flipDirection=False`（同理）
- **多刀切除必须显式锁定 `_activeSketchName`**（依赖自动选择首刀会错）
- 用 `/api/body-volume` 端点逐刀对比 vol_before/vol_after 是诊断神器

### 11.5 板凳建模（LLM 端到端）
- 圆角多边必须拆分（`edgeSelection="top"` 会 400，必须是 `top_front`）
- 面名下划线连接表示两面交线："top_front" = 顶+前 交线
- 所有边圆角用 `edgeSelection="all"` 一步搞定；语义化边必须逐边

### 11.6 SolidWorks API 射线选面
- LLM 常搞反射线方向（要选底面 Y=0 时应从下方向上射入实体，dy=+1）
- 服务端已实现**自动反转方向**兜底（原方向未命中就翻转再射）
- Skill prompt 里明确写"射线起点在目标面外侧，方向指向实体内部"

### 11.7 项目 Git 偏好
- 每次 `git commit` 之后**必须立即** `git push origin main`
- 一次 commit = 一次 push（无需再问）
- 默认分支 main，远程 origin/main

### 11.8 编码 & 权限
- `PYTHONUTF8=1` + `python -X utf8` 是标配
- SolidWorks + 命令行 **权限必须一致**（都普通 or 都管理员）

---

## 12. 立即上手的验证步骤（30 分钟）

```powershell
# 假设你在 c:\codes\sw\SolidWorksTutorial

# 【1】拉最新代码
git pull origin main

# 【2】启动 SolidWorks 桌面软件（手动）

# 【3】构建 & 启动 C# 服务
.\build_service.bat
.\run_service.bat
# 另开一个 shell 验证:
curl.exe http://localhost:5100/api/ping

# 【4】Python 环境准备
cd LLMPlanner
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
set OPENAI_API_KEY=<key>
set OPENAI_BASE_URL=https://api.juheai.top/v1
set PLANNER_MODEL=basic/claude-sonnet-5
set PYTHONUTF8=1

# 【5】跑最简单的三视图样例（B01 已预置 gt.json）
python -X utf8 test\test_three_view_e2e.py B01
# 期望: 9/9 步一次通过, 包围盒接近 60×40×20

# 【6】跑装配体端到端冒烟
python -X utf8 assembly_smoke\phase3_via_csharp_api.py
# 期望: 6/6 步通过, 生成 gear_pair_assembly_v4.SLDASM

# 【7】启动 Web demo
python -X utf8 -m web.app --port 5000
# 浏览器 http://localhost:5000
# 点齿轮啮合组按钮, 应看到 "⭐ 端到端缓存命中"
```

如果这 7 步都跑通，你就完全接手了这个项目。

---

## 13. 下一步开发建议

### 13.1 短期（1 周内可完成）
- ✅ 熟悉代码，跑通 §12 全流程
- ✅ Motion Study API 已打通，齿轮可由 Web 控制播放/停止
- 建立与生产 Skill 完全隔离的 held-out 三视图评测集

### 13.2 中期（2~4 周）
- Planner 端支持装配体（§10.4）
- 新增 D 系列样例（装配体），如"会转的齿轮减速器"、"折叠椅"、"活塞连杆"
- 手绘草图输入模式（CAD-VGDrawing SVG 直读）

### 13.3 长期
- Motion Study 完整支持（马达、力、约束求解）
- 有限元分析（Simulation API）
- 工程图自动生成（Drawing）
- 高级配合（凸轮配合、路径配合、限制距离配合）

---

## 14. 联系点 & 资源

- **项目仓库**：https://github.com/fansunqi/SolidWorksTutorial
- **SolidWorks API 文档**（本地）：`c:\codes\sw\offline-solidworks-api-docs\`
  - XML 元数据：`50_extract_type_member_details\metadata\api_member_details.xml`
  - **注意**：中转过滤后 XML 里没有 IComponent2 / IDragOperator 类型的成员，需要在线查 https://help.solidworks.com/2026/english/api/
- **Xarial.XCad**（第三方 SolidWorks 抽象层）：https://github.com/xarial/xcad
- **juheai.top**（LLM API 中转）：https://api.juheai.top/v1
- **参考 Skill 文档**：`planner/skills/core/01_operations.md` 是最详细的建模规则手册

---

## 15. 附录：Cursor + GPT-5.6 上手建议

1. **让 Cursor 先读 CODEBUDDY.md 和 TEST.md** —— 这是项目最重要的两份文档（AI 会话开头必读）
2. **让 Cursor 索引整个 LLMPlanner 目录**（尤其 `planner/skills/`）
3. **常用命令别名**（PowerShell profile 里加）：
   ```powershell
   function sw-build { cd c:\codes\sw\SolidWorksTutorial; .\build_service.bat }
   function sw-run { cd c:\codes\sw\SolidWorksTutorial; .\run_service.bat }
   function sw-test { cd c:\codes\sw\SolidWorksTutorial\LLMPlanner; venv\Scripts\python.exe -X utf8 test\test_three_view_e2e.py $args }
   ```
4. **GPT-5.6 与 Claude Sonnet 5 差异**：GPT-5.6 对代码理解可能更严谨但对中文注释理解可能略弱；建议 skill 里的 "系统 prompt" 若做优化，让 Cursor 直接测试两个模型都跑一遍验证。
5. **保持 Definition of Done 强约束**：新增建模物体必须走完 5 步（测试 / 日志 / TEST.md / Web demo / commit+push），已在 CODEBUDDY.md 顶部明写。

---

**最后：本项目已经沉淀了大量踩坑经验（60+ 个坑，见 memory），Cursor + GPT-5.6 只要严格遵守 CODEBUDDY.md 和 TEST.md 的约束，就能安全高效地继续开发。**

祝顺利！
