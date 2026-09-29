# Workflow-CAD：借助 SolidWorks 运行的 LLM 辅助 CAD 建模系统

Workflow-CAD 让大语言模型（LLM）直接在 SolidWorks 里建出原生参数化零件（带完整特征树的 `.SLDPRT`）。它提供两种动作层级：

- **L0（原子操作）**：LLM 一次性规划出草图、拉伸、切除、圆角等原子操作序列，再按顺序执行。这是以往 API 级规划器的做法。
- **L1（工程师级工作流）**：LLM 作为闭环 Agent，调用 24 个从专业建模过程中提炼出来的工具。每次调用后读取真实模型状态，再决定下一步。每个特征都作为一个事务执行，失败或不起作用就回滚。

本文说明如何在一台 Windows 机器上把系统跑起来，以及各部分分别负责什么。评测方法见 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md)。

---

## 1. 架构

```
自然语言设计要求
      │  POST /api/run   (http://localhost:5000)
      ▼
LLMPlanner Web 后端（Flask，Python）
  ├─ L0：planner/llm_client.py  一次规划 → planner/executor.py 顺序执行
  └─ L1：planner/workflow/agent.py 闭环 Agent + planner/workflow/tools.py 24 个工具
      │  HTTP JSON   (http://localhost:5100)
      ▼
C# SolidWorks 服务（Chapter1.ConnectToSw，Xarial.XCad + COM Interop）
      │  COM
      ▼
SolidWorks 2025（单实例，串行执行）
```

LLM 只和 Python 端通信，Python 端只通过 HTTP 调用 C# 服务。因此换模型只需要改配置，换 CAD 后端只需要替换 C# 服务。

| 组件 | 位置 | 端口 | 作用 |
|---|---|---|---|
| C# 服务 | `Chapter1.ConnectToSw/` | 5100 | 把 SolidWorks 的建模、查询、导出操作封装成 HTTP API |
| Web 后端 | `LLMPlanner/web/app.py` | 5000 | 接收设计要求，调用 LLM，驱动 L0 或 L1 流程；也提供网页界面 |
| L1 Agent 与工具 | `LLMPlanner/planner/workflow/` | — | 闭环 Agent、24 个工具、坐标系、面引用、轮廓预检、参数表达式 |
| 模型观察 | `LLMPlanner/planner/observer.py` | — | 读取特征树、包围盒、体积、面拓扑，作为 Agent 的观察 |
| 几何检查器 | `LLMPlanner/geomcheck/` | — | 用 OpenCASCADE 对 STEP 做 rubric 打分，与建模系统无关 |

---

## 2. 环境准备

### 2.1 前置条件

- Windows 10/11，已安装并激活 **SolidWorks**（我们使用 2025 版）。
- **Visual Studio 或 MSBuild**，用来构建 .NET Framework 4.7.2 的 C# 服务。
- **Python 3.11 或更高版本**。
- 一个 **OpenAI 兼容的 LLM 接口**，需要支持 function calling（L1 用）。论文实验使用 Qwen3.5-397B-A17B，温度 0。

### 2.2 构建 C# 服务

```cmd
cd C:\codes\sw\SolidWorksTutorial
nuget restore SolidWorksTutorial.slnx
build_service.bat
```

`build_service.bat` 里写死了 MSBuild 路径，找不到时按脚本提示修改。如果 SolidWorks 版本不是 2025，要调整 `Chapter1.ConnectToSw.csproj` 中 COM 引用的版本号和 `HintPath`（见根目录 `README.md` 的“复现注意事项”）。

### 2.3 Python 环境

LLMPlanner 使用两个虚拟环境，互不干扰：

```powershell
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

# 主环境：Web 后端、Agent、评测脚本
python -m venv venv
venv\Scripts\pip install -r requirements.txt

# 几何检查环境：OpenCASCADE（OCP）+ numpy + PyYAML + pytest
python -m venv venv-occ
venv-occ\Scripts\pip install -r geomcheck\requirements.txt
```

### 2.4 配置 LLM

复制 `.env.example` 为 `.env` 并填写（`.env` 已被 git 忽略，不要提交）：

```ini
OPENAI_API_KEY=<你的 key>
OPENAI_BASE_URL=https://aiping.cn/api/v1      # 任意 OpenAI 兼容地址
PLAN_MODEL=Qwen3.5-397B-A17B                  # 规划 / Agent 使用的模型
VISION_MODEL=GLM-4.6V                         # 仅三视图输入时使用
SW_API_URL=http://localhost:5100
```

请求里也可以临时指定 `api_key`、`base_url` 和 `plan_model`，这时会覆盖 `.env` 中的配置。

---

## 3. 启动（三步）

1. **启动 SolidWorks**。以普通权限启动，不要用管理员身份，否则 COM 连接会失败。
2. **启动 C# 服务**：

   ```cmd
   run_service.bat
   ```

   看到 `[OK] 已连接到 SolidWorks` 和 `[OK] HTTP API 服务已启动` 就说明成功了。可以用 `curl http://localhost:5100/api/ping` 自检。
3. **启动 Web 后端**：

   ```powershell
   cd LLMPlanner
   venv\Scripts\python.exe -X utf8 -m web.app          # 默认端口 5000，--port 可改
   ```

然后在浏览器打开 `http://localhost:5000` 使用网页界面，或者按下一节直接调用 HTTP 接口。

---

## 4. 使用

### 4.1 HTTP 接口（推荐用于脚本和评测）

