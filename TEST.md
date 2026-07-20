# 测试指南（TEST.md）

本文件整理了 LLMPlanner 的端到端测试用例与运行方式。所有测试均通过 HTTP 调用 C# 服务驱动 SolidWorks 实际建模。

## 🛡️ 强约定（新增自动建模物体的 Definition of Done）

**每加一个自动建模物体，只有以下 5 项**全部**完成，任务才算完成**。缺任何一项都视为半成品，禁止提交：

### 约定 A ｜ 文字建模任务（自然语言 → CAD）

新增一个物体（骰子/板凳/齿轮/...）时必须：

1. **编写测试脚本** `test/objects/test_<name>_llm.py`，跑通端到端 LLM 建模 + 几何断言（包围盒/体积/特征数至少一项自动校验）
2. **执行测试并留存日志**：`> log\test_<name>.log 2>&1`，至少展示末尾"判定"行说明通过
3. **更新 TEST.md**：（a）§十 分类表新增一行；（b）在合适章节写完整 `Prompt:` 原文
4. 如需加到 Web Demo，只提供用户可见的自然语言几何规格；完整操作提示保留在 test/eval，
   禁止把 benchmark 答案同步到生产 Prompt 或 Skill
5. **git commit + push**，commit message 引用测试通过的关键指标

### 约定 B ｜ 三视图多模态样例（图纸 → CAD）

新增一个三视图样例（B04/B05/...）时必须：

1. **改 samples.json 一处** — `datasets/manual_three_view/samples.json` 追加一个对象（含 `profile_xy`/`depth_z`/`holes`/`ui`/`test` 五组字段）
2. **一键生成图纸**：`python datasets\manual_three_view\build_all.py`
3. **清缓存跑端到端**：`python test\test_three_view_e2e.py <part_id> > log\test_3view_<id>.log 2>&1`，末尾"总结"行必须显示"1/1 样例通过"
4. **更新 TEST.md §12**：（a）§12.2 表格添加一行（步数/体积/特征）；（b）§12.3 追加该样例的 `text_hint`
5. **git commit + push**

（Web demo 按钮由 samples.json 自动驱动，`app.js` 会 fetch `/api/three-view-samples` 自动生成，**不需要**手动改前端；只需重启 Flask 加载最新 JSON。这就是"单一数据源架构"的收益。）

> 若第 3 步测试未通过：先看 LLM 计划——如果是形状级问题（画法错），在 `planner/skills/input_modes/three_view.md` §7"阶段 B 特别指引"仿照 L 形/T 形补一段专属规则；如果是尺寸识别错，检查图纸清晰度或补图纸标注。清缓存重跑。完整故障排查 SOP 见 §12.5。

> ⚠️ **对 AI 助手的额外指令**：以上约定就是"完成"的定义。**禁止在测试通过后直接 git commit** 而不更新 TEST.md 与 Web demo；也禁止只跑一次通过就当作稳定（多模态测试有 LLM 随机性，B02/B03 只跑一次就算通过是可以接受的，但要在 commit message 中说明"首次跑通"）。

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

REM 六角螺母（M20 · LLM 端到端）：正六边形外形 + 中心贯穿圆孔
REM （考验：新增的 sketch_polygon API、对边距↔外接圆半径换算、midPlane 居中、贯穿孔）
REM 实测 7/7 步通过：外接圆半径 17.322mm、厚度 8mm、⌀10mm 贯穿孔、体积 5608 mm³ 与理论完全吻合。
python test_hex_nut_llm.py

REM 马克杯（含把手 · LLM 端到端）：圆柱杯体 + 顶面内腔 + 前视基准面上的圆环把手
REM （考验：多次进入草图；实体面切除内腔；同心双圆拉伸自动形成环形凸台；
REM  把手位置需与杯壁"相交而非相切"才能合并成单一实体；加料/切除操作不混淆）
REM 实测 12/12 步通过：包围盒 95×90×70mm、体积 124045 mm³。
python test_mug_llm.py

REM 开口扳手（LLM 端到端）：头部圆盘 + 手柄矩形梁 + 顶面六角螺母卡口贯穿孔
REM （考验：两次 sketch+extrusion 组合合并成 T 形零件（因圆和矩形不能画在同一草图里）；
REM  sketch_polygon 用于「切除」（新场景，之前只用于拉伸）；顶面中心贯穿孔坐标）
REM 实测 10/10 步通过：包围盒 140×8×40mm、体积 20938 mm³ 与理论完全吻合。
python test_wrench_llm.py

REM 花瓶（LLM 端到端）：8 段直线组成的复杂轴截面 + revolve 生成瓶身 + 顶面挖内腔
REM （考验：feature_revolve 与多段直线组合成"底部→收腰→膨大→颈部→瓶口"的花瓶轮廓；
REM  轮廓端点必须落在旋转轴上被自动封闭；旋转后再从顶面挖圆柱形内腔留底厚）
REM 实测 15/15 步通过：包围盒 90×150×90mm、体积 416504 mm³ 与理论完全吻合。
python test_vase_llm.py

REM 钥匙（LLM 端到端）：⌀20mm 圆盘钥匙圈头 + 42mm 矩形杆 + 顶面 10×5mm 长圆钥匙圈孔 + 5 齿锯齿
REM （考验：新增 sketch_slot API；两次拉伸合并成一体；顶面开孔坐标；
REM  钥匙齿——一草图内 5 个独立齿槽 sketch_slot 贴齐杆的一条长边 + 一次贯穿切除形成锯齿）
REM 实测 17/17 步通过：包围盒 59×2×20mm、体积 1096.7 mm³ 与理论完全吻合。
REM ⚠️ sketch_slot 在本地 SW2025 上当前退化为矩形近似（角是直角而非圆角），功能上等价。
python test_key_llm.py

REM 齿轮（LLM 端到端）：⌀60mm 圆盘 + 12 个沿圆周均布的齿槽 + 中心孔
REM （考验：LLM 精确计算 12 个齿槽的 sin/cos 圆周坐标；一次草图画多个独立轮廓；
REM  用 sketch_slot 而非 sketch_rectangle 在实体面上画多轮廓避免坐标混淆）
REM 实测 21/21 步通过：包围盒 60×8×60mm。
python test_gear_llm.py

REM 圆形法兰盘（⌀100×12mm + ⌀30 中心孔 + PCD⌀80 上 6×⌀8 螺栓孔）
python test_flange_llm.py

REM 简化皮带轮（⌀100×20mm + 90/70mm 环槽 + ⌀25 轴孔 + C1 外缘倒角）
python test_pulley_llm.py

REM 三段阶梯轴（⌀35×40 + ⌀50×30 + ⌀25×20，一次旋转成型）
python test_stepped_shaft_llm.py

