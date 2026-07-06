// ========================================
// SolidWorks LLM Demo — 前端交互
// ========================================

const API = "";  // 同源，无前缀
const chatEl = document.getElementById("chat");
const inputEl = document.getElementById("input");
const sendBtn = document.getElementById("send-btn");
const dotEl = document.getElementById("status-dot");
const statusText = document.getElementById("status-text");

// 设置面板
const settingsEl = document.getElementById("settings");
const settingsToggle = document.getElementById("settings-toggle");
const apiKeyEl = document.getElementById("api-key");
const baseUrlEl = document.getElementById("base-url");
const modelEl = document.getElementById("model-select");

let busy = false;

// ---- 设置面板切换 ----
settingsToggle.addEventListener("click", () => {
  settingsEl.classList.toggle("open");
  settingsToggle.classList.toggle("active");
});

// ---- localStorage 持久化设置 ----
function loadSettings() {
  apiKeyEl.value = localStorage.getItem("sw_api_key") || "";
  baseUrlEl.value = localStorage.getItem("sw_base_url") || "";
  modelEl.value = localStorage.getItem("sw_model") || "gpt-4o-mini";
  // 如果还没填 key，默认展开设置面板
  if (!apiKeyEl.value) {
    settingsEl.classList.add("open");
    settingsToggle.classList.add("active");
  }
}
function saveSettings() {
  localStorage.setItem("sw_api_key", apiKeyEl.value);
  localStorage.setItem("sw_base_url", baseUrlEl.value);
  localStorage.setItem("sw_model", modelEl.value);
}
apiKeyEl.addEventListener("input", saveSettings);
baseUrlEl.addEventListener("input", saveSettings);
modelEl.addEventListener("change", saveSettings);

// ---- 初始化 ----
async function init() {
  loadSettings();
  try {
    const res = await fetch(`${API}/api/ping`);
    const data = await res.json();
    if (data.sw_connected) {
      dotEl.classList.add("ok");
      statusText.textContent = "SolidWorks 已连接";
    } else {
      statusText.textContent = "SolidWorks 未连接（可用 dry-run）";
    }
  } catch {
    statusText.textContent = "后端未启动";
  }
}

