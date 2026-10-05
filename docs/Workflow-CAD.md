# Workflow-CAD：借助 SolidWorks 运行的 LLM 辅助 CAD 建模系统

Workflow-CAD 让大语言模型（LLM）直接在 SolidWorks 里建出原生参数化零件（带完整特征树的 `.SLDPRT`）。它提供两种动作层级：

- **L0（原子操作）**：LLM 一次性规划出草图、拉伸、切除、圆角等原子操作序列，再按顺序执行。这是以往 API 级规划器的做法。
- **L1（工程师级工作流）**：LLM 作为闭环 Agent，调用 24 个从专业建模过程中提炼出来的工具。每次调用后读取真实模型状态，再决定下一步。每个特征都作为一个事务执行，失败或不起作用就回滚。

本文说明如何在一台 Windows 机器上把系统跑起来，以及各部分分别负责什么。评测方法见 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md)。由三视图生成零件（多模态 L1 端到端）的运行方式见 [`Workflow-CAD-3Views.md`](./Workflow-CAD-3Views.md)。

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

## 2. 在一台新机器上配置环境

下面按照从零开始的顺序来写。仓库里除了 `build_service.bat` 中的 MSBuild 路径以外，没有写死的绝对路径，放在任何目录下都能运行。下文用 `<repo>` 表示仓库根目录。

### 2.1 需要安装的软件

| 软件 | 我们测试过的版本 | 说明 |
|---|---|---|
| Windows | 10 / 11，x64 | SolidWorks 与 COM 自动化只能在 Windows 上运行 |
| SolidWorks | 2025（中文版），已激活 | 必须能正常启动并新建零件。其他版本见 2.4 节 |
| Visual Studio 2022 或更新版本，或 Build Tools for Visual Studio | Community 版 | 安装“.NET 桌面开发”工作负载，并勾选 **.NET Framework 4.7.2 目标包** |
| NuGet 命令行工具 `nuget.exe` | 6.x | 从 nuget.org 下载后放到 PATH 中，用来还原 `packages.config` |
| Git | 任意 | 用来获取代码 |
| Python | 3.14.3 | 主环境用 3.11 或更高版本即可；检查器环境需要 `cadquery-ocp` 有对应 Python 版本的 wheel，直接用 3.14 最省事 |
| LLM 接口 | 任意 OpenAI 兼容接口 | 需要支持 function calling（L1 用）。论文用的是 Qwen3.5-397B-A17B，温度 0 |

只想用 benchmark 给 STEP 文件打分的话，只需要 Python，不需要 SolidWorks 和 Visual Studio，见 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md)。

### 2.2 获取代码

```powershell
git clone https://github.com/fansunqi/SolidWorksTutorial.git     # 或 git@github.com:fansunqi/SolidWorksTutorial.git
cd SolidWorksTutorial
```

仓库里不包含这些内容：NuGet 包（`packages/`）、Python 虚拟环境（`LLMPlanner/venv`、`LLMPlanner/venv-occ`）、密钥文件（`LLMPlanner/.env`），以及评测产生的 STEP/SLDPRT（`LLMPlanner/eval/artifacts/`）。下面几步会依次把它们准备好。

### 2.3 构建 C# 服务

1. **还原 NuGet 包**（Xarial.XCad 0.8.3 系列，其中自带 SolidWorks Interop 程序集）：

   ```cmd
   cd <repo>
   nuget restore Chapter1.ConnectToSw\packages.config -PackagesDirectory packages
   ```

2. **找到 MSBuild**。`build_service.bat` 默认写的是 `C:\Program Files\Microsoft Visual Studio\18\Community\...`，路径不同就要改脚本第 7 行，或者直接用下一步的命令。查找 MSBuild 的命令：

   ```cmd
   "%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -requires Microsoft.Component.MSBuild -find MSBuild\**\Bin\MSBuild.exe
   ```

3. **构建**：

   ```cmd
   "<上一步得到的 MSBuild.exe>" Chapter1.ConnectToSw\Chapter1.ConnectToSw.csproj /p:Configuration=Debug /t:Rebuild /v:minimal
   ```

   成功后会生成 `Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe`，`run_service.bat` 启动的就是它。

### 2.4 SolidWorks 安装位置与版本

- 项目会从 SolidWorks 安装目录引用 `api\redist\SolidWorks.Interop.swmotionstudy.dll`。`.csproj` 会自动在 `C:\Program Files\SOLIDWORKS Corp\SOLIDWORKS\` 下查找。如果装在别的地方，构建时加上参数：

  ```cmd
  ... /p:SolidWorksInstallDir="D:\Apps\SOLIDWORKS Corp\SOLIDWORKS\\"
  ```

- 其余 Interop 程序集（`sldworks`、`swconst`、`swpublished`）来自 NuGet 包，引用的版本号是 `34.1.1.11`，对应 SolidWorks 2025。如果用的是其他版本，构建报“找不到引用或版本不匹配”时，把 `.csproj` 里这三个引用的版本号改成本机的版本，或者把 `HintPath` 改为指向本机 `api\redist` 下的同名 DLL。
- 零件建模按特征类型定位基准面，不依赖“前视基准面 / Front Plane”这类名称，所以英文版 SolidWorks 也能用于 text-to-CAD。装配相关功能目前按中文版的默认基准面名称编写。
- 新建零件时使用 SolidWorks 的默认零件模板（“选项 → 默认模板”），请确认这个模板文件存在。服务内部统一按 SolidWorks API 的米制单位传尺寸，导出的 STEP 自带单位，所以模板用什么单位制都不影响结果。

### 2.5 Python 环境

LLMPlanner 用两个互不干扰的虚拟环境：

```powershell
cd <repo>\LLMPlanner