REM 内六角圆柱头螺栓（⌀10×50 杆 + ⌀18×6 头 + 8mm 对边内六角 + R0.5）
python test_socket_bolt_llm.py
```

> **`test_storage_box.py` 使用的完整 LLM Prompt**：
> ```text
> 创建一个长方形收纳盒：外部尺寸为长 120mm、宽 80mm、高 50mm，盒壁厚度 8mm，盒子内部是一个开口向上的空腔（顶部敞开），底部厚度也是 8mm。最后对盒子顶部开口的四条边做 R3mm 圆角。
> ```
>
> **`test_storage_box.py` 验证点**：脚本基于 `_common.py` 的 `run_object_test()` 通用流程，依次校验以下几项：
> 1. **前置检查**：`OPENAI_API_KEY` 已设置 + C# 服务（`localhost:5100`）能 ping 通；
> 2. **LLM 调用成功**：`SwLLMClient.plan()` 返回合法 `SwModelingPlan`（Structured Outputs 模式）；
> 3. **计划结构完整性**：步骤中必须出现 `new_part`、`feature_extrusion`、`feature_cut_extrusion` 三种操作（`required_ops`）；
> 4. **执行成功率**：调用 C# HTTP API 逐步执行，要求**全部步骤** `success=true`，否则判定失败；
> 5. **几何包围盒**：调用 `/api/body-bounding-box` 取实体包围盒，要求 X=120、Y=50、Z=80 mm，每轴容差 ±20mm（脚本判定的**最终判据**）；
> 6. **特征计数**（仅告警不影响判定）：特征树里 `Extrusion ≥ 1`、`Cut ≥ 1`，未达只输出 `⚠️` 提示。
>
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
> **`test_dice_llm.py` 验证点**：脚本自带独立的 `main()` 流程（不走 `run_object_test`），依次校验以下几项：
> 1. **前置检查**：`OPENAI_API_KEY` 已设置 + C# 服务（`localhost:5100`）能 ping 通；
> 2. **LLM 计划生成**：`SwLLMClient.plan(PROMPT)` 返回合法 `SwModelingPlan`，并打印**操作类型统计**（如 `feature_cut_extrusion`、`feature_fillet`、`sketch_circle` 各多少次），用于直观判断 LLM 是否覆盖到 6 个面的 21 个凹点；
> 3. **执行成功率（强判据）**：`results` 中**每一步**都 `success=true`，否则失败；
> 4. **包围盒**：调用 `/api/body-bounding-box`，要求 X/Y/Z 三轴尺寸都 ≈ 20mm（容差 ±3mm，比收纳盒更严）；
> 5. **体积**：调用 `/api/body-volume` 取实体体积，参考理论值 ≈ 7600~7900 mm³（原始立方体 8000 mm³ 减去 12 棱圆角切削 + 21 个凹点的损耗）；体积仅打印参考，不参与硬判定；
> 6. **特征数量**：`feature_cut_extrusion ≥ 1`（实测应为 6，每面 1 次）+ `feature_fillet`（实测应为 1，`edgeSelection="all"` 一次圆角全部 12 条棱），打印 `切除步数 / 圆角步数` 供观察。
>
> 综合判据：`全步骤成功 AND 包围盒≈20 AND 切除步数≥1` 三者同时满足才打印 `🎉 LLM 自动建骰子通过！`。
>
> 该任务考验 LLM 的多项规划能力：居中建模（midPlane）、面内轴 vs 法向轴映射、对面点数和为 7、凹坑切除方向（flipDirection=false）、以及一次性完整枚举约 50 个步骤而不偷懒。
> ⚠️ 复现要点：必须 `temperature=0`（否则长计划时而完整时而截断），且改过 prompt 后要清 LLM 缓存。

> ⚠️ **实体面上画矩形的坑**：在实体面（如盒子顶面 Y=0.05）上用 sketch_rectangle 画内腔时，
> 必须传完整 3D 坐标——y1=y2=面的Y坐标，Z 方向偏移放进 z1/z2（不能放 y）。
> 此规则已写入 system prompt，LLM 现已能正确生成。

> **`test_hex_nut_llm.py` 使用的完整 LLM Prompt**：
> ```text
> 创建一个 M20 六角螺母：正六边形外形（对边距 30mm，即两条平行边之间的距离为 30mm），厚度 8mm，中心有一个直径 10mm 的贯穿圆孔。螺母沿厚度方向以原点居中。
> ```
>
> **`test_hex_nut_llm.py` 验证点**：脚本自带独立的 `main()` 流程，依次校验：
> 1. **前置检查**：`OPENAI_API_KEY` 已设置 + C# 服务能 ping 通；
> 2. **LLM 计划生成 + 操作统计打印**（如 `sketch_polygon` / `feature_cut_extrusion` 各多少次）；
> 3. **执行成功率（强判据）**：全部步骤 `success=true`；
> 4. **`sketch_polygon` 步数 ≥ 1**（考验 LLM 是否用了新的多边形 API，而非试图用 6 段 sketch_line 拼六边形）；
> 5. **`feature_cut_extrusion` 步数 ≥ 1**（中心贯穿孔）；
> 6. **厚度 ≈ 8mm**（容差 1mm，三轴中最小的一轴）；
> 7. **六边形外接尺寸吻合**：其余两轴一个 ≈ 30mm（对边距）、一个 ≈ 34.64mm（顶点距 = 2R），容差 ±3mm；
> 8. **体积 ∈ [4500, 6500] mm³**（理论 ≈5607 = 六边形面积×厚 − 圆孔体积）。
>
> 综合判据：以上 5、6、7、8 全部满足 + 全步骤成功 才打印 `🎉 LLM 自动建六角螺母通过！`
>
> 该任务考验 LLM：正确使用新的 `sketch_polygon` 操作 + 完成"对边距 → 外接圆半径"的三角换算（六边形：外接圆半径 = 对边距 / √3 ≈ 对边距 × 0.5774）+ 用 midPlane 让螺母沿厚度方向居中。

> **`test_mug_llm.py` 使用的完整 LLM Prompt**：
> ```text
> 请建模一个马克杯，**严格按以下顺序**执行，因为顺序会影响最终形状是否干净：
> 1. 先创建杯体：用上视基准面画外径 70mm 的圆，向上拉伸 90mm，形成实心圆柱；
> 2. 接着**加**把手（**必须在挖空腔之前加**，否则把手内嵌部分会伸入未来的空腔里，在杯内壁形成难看的圆环凸起）：在前视基准面上画两个同心圆（外径 40mm、内径 24mm，即把手条粗 8mm），然后**用 feature_extrusion（加料操作，不是 feature_cut_extrusion！）**沿前后（Z）方向 midPlane 拉伸 8mm 厚度，把这个环形轮廓变成实心的把手（外圆做外形、内圆做中间的空洞）。
>    把手位置要求（**必须与杯壁相交合并**，不能只是相切）：
>    - 同心圆中心 X 应把内圆左沿嵌入杯壁至少 2mm。杯外壁在 X=35mm，取把手中心 X≈48mm，则内圆左沿到 X=48−12=36mm，勉强嵌入 1mm——不够；
>    - 正确做法：把手中心取 X=48mm 时最外沿 X=68mm。但为了确保内圆稳固地嵌入实体杯壁至少 5mm，建议把手中心 X 取 40mm（内圆左沿到 X=28mm，深入实心杯身 7mm），此时把手最外沿到 X=60mm；
>    - 把手垂直方向 Y 位置居中于杯身中部，即 Y=45mm；
> 3. 最后再挖空腔：从杯体顶面向下挖，用射线选顶面，画一个直径 60mm 的圆（即壁厚 5mm），切除 80mm 深（即底厚 10mm），顶部敞开、底部封闭。此步会同时把嵌入杯身的把手内圆那段一起挖掉，使杯内壁保持光滑。
> 4. 杯子沿 Y 方向从 0 延伸到 90mm（不居中）。
> 所有尺寸使用米制，注意换算（1mm = 0.001m）。
> ```
>
> **`test_mug_llm.py` 验证点**：
> 1. 前置检查 + LLM 计划生成 + 操作统计；
> 2. **执行成功率（强判据）**：全部步骤 `success=true`；
> 3. **`sketch_circle` 步数 ≥ 3**（1 杯体外圆 + 1 内腔圆 + 2 把手同心圆 = 4）；
> 4. **`feature_extrusion` 步数 ≥ 2**（1 次杯体拉伸 + 1 次把手拉伸；⚠️ 常见错误是把把手拉伸误写成 cut_extrusion）；
> 5. **`feature_cut_extrusion` 步数 ≥ 1**（内腔）；
> 6. **`sizeY ≈ 90mm`**（杯高，容差 3mm）；
> 7. **`sizeZ ≈ 70mm`**（杯身直径方向，容差 3mm）；
> 8. **把手合并判据 `sizeX > 90mm` 且 `maxX > 50mm`**（说明把手向外突出成功，且已与杯身合并成单一实体）；
> 9. **体积 ∈ [100k, 145k] mm³**（理论 ≈126000 = 外圆柱 346361 − 内腔 226195 + 把手圆环 6434）。
>
> 综合判据：3~9 全部满足 + 全步骤成功 才打印 `🎉 LLM 自动建马克杯通过！`
>
> 该任务考验 LLM：多次切换草图平面（基准面/实体面/另一基准面）+ 同心双圆拉伸自动形成环形凸台 + 加料（`feature_extrusion`）与切除（`feature_cut_extrusion`）不能混淆 + 把手位置需**内嵌**而非相切（否则不合并、把手成为孤立实体）。

> **`test_wrench_llm.py` 使用的完整 LLM Prompt**：
> ```text
> 请建模一把开口扳手（板手），由头部圆盘、手柄矩形梁、以及头部中心的六角螺母卡口组成：
> 1. 扳手整体沿厚度方向（Y 轴）以原点居中，厚度 8mm；
> 2. **头部**是一个圆盘：外径 40mm，圆心在原点。用上视基准面画圆，midPlane 拉伸 8mm；
> 3. **手柄**是一个矩形梁：长 120mm（沿 +X 方向），宽 15mm（沿 Z 方向居中），厚度同样 8mm。手柄末端**必须嵌入头部圆盘内 5mm 以确保合并**成单一实体——即矩形沿 X 方向从 X=15mm（圆盘半径 20mm 减 5mm）延伸到 X=120mm，沿 Z 从 -7.5mm 到 +7.5mm。需要**用第二次 select_plane（上视面）+ sketch_rectangle + feature_extrusion**（不能和头部画在同一个草图里，因为 SolidWorks 不允许一个草图里画圆和矩形两个相交但不嵌套的轮廓）；
> 4. **六角卡口**：在头部顶面用 select_face_and_sketch 选顶面，用 sketch_polygon 画一个**正六边形**（对边距 13mm、尖朝上），然后 feature_cut_extrusion 贯穿切除，形成螺母卡口。对边距 13mm 对应外接圆半径 = 13 / √3 ≈ 7.51mm；
> 5. 六边形中心应对齐头部圆盘中心：cx=0、cz=0（面内两个方向对齐），cy=顶面 Y 坐标 (+0.004m)。
> 所有尺寸使用米制（1mm = 0.001m）。
> ```
>
> **`test_wrench_llm.py` 验证点**：
> 1. 前置检查 + LLM 计划生成 + 操作统计；
> 2. **执行成功率（强判据）**：全部步骤 `success=true`；
> 3. **`feature_extrusion` 步数 ≥ 2**（头部拉伸 + 手柄拉伸，不能合并成一次因为圆+矩形不能同草图）；
> 4. **`sketch_polygon` 步数 ≥ 1**（六角卡口，验证 polygon 用于「切除」的新场景）；
> 5. **`feature_cut_extrusion` 步数 ≥ 1**（贯穿六角孔）；
> 6. **`sizeY ≈ 8mm`**（厚度，容差 1mm）；
> 7. **`sizeX ≈ 140mm`**（扳手总长=头部半径 20 + 手柄 120，容差 ±8mm）；
> 8. **`sizeZ ≈ 40mm`**（头部圆盘直径决定，容差 ±3mm）；
> 9. **`minX < -15 且 maxX > 100`**（头部与手柄成功合并为一整块，头部延伸到 -X 侧、手柄延伸到 +X 侧）；
> 10. **体积 ∈ [17k, 24k] mm³**（理论 ≈20880 = 圆盘 + 手柄 − 重叠 − 六角孔）。
>
> 综合判据：3~10 全部满足 + 全步骤成功 才打印 `🎉 LLM 自动建扳手通过！`
>
> 该任务考验 LLM：**一个草图只能画一个连续闭合轮廓**（圆和矩形相交但不嵌套时必须分两次拉伸）+ `sketch_polygon` 用于**切除**场景（之前只在拉伸场景用过）+ 顶面中心贯穿孔的正确坐标（cx=cz=0 面内对齐，cy=顶面Y坐标）。

> **`test_vase_llm.py` 使用的完整 LLM Prompt**（含精确的 8 段直线坐标，考验 LLM 按指令严格绘制复杂轴截面）：
> ```text
> 请建模一个花瓶：轴对称形状（用旋转特征生成），底部平坦、瓶身中部略膨大、瓶颈收窄、瓶口再略外扩。
> 在**前视基准面**（select_plane {index:0}）上画一个位于旋转轴右侧的**半截面轮廓**并旋转 360°：
> 1. 首先画中心线（旋转轴）：从 (x=0, y=0) 到 (x=0, y=0.15)，沿 Y 轴向上；
> 2. 然后**按顺序依次画 8 段直线**首尾相连组成花瓶的右半轮廓（所有坐标单位：米）：
>    ① sketch_line (0, 0) → (0.03, 0)      ← 底面（旋转轴到底盘外沿）
>    ② sketch_line (0.03, 0) → (0.03, 0.01)  ← 底盘垂直段
>    ③ sketch_line (0.03, 0.01) → (0.045, 0.04)  ← 斜向外扩到最粗处
>    ④ sketch_line (0.045, 0.04) → (0.045, 0.07)  ← 中部最粗处垂直段
>    ⑤ sketch_line (0.045, 0.07) → (0.02, 0.1)   ← 收腰到瓶颈
>    ⑥ sketch_line (0.02, 0.1) → (0.0275, 0.13)  ← 瓶颈外扩形成瓶口
>    ⑦ sketch_line (0.0275, 0.13) → (0.0275, 0.15) ← 瓶口垂直段
>    ⑧ sketch_line (0.0275, 0.15) → (0, 0.15)   ← 顶面（回到旋转轴）
> 3. 调用 feature_revolve {angle: 360}，形成实心花瓶（此时**不需要**画额外直线封闭轮廓，轮廓两端都在旋转轴上，SolidWorks 会用旋转轴自动封闭）；
> 4. 花瓶应有内腔（空心）：**旋转完成后**用 select_face_and_sketch 选中顶面（射线起点 ry=0.2 大于瓶高 0.15，方向 dy=-1），在顶面上画一个直径 36mm 的圆（cx=0, cy=0.15, cz=0, radius=0.018），然后 feature_cut_extrusion 盲孔切除 140mm 深度（endCondition='blind'），留下 10mm 底厚（花瓶高 150mm，切 140mm 后剩底部 10mm）。
> 所有尺寸均为米制。整个花瓶从底部 Y=0 延伸到顶部 Y=0.15。
> ```
>
> **`test_vase_llm.py` 验证点**：
> 1. 前置检查 + LLM 计划生成 + 操作统计；
> 2. **执行成功率（强判据）**：全部步骤 `success=true`（预期约 15 步）；
> 3. **`feature_revolve` 步数 ≥ 1**（生成花瓶外形）；
> 4. **`sketch_centerline` 步数 ≥ 1**（旋转轴必须先画）；
> 5. **`sketch_line` 步数 ≥ 6**（至少 6 段直线组成瓶身轮廓，预期 8 段）；
> 6. **`feature_cut_extrusion` 步数 ≥ 1**（挖内腔）；
> 7. **高度 ≈ 150mm**（`sizeY`，容差 5mm）；
> 8. **直径 ≈ 90mm**（`sizeX`、`sizeZ` 都在 [85, 95] mm，即瓶身最粗处的直径）；
> 9. **内腔挖空验证 `300k < 体积 < 500k mm³`**（实心花瓶 ≈559k，挖空后 ≈416k，如果没挖或体积远小则失败）。
>
> 综合判据：3~9 全部满足 + 全步骤成功 才打印 `🎉 LLM 自动建花瓶通过！`
>
> 该任务考验 LLM：**用多段直线精确按顺序**画复杂轴截面（8 段直线组成"底部→收腰→膨大→颈部→瓶口"的曲线）+ 理解"轮廓端点必须落在旋转轴上以便被自动封闭"+ `feature_revolve` 生成实体后再切除内腔的顺序。

> **`test_key_llm.py` 使用的完整 LLM Prompt**：
> ```text
> 请建模一把小型钥匙，包含钥匙圈头（带圆孔）、钥匙杆、钥匙齿三部分：
> 1. 整体沿 Y 轴（厚度方向）以原点为中心居中，厚度 2mm；
> 2. 钥匙圈头：⌀20mm 圆盘（上视基准面 + sketch_circle + feature_extrusion depth=0.002 midPlane）；
> 3. 钥匙杆：42mm×8mm 矩形梁（X∈[0.007, 0.049], Z∈[-0.004, +0.004]），第二次 select_plane(1) + sketch_rectangle + feature_extrusion，末端嵌入圈头 3mm；
> 4. 钥匙圈孔：select_face_and_sketch 选圈头顶面 + sketch_slot(cx=0, cy=0.001, cz=0, length=0.01, width=0.005) + feature_cut_extrusion 贯穿；
> 5. **钥匙齿**：再一次 select_face_and_sketch 选钥匙杆顶面（射线起点 rx=0.03, ry=0.01, rz=0, 方向 dy=-1），在同一草图内画 5 个 sketch_slot 齿槽：cx=[0.017, 0.023, 0.029, 0.035, 0.041]、cy=0.001、cz=0.003（贴齐杆 +Z 长边）、length=0.003、width=0.002，一次 feature_cut_extrusion 贯穿形成锯齿。
> ```
>
> **`test_key_llm.py` 验证点**：
> 1. 前置检查 + LLM 计划生成；
> 2. **全部步骤成功**（预期约 17 步：1 圆盘草图 + 1 拉伸 + 1 选面 + 1 矩形 + 1 拉伸 + 1 选面 + 1 圈孔 slot + 1 切除 + 1 选面 + 5 齿槽 slot + 1 切除 = 17）；
> 3. **`sketch_slot` 步数 ≥ 6**（1 圈孔 + 5 齿槽）；
> 4. **`feature_cut_extrusion` 步数 ≥ 2**（切圈孔 + 切齿槽）；
> 5. **包围盒 59×2×20mm**（容差 ±4mm）——sizeZ=20mm 是圆盘直径决定；
> 6. **体积 ≈ 1096.7 mm³**（圆盘 π·10²·2 − 圈孔 π·(5×2.5)·2 ≈ 圆盘净值 + 杆 42×8×2 = 672 − 齿槽 5×(3×2×2)=60，实测精确吻合）。
>
> 该任务考验：新增 `sketch_slot` API 用于顶面切除、两次拉伸合并成一体（圆+矩形不能同草图）、顶面开孔的正确坐标、**在钥匙杆顶面一草图内画 5 个独立齿槽轮廓 + 一次贯穿切除形成锯齿边（这是钥匙的标志性特征）**。

> **`test_gear_llm.py` 使用的完整 LLM Prompt**：
> ```text
> 请建模一个简化的圆柱齿轮：
> 1. 齿轮主体：⌀60mm 圆盘，厚 8mm，用上视基准面画外圆后 midPlane 拉伸；
> 2. **齿槽（12 个，沿圆周均布）**：在圆盘顶面用 select_face_and_sketch 选顶面，然后**在同一草图**内画 12 个 sketch_slot（各自独立、不相交、必须完全在圆盘内）。第 k 个齿槽的中心角 θ_k=2π·k/12，中心距原点 25mm（比圆盘半径 30mm 内缩 5mm），中心坐标 (cx,cz)=(0.025·cos(θ_k), 0.025·sin(θ_k))。每个齿槽用 sketch_slot {cx: cx_k, cy: 0.004, cz: cz_k, length: 0.003, width: 0.002}；
> 3. 画完 12 个齿槽后，一次 feature_cut_extrusion 贯穿切除；
> 4. **中心孔**：再用 select_face_and_sketch 选顶面 + sketch_circle {cx:0, cy:0.004, cz:0, radius:0.006} + feature_cut_extrusion 贯穿。
> ```
>
> **`test_gear_llm.py` 验证点**：
> 1. 前置检查 + LLM 计划生成；
> 2. **全部步骤成功**（预期约 21 步：1 圆盘草图+1 拉伸+1 选面+12 齿槽+1 切除+1 选面+1 中心圆+1 切除+…）；
> 3. **`sketch_slot` 步数 ≥ 12**（12 个齿槽用长圆轮廓）；
> 4. **`feature_extrusion` 步数 ≥ 1、`feature_cut_extrusion` 步数 ≥ 2**（1 次切齿槽 + 1 次切中心孔）；
> 5. **包围盒 60×8×60mm**（容差 ±4mm）——外径 60mm 决定 X/Z。
>
> 该任务考验：LLM 精确计算 12 个圆周分布点的三角函数坐标 + **一次草图画多个完全独立的封闭轮廓**（SolidWorks 允许多轮廓拉伸/切除，但轮廓必须不相交或完全嵌套）+ 在实体面上用 sketch_slot 而非 sketch_rectangle（后者 y1/y2 会被误解为模型 Y）。**注意**：本项目也提供了 `feature_circular_pattern` API，但本地 SW2025 上几何生效有问题，齿轮改用手工画 N 份而不是阵列，更可靠。

### 9.1 `dev/yrq` 合入的复杂物件 Prompt

以下记录 4 个测例的完整建模语义；测试脚本与 Web demo 使用更详细、逐步骤的同义 Prompt。完整逐字版及几何判据见 `object_test_yrq.md`。

**圆形法兰盘：**
```text
请建模一个圆形法兰盘，严格按以下顺序执行：先在上视基准面画直径 100mm 的圆，沿厚度方向居中拉伸 12mm；在盘顶面中心打直径 30mm 的贯穿孔；最后在节圆直径 80mm 上均布 6 个直径 8mm 的贯穿螺栓孔，相邻孔夹角 60°，第一个孔在正 X 轴上。中心孔打通后，后续选面射线必须落在环形实体区域，不能再从圆心选面。请自行计算其余 5 个孔心坐标。所有尺寸使用米制。
```

**简化 V 型槽皮带轮：**
```text
请建模一个简化的 V 型槽皮带轮：先在上视基准面画直径 100mm 的圆并向上拉伸 20mm；在 Y=20mm 顶面画直径 90mm 和 70mm 的两个同心圆，向下盲切 10mm 形成平底环形槽；再在中心打直径 25mm 的贯穿孔；最后仅对轮缘顶部外圆边做 C1mm 等距倒角，不要对所有边倒角。轮体底面位于 Y=0、顶面位于 Y=20mm。
```

**三段阶梯轴：**
```text
请用旋转法创建一根总长 90mm、以原点居中的三段阶梯轴。在前视基准面以 Y 轴为旋转轴，轮廓全部位于 X≥0：最下段 ⌀35mm、高 40mm（Y=-45~-5）；中段 ⌀50mm、高 30mm（Y=-5~+25）；最上段 ⌀25mm、高 20mm（Y=+25~+45）。画中心线 (0,-45)→(0,+45)，再依次画 7 段闭合半剖轮廓：(0,-45)→(17.5,-45)→(17.5,-5)→(25,-5)→(25,25)→(12.5,25)→(12.5,45)→(0,45)，最后一次旋转 360°。不要使用三次圆柱拉伸，也不要沿中心线额外画轮廓线。
```

**内六角圆柱头螺栓：**
```text
请建模一颗无螺纹的内六角圆柱头螺栓：在上视基准面创建 ⌀10mm、长 50mm 的螺杆（Y=0~50）；在螺杆顶面加料拉伸 ⌀18mm、高 6mm 的圆柱头（Y=50~56）；从头部顶面中心向下盲挖 4mm 深的正六边形内六角，对边距 8mm、外接圆半径 8/√3≈4.619mm；最后仅对 Y=50mm 的头杆交界圆边做 R0.5mm 圆角。圆柱台阶圆边必须用 edgeSelection="ray" 并提供六个射线参数，禁止使用 bottom_top 等方盒语义边名。
```

合入 main 后使用 `basic/claude-sonnet-5`、`skill_mode="routed"` 的真实 SolidWorks 回归结果：

- 法兰盘：15/15 步，100×100×12mm，82146.4mm³，6 个螺栓孔孔心距均为 40.0mm。
- 皮带轮：12/12 步，100×100×20mm，121973.4mm³，环槽盲切 10mm、轴孔贯穿、C1 ray 倒角成功。
- 三段阶梯轴：11/11 步，50×50×90mm，107206.8mm³，7 段轮廓一次旋转成功。
- 内六角螺栓：11/11 步，18×18×56mm，5229.1mm³，内六角 4.619mm 外接圆和 R0.5 ray 圆角成功。

日志：`LLMPlanner/log/test_{flange,pulley,stepped_shaft,socket_bolt}_merge.log`（本地留存，gitignore 排除）。

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
| `objects/test_hex_nut_llm` | LLM 规划 | M20 六角螺母（正六边形 + 中心贯穿孔，验证新增 `sketch_polygon` API） |
| `objects/test_mug_llm` | LLM 规划 | 马克杯（圆柱杯体 + 顶面内腔 + 圆环形把手，考验加料/切除辨析） |
| `objects/test_wrench_llm` | LLM 规划 | 开口扳手（圆盘头 + 矩形手柄 + 六角卡口贯穿孔，考验多次拉伸合并 + polygon 切除） |
| `objects/test_vase_llm` | LLM 规划 | 花瓶（8 段直线组成的复杂轴截面 revolve + 顶面挖内腔） |
| `objects/test_key_llm` | LLM 规划 | 钥匙（圆盘头 + 矩形杆 + 长圆钥匙圈孔 + 5 齿锯齿边，验证新增 `sketch_slot` API 及多轮廓齿槽切除） |
| `objects/test_gear_llm` | LLM 规划 | 齿轮（圆盘 + 12 齿槽沿圆周分布 + 中心孔，考验 LLM 三角函数计算 + 一草图多轮廓） |
| `objects/test_flange_llm` | LLM 规划 | 圆形法兰盘（中心孔 + PCD 上 6 个螺栓孔，验证环形实体区域选面与极坐标换算） |
| `objects/test_pulley_llm` | LLM 规划 | 皮带轮（同心双圆环槽 + 中心孔 + 圆柱外缘 ray 倒角） |
| `objects/test_stepped_shaft_llm` | LLM 规划 | 三段阶梯轴（7 段半剖轮廓一次旋转，验证复杂轴对称轮廓） |
| `objects/test_socket_bolt_llm` | LLM 规划 | 内六角圆柱头螺栓（两次加料 + 六角盲孔 + 圆柱台阶 ray 圆角） |
| `test_skill_regression` | Skill 架构 | Skill 动态加载零回归验证（9 项：registry 加载 / SYSTEM_PROMPT 包含 core / full 模式注入所有 input_modes / cache_key 稳定 / routed 模式命中规则 / _build_user_message 图文格式 / stage_b_prompt 拼装 …） |
| `test_three_view_e2e B01` | 多模态 LLM | 三视图端到端 · B01 L 型支架（60×40×20mm，含右上角 30×20 缺口） |
| `test_three_view_e2e B02` | 多模态 LLM | 三视图端到端 · B02 带 2 孔平板（80×40×10mm + 2×⌀10 通孔） |
| `test_three_view_e2e B03` | 多模态 LLM | 三视图端到端 · B03 T 型支架（60×45×15mm，T 形双凹角复杂轮廓） |
| `test_three_view_e2e B04` | 多模态 LLM | 三视图端到端 · B04 键盘键帽（底 18×18/顶 14×14/高 8mm，**拔模拉伸首例**） |
| `test_three_view_e2e B07` | 多模态 LLM | 三视图端到端 · B07 86 型开关面板（86×86×10mm + 中央凹槽 + 4 贯穿孔 + 4 竖直棱边圆角，**复合特征首例**） |
| `test_three_view_e2e B06` | 多模态 LLM | 三视图端到端 · B06 门锁扣板（30×80×3mm + 中央腰形孔 + 2 螺丝孔 + 2 长边圆角，**首个含 slot 腰形孔样例**） |
| `test_three_view_e2e B05` | 多模态 LLM | 三视图端到端 · B05 完整版键帽（底 18×18/顶 14×14/高 8mm + 底面凹槽 + 4 顶面圆角，**首个三能力融合样例**） |
| `test_three_view_e2e B11` | 多模态 LLM | 三视图端到端 · B11 齿轮盘（⌀60×10mm + 中心 ⌀15 孔 + 12 齿孔圆周阵列，**圆周阵列首例**） |
| `test_three_view_e2e B22` | 多模态 LLM | 三视图端到端 · B22 圆碗（⌀80×40mm + 顶面 ⌀68×34 深腔，**容器类首例**，用 Shell 替代法） |
| `test_three_view_e2e C1` | 多模态 LLM | 三视图端到端 · C1 迷你机械键盘（285×95×20mm + 65 键 5×13 二维线性阵列，**LinearPattern 2D 首例**，用 basic/claude-sonnet-5） |
| `test_three_view_e2e C2` | 多模态 LLM | 三视图端到端 · C2 齿轮减速器外壳（200×120×100mm 三层复合：法兰+箱体+内腔，前后 ⌀25 轴承通孔 through_all 一次贯穿，法兰 4 安装孔 LinearPattern 2×2，**首个三层复合 + 双向面选择 + 贯穿两平行面**） |
| `test_three_view_e2e C3` | 多模态 LLM | 三视图端到端 · C3 齿轮啮合组（10 齿小齿轮 ⌀40 + 20 齿大齿轮 ⌀80, 中心距 70mm, 减速比 2:1，**首个多实体零件 + 首个真"减速器"传动组**） |
| `assembly_smoke/test_motion_study` | 装配体 Motion Study | 旋转马达驱动小齿轮，大齿轮通过 Gear Mate 按 2:1 联动；验证 timebar 与两组件 Transform 均变化 |

> **API 直调**类测试直接构造操作序列调用 C# 服务，不依赖 LLM，可用于验证底层建模能力；
> **LLM 规划**类测试走完整链路（自然语言 → LLM 解析 → 执行），需要配置好 API Key；
> **Skill 架构**类测试验证文件驱动的 Skill 动态加载（Prompt 优雅化）零回归；
> **多模态 LLM** 类测试走两阶段流水线（图片+文字 → Vision IR → 建模 Plan → 执行）。

---

## 十一、Skill 架构零回归测试

400 行硬编码 `SYSTEM_PROMPT` 已重构为 `planner/skills/{core,recipes,input_modes}/*.md`（文件驱动，加物件 = 加一个 md）。该测试验证：

- Registry 正确加载 5 个 core skill（header / operations / units / rules / editing）
- 全局 `SYSTEM_PROMPT`（`skill_mode=full`）包含所有 core skill 正文（保证行为等价）
- `skill_mode=routed`：text 输入不误注入 three_view skill；three_view 输入正确注入 skill
- Cache key 计算稳定（相同输入 → 相同 key）
- 多模态入口 `_build_user_message` 在图文/纯文场景返回正确的 OpenAI messages 结构
- `_compose_stage_b_prompt` 正确拼装 IR + 用户文字 + 阶段 B 指引

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner
venv\Scripts\python.exe -X utf8 test\test_skill_regression.py
```

实测：**9/9 项全部通过**，秒级完成（不调 API、不建模）。

> 若想额外做真实 LLM 层的零回归验证，跑一个已知能建成功的 LLM 测试（如 `test_hex_nut_llm`）——实测重构后依然 7/7 步通过，体积 5606.7 mm³ 与理论 5607 精确吻合。

---

## 十二、多模态三视图建模（第一期）

Web 界面点击 **📎** 按钮上传三视图 PNG（或直接拖入），后端两阶段流水线：Vision 看图 → `ThreeViewIR` 结构化 IR → 建模 `SwModelingPlan` → SolidWorks 建模。

### 12.1 单一数据源架构

所有三视图样例的 **几何定义、UI 元数据、Vision prompt** 三合一集中在：

```
LLMPlanner/datasets/manual_three_view/samples.json
```

该 JSON 被三处共用（改一处三处同步）：

| 消费方 | 用到的字段 |
|---|---|
| `datasets/manual_three_view/build_all.py`（生成 PNG + groundtruth） | `profile_xy` / `depth_z` / `holes` / `name` / `notes` |
| `test/test_three_view_e2e.py`（端到端 LLM 测试） | `part_id` / `subdir` / `test.text_hint` |
| Web demo `/api/three-view-samples` → `app.js` 欢迎区按钮 | `part_id` / `subdir` / `ui.label` / `ui.prompt` |

### 12.2 现有样例（12 个，全部端到端通过）

| ID | 名称 | 尺寸 | 特征 | 步数 | 体积（mm³） |
|---|---|---|---|---|---|
| **B01** | L 型支架 | 60×40×20 mm | 右上角切 30×20 缺口 | 9/9 | 35999.999 |
| **B02** | 带 2 孔平板 | 80×40×10 mm | 2 × ⌀10 通孔（中心距边 20mm） | 8/8 | 30429.20 |
| **B03** | T 型支架 | 60×45×15 mm | T 形（横杆 60×15 + 竖杆 20×30 居中） | 11/11 | 22500 |
| **B04** | 键盘键帽（教学版） | 底 18×18、顶 14×14、高 8 mm | **拔模拉伸**（内收 14.04°），首个变截面样例 | 4/4 | 2058.53 |
| **B07** | 86 型开关面板 | 86×86×10 mm | 中央 50×50×3 凹槽 + 4 角 ⌀3 贯穿孔 + 4 竖直棱边 R3 圆角，**首个复合特征样例** | 17/17 | 65597.67 |
| **B06** | 门锁扣板 | 30×80×3 mm | 中央 20×10 腰形孔贯穿 + 两端 (0,±32) ⌀5 螺丝孔贯穿 + 顶面前后长边 R3 圆角，**首个含 slot 腰形孔样例** | 13/13 | 6173.16 |
| **B05** | 完整版键盘键帽 | 底 18×18/顶 14×14/高 8 mm | 拔模拉伸 14.04° + 底面 12×12×3 键轴凹槽 + 顶面周边 4 条边 R1.5 圆角，**首个三能力融合样例**（拔模+面上凹槽+圆角） | 11/11 | 1598.59 |
| **B11** | 齿轮盘 | ⌀60×10 mm | ⌀15 中心贯穿孔 + 12 个 ⌀4 齿孔沿 PCD⌀48 均布，**圆周阵列首例**（修复了 feature_circular_pattern 死特征 bug） | 11/11 | 24999.22 |
| **B22** | 圆碗 | ⌀80×40 mm | 顶面挖 ⌀68×34mm 深腔形成碗形（壁厚 6mm 底厚 6mm），**首个容器类样例**（用 Shell 替代法，即顶面挖深孔，因 C# 服务未实现 FeatureShell） | 7/7 | 77584.77 |
| **C1** | 迷你机械键盘 | 285×95×20 mm | 285×95×15 底座 + 5×13=**65 键**方形拔模凸起(15×15×5mm, 拔模 11.31°)一步 LinearPattern 阵列生成，**首个二维线性阵列样例 + 首次使用 basic/claude-sonnet-5** | 8/8 | 469933.28 |
| **C2** | 齿轮减速器外壳 | 200×120×100 mm | 200×120×20 法兰底座 + 上部 160×100×80 中空箱体(内腔 144×84×74, 壁厚 8mm 底厚 6mm) + 前后 ⌀25 轴承通孔(沿 Y 贯穿, 一次 through_all 打通前后壁) + 法兰 4 角 ⌀8 安装孔(LinearPattern 2×2 阵列), **首个三层复合特征 + 双向面选择(+Z 顶面挖内腔 + +Y 前面挖侧孔) + 首次 through_all 一次贯穿两个平行面** | 19/19 | 853020.78 |
| **C3** | 齿轮啮合组 | 138×88×15 mm | 小齿轮 ⌀40+10 齿(中心 X=-35) + 大齿轮 ⌀80+20 齿(中心 X=+35), 中心距 **70mm**(齿顶留 2mm 视觉间隙, 避免矩形齿穿模), 减速比 2:1, 矩形齿高 4mm 齿宽 5mm, 两个齿轮依次完成圆盘拉伸、种子齿拉伸和 circular_pattern 阵列, **首个多实体零件(同一 sldprt 里两个不相连实体) + 首个真"减速器"传动组**（暂无轴孔, 因齿轮阵列后特征树面数过多导致选面超时；本简化版为实体齿轮） | 15/15 | 103247.78 |

### 12.3 从零复现全部三视图端到端验证

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

REM 【步骤 1】从 samples.json 一键批量生成所有三视图 PNG + groundtruth.json
venv\Scripts\python.exe -X utf8 datasets\manual_three_view\build_all.py

REM 【步骤 2】清 LLM 缓存（首次跑或改过 skill 后必须）
venv\Scripts\python.exe -X utf8 -c "import diskcache; c=diskcache.Cache('planner/.llm_cache'); c.clear(); c.close()"

REM 【步骤 3】端到端 LLM 建模（可指定单个或多个 part_id；不带参数默认只跑 B01）
venv\Scripts\python.exe -X utf8 test\test_three_view_e2e.py B01 B02 B03 > log\test_3view_all.log 2>&1
```

**每个样例的 LLM prompt**（阶段 A 用户文字，与 samples.json `test.text_hint` 保持一致）：

```text
B01: 这是一张 L 型支架的三视图。请根据图纸精确建模，注意主视图轮廓、俯视图/侧视图给出的深度，以及所有标注尺寸。

B02: 这是一张带 2 个通孔的矩形平板三视图。请根据图纸精确建模，注意平板的长宽高、孔的直径与位置，以及所有标注尺寸。孔沿 Z 方向贯穿。

B03: 这是一张 T 型支架的三视图。请根据图纸精确建模，T 形是顶部横杆加中间竖杆的结构。注意主视图的 T 形轮廓、俯视图/侧视图给出的深度，以及所有标注尺寸。

B04: 这是一张键盘键帽的三视图。键帽是**梯形拉伸**（底面大、顶面小、四周斜面向内收），底面 18×18mm、顶面 14×14mm、高度 8mm。必须使用 feature_extrusion 的拔模参数（draftAngleDeg + draftOutward=false）一次成型，不要用两次拉伸拼接。请精确计算拔模角度并建模。

B07: 这是一张家用 86 型开关面板的三视图。面板是 86×86×10mm 的方形板，四个竖直棱边有 R3 圆角，顶面中央有一个 50×50×3mm 的矩形凹槽（用于安装开关按键），四角各有一个 ⌀3mm 的贯穿螺丝孔。请按底座→凹槽→贯穿孔→圆角的顺序建模。

B06: 这是一张门锁扣板的三视图。底板为矩形薄板 30(X方向,短边)×80(Y方向,长边)×3(Z方向,厚度)mm，沿原点居中。顶面中央有一个 20×10mm 的腰形（长圆）孔用于容纳锁舌（长轴沿短边 X 方向）。长边方向即 Y 方向的两端各有一个 ⌀5mm 螺丝孔（cx=0, cy=±32mm）。顶面前后两条长边 R3 圆角。所有孔都贯穿。请按 底座→腰形孔→2 螺丝孔→2 次圆角 的顺序建模。

B05: 这是一张键盘键帽（完整版）的三视图。键帽是梯形拉伸（拔模内收）+ 底面挖凹槽 + 顶面圆角的组合。底面 18×18mm、顶面 14×14mm、高度 8mm。底面中央 12×12×3mm 键轴凹槽（不贯穿）。顶面周边 4 条边 R1.5 圆角。按 拔模拉伸→底面凹槽→4 次顶面圆角 的顺序建模。

B11: 这是一张齿轮盘的三视图。齿轮盘是一个 ⌀60mm×10mm 的圆盘，中心贯穿一个 ⌀15mm 的圆孔，外圈 12 个 ⌀4mm 的齿孔沿 PCD⌀48mm 均匀分布。请用圆周阵列建模：拉伸圆盘 → 挖中心孔 → 挖一个种子齿孔在 (X=24mm, Y=0) → feature_circular_pattern count=12 axis=z angleDeg=360 一步阵列出其余 11 个。

B22: 这是一张圆碗的三视图。圆碗外形是一个 ⌀80mm×40mm 的圆盘，顶面中央挖一个 ⌀68mm×34mm 的圆形深坑作为碗内腔（壁厚 6mm，底厚 6mm，顶部敞开）。用 Shell 替代法：sketch_circle 画 ⌀80 圆 → 拉伸 40mm → 选顶面 → sketch_circle 画 ⌀68 内腔 → cut 34mm（不 through_all，底部留 6mm）。

C1: 这是一张 60% 迷你机械键盘的三视图。键盘外形是 285×95×15mm 的矩形底座, 顶面均匀排布 5 行 × 13 列共 65 个方形按键. 每个按键 15×15mm, 从底座顶面凸起 5mm(向上略微内收/拔模让键面更精致). 相邻按键之间留 3mm 间隙, 整个键位区域在底座顶面上居中排列. 外形无圆角，底座边缘保持直角，也无螺丝孔或其他额外特征.

C2: 这是一张小型齿轮减速器外壳(俗称齿轮箱)的三视图。整体像一个方盒: 底座 200×120×20mm 是安装法兰盘, 上面立着一个 160×100×80mm 的箱体(法兰盘和箱体都在 XY 平面内居中排列, 箱体位于法兰盘正上方). 箱体是中空的用来装齿轮, 内腔 144×84×74mm(壁厚 8mm 底厚 6mm 顶部敞开). 箱体前后两面各有一个 ⌀25mm 的轴承孔沿 Y 方向贯穿箱体前后壁(供输入/输出轴穿过, 孔中心位于箱体前面的几何中心). 法兰盘四角各有一个 ⌀8mm 的安装孔(沿 Z 方向贯穿法兰厚度, 用于螺栓固定到设备上, 孔中心距法兰边缘 15mm).

C3: 这是一张齿轮啮合组的三视图, 由两个平行放置的直齿圆柱齿轮组成. 小齿轮(输入): 分度圆 ⌀40mm × 厚 15mm, 沿圆周均匀分布 10 个矩形凸齿(齿高 4mm, 齿宽 5mm, 使外接圆达 ⌀48mm); 中心位于 (X=-35mm, Y=0). 大齿轮(输出): 分度圆 ⌀80mm × 厚 15mm, 沿圆周均匀分布 20 个矩形凸齿(齿高 4mm, 齿宽 5mm, 使外接圆达 ⌀88mm); 中心位于 (X=+35mm, Y=0). 两齿轮中心距 70mm, 两齿轮齿顶留 2mm 视觉间隙(避免矩形齿几何干涉). 两齿轮各自独立(不合并为一个实体), 都在同一 XY 平面上并排放置, Z 方向厚度相同 15mm. **本简化版本不需要轴孔**(实体齿轮), 建完两个齿轮的齿即完成; 若加轴孔会因齿轮阵列后特征树面数过多导致选面超时.
```

### 12.4 验证点

- **阶段 A（Vision）**：读到 `part_type` 且 `dimensions` 数组枚举出图纸上所有标注尺寸（宁多不少）
- **阶段 B（IR→Plan）**：对 L 形/T 形等凹形轮廓采纳 skill 中的"逐段 sketch_line 闭合轮廓"画法（严禁两个矩形拼接）；对带孔零件用 `select_face_and_sketch + sketch_circle + feature_cut_extrusion(through_all)` 组合
- **执行**：所有步骤 HTTP 返回 success
- **几何验证**：包围盒 ±3mm 内匹配 groundtruth；体积 ±5% 内匹配 groundtruth

**Web 界面手动测试**（打开 `http://localhost:5000`）：

1. 欢迎区“由三视图生成”分组下会有 12 个按钮（B01~C3）
2. 按钮由 `/api/three-view-samples` 从 samples.json 动态渲染 —— 新增样例自动出现，无需改前端代码
3. 点任一按钮 → 自动填 prompt + 加载图片预览 → 按发送即可

### 12.5 SOP：新增一个三视图样例（一处修改，三处同步）

> 目标：加一个样例，让它同时出现在 `build_all.py` 生成产物、端到端测试、Web demo 按钮中。

1. **打开** `datasets/manual_three_view/samples.json`，在 `samples` 数组末尾追加一个对象：
   ```json
   {
     "part_id": "B04",
     "subdir": "B04_xxx",
     "name": "B04 XX 型零件",
     "part_type": "xxx",
     "notes": "…",
     "profile_xy": [[0,0], [50,0], [50,30], [0,30]],
     "depth_z": 25,
     "holes": [{"cx":10,"cy":10,"radius":3,"label":"…"}],
     "ui": {"label": "🔷 XX 型", "prompt": "这是一张 XX 型…"},
     "test": {"text_hint": "这是一张 XX 型…"}
   }
   ```

2. **生成图纸**：
   ```cmd
   venv\Scripts\python.exe -X utf8 datasets\manual_three_view\build_all.py
   ```

3. **清缓存跑端到端测试**（只跑新增的）：
   ```cmd
   venv\Scripts\python.exe -X utf8 -c "import diskcache; c=diskcache.Cache('planner/.llm_cache'); c.clear(); c.close()"
   venv\Scripts\python.exe -X utf8 test\test_three_view_e2e.py B04 > log\test_3view_B04.log 2>&1
   ```

4. **测试通过** → 提交入库（含 `drawing.png` + `groundtruth.json` + samples.json 变更）。**Web demo 无需重启前端**（下次访问会自动 fetch 新按钮），若后端未重启则重启 Flask 加载 samples.json 的新内容。

5. **若测试未通过**：常见原因是 LLM 对该形状没有对应的 skill 指引。检查 `LLMPlanner/planner/skills/input_modes/three_view.md` §7"阶段 B 特别指引"，仿照 L 形/T 形补一段专属规则（正确画法示例 + 反例禁止清单），清缓存重跑。

---

## 十三、Motion Study 齿轮动画测试

该测试不调用 LLM，打开预置 `gear_pair_assembly_v4.SLDASM`，先验证同模数渐开线齿轮的理论中心距为 60mm，再创建恒速旋转马达并播放，验证 Motion timebar 推进以及小/大齿轮 Transform 均发生变化。齿轮参数为模数 4mm、压力角 30°、齿数 10:20、齿根深度 1.25m；大齿轮预先错相半齿距 9°。

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner
venv\Scripts\python.exe -X utf8 assembly_smoke\test_motion_study.py > log\test_motion_study.log 2>&1
venv\Scripts\python.exe -X utf8 assembly_smoke\test_gear_geometry.py
venv\Scripts\python.exe -X utf8 assembly_smoke\capture_gear_animation.py
```

实测：中心距=60.000mm，主动小齿轮转角 -64.458°、从动大齿轮 +32.229°，满足外啮合反向 `-2:1`；纯几何测试在完整齿周期内采样 73 帧，无边线交叉或包含型重叠；SolidWorks 原生干涉检测对导出的 25 帧全部返回零干涉。GIF 输出到 `log/gear_validation/gear_cycle.gif`。

---

## 十四、参数化双级减速箱 Multi-turn 装配测试

该测试先确定性生成 5 个参数化零件，再让 Multi-turn Agent 根据自然语言参数、
零件路径和里程碑完成装配。运行时不读取评测 ground truth 或 golden operations。

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner
venv\Scripts\python.exe -X utf8 eval\eval_two_stage_gearbox_multiturn.py --max-turns 18
```

Web Demo 可在“5. 装配体相关”中点击“Multi-turn 构建双级减速箱”。首次运行会调用
LLM；再次以相同模型和任务运行时，Vision/蓝图/完整状态 Agent 决策可自然命中缓存。
缓存只跳过 LLM 请求，零件准备、SolidWorks 装配操作、observation 与确定性验收仍会
重新执行。页面会显示本次新增 token；全部决策命中时应为 `0`。

2026-07-20 修复后实测：冷启动 4 轮、14/14 操作、50,842 tokens；立即重复运行
仍为 4 轮、14/14 操作且 **0 tokens**，组件 5、Mate 5、最终验收通过。
Gear Mate 由 C# 根据组件 Transform 自动选择中心孔圆柱面，不再要求 LLM 提供射线。

装配体生成后，可在同一区域选择输入轴、中间轴任一齿轮或输出轴齿轮，点击
“选中并加载马达”后播放 5 圈。该路径不调用 LLM。实测选择输入轴后 Motion Study
从 0s 推进至 5s，四个齿轮的 Transform 均发生变化。

验收项：5 个组件原点位置 ±1mm；3 个未压制 Gear Mate，归一化比值恰为
`{1,2,3}`；2 个 Coincident Mate；原生干涉数 0；`.SLDASM` 已保存。
每次零件生成后调用 `/api/close-all-documents`；装配完成后调用
`/api/close-inactive-documents`，关闭预加载零件窗口并保留最终装配体。

---

## 十五、单缸曲柄滑块正时发动机 Multi-turn 装配测试

该测例确定性生成 10 个零件，再由 Multi-turn Agent 插入组件、建立 5 个传动
关系、设置机构初始姿态并保存。最终机器验收额外采样完整曲柄周期，不依赖
LLM 自报完成。

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

REM 只生成零件；每个零件保存后自动关闭文档
py eval\eval_single_cylinder_engine_multiturn.py --prepare-only --force-parts

REM 完整 Multi-turn 运行；重复运行启用成功状态缓存
py eval\eval_single_cylinder_engine_multiturn.py --use-cache --max-turns 24

REM 对已保存装配体做 0-token 静态和五角度运动学复验
py eval\eval_single_cylinder_engine_multiturn.py --validate-existing
```

验收项：

- 10 个组件，原点位置容差 ±1mm。
- 5 个 Gear Mate；归一化比值为 `[1,1,1,1,2]`。
- 0°/90°/180°/270°/360° 五点的活塞解析位置误差不超过 0.25mm。
- 行程为 50mm，360° 回到初始位置，凸轮系为曲柄反向半速。
- `.SLDASM` 已保存；测试后只保留最终装配体窗口。

Web Demo 在“5. 装配体相关”区域提供“Multi-turn 构建单缸正时发动机”按钮。
生成后可用曲柄角滑块设置确定性姿态；姿态控制只调用 SolidWorks API，不调用
LLM。实测完全缓存重建为 6 轮、20/20 操作、0 provider 调用和 0 tokens。

---

## 十六、复古 16mm 电影放映机 Multi-turn 装配测试

该测例包含机身、双卷盘、拨盘、四槽分度轮、送片轮、双叶快门、胶片门、
镜头、灯箱和冷却风扇等 15 个组件。Multi-turn Agent 负责按语义装配并建立
5 个传动关系；机器验收用通用间歇分度 API 复验四个输入周期。

```cmd
cd C:\codes\sw\SolidWorksTutorial\LLMPlanner

REM 确定性生成零件，保存后逐个关闭
py eval\eval_film_projector_multiturn.py --prepare-only --force-parts

REM Multi-turn 装配；相同任务复跑可命中成功状态缓存
py eval\eval_film_projector_multiturn.py --use-cache --max-turns 28

REM 对保存的装配体做 0-token 静态与分度复验
py eval\eval_film_projector_multiturn.py --validate-existing
```

验收项：

- 15 个组件，5 个未压制 Gear Mate，归一化齿比 `[1,1,2,4,4]`。
- 输入相位 0°–120° 和 240°–360° 为停歇锁止区；120°–240° 平滑分度。
- 每个输入周期输出 90°；四个输入周期输出 360°。
- 双叶快门相对输入固定超前 90°；冷却风扇两倍速；送片轮跟随分度轮，
  双卷盘以四分之一速度反向收放片。
- `.SLDASM` 已保存，测试后关闭 15 个非活动零件窗口。

Web Demo 的装配体区域提供“Multi-turn 构建复古 16mm 放映机”按钮和
0°–1440°拨盘滑块。滑块直接调用 SolidWorks API：180° 输入对应 45°输出且
处于啮合，0°/60°/120°输出均为 0°停歇；不产生 LLM token。
