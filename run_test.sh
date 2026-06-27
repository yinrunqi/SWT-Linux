# ============================================================
# 运行方式
# 在 cmd bash 上运行

# cd C:\codes\sw\SolidWorksTutorial\LLMPlanner
# venv\Scripts\activate.bat
# set OPENAI_API_KEY=sk-YdzOBanGq5rkDotCp3ziSTmLKq0i1l4eoZoIyNHzS6gkplVl
# set OPENAI_BASE_URL=https://api.juheai.top/v1

# # 交互模式
# # python -m planner.main

# # 单次执行（dry-run 模拟）
# python -m planner.main --dry-run "创建一个 100mm x 50mm x 30mm 的长方体"

# python -m planner.main "创建一个方块，长为 100mm，宽为 50mm，高为 30mm"

# python -m planner.main "创建一个凸台，地面是长为 100mm、宽为 50mm 的长方形，拉伸 30mm"

# python -m planner.main "将凸台再向上拉伸 50mm"

# ============================================================
# SolidWorks Copilot 测试入口
# 用法: bash run_test.sh [测试名]
# ============================================================

# 测试需包含运行命令与输给 LLM 的 prompt

# ---------- 基础测试 ----------

# 创建长方体（最简单的端到端测试）
# python LLMPlanner/test/test_create_box.py

# LLM API 连通性测试
# python LLMPlanner/test/test_llm_api.py

# ---------- 切除拉伸测试 ----------

# 运行全部切除拉伸测试（盲孔+贯穿孔）
# python LLMPlanner/test/test_cut_extrusion.py

# 仅运行盲孔测试
# python LLMPlanner/test/test_cut_extrusion.py blind
# 新建零件，在前视基准面画一个 100mm×50mm 的矩形，拉伸 30mm 创建长方体。然后在上表面中心打一个直径 20mm、深度 20mm 的盲孔

# 仅运行贯穿孔测试
# python LLMPlanner/test/test_cut_extrusion.py through
# 新建零件，在前视基准面画一个 80mm×60mm 的矩形，拉伸 10mm 创建薄板。然后在上表面中心打一个直径 6mm 的贯穿孔

# ---------- 旋转特征测试（API 直接调用） ----------

# 运行全部旋转特征 API 测试（圆柱体矩形轮廓 + 圆柱体线段轮廓 + 球体圆弧轮廓）
# python LLMPlanner/test/test_revolve.py

# 仅测试圆柱体（矩形轮廓 + 中心线 → 旋转360°，预期 ⌀40mm×80mm）
# python LLMPlanner/test/test_revolve.py cylinder

# 仅测试圆柱体（线段轮廓 + 中心线 → 旋转360°，预期 ⌀30mm×60mm）
# python LLMPlanner/test/test_revolve.py cylinder2

# 仅测试球体（圆弧轮廓 + 中心线 → 旋转360°，预期 ⌀40mm）
# python LLMPlanner/test/test_revolve.py sphere

# ---------- 旋转特征测试（LLM Prompt → 自动规划 → 执行） ----------

# 运行全部 LLM 旋转测试
# python LLMPlanner/test/test_revolve_llm.py

# LLM Prompt: "用旋转法创建一个直径 40mm、高度 80mm 的圆柱体"
# python LLMPlanner/test/test_revolve_llm.py cylinder

# LLM Prompt: "用旋转法创建一个直径 50mm 的球体"
# python LLMPlanner/test/test_revolve_llm.py sphere

# ---------- 圆角 / 倒角测试 ----------

# 运行全部圆角倒角测试（5 个用例）
# python LLMPlanner/test/test_fillet_chamfer.py

# 仅运行所有边圆角测试
# python LLMPlanner/test/test_fillet_chamfer.py fillet
# 新建 100mm 立方体，对所有边做 R5mm 圆角

# 仅运行射线选边圆角测试
# python LLMPlanner/test/test_fillet_chamfer.py fillet_ray
# 新建 80×60×40mm 长方体，射线选中顶部前侧边做 R3mm 圆角

# 仅运行等距倒角测试
# python LLMPlanner/test/test_fillet_chamfer.py chamfer
# 新建 80×60×40mm 长方体，对所有边做 C3mm 等距倒角

# 仅运行不等距倒角测试
# python LLMPlanner/test/test_fillet_chamfer.py chamfer_dd
# 新建 60×60×30mm 长方体，射线选顶边做 2mm×4mm 不等距倒角

# 圆角+倒角组合测试
# python LLMPlanner/test/test_fillet_chamfer.py combo
# 新建 100×80×50mm 长方体，底部前边 R5mm 圆角 + 顶部前边 C3mm 倒角

# ---------- 板凳建模测试（阶段一：拉伸方向翻转） ----------

# 运行全部板凳测试（3 个用例）
# python LLMPlanner/test/test_stool.py

# 仅测试反向拉伸基础功能
# python LLMPlanner/test/test_stool.py flip  # 上视面画 50×50mm 矩形，flipDirection=true 向下拉伸 30mm，验证包围盒 Y 方向

# 仅测试四脚板凳完整建模
# python LLMPlanner/test/test_stool.py stool # 凳面 200×200×20mm + 4条圆柱腿（⌀20mm×300mm），总高 320mm

# 带圆角的四脚板凳
# python LLMPlanner/test/test_stool.py stool_fillet # 凳面顶部 4 边 R5mm 圆角 + 4条腿，验证圆角特征存在

# ---------- 阶段2：LLM 自动建模（自然语言 → 自动规划 → 执行） ----------

# 运行全部 LLM 板凳测试
# python LLMPlanner/test/test_stool_llm.py

# LLM 四脚板凳自动建模
# python LLMPlanner/test/test_stool_llm.py stool
# LLM Prompt: "创建一个四脚板凳：凳面为 200mm×200mm×20mm 的方形平板，在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 200mm），腿的圆心距凳面边缘 30mm。"

# LLM 带圆角的四脚板凳自动建模
# python LLMPlanner/test/test_stool_llm.py stool_fillet
# LLM Prompt: "创建一个四脚板凳并对凳面做圆角处理：凳面为 200mm×200mm×20mm 的方形平板，在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 200mm），腿的圆心距凳面边缘 30mm。最后对凳面顶部的 4 条边做 R5mm 圆角。"

