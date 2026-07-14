// TEST.md 测例 prompt 目录 — 与测试脚本 / 文档原文对齐
// 分区 1：基础测试（§一、§三~§七）
// 分区 2：由文本生成（§五、§八、§九 LLM 物体）

// ---- 长 prompt 常量（与 test/objects/*_llm.py 逐字一致）----

const DICE_PROMPT =
  "请帮我建模一颗标准的六面骰子（色子），要求如下：\n" +
  "1. 骰子主体是一个边长 20mm 的正方体；\n" +
  "2. 正方体的 12 条棱边都做 R1.5mm 的圆角，让边角圆润；\n" +
  "3. 六个面上分别有 1、2、3、4、5、6 个圆形点数；每个点数是一个直径约 3mm、深约 1.5mm 的圆形凹坑（向内凹陷，不是凸起）；\n" +
  "4. 遵循标准骰子规则：相对的两个面点数之和为 7（即 1 对 6、2 对 5、3 对 4）；\n" +
  "5. 各面点数的常规排布：1 点在面中心；2 点和 3 点沿面的一条对角线均匀分布；4 点在面的四个角附近；5 点是四个角加面中心；6 点是两列、每列三个点。\n" +
  "请自行计算每个面以及每个凹点在模型空间中的位置，完成完整建模。";

const STORAGE_BOX_PROMPT =
  "创建一个长方形收纳盒：外部尺寸为长 120mm、宽 80mm、高 50mm，" +
  "盒壁厚度 8mm，盒子内部是一个开口向上的空腔（顶部敞开），" +
  "底部厚度也是 8mm。最后对盒子顶部开口的四条边做 R3mm 圆角。";

const EDIT_BASE_PROMPT =
  "创建一个凸台，底面是长为 100mm、宽为 50mm 的长方形，拉伸 30mm";

const EDIT_CONTINUE_PROMPT =
  "将凸台再向上拉伸 50mm";

const HEX_NUT_PROMPT =
  "创建一个 M20 六角螺母：正六边形外形（对边距 30mm，即两条平行边之间的距离为 30mm），" +
  "厚度 8mm，中心有一个直径 10mm 的贯穿圆孔。螺母沿厚度方向以原点居中。";

const MUG_PROMPT =
  "请建模一个马克杯，**严格按以下顺序**执行，因为顺序会影响最终形状是否干净：\n" +
  "1. 先创建杯体：用上视基准面画外径 70mm 的圆，向上拉伸 90mm，形成实心圆柱；\n" +
  "2. 接着**加**把手（**必须在挖空腔之前加**，否则把手内嵌部分会伸入未来的空腔里，" +
  "在杯内壁形成难看的圆环凸起）：在前视基准面上画两个同心圆（外径 40mm、内径 24mm，" +
  "即把手条粗 8mm），然后**用 feature_extrusion（加料操作，不是 feature_cut_extrusion！）**沿前后（Z）方向 midPlane 拉伸 8mm 厚度，" +
  "把这个环形轮廓变成实心的把手（外圆做外形、内圆做中间的空洞）。\n" +
  "   把手位置要求（**必须与杯壁相交合并**，不能只是相切）：\n" +
  "   - 同心圆中心 X 应把内圆左沿嵌入杯壁至少 2mm。杯外壁在 X=35mm，" +
  "取把手中心 X≈48mm，则内圆左沿到 X=48−12=36mm，勉强嵌入 1mm——不够；\n" +
  "   - 正确做法：把手中心取 X=48mm 时最外沿 X=68mm。但为了确保内圆稳固地嵌入实体杯壁至少 5mm，" +
  "建议把手中心 X 取 40mm（内圆左沿到 X=28mm，深入实心杯身 7mm），" +
  "此时把手最外沿到 X=60mm；\n" +
  "   - 把手垂直方向 Y 位置居中于杯身中部，即 Y=45mm；\n" +
  "3. 最后再挖空腔：从杯体顶面向下挖，用射线选顶面，画一个直径 60mm 的圆（即壁厚 5mm），" +
  "切除 80mm 深（即底厚 10mm），顶部敞开、底部封闭。此步会同时把嵌入杯身的把手内圆那段一起挖掉，" +
  "使杯内壁保持光滑。\n" +
  "4. 杯子沿 Y 方向从 0 延伸到 90mm（不居中）。\n" +
  "所有尺寸使用米制，注意换算（1mm = 0.001m）。";