```python
import requests

resp = requests.post("http://localhost:5000/api/run", json={
    "instruction": "创建 80×60×10mm 的安装板，四个角 R8 圆角，4-Ø7 贯穿孔，孔心距相邻两边各 12mm。",
    "action_level": "L1",      # "L0" = 原子操作单轮规划；"L1" = 24 工具闭环 Agent
    "max_turns": 40,           # L1 时是 LLM 调用预算
    "plan_model": "Qwen3.5-397B-A17B",
    "use_cache": False,        # 评测时关掉缓存
    "capture_trace": True,     # 返回完整的 LLM 对话记录
}, timeout=900).json()

print(resp["summary"])         # status、llm_calls、tool_calls、token_usage
```

建模完成后，零件停留在 SolidWorks 中。通过 C# 服务保存或导出：

```python
requests.post("http://localhost:5100/api/save-part-as", json={"savePath": r"C:\out\part.SLDPRT"})
requests.post("http://localhost:5100/api/export-step",  json={"stepPath": r"C:\out\part.step"})
requests.post("http://localhost:5100/api/close-all-documents", json={})   # 下一个任务前清场
```

### 4.2 L0 与 L1 的区别

| | L0 单轮 | L1（Workflow-CAD） |
|---|---|---|
| 动作 | 原子操作：选面、画线/圆、拉伸、切除…… | 24 个工程师级工具（见下表） |
| 控制方式 | 一次生成完整计划，顺序执行 | 闭环：调用工具 → 读取真实状态 → 决定下一步 → `finish` |
| 草图坐标 | LLM 自己推算每个基准面的原生坐标 | 每个平面都有规范的 (u, v) 坐标系，面可以用语义引用（如 `top`、`face#12`） |
| 出错处理 | 单步失败后，后续步骤建立在错误状态之上 | 轮廓先预检；特征执行后校验效果，失败或无效就回滚 |
| 参数化 | 固定数值 | `set_parameters` 定义参数，其余参数可以写成 `"=L/2"` 这样的表达式 |

### 4.3 L1 的 24 个工具

| 类别 | 工具 |
|---|---|
| 状态与参数 | `get_model_state`、`set_parameters`、`finish` |
| 基础特征 | `extrude`（支持 `up_to` 拉伸到面、拔模）、`cut`、`revolve`、`fillet`、`chamfer` |
| 孔 | `hole`、`counterbore`、`countersink`、`tapped_hole` |
| 阵列与对称 | `linear_pattern`、`circular_pattern`、`mirror`、`ref_plane`（偏移基准面） |
| 高级特征 | `rib`、`sweep`、`loft`、`shell`、`draft`、`sheet_metal`（基体法兰） |
| 编辑 | `edit_feature`（原位修改特征尺寸）、`delete_feature` |

轮廓统一用 7 种类型化图元描述：`circle`、`rect`（可带圆角）、`regular_polygon`、`slot`、`polygon`（可逐点加圆角）、`annular_sector`（扇环）、`path`（直线与圆弧组成的任意闭合轮廓），坐标都是所在平面的 (u, v)，定义见 `planner/workflow/profiles.py`。草图平面可以是三个基准面、`ref_plane` 新建的基准面，也可以是实体面的语义引用（`top`/`bottom`/`front`/…、`face(+Y, largest)`、`face#<index>`）。每个工具的参数模式在 `planner/workflow/tools.py` 的 `TOOL_SPECS` 里，这也是 Agent 实际看到的内容。

### 4.4 批量运行 50 个 text-to-CAD 任务

```powershell
cd LLMPlanner
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py `
    --action-level L1 --split all --repeats 2 `
    --output eval\text_workflow_l1_<model>_all_result.json
```

- 每次运行前会关闭所有文档，运行后把 `SLDPRT` 和 `STEP` 保存到 `eval/artifacts/<output 文件名>/<case>_r<k>.*`。
- `--ids soap_dish mug` 只跑指定的任务；`--action-level L0` 跑单轮原子规划；`--model` 换骨干模型。
- 打分和汇总见 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md)。

---

## 5. 代码导览

```
LLMPlanner/planner/workflow/
├── agent.py         # WorkflowAgent：系统提示、工具调用循环、调用预算、finish 校验
├── tools.py         # 24 个工具的模式（TOOL_SPECS）与 WorkflowSession（执行、预检、回滚）
├── frames.py        # 每个平面的规范 (u, v) 坐标系
├── refs.py          # 语义面引用：基准面、top/bottom、face#index 等
├── profiles.py      # 7 种类型化轮廓图元
├── sketch_check.py  # 轮廓预检：闭合、自交、重叠、越界
├── params.py        # 参数表与 "=表达式" 求值
├── curves.py        # 扫描路径、放样截面
└── errors.py        # 统一的错误码，Agent 据此决定如何修正
```

在 C# 端，每个 L1 工具最终会调用 `Chapter1.ConnectToSw` 中的一个或几个 `/api/...` 端点（路由在 `Program.cs`，实现在 `SwCommands.cs` 及同目录的特征文件中）。完整的端点列表见根目录的 `CODEBUDDY.md`。

---

## 6. 常见问题

- **C# 服务连不上 SolidWorks**：确认 SolidWorks 已经启动，并且和服务使用相同的权限级别（都是普通权限）。更多排查方法见 `Chapter1.ConnectToSw/README.md`。
- **构建时 exe 被占用**：先关掉正在运行的 `run_service.bat` 窗口。
- **并发请求**：SolidWorks 只有一个实例，Web 后端会把并发请求串行执行。批量评测时不要同时启动两个评测脚本。
- **文档越开越多**：每个任务结束后调用 `/api/close-all-documents`，评测脚本已经自动做了这一步。
- **LLM 报错或超时**：检查 `.env` 中的 `OPENAI_BASE_URL` 和模型名。L1 需要模型支持 function calling。
- **泛化原则**：改进系统时不能针对具体测例。不要把测例答案、测例名称或只适用于某题的步骤写进提示词、工具或代码（见 `LLMPlanner/README.md` 的 Generalization-first 原则）。
