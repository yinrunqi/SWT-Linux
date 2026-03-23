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

// ---- 事件绑定 ----
sendBtn.addEventListener("click", send);
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});

init();