const WRENCH_PROMPT =
  "请建模一把开口扳手（板手），由头部圆盘、手柄矩形梁、以及头部中心的六角螺母卡口组成：\n" +
  "1. 扳手整体沿厚度方向（Y 轴）以原点居中，厚度 8mm；\n" +
  "2. **头部**是一个圆盘：外径 40mm，圆心在原点。用上视基准面画圆，midPlane 拉伸 8mm；\n" +
  "3. **手柄**是一个矩形梁：长 120mm（沿 +X 方向），宽 15mm（沿 Z 方向居中），厚度同样 8mm。" +
  "手柄末端**必须嵌入头部圆盘内 5mm 以确保合并**成单一实体——" +
  "即矩形沿 X 方向从 X=15mm（圆盘半径 20mm 减 5mm）延伸到 X=120mm，" +
  "沿 Z 从 -7.5mm 到 +7.5mm。需要**用第二次 select_plane（上视面）+ sketch_rectangle + feature_extrusion**" +
  "（不能和头部画在同一个草图里，因为 SolidWorks 不允许一个草图里画圆和矩形两个相交但不嵌套的轮廓）；\n" +
  "4. **六角卡口**：在头部顶面用 select_face_and_sketch 选顶面，用 sketch_polygon 画一个" +
  "**正六边形**（对边距 13mm、尖朝上），然后 feature_cut_extrusion 贯穿切除，形成螺母卡口。" +
  "对边距 13mm 对应外接圆半径 = 13 / √3 ≈ 7.51mm；\n" +
  "5. 六边形中心应对齐头部圆盘中心：cx=0、cz=0（面内两个方向对齐），" +
  "cy=顶面 Y 坐标 (+0.004m)。\n" +
  "所有尺寸使用米制（1mm = 0.001m）。";

const VASE_PROMPT =
  "请建模一个花瓶：轴对称形状（用旋转特征生成），底部平坦、瓶身中部略膨大、瓶颈收窄、瓶口再略外扩。\n" +
  "在**前视基准面**（select_plane {index:0}）上画一个位于旋转轴右侧的**半截面轮廓**并旋转 360°：\n" +
  "1. 首先画中心线（旋转轴）：从 (x=0, y=0) 到 (x=0, y=0.15)，沿 Y 轴向上；\n" +
  "2. 然后**按顺序依次画 8 段直线**首尾相连组成花瓶的右半轮廓（所有坐标单位：米）：\n" +
  "   ① sketch_line (0, 0) → (0.03, 0)      ← 底面（旋转轴到底盘外沿）\n" +
  "   ② sketch_line (0.03, 0) → (0.03, 0.01)  ← 底盘垂直段\n" +
  "   ③ sketch_line (0.03, 0.01) → (0.045, 0.04)  ← 斜向外扩到最粗处\n" +
  "   ④ sketch_line (0.045, 0.04) → (0.045, 0.07)  ← 中部最粗处垂直段\n" +
  "   ⑤ sketch_line (0.045, 0.07) → (0.02, 0.1)   ← 收腰到瓶颈\n" +
  "   ⑥ sketch_line (0.02, 0.1) → (0.0275, 0.13)  ← 瓶颈外扩形成瓶口\n" +
  "   ⑦ sketch_line (0.0275, 0.13) → (0.0275, 0.15) ← 瓶口垂直段\n" +
  "   ⑧ sketch_line (0.0275, 0.15) → (0, 0.15)   ← 顶面（回到旋转轴）\n" +
  "3. 调用 feature_revolve {angle: 360}，形成实心花瓶（此时**不需要**画额外直线封闭轮廓，" +
  "轮廓两端都在旋转轴上，SolidWorks 会用旋转轴自动封闭）；\n" +
  "4. 花瓶应有内腔（空心）：**旋转完成后**用 select_face_and_sketch 选中顶面（射线起点 ry=0.2 大于瓶高 0.15，" +
  "方向 dy=-1），在顶面上画一个直径 36mm 的圆（cx=0, cy=0.15, cz=0, radius=0.018），" +
  "然后 feature_cut_extrusion 盲孔切除 140mm 深度（endCondition='blind'），" +
  "留下 10mm 底厚（花瓶高 150mm，切 140mm 后剩底部 10mm）。\n" +
  "所有尺寸均为米制。整个花瓶从底部 Y=0 延伸到顶部 Y=0.15。";

