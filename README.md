# SolidWorksCopilot — 基于 LLM 的 SolidWorks 自然语言自动建模

## 一、项目背景

本项目是一个 **SolidWorks 二次开发 + LLM Agent** 的探索性工程，目标是让用户用**自然语言**驱动 SolidWorks 完成参数化建模。

整体思路：

1. **C# 端**：基于 `Xarial.XCad` 封装层与 SolidWorks COM Interop，把常用建模操作（草图、拉伸、旋转、切除、圆角、倒角等）封装成原子 API，并以 **HTTP 服务**的形式对外暴露（监听 `http://localhost:5100/`）。
2. **Python 端（LLMPlanner）**：把用户的自然语言指令（如「创建一个 100mm×50mm×30mm 的长方体」）交给 LLM，解析为**结构化的 SolidWorks 操作序列**，再逐步调用上述 HTTP API 完成实际建模。

这样就把「自然语言理解」（Python + LLM）与「CAD 建模执行」（C# + SolidWorks COM）解耦，两端通过 HTTP 协作。

```
用户自然语言指令
      ↓
Python LLMPlanner (LLM 解析 → 结构化操作序列)
      ↓  HTTP (localhost:5100)
C# HTTP API 服务 (Xarial.XCad + COM Interop)
      ↓
SolidWorks (实际建模)
```

---

## 二、代码结构

```
SolidWorksTutorial/
├── SolidWorksTutorial.slnx          # Visual Studio 解决方案（新版 slnx 格式）
├── README.md                        # 本文件
├── TEST.md                          # 测试用例与运行指南
├── CODEBUDDY.md                     # 项目开发指引（架构 / API 端点详表）
├── build_service.bat                # 命令行构建 C# 服务（无需打开 VS）
├── run_service.bat                  # 命令行运行 C# 服务
│
├── Chapter1.ConnectToSw/            # C# HTTP API 服务（.NET Framework 4.7.2）
│   ├── Program.cs                   #   HTTP 服务入口：监听循环 + 路由分发
│   ├── SwCommands.cs                #   SolidWorks 操作封装（建模/查询/选择）
│   ├── TutorialExamples.cs          #   教程示例代码
│   ├── Chapter1.ConnectToSw.csproj  #   项目文件
│   ├── packages.config              #   NuGet 依赖清单
│   ├── App.config                   #   应用配置（程序集绑定重定向）
│   ├── Properties/AssemblyInfo.cs   #   程序集元数据
│   └── README.md                    #   C# 服务的构建/运行/权限排查文档
│
├── LLMPlanner/                      # Python LLM 规划与执行
│   ├── planner/
│   │   ├── schemas.py               #   Pydantic 结构化输出模型
│   │   ├── llm_client.py            #   LLM 客户端（缓存 + 重试 + 结构化输出）
│   │   ├── executor.py              #   HTTP API 执行器
│   │   └── main.py                  #   命令行交互入口
│   ├── test/                        #   端到端测试用例（见 TEST.md）
│   ├── web/                         #   ChatGPT 风格 Web Demo（Flask）
│   ├── requirements.txt             #   Python 依赖
│   └── README.md                    #   LLMPlanner 详细说明
│
├── docs/                            # 项目规划文档
└── packages/                        # NuGet 包（不入库，靠 packages.config 还原）
```

> 更详细的 HTTP API 端点列表、SolidWorks API 交互模式见 [`CODEBUDDY.md`](./CODEBUDDY.md)。

---

## 三、如何复现

### 前置条件

- **Windows** + 已安装 **SolidWorks**（项目依赖其 COM 注册）。
- **Visual Studio / MSBuild**（用于构建 C# 项目，可不打开 VS，仅用命令行 MSBuild）。
- **Python 3.x**（用于 LLMPlanner）。
- 一个 **OpenAI 兼容的 LLM API**（Key + Base URL）。

### 步骤 1：构建并运行 C# HTTP API 服务

```cmd
REM 1) 还原 NuGet 包（首次或包缺失时）
nuget restore SolidWorksTutorial.slnx

REM 2) 命令行构建（也可直接用 Visual Studio 打开 .slnx 构建）
build_service.bat

REM 3) 先启动 SolidWorks（普通权限），再运行服务
run_service.bat
```

服务启动成功后会监听 `http://localhost:5100/`。构建、运行、以及 **COM 连接权限问题排查**的完整说明见
[`Chapter1.ConnectToSw/README.md`](./Chapter1.ConnectToSw/README.md)。

### 步骤 2：运行 Python LLMPlanner

```cmd
cd LLMPlanner
venv\Scripts\activate.bat
pip install -r requirements.txt

REM 配置 LLM API
set OPENAI_API_KEY=<你的_API_KEY>
set OPENAI_BASE_URL=<你的_API_BASE_URL>

REM 自然语言建模
python -m planner.main "创建一个 100mm x 50mm x 30mm 的长方体"
```

LLMPlanner 的完整用法（交互模式、dry-run、Web Demo 等）见 [`LLMPlanner/README.md`](./LLMPlanner/README.md)。

### ⚠️ 复现注意事项：环境差异导致的 C# 引用问题

本项目的 `Chapter1.ConnectToSw.csproj` 中，对 `Xarial.XCad` 系列程序集与 SolidWorks COM Interop 程序集的引用，使用了**本机环境相关的路径与版本号**：

- **`HintPath` 路径**：指向 `..\packages\Xarial.XCad.*`。如果你的 NuGet 包还原位置或版本不同，需相应调整 `.csproj` 中的 `HintPath`。
- **COM 引用版本号**：`SolidWorks.Interop.sldworks` 等引用带有具体版本号（如 `Version=34.1.1.11`，对应 SolidWorks 2025）。如果你安装的 **SolidWorks 版本不同**，这些版本号可能需要调整为你本机对应的版本。

> 这属于**环境差异**，并非代码逻辑问题。若构建时报「找不到引用 / 版本不匹配」，优先检查并修正 `.csproj` 中的 `HintPath` 与 COM 引用版本号。

### 完整链路自检

1. SolidWorks 已启动（普通权限）
2. `run_service.bat` → 看到 `[OK] 已连接到 SolidWorks` 与 `[OK] HTTP API 服务已启动`
3. 另开终端：`python -m planner.main --dry-run "..."` 先验证 LLM 规划是否正常
4. 去掉 `--dry-run` 实际建模

---

## 四、测试

各功能模块（长方体、切除拉伸、旋转、圆角/倒角、板凳建模等）的端到端测试用例与运行命令，整理在 [`TEST.md`](./TEST.md) 中，涵盖：

- **API 直调测试**：直接构造操作序列调用 C# 服务，验证底层建模能力（不依赖 LLM）。
- **LLM 规划测试**：走完整链路（自然语言 → LLM 解析 → 执行），需配置好 API Key。

运行任意测试前，请确保已满足 `TEST.md` 中列出的前置条件（SolidWorks 已启动、C# 服务已运行、Python 环境已配置）。
