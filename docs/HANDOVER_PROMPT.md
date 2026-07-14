# 项目接手 Prompt（复制到 Cursor + GPT-5.6 Sonnet 新会话开头）

> 复制下方 `---` 分隔线之间的整段内容作为你 Cursor 新会话的首条消息。
> Cursor 会自动读取项目里的 `docs/HANDOVER.md` 等文档，模型即可进入工作状态。

---

你好，我把一个进行中的项目从 CodeBuddy IDE + Claude Sonnet 5 交接给你（Cursor + GPT-5.6 Sonnet）。这是一个**用 LLM 自动驱动 SolidWorks 建模**的项目，仓库在本地 `c:\codes\sw\SolidWorksTutorial`，GitHub 在 https://github.com/fansunqi/SolidWorksTutorial 。

## 📖 交接资料（请依次读取）

在开始任何工作前，请**按顺序阅读**以下 3 份文档，它们是这个项目最重要的上下文：

1. **`docs/HANDOVER.md`** — **最重要**。项目全景 / 60+ 个已踩过的坑 / 未完成清单 / 30 分钟上手验证流程。请**完整读完**。
2. **`CODEBUDDY.md`** — HTTP API 端点速查表（40+ 个端点）+ 项目约定（含新增建模物体的 Definition of Done 强约束）。
3. **`TEST.md`** — 测试样例清单 + Definition of Done 强约束的详细说明。

## 🎯 一句话背景

- **系统架构**：Python（LLMPlanner，负责调 LLM 生成 plan）→ HTTP → C#（Chapter1.ConnectToSw，通过 COM 驱动 SolidWorks）→ SolidWorks 2025 中文版桌面进程
- **已实现**：零件建模（15+ 样例端到端跑通）、装配体建模（含齿轮 Gear Mate 联动）、多模态三视图输入（12 个手工样例）、Skill 化 Prompt 架构、端到端 cache
- **最新进展**：Motion Study 旋转马达已打通，Web demo 可加载齿轮装配体、播放 5 圈并停止；C3 草图选择回归也已修复。下一项主要工作是 Planner 端支持装配体。见 `HANDOVER.md` §10.1、§10.4。

## ⛔ 必须遵守的强约束（Definition of Done）

**新增建模物体（无论文字建模还是三视图样例），只有以下 5 件事全部完成才算完成，缺任何一项都是半成品。禁止只跑通测试就 `git commit`。**

### 文字建模类（自然语言 → CAD）
1. 编写测试脚本 `LLMPlanner/test/objects/test_<name>_llm.py`（含几何断言）
2. 执行测试并把日志留在 `LLMPlanner/log/`
3. 更新 `TEST.md`：分类表新增一行 + 完整 Prompt 原文
4. 加到 Web demo：`web/static/index.html` 新增示例按钮 + `app.js` 加 `fillXxxPrompt` 函数
5. `git commit + push`（commit message 引用测试关键指标）

### 三视图多模态类（图纸 → CAD）
1. 追加 `LLMPlanner/datasets/manual_three_view/samples.json`（**只改这一处**，三处消费方自动同步）
2. 跑 `datasets\manual_three_view\build_all.py` 生成 PNG + groundtruth
3. 清 diskcache 后跑 `test\test_three_view_e2e.py <part_id> > log\test_3view_<id>.log 2>&1`
4. 更新 `TEST.md` §12.2 表格新增一行 + §12.3 追加 text_hint prompt
5. `git commit + push`（Web demo 按钮由 samples.json 自动驱动，无需改前端）

**每次 `git commit` 之后必须立即 `git push origin main`**——用户偏好一次 commit = 一次 push。

## 🚨 高频陷阱预警（务必记住，`HANDOVER.md` §6 有完整清单）

1. **权限一致性**：SolidWorks 与 C# 服务必须**同权限**（都普通 or 都管理员），否则 COM 拒绝连接。若 SW 因 `RUNASADMIN` 兼容性标志强制以管理员启动，需删除注册表项。
2. **温度必须 =0**：`llm_client.py` 里 structured output 和 json mode 都要设 `temperature=0`，否则 Sonnet 5 长 plan 会截断/畸形。
3. **矩形中心+宽高转对角线易算错**：`x2 = cx + w/2` 不是 `x2 = cx + w`。LLM 极易在这里翻车（skill 已加正例反例约束）。
4. **切凹坑/孔用 `flipDirection=false`**：`true` 会切掉整个面外侧环形区域。
5. **CutExtrusion 退出草图**：必须用 `IModelDoc2.InsertSketch2(true)`，不能用 `SketchManager.InsertSketch(true)`。
6. **装配体第一个组件默认 Fixed**：`AddComponent5` 加进来的第一个组件不能被 Transform 修改，需先调 `UnfixComponent`。
7. **SW2025 中文版没有 .asmdot 装配体模板**：项目已内置 `Chapter1.ConnectToSw/resources/empty_assembly_template.SLDASM` 绕过。
8. **前端样例图片不能走 canvas JPEG 压缩**：会破坏 image_hash 稳定性，导致 cache 永远 miss。已在 `app.js` 用 `loadRawPngAsImage()` 修复。
9. **Windows 编码**：运行含 emoji 的 Python 必须 `PYTHONUTF8=1` + `python -X utf8`，否则 UnicodeEncodeError。
10. **重建 C# 前先杀服务**：`Get-Process Chapter1.ConnectToSw | Stop-Process -Force`，否则新 exe 被锁定构建失败。

## 🔧 工作方式约定

1. **优先修改现有代码而非新建文件**：项目结构已经稳定，不要主动创建新目录/文件除非有明确必要。
2. **中文注释 + 中文回复**：项目面向中文开发者，代码注释和你的回复都用中文。
3. **使用 replace_in_file 而非 write_to_file**：编辑现有文件时用局部替换，除非需要完全重写。
4. **主动读取工具优先**：需要理解代码时先 read_file/search_content，不要凭感觉猜测。
5. **测试驱动**：修改核心逻辑（llm_client / SwCommands / skills）后要跑对应的回归测试（`test_skill_regression.py` 等）。
6. **Definition of Done 强约束不可绕过**：新增建模物体必须走完 5 步。

## 🚀 立即上手的第一件事

请你现在：

1. **读完 `docs/HANDOVER.md`**（30~60 分钟，Cursor 应该会自动索引）
2. **读完 `CODEBUDDY.md`** 和 `TEST.md`
3. **告诉我你理解到的**：
   - 这个项目在做什么？
   - 系统的三层架构是什么？
   - 当前最重要的未完成问题是什么？（提示：Planner 端装配体支持）
   - Definition of Done 的 5 步是什么？
4. **问我 3 个你觉得对上手最有帮助的问题**（可以是代码位置、历史决策、优先级等）

之后我们再一起规划下一个 milestone。请开始吧。

---