const KEY_PROMPT =
  "请建模一把小型钥匙，包含**钥匙圈头（带圆孔）、钥匙杆、钥匙齿**三部分。" +
  "所有尺寸使用米制（1mm = 0.001m），整体沿 Y 轴（厚度方向）以原点为中心居中，厚度 2mm。\n" +
  "\n" +
  "1. **钥匙圈头**：一个圆盘外径 20mm，圆心在原点。用上视基准面（select_plane {index:1}）画圆，" +
  "然后 feature_extrusion {depth: 0.002, midPlane: true} 一次拉伸；\n" +
  "\n" +
  "2. **钥匙杆**：一个矩形梁，长 42mm（沿 +X 方向），宽 8mm（沿 Z 方向居中）。" +
  "杆的末端**必须嵌入钥匙圈头内 3mm 以确保合并**成单一实体——" +
  "即矩形沿 X 从 X=7mm（钥匙圈半径 10mm 减 3mm 嵌入）延伸到 X=49mm，" +
  "沿 Z 从 Z=-4mm 到 Z=+4mm。**用第二次 select_plane {index:1}（上视面）" +
  "+ sketch_rectangle {x1:0.007, y1:-0.004, x2:0.049, y2:0.004} + feature_extrusion " +
  "{depth: 0.002, midPlane: true}**（不能和圆盘画在同一草图里）；\n" +
  "\n" +
  "3. **钥匙圈孔**：在钥匙圈头顶面用 select_face_and_sketch 选顶面" +
  "（射线 rx=0, ry=0.01, rz=0, dx=0, dy=-1, dz=0——从上方射向 -Y 命中顶面），" +
  "然后**用 sketch_slot 画一个长圆孔**：cx=0, cy=0.001（顶面 Y 坐标）, cz=0，" +
  "长 10mm（沿 X）、宽 5mm，然后 feature_cut_extrusion " +
  "{depth: 0.002, endCondition: 'through_all'} 贯穿切除。\n" +
  "\n" +
  "4. **钥匙齿（关键！这是钥匙的标志性特征）**：在钥匙杆顶面开出 5 个锯齿状凹槽。" +
  "**再一次** select_face_and_sketch 选钥匙杆的顶面" +
  "（射线 rx=0.03, ry=0.01, rz=0, dx=0, dy=-1, dz=0）。" +
  "然后**在同一草图**内画 5 个 sketch_slot 齿槽（各自独立、不相交、必须完全在杆内）——" +
  "每个齿槽 length=3mm（沿 X）、width=2mm（沿 Z），" +
  "**齿槽的 Z 中心固定 cz=+0.003（贴近杆的一条长边 Z=+4mm）**，" +
  "5 个齿槽沿 +X 方向均布，中心 X 坐标依次为 0.017, 0.023, 0.029, 0.035, 0.041。" +
  "**必须一次列出全部 5 个 sketch_slot 步骤**：" +
  "sketch_slot {cx: <上面的X>, cy: 0.001, cz: 0.003, length: 0.003, width: 0.002}。" +
  "画完 5 个齿槽后一次 feature_cut_extrusion {depth: 0.002, endCondition: 'through_all'} 贯穿切除。\n" +
  "\n" +
  "⚠️ 重要规则：\n" +
  "- sketch_slot 的 length 必须严格大于 width（否则会报错），本例齿槽 3mm > 2mm 满足。\n" +
  "- 在实体面上画 sketch_slot 时，cx/cy/cz 都是**模型 3D 坐标**（不是草图 2D）。cy 必须等于所在面的 Y 坐标。";