// ---- 发送指令 ----
async function send() {
  const text = inputEl.value.trim();
  if (!text || busy) return;

  const apiKey = apiKeyEl.value.trim();
  if (!apiKey) {
    appendError("请先在顶部设置面板中填写 API Key");
    settingsEl.classList.add("open");
    settingsToggle.classList.add("active");
    apiKeyEl.focus();
    return;
  }

  busy = true;
  sendBtn.disabled = true;
  inputEl.value = "";

  // 移除欢迎区
  const welcome = document.querySelector(".welcome");
  if (welcome) welcome.remove();

  // 用户消息
  appendUser(text);

  // Loading
  const loadingId = appendLoading();

  try {
    const res = await fetch(`${API}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instruction: text,
        api_key: apiKey,
        base_url: baseUrlEl.value.trim(),
        model: modelEl.value,
      }),
    });
    const data = await res.json();
    removeLoading(loadingId);

    if (data.success) {
      appendAIResponse(data.plan, data.results, data.summary);
    } else {
      appendError(data.error || "未知错误");
    }
  } catch (e) {
    removeLoading(loadingId);
    appendError("请求失败: " + e.message);
  }

  busy = false;
  sendBtn.disabled = false;
  inputEl.focus();
}

// ---- 渲染函数 ----

function appendUser(text) {
  const div = document.createElement("div");
  div.className = "msg";
  div.innerHTML = `<div class="msg-user">${esc(text)}</div>`;
  chatEl.appendChild(div);
  scrollBottom();
}

let loadingCounter = 0;
function appendLoading() {
  const id = "loading-" + (++loadingCounter);
  const div = document.createElement("div");
  div.className = "msg";
  div.id = id;
  div.innerHTML = `<div class="msg-ai"><div class="loading"><div class="spinner"></div>正在思考并执行中…</div></div>`;
  chatEl.appendChild(div);
  scrollBottom();
  return id;
}

function removeLoading(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

function appendAIResponse(plan, results, summary) {
  const div = document.createElement("div");
  div.className = "msg";

  // 规划阶段
  let stepsHtml = plan.steps.map((s, i) => {
    const paramStr = s.params && Object.keys(s.params).length
      ? ` <span class="step-op">${esc(JSON.stringify(s.params))}</span>` : "";
    return `<li>
      <span class="step-num">${i + 1}</span>
      <span class="step-desc">
        <span class="step-op">${esc(s.operation)}</span>
        ${s.description ? " — " + esc(s.description) : ""}${paramStr}
      </span>
    </li>`;
  }).join("");

  // 执行阶段
  let execHtml = results.map(r => {
    const ok = r.success || r.status === "dry_run";
    const icon = ok ? "✅" : "❌";
    return `<li><span class="icon">${icon}</span>[${r.step}] ${esc(r.operation)} — ${esc(r.message || "")}</li>`;
  }).join("");

  const allOk = summary.all_ok;
  const sumClass = allOk ? "ok" : "fail";
  const sumIcon = allOk ? "✅" : "⚠️";

  div.innerHTML = `<div class="msg-ai">
    <div class="phase">
      <div class="phase-header" onclick="togglePhase(this)">🤔 建模计划 — ${esc(plan.user_intent)}</div>
      <div class="phase-body">
        ${plan.notes ? "<p>📝 " + esc(plan.notes) + "</p>" : ""}
        <ol class="step-list">${stepsHtml}</ol>
      </div>
    </div>
    <div class="phase">
      <div class="phase-header" onclick="togglePhase(this)">⚙️ 执行过程</div>
      <div class="phase-body">
        <ul class="exec-list">${execHtml}</ul>
      </div>
    </div>
    <div class="summary ${sumClass}">${sumIcon} 完成: ${summary.succeeded}/${summary.total} 步骤成功</div>
  </div>`;

  chatEl.appendChild(div);
  scrollBottom();
}

function appendError(msg) {
  const div = document.createElement("div");
  div.className = "msg";
  div.innerHTML = `<div class="msg-ai"><div class="summary fail">❌ ${esc(msg)}</div></div>`;
  chatEl.appendChild(div);
  scrollBottom();
}

// ---- 工具函数 ----

function togglePhase(header) {
  const body = header.nextElementSibling;
  if (body) body.style.display = body.style.display === "none" ? "" : "none";
}

function esc(s) {
  if (typeof s !== "string") s = String(s);
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function scrollBottom() {
  requestAnimationFrame(() => chatEl.scrollTop = chatEl.scrollHeight);
}

function useExample(text) {
  inputEl.value = text;
  inputEl.focus();
}

// 标准骰子的完整 prompt——与 test/objects/test_dice_llm.py 中的 PROMPT 逐字一致，
// 点击示例按钮一键填入，可命中已验证的成功计划缓存，稳定建出标准骰子。
const DICE_PROMPT =
  "请帮我建模一颗标准的六面骰子（色子），要求如下：\n" +
  "1. 骰子主体是一个边长 20mm 的正方体；\n" +
  "2. 正方体的 12 条棱边都做 R1.5mm 的圆角，让边角圆润；\n" +
  "3. 六个面上分别有 1、2、3、4、5、6 个圆形点数；每个点数是一个直径约 3mm、深约 1.5mm 的圆形凹坑（向内凹陷，不是凸起）；\n" +
  "4. 遵循标准骰子规则：相对的两个面点数之和为 7（即 1 对 6、2 对 5、3 对 4）；\n" +
  "5. 各面点数的常规排布：1 点在面中心；2 点和 3 点沿面的一条对角线均匀分布；4 点在面的四个角附近；5 点是四个角加面中心；6 点是两列、每列三个点。\n" +
  "请自行计算每个面以及每个凹点在模型空间中的位置，完成完整建模。";

window.fillDicePrompt = function () { useExample(DICE_PROMPT); };

// 收纳盒的完整 prompt——与 test/objects/test_storage_box.py 中的 prompt 逐字一致。
// 实测 11/11 步通过：盒体拉伸 + 顶面切除内腔 + 顶部 4 边 R3mm 圆角。
const STORAGE_BOX_PROMPT =
  "创建一个长方形收纳盒：外部尺寸为长 120mm、宽 80mm、高 50mm，" +
  "盒壁厚度 8mm，盒子内部是一个开口向上的空腔（顶部敞开），" +
  "底部厚度也是 8mm。最后对盒子顶部开口的四条边做 R3mm 圆角。";

window.fillStorageBoxPrompt = function () { useExample(STORAGE_BOX_PROMPT); };

// 六角螺母（M20）的完整 prompt——与 test/objects/test_hex_nut_llm.py 中的 PROMPT 逐字一致。
// 实测 7/7 步通过：外接圆半径 17.322mm、厚度 8mm、⌀10mm 贯穿孔，体积 5608 mm³。
const HEX_NUT_PROMPT =
  "创建一个 M20 六角螺母：正六边形外形（对边距 30mm，即两条平行边之间的距离为 30mm），" +
  "厚度 8mm，中心有一个直径 10mm 的贯穿圆孔。螺母沿厚度方向以原点居中。";

window.fillHexNutPrompt = function () { useExample(HEX_NUT_PROMPT); };

// 马克杯（含圆环把手）的完整 prompt——与 test/objects/test_mug_llm.py 中的 PROMPT 逐字一致。
// 采用「杯身→把手→挖空腔」的顺序，避免把手内嵌部分残留在杯内壁形成难看的凸起。
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

window.fillMugPrompt = function () { useExample(MUG_PROMPT); };

// 开口扳手 prompt——与 test/objects/test_wrench_llm.py 中的 PROMPT 逐字一致。
// 实测 10/10 步通过：包围盒 140×8×40mm、体积 20938 mm³。
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

window.fillWrenchPrompt = function () { useExample(WRENCH_PROMPT); };

// 花瓶 prompt——与 test/objects/test_vase_llm.py 中的 PROMPT 逐字一致。
// 实测 15/15 步通过：包围盒 90×150×90mm、体积 416504 mm³。
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

window.fillVasePrompt = function () { useExample(VASE_PROMPT); };

// ---- 事件绑定 ----
sendBtn.addEventListener("click", send);
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});

init();