# 主环境：Web 后端、Agent、评测脚本（openai、pydantic、flask、requests 等）
py -3.14 -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt

# 检查器环境：OpenCASCADE 的 Python 绑定（cadquery-ocp）、numpy、PyYAML、pytest
py -3.14 -m venv venv-occ
venv-occ\Scripts\python.exe -m pip install -r geomcheck\requirements.txt
```

检查一下两个环境是否装好：

```powershell
venv\Scripts\python.exe -c "import openai, flask, pydantic, requests; print('main ok')"
venv-occ\Scripts\python.exe -c "import OCP, numpy, yaml; print('occ ok')"
venv-occ\Scripts\python.exe -m pytest geomcheck/tests -q -p no:cacheprovider    # 应为 125 passed，不需要 SolidWorks
```

### 2.6 配置 LLM

```powershell
copy .env.example .env      # 在 LLMPlanner 目录下执行
```

然后编辑 `.env`（它已被 git 忽略，不要提交）：

```ini
OPENAI_API_KEY=sk-ttsw-local                    # 默认：本机 TT Switch 网关
OPENAI_BASE_URL=http://127.0.0.1:4200/native    # 换别的 OpenAI 兼容服务时改这两项
PLAN_MODEL=gemini-3.5-flash                     # 规划 / Agent 用的模型名
VISION_MODEL=gemini-3.5-flash                   # 只在三视图输入时使用
SW_API_URL=http://localhost:5100
```

TT Switch 网关的启动方式和可用模型见 `tt_switch_api_examples/README.md`。电脑重启后网关不会自动运行，连接被拒绝时先启动它。AI Ping、juheai 的配置可以分别写在 `AIPING_*`、`JUHEAI_*` 里备用，Web 设置里可以切换。

请求里的 `provider` 决定 Web 后端读哪一组配置：`ttswitch`（Web 默认）读 `OPENAI_*`，`aiping`、`juheai` 分别读 `AIPING_*`、`JUHEAI_*`。评测脚本 `eval_text_singleturn_qwen.py`、`eval_three_view_bench.py` 默认发 `aiping`，走 TT Switch 网关时要加 `--provider ttswitch`。L1 每次运行带一个独立的 `X-Session-Id` 请求头，TT Switch 据此复用 prompt 缓存。

请求里也可以临时指定 `api_key`、`base_url` 和 `plan_model`，会覆盖 `.env` 中的值。评测结果里的 `spent_yuan`（费用）依赖我们本机的 AI Ping 余额查询脚本，在别的机器上会是 `null`，不影响运行。需要的话，可以用结果里的 `token_usage` 乘以服务商的单价自己算。

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

**端到端自检**：在新机器上配好环境后，建议先用一道简单的题把整条链路跑通，再做批量评测。在 `LLMPlanner` 目录下另开一个终端，执行：

```powershell
venv\Scripts\python.exe -X utf8 -u eval\eval_text_singleturn_qwen.py --action-level L1 --ids stool --repeats 1 `
    --output eval\smoke_l1_result.json
venv-occ\Scripts\python.exe -X utf8 -m geomcheck.rubric --artifacts eval\artifacts\smoke_l1_result
```

SolidWorks 里会建出一张四脚板凳，`eval\artifacts\smoke_l1_result\` 下会出现 `stool_r1.step` 和 `stool_r1.SLDPRT`，打分输出形如 `stool_r1.step: score=100.0 ... accepted=True`。哪一步失败，就对照第 6 节排查。

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
- `--split all` 只包含 50 道核心任务；8 道复杂综合件（四旋翼机架、凸轮轴、离心叶轮等）用 `--split hard` 单独跑，适合做演示。
- 从零生成零件再装配（减速箱 / 发动机试点）见 [`Workflow-CAD-Assembly.md`](./Workflow-CAD-Assembly.md)。
- 打分和汇总见 [`Workflow-CAD-Bench.md`](./Workflow-CAD-Bench.md)。

### 4.5 重放已保存的建模过程（录制演示）

`eval/replay_demo.py` 读取评测结果里保存的 LLM 原始响应和工具日志，不再调用 LLM，按原顺序把每个工具调用发给与评测时相同的会话，在 SolidWorks 里重现建模过程。每一步打印模型这一轮的文字和工具参数，执行后通过 C# 服务的 `/api/view/fit` 切到等轴测并缩放到全屏。

```powershell
# text-to-CAD（L1）
venv\Scripts\python.exe -X utf8 -u eval\replay_demo.py text eval\text_workflow_l1_<model>_hard_en_result.json --case camshaft

# 装配：零件用评测时保存的 .SLDPRT，--animate 在驱动件上加马达播放 Motion Study（多位姿测例依次摆位姿）
venv\Scripts\python.exe -X utf8 -u eval\replay_demo.py assembly eval\assembly_gen_<model>_v2.json --case pumpjack_drive `
    --artifacts eval\artifacts\assembly_gen_<model>_v2 --animate
```

- 默认原样重放全部调用，包括评测时失败并已回滚的调用；跳过它们可能改变 SolidWorks 自动编号的特征名，后面按名称引用特征的调用会对不上。
- 装配的 `save_assembly` 一律写到 `eval/artifacts/replay/<case_id>.SLDASM`（或 `--save-as`），不覆盖评测产物。
- 结束时打印成败与评测时不一致的调用数，零件还会对比最终体积；不为 0 说明重放没有复现评测结果。
- `--delay` 控制每步停顿，`--pause` 每步等回车，`--view` 选视向（`off` 不动视图）。

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