const GEAR_PROMPT =
  "请建模一个简化的圆柱齿轮：\n" +
  "1. 齿轮主体：⌀60mm 圆盘，厚 8mm，用上视基准面画外圆 (cx=0,cy=0,radius=0.03)，" +
  "然后 feature_extrusion {depth: 0.008, midPlane: true} 居中拉伸；\n" +
  "2. **齿槽（12 个，沿圆周均布）**：在圆盘顶面（Y=+0.004m）上用 select_face_and_sketch 选顶面，" +
  "然后**在同一草图**内画 12 个小矩形（用 sketch_slot 画,不能相交,必须完全在圆盘内）。\n" +
  "   计算：第 k 个齿槽（k=0..11）的中心角 θ_k = 2π·k/12（弧度），" +
  "**中心距原点 25mm（圆盘半径 30mm 内缩 5mm）**，" +
  "中心坐标 (cx_k, cz_k) = (0.025·cos(θ_k), 0.025·sin(θ_k))。\n" +
  "   ⚠️ **用 sketch_slot 而不是 sketch_rectangle**（sketch_rectangle 的 y1/y2 在实体面上会被误解为模型 Y）。\n" +
  "   每个齿槽：sketch_slot {cx: cx_k, cy: 0.004, cz: cz_k, length: 0.003, width: 0.002}\n" +
  "   ⚠️ **必须自己算好 12 个齿槽的 cx/cz**（Python 中 cos(0)=1,sin(0)=0," +
  "cos(π/6)≈0.866,sin(π/6)=0.5 等等,把 0.025 乘上去），一次给出全部 12 个 sketch_slot 步骤。\n" +
  "   ⚠️ **不要偷懒用文字说\"依此类推\"，必须真的列出全部 12 个 sketch_slot 步骤**。\n" +
  "3. 画完 12 个齿槽后，一次 **feature_cut_extrusion {depth: 0.008, " +
  "endCondition: 'through_all'}** 贯穿切除，形成 12 个齿槽；\n" +
  "4. **中心孔**：再用 select_face_and_sketch 选顶面（射线 rx=0, ry=0.01, rz=0, dy=-1），" +
  "然后 sketch_circle {cx: 0, cy: 0.004, cz: 0, radius: 0.006} 画中心 ⌀12mm 圆，" +
  "feature_cut_extrusion {depth: 0.008, endCondition: 'through_all'} 贯穿。\n" +
  "所有尺寸使用米制（1mm = 0.001m）。";

