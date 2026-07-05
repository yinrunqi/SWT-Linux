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
// 实测 11/11 步通过：包围盒 97×90×70mm、体积 125732 mm³ 与理论 ≈126000 完全吻合。
const MUG_PROMPT =
  "请建模一个马克杯：\n" +
  "1. 杯体是一个圆柱：外径 70mm、高 90mm，实心柱体，用上视基准面画外圆后向上拉伸；\n" +
  "2. 从杯体顶面向下挖一个空腔：直径 60mm（即壁厚 5mm）、深度 80mm（即底厚 10mm），" +
  "顶部敞开、底部封闭；\n" +
  "3. 加一个圆环形把手（形似 'D 字母' 的截面，用一个环形轮廓沿水平方向拉伸而成）：" +
  "在前视基准面上画两个同心圆（外径 40mm、内径 24mm，即把手条粗 8mm），" +
  "沿前后（Z）方向使用 midPlane 拉伸 8mm 厚度；\n" +
  "4. **把手必须与杯壁相交合并**（不能只是相切！）：把手同心圆的中心位于杯壁外侧偏内的位置——" +
  "让内圆的左沿伸入杯壁内部至少 2mm。（提示：杯外壁在 X=35mm 处，" +
  "把手中心 X 大约取 35 + 内半径 − 5 ≈ 42mm，能让内圆左沿到 X≈30mm，深入杯壁 5mm）；\n" +
  "5. 把手在垂直方向（Y）的位置居中于杯高中部，即 Y≈45mm；\n" +
  "6. 杯子沿 Y 方向从 0 延伸到 90mm（不用居中）。\n" +
  "所有尺寸使用米制，注意换算（1mm = 0.001m）。";

window.fillMugPrompt = function () { useExample(MUG_PROMPT); };

// ---- 事件绑定 ----
sendBtn.addEventListener("click", send);
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});

init();
