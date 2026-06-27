# 测试指南（TEST.md）

本文件整理了 LLMPlanner 的端到端测试用例与运行方式。所有测试均通过 HTTP 调用 C# 服务驱动 SolidWorks 实际建模。

> 前置条件（每次测试都需要）：
> 1. **SolidWorks 已启动**（普通权限，详见 `Chapter1.ConnectToSw/README.md`）
> 2. **C# HTTP API 服务已运行**（`run_service.bat` 或 `Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe`）
> 3. **已激活 Python 虚拟环境并配置好 API Key**（见下方「环境准备」）

---

## 环境准备

在 cmd bash 上运行：

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner
venv\Scripts\activate.bat

REM 配置 LLM API（请替换为你自己的 Key 与地址）
set OPENAI_API_KEY=<你的_API_KEY>
set OPENAI_BASE_URL=<你的_API_BASE_URL，如 https://api.openai.com/v1>
```

---

## 一、基础用法（命令行直接建模）

```cmd
REM 交互模式
python -m planner.main

REM 单次执行（dry-run 模拟，不调用 SolidWorks，仅查看规划）
python -m planner.main --dry-run "创建一个 100mm x 50mm x 30mm 的长方体"

REM 实际建模
python -m planner.main "创建一个方块，长为 100mm，宽为 50mm，高为 30mm"
python -m planner.main "创建一个凸台，底面是长为 100mm、宽为 50mm 的长方形，拉伸 30mm"
python -m planner.main "将凸台再向上拉伸 50mm"
```

---

## 二、基础测试

```cmd
REM 创建长方体（最简单的端到端测试）
python LLMPlanner/test/test_create_box.py

REM LLM API 连通性测试
python LLMPlanner/test/test_llm_api.py
```

---

## 三、切除拉伸测试

```cmd
REM 运行全部切除拉伸测试（盲孔 + 贯穿孔）
python LLMPlanner/test/test_cut_extrusion.py

REM 仅盲孔：新建零件，前视面画 100×50mm 矩形拉伸 30mm，上表面中心打 ⌀20mm、深 20mm 盲孔
python LLMPlanner/test/test_cut_extrusion.py blind

REM 仅贯穿孔：新建零件，前视面画 80×60mm 矩形拉伸 10mm 薄板，上表面中心打 ⌀6mm 贯穿孔
python LLMPlanner/test/test_cut_extrusion.py through
```

---

## 四、旋转特征测试（API 直接调用）

```cmd
REM 运行全部旋转特征 API 测试
python LLMPlanner/test/test_revolve.py

REM 圆柱体（矩形轮廓 + 中心线 → 旋转 360°，预期 ⌀40mm×80mm）
python LLMPlanner/test/test_revolve.py cylinder

REM 圆柱体（线段轮廓 + 中心线 → 旋转 360°，预期 ⌀30mm×60mm）
python LLMPlanner/test/test_revolve.py cylinder2

REM 球体（圆弧轮廓 + 中心线 → 旋转 360°，预期 ⌀40mm）
python LLMPlanner/test/test_revolve.py sphere
```

---

## 五、旋转特征测试（LLM Prompt → 自动规划 → 执行）

```cmd
REM 运行全部 LLM 旋转测试
python LLMPlanner/test/test_revolve_llm.py

REM LLM Prompt: "用旋转法创建一个直径 40mm、高度 80mm 的圆柱体"
python LLMPlanner/test/test_revolve_llm.py cylinder

REM LLM Prompt: "用旋转法创建一个直径 50mm 的球体"
python LLMPlanner/test/test_revolve_llm.py sphere
```

---

## 六、圆角 / 倒角测试

```cmd
REM 运行全部圆角倒角测试（5 个用例）
python LLMPlanner/test/test_fillet_chamfer.py

REM 所有边圆角：新建 100mm 立方体，对所有边做 R5mm 圆角
python LLMPlanner/test/test_fillet_chamfer.py fillet

REM 射线选边圆角：新建 80×60×40mm 长方体，射线选中顶部前侧边做 R3mm 圆角
python LLMPlanner/test/test_fillet_chamfer.py fillet_ray

REM 等距倒角：新建 80×60×40mm 长方体，对所有边做 C3mm 等距倒角
python LLMPlanner/test/test_fillet_chamfer.py chamfer

REM 不等距倒角：新建 60×60×30mm 长方体，射线选顶边做 2mm×4mm 不等距倒角
python LLMPlanner/test/test_fillet_chamfer.py chamfer_dd

REM 圆角+倒角组合：新建 100×80×50mm 长方体，底部前边 R5mm 圆角 + 顶部前边 C3mm 倒角
python LLMPlanner/test/test_fillet_chamfer.py combo
```

---

## 七、板凳建模测试（阶段一：API 调用，含拉伸方向翻转）

```cmd
REM 运行全部板凳测试（3 个用例）
python LLMPlanner/test/test_stool.py

REM 反向拉伸基础功能：上视面画 50×50mm 矩形，flipDirection=true 向下拉伸 30mm，验证包围盒 Y 方向
python LLMPlanner/test/test_stool.py flip

REM 四脚板凳完整建模：凳面 200×200×20mm + 4 条圆柱腿（⌀20mm×300mm），总高 320mm
python LLMPlanner/test/test_stool.py stool

REM 带圆角的四脚板凳：凳面顶部 4 边 R5mm 圆角 + 4 条腿，验证圆角特征存在
python LLMPlanner/test/test_stool.py stool_fillet
```

---

## 八、板凳建模测试（阶段二：LLM 自然语言 → 自动规划 → 执行）

```cmd
REM 运行全部 LLM 板凳测试
python LLMPlanner/test/test_stool_llm.py

REM LLM 四脚板凳自动建模
REM Prompt: "创建一个四脚板凳：凳面为 200mm×200mm×20mm 的方形平板，在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 200mm），腿的圆心距凳面边缘 30mm。"
python LLMPlanner/test/test_stool_llm.py stool

REM LLM 带圆角的四脚板凳自动建模
REM Prompt: "创建一个四脚板凳并对凳面做圆角处理：……最后对凳面顶部的 4 条边做 R5mm 圆角。"
python LLMPlanner/test/test_stool_llm.py stool_fillet
```

---

## 测试分类说明

| 类别 | 驱动方式 | 说明 |
|---|---|---|
| `test_create_box` / `test_llm_api` | 基础 | 端到端连通性、LLM API 连通性 |
| `test_cut_extrusion` | API 直调 | 切除拉伸（盲孔 / 贯穿孔） |
| `test_revolve` | API 直调 | 旋转特征（圆柱 / 球体） |
| `test_revolve_llm` | LLM 规划 | 自然语言 → 旋转建模 |
| `test_fillet_chamfer` | API 直调 | 圆角 / 倒角 |
| `test_stool` | API 直调 | 板凳建模（含反向拉伸） |
| `test_stool_llm` | LLM 规划 | 自然语言 → 板凳建模 |

> **API 直调**类测试直接构造操作序列调用 C# 服务，不依赖 LLM，可用于验证底层建模能力；
> **LLM 规划**类测试走完整链路（自然语言 → LLM 解析 → 执行），需要配置好 API Key。
