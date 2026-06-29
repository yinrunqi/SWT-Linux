# 测试指南（TEST.md）

本文件整理了 LLMPlanner 的端到端测试用例与运行方式。所有测试均通过 HTTP 调用 C# 服务驱动 SolidWorks 实际建模。

> 📌 **约定**：所有"LLM 自动建模任务"使用的自然语言 Prompt 都记录在本文件中（见各 LLM 规划类用例旁的 `Prompt:` 标注），便于随时复现。新增自动建模任务时，请同步把其 Prompt 补充到这里。

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

## 测试日志约定

测试运行的输出日志**统一存放在 `LLMPlanner/log/` 目录**，保持仓库整洁。该目录已在 `.gitignore` 中排除（`LLMPlanner/log/**` 与 `LLMPlanner/**/*.log`），**不会提交到 git**。

将测试输出重定向到该目录，例如：

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner
if not exist log mkdir log
python test\test_stool_llm.py stool_fillet > log\test_stool_fillet.log 2>&1
```

> **Windows 编码提示**：测试输出含 emoji（✅ 🎉 等），Windows 控制台默认 GBK 编码会报 `UnicodeEncodeError`。
> 运行 Python 测试前请设置 UTF-8，并用 `-X utf8` 启动：
> ```cmd
> set PYTHONUTF8=1
> set PYTHONIOENCODING=utf-8
> python -X utf8 test\test_stool_llm.py stool_fillet > log\test_stool_fillet.log 2>&1
> ```

> **缓存提示**：修改了 `planner/llm_client.py` 中的 prompt 后，需先清空 LLM 缓存，否则会命中旧计划：
> ```cmd
> python -c "import diskcache; c=diskcache.Cache('planner/.llm_cache'); c.clear(); c.close(); print('缓存已清空')"
> ```

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

REM 四脚板凳完整建模：凳面 200×200×20mm + 4 条圆柱腿（⌀20mm×150mm），总高 170mm
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
REM Prompt: "创建一个四脚板凳：凳面为 200mm×200mm×20mm 的方形平板，在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 150mm），腿的圆心距凳面边缘 30mm。"
python LLMPlanner/test/test_stool_llm.py stool

REM LLM 带圆角的四脚板凳自动建模
REM Prompt: "创建一个四脚板凳并对凳面做圆角处理：……最后对凳面顶部的 4 条边做 R5mm 圆角。"
python LLMPlanner/test/test_stool_llm.py stool_fillet
```

---

## 九、日常物体建模（LLM 自然语言 → 自动规划 → 执行）

实用日常物体的自动建模测试，位于 `test/objects/` 目录，每个物体一个独立脚本，
共享工具在 `test/objects/_common.py`（封装前置检查、LLM 规划+执行、几何验证）。

> 这些脚本用 `from _common import ...`，需**从 `test/objects/` 目录运行**。

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner\test\objects

REM 收纳盒：外形 120×80×50mm，壁厚 8mm，顶部开口内腔 + 顶边 R3mm 圆角
REM （考验：拉伸 + 实体面切除内腔[抽壳替代法] + 圆角）
REM 实测 11/11 步通过：盒体 1 次拉伸 + 顶面切除内腔 1 次 + 顶部 4 边各 R3mm 圆角。
python test_storage_box.py

REM 骰子（简化版）：20mm 正方体 + 所有 12 条棱边 R2mm 圆角（圆润手感）
REM （考验：拉伸 + edgeSelection="all" 全边圆角）
python test_dice.py

REM 骰子（完整标准版 · API 直调，确定性）：20mm 立方体 + 12 棱 R1.5mm 圆角 + 6 面共 21 个点数凹坑
REM 坐标人工算好写死，不经过 LLM；逐刀打印体积变化用于验证（每点≈10.6mm³）。
python test_dice_full.py

REM 骰子（完整标准版 · LLM 端到端）：自然语言 → GPT-4o 规划 → 自动建出标准骰子
REM 仅给规则、不给坐标，全靠 LLM 推算；预期 38 步全过、包围盒 20mm、对面点数和为 7。
python test_dice_llm.py
```

> **`test_storage_box.py` 使用的完整 LLM Prompt**：
> ```text
> 创建一个长方形收纳盒：外部尺寸为长 120mm、宽 80mm、高 50mm，盒壁厚度 8mm，盒子内部是一个开口向上的空腔（顶部敞开），底部厚度也是 8mm。最后对盒子顶部开口的四条边做 R3mm 圆角。
> ```
> 该任务考验 LLM 的多项规划能力：拉伸外形 + 在顶面画内腔轮廓做"抽壳替代法"切除（内腔尺寸=外形−2×壁厚、深度=外形高−底厚）+ 顶部开口四条边各做一次圆角（语义化边名 `top_front/top_back/top_left/top_right` 不可合并）。

> **`test_dice_llm.py` 使用的完整 LLM Prompt（只给规则、不给坐标/步骤，考验 LLM 规划能力）**：
> ```text
> 请帮我建模一颗标准的六面骰子（色子），要求如下：
> 1. 骰子主体是一个边长 20mm 的正方体；
> 2. 正方体的 12 条棱边都做 R1.5mm 的圆角，让边角圆润；
> 3. 六个面上分别有 1、2、3、4、5、6 个圆形点数；每个点数是一个直径约 3mm、深约 1.5mm 的圆形凹坑（向内凹陷，不是凸起）；
> 4. 遵循标准骰子规则：相对的两个面点数之和为 7（即 1 对 6、2 对 5、3 对 4）；
> 5. 各面点数的常规排布：1 点在面中心；2 点和 3 点沿面的一条对角线均匀分布；4 点在面的四个角附近；5 点是四个角加面中心；6 点是两列、每列三个点。
> 请自行计算每个面以及每个凹点在模型空间中的位置，完成完整建模。
> ```
> 该任务考验 LLM 的多项规划能力：居中建模（midPlane）、面内轴 vs 法向轴映射、对面点数和为 7、凹坑切除方向（flipDirection=false）、以及一次性完整枚举约 50 个步骤而不偷懒。
> ⚠️ 复现要点：必须 `temperature=0`（否则长计划时而完整时而截断），且改过 prompt 后要清 LLM 缓存。

> ⚠️ **实体面上画矩形的坑**：在实体面（如盒子顶面 Y=0.05）上用 sketch_rectangle 画内腔时，
> 必须传完整 3D 坐标——y1=y2=面的Y坐标，Z 方向偏移放进 z1/z2（不能放 y）。
> 此规则已写入 system prompt，LLM 现已能正确生成。

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
| `objects/test_storage_box` | LLM 规划 | 收纳盒（内腔 + 圆角） |
| `objects/test_dice` | LLM 规划 | 骰子（简化版：圆角立方体） |
| `objects/test_dice_full` | API 直调 | 标准骰子（确定性：立方体 + 圆角 + 21 凹点） |
| `objects/test_dice_llm` | LLM 规划 | 标准骰子（端到端：自然语言 → 完整 21 凹点骰子） |

> **API 直调**类测试直接构造操作序列调用 C# 服务，不依赖 LLM，可用于验证底层建模能力；
> **LLM 规划**类测试走完整链路（自然语言 → LLM 解析 → 执行），需要配置好 API Key。
