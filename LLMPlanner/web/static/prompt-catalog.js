// Web Demo 自然语言示例。这里只描述目标几何，不提供 API 步骤、坐标答案或固定策略。

const EDIT_BASE_PROMPT =
  "创建一个凸台，底面是长 100mm、宽 50mm 的长方形，拉伸 30mm。";

const EDIT_CONTINUE_PROMPT =
  "将当前凸台沿原拉伸方向再增加 50mm。";

const PHONE_STAND_PROMPT =
  "创建一个一体式桌面手机支架，所有结构必须合并为单一实体。"
  + "底座长110mm、宽78mm、厚8mm；78mm宽的倾斜背板厚6mm，"
  + "与水平面成74°，从底座上表面升起，成品总高度118mm。"
  + "底座前端设置贯穿全宽的挡唇，前后厚10mm，高出底座上表面12mm，"
  + "使成品前端总高度20mm。背板左右两侧各有一个三角加强筋。"
  + "背板下部中央开一个宽16mm、向下开口高度28mm的充电线U形通槽；"
  + "背板中上部开3个36×8mm的水平贯穿散热槽，槽中心竖向间距18mm。"
  + "底面加工两个24×10mm、深1.5mm的防滑垫浅凹槽。"
  + "最后尽可能对适合的外轮廓做R4mm圆角，同时保持底面平整、支架左右对称。";

const BASIC_PROMPTS = [
  { label: "长方体 100×50×30", test: "basic box", prompt: "创建一个 100mm × 50mm × 30mm 的长方体。" },
  { label: "方块", test: "basic cube", prompt: "创建一个边长 40mm 的立方体。" },
  { label: "创建凸台", test: "edit base", prompt: EDIT_BASE_PROMPT },
  { label: "盲孔", test: "blind hole", prompt: "创建 100×50×30mm 的矩形块，并在一个大面中心加工直径 20mm、深 20mm 的盲孔。" },
  { label: "贯穿孔", test: "through hole", prompt: "创建 80×60×10mm 的薄板，并在板中心加工直径 6mm 的贯穿孔。" },
  { label: "旋转圆柱", test: "revolve cylinder", prompt: "使用旋转特征创建直径 40mm、高 80mm 的实心圆柱。" },
  { label: "旋转球体", test: "revolve sphere", prompt: "使用旋转特征创建直径 40mm 的球体。" },
  { label: "全边圆角", test: "fillet", prompt: "创建 100mm 立方体，并对全部棱边做 R5mm 圆角。" },
  { label: "局部倒角", test: "chamfer", prompt: "创建 80×60×40mm 长方体，仅对顶部外轮廓的一条边做 C3mm 等距倒角。" },
  { label: "反向拉伸", test: "reverse extrusion", prompt: "在上视基准面创建 50×50mm 方形轮廓，向基准面负法向拉伸 30mm。" },
];

const TEXT_PROMPTS = [
  {
    label: "一体式手机支架",
    test: "one-piece phone stand",
    prompt: PHONE_STAND_PROMPT,
  },
  {
    label: "四脚板凳",
    test: "stool",
    prompt: "创建四脚板凳：凳面 200×200×20mm；四条圆柱腿直径 20mm、长 150mm，腿中心距相邻边 30mm。",
  },
  {
    label: "圆角板凳",
    test: "rounded stool",
    prompt: "创建四脚板凳：凳面 200×200×20mm；四条圆柱腿直径 20mm、长 150mm，距边 30mm；凳面顶部四条边做 R5mm 圆角。",
  },
  {
    label: "收纳盒",
    test: "storage box",
    prompt: "创建顶部敞开的长方形收纳盒，外部 120×80×50mm，壁厚和底厚均为 8mm，顶部开口边做 R3mm 圆角。",
  },
  {
    label: "标准骰子",
    test: "standard die",
    prompt: "创建边长 20mm 的标准六面骰子，全部棱边 R1.5mm；六面分别为 1 至 6 点、相对面点数和为 7；点为直径 3mm、深 1.5mm 的凹坑。",
  },
  {
    label: "六角螺母",
    test: "hex nut",
    prompt: "创建对边距 30mm、厚 8mm 的正六角螺母，中心有直径 10mm 的贯穿孔，厚度方向关于原点居中。",
  },
  {
    label: "马克杯",
    test: "mug",
    prompt: "创建高 90mm、外径 70mm、壁厚 5mm、底厚 10mm 的圆柱形马克杯，并添加外径 40mm、条粗 8mm、厚 8mm 的环形把手。",
  },
  {
    label: "扳手",
    test: "wrench",
    prompt: "创建厚 8mm 的单头扳手：头部外径 40mm、中心正六角卡口对边距 13mm；手柄长 120mm、宽 15mm，并与头部可靠合并。",
  },
  {
    label: "花瓶",
    test: "vase",
    prompt: "创建高 150mm 的轴对称空心花瓶：底部平坦，中部最大直径 90mm，瓶颈直径 40mm，瓶口直径 55mm，底厚 10mm。",
  },
  {
    label: "钥匙",
    test: "key",
    prompt: "创建厚 2mm 的小型钥匙：外径 20mm 的钥匙圈头、10×5mm 圈孔、42×8mm 钥匙杆，并在杆的一侧均布 5 个齿槽。",
  },
  {
    label: "齿轮盘",
    test: "gear disc",
    prompt: "创建直径 60mm、厚 8mm 的圆盘，中心有直径 12mm 贯穿孔，并在接近外缘处均布 12 个相同的小贯穿槽。",
  },
  {
    label: "法兰盘",
    test: "flange",
    prompt: "创建直径 100mm、厚 12mm 的圆形法兰，中心贯穿孔直径 30mm，并在节圆直径 80mm 上均布 6 个直径 8mm 的贯穿孔。",
  },
  {
    label: "皮带轮",
    test: "pulley",
    prompt: "创建直径 100mm、厚 20mm 的简化皮带轮，中心贯穿孔直径 25mm，顶部有外径 90mm、内径 70mm、深 10mm 的环槽，外轮缘做 C1mm 倒角。",
  },
  {
    label: "三段阶梯轴",
    test: "stepped shaft",
    prompt: "使用一次旋转创建总长 90mm 的三段阶梯轴：各段直径和长度依次为 35×40mm、50×30mm、25×20mm，轴向关于原点居中。",
  },
  {
    label: "内六角螺栓",
    test: "socket bolt",
    prompt: "创建简化内六角圆柱头螺栓：杆径 10mm、杆长 50mm；头部直径 18mm、高 6mm；顶部内六角对边距 8mm、盲孔深 4mm；头杆交界 R0.5mm。",
  },
];

// 兼容旧的按钮处理函数；内容仍来自上面的自然语言规格。
const DICE_PROMPT = TEXT_PROMPTS.find((item) => item.test === "standard die").prompt;
const STORAGE_BOX_PROMPT = TEXT_PROMPTS.find((item) => item.test === "storage box").prompt;
const HEX_NUT_PROMPT = TEXT_PROMPTS.find((item) => item.test === "hex nut").prompt;
const MUG_PROMPT = TEXT_PROMPTS.find((item) => item.test === "mug").prompt;
const WRENCH_PROMPT = TEXT_PROMPTS.find((item) => item.test === "wrench").prompt;
const VASE_PROMPT = TEXT_PROMPTS.find((item) => item.test === "vase").prompt;
const KEY_PROMPT = TEXT_PROMPTS.find((item) => item.test === "key").prompt;
const GEAR_PROMPT = TEXT_PROMPTS.find((item) => item.test === "gear disc").prompt;