const BASIC_PROMPTS = [
  // §一 基础用法
  { label: "📦 长方体 100×50×30", test: "§一 dry-run / test_create_box", prompt: "创建一个 100mm x 50mm x 30mm 的长方体" },
  { label: "📦 方块", test: "§一 planner.main", prompt: "创建一个方块，长为 100mm，宽为 50mm，高为 30mm" },

  // §三 切除拉伸
  { label: "🕳️ 盲孔", test: "test_cut_extrusion blind", prompt: "新建零件，前视面画 100×50mm 矩形拉伸 30mm，上表面中心打 ⌀20mm、深 20mm 盲孔" },
  { label: "🕳️ 贯穿孔", test: "test_cut_extrusion through", prompt: "新建零件，前视面画 80×60mm 矩形拉伸 10mm 薄板，上表面中心打 ⌀6mm 贯穿孔" },

  // §四 旋转特征 API
  { label: "🔄 旋转圆柱 ⌀40×80", test: "test_revolve cylinder", prompt: "用旋转法创建一个直径 40mm、高度 80mm 的圆柱体（在前视基准面画矩形截面 + 中心线，旋转 360°）" },
  { label: "🔄 旋转圆柱 ⌀30×60", test: "test_revolve cylinder2", prompt: "用旋转法创建一个直径 30mm、高度 60mm 的圆柱体（线段截面 + 中心线，旋转 360°）" },
  { label: "⚪ 旋转球体 ⌀40", test: "test_revolve sphere", prompt: "用旋转法创建一个直径 40mm 的球体（圆弧截面 + 中心线，旋转 360°）" },

  // §六 圆角 / 倒角
  { label: "🔘 全边圆角 R5", test: "test_fillet_chamfer fillet", prompt: "新建 100mm 立方体，对所有边做 R5mm 圆角" },
  { label: "🔘 射线选边圆角 R3", test: "test_fillet_chamfer fillet_ray", prompt: "新建 80×60×40mm 长方体，射线选中顶部前侧边做 R3mm 圆角" },
  { label: "📐 全边倒角 C3", test: "test_fillet_chamfer chamfer", prompt: "新建 80×60×40mm 长方体，对所有边做 C3mm 等距倒角" },
  { label: "📐 不等距倒角", test: "test_fillet_chamfer chamfer_dd", prompt: "新建 60×60×30mm 长方体，射线选顶边做 2mm×4mm 不等距倒角" },
  { label: "📐 圆角+倒角组合", test: "test_fillet_chamfer combo", prompt: "新建 100×80×50mm 长方体，底部前边 R5mm 圆角 + 顶部前边 C3mm 倒角" },

  // §七 板凳 API（Web demo 走 LLM，四脚/圆角板凳见 TEXT_PROMPTS §八）
  { label: "⬇️ 反向拉伸", test: "test_stool flip", prompt: "在上视基准面画 50×50mm 矩形，使用 flipDirection=true 向下拉伸 30mm" },
];

const TEXT_PROMPTS = [
  // §八 板凳 LLM
  { label: "🪑 四脚板凳", test: "test_stool_llm stool", prompt: "创建一个四脚板凳：凳面为 200mm×200mm×20mm 的方形平板，在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 150mm），腿的圆心距凳面边缘 30mm。" },
  { label: "🪑 圆角板凳", test: "test_stool_llm stool_fillet", prompt: "创建一个四脚板凳并对凳面做圆角处理：凳面为 200mm×200mm×20mm 的方形平板，在凳面底部四角各有一条圆柱形凳腿（直径 20mm，长 150mm），腿的圆心距凳面边缘 30mm。最后对凳面顶部的 4 条边做 R5mm 圆角。" },

  // §九 日常物体
  { label: "📦 收纳盒", test: "test_storage_box", prompt: STORAGE_BOX_PROMPT },
  { label: "🎲 简化骰子", test: "test_dice", prompt: "创建一个骰子的基础外形：先做一个边长 20mm 的正方体，然后对正方体的所有 12 条棱边做 R2mm 的圆角，使其成为一个边角圆润的立方块。" },
  { label: "🎲 标准骰子", test: "test_dice_llm", prompt: DICE_PROMPT },
  { label: "🔩 六角螺母", test: "test_hex_nut_llm", prompt: HEX_NUT_PROMPT },
  { label: "☕ 马克杯", test: "test_mug_llm", prompt: MUG_PROMPT },
  { label: "🔧 扳手", test: "test_wrench_llm", prompt: WRENCH_PROMPT },
  { label: "🏺 花瓶", test: "test_vase_llm", prompt: VASE_PROMPT },
  { label: "🔑 钥匙", test: "test_key_llm", prompt: KEY_PROMPT },
  { label: "⚙️ 齿轮", test: "test_gear_llm", prompt: GEAR_PROMPT },
];
