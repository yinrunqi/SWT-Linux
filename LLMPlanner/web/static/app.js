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
const visionModelEl = document.getElementById("vision-model-select");
const planModelEl = document.getElementById("plan-model-select");

// 齿轮 Motion Study 控件
const gearLoadBtn = document.getElementById("gear-load-btn");
const gearPlayBtn = document.getElementById("gear-play-btn");
const gearStopBtn = document.getElementById("gear-stop-btn");
const gearAnimationStatus = document.getElementById("gear-animation-status");
let gearStatusTimer = null;

// 图片上传相关
const fileInputEl = document.getElementById("file-input");
const attachBtn = document.getElementById("attach-btn");
const imgPreviewBar = document.getElementById("image-preview-bar");
const imgPreviewThumb = document.getElementById("image-preview-thumb");
const imgPreviewName = document.getElementById("image-preview-name");
const imgPreviewSize = document.getElementById("image-preview-size");
const imgPreviewRemove = document.getElementById("image-preview-remove");
const dropOverlay = document.getElementById("drop-overlay");
const imageModal = document.getElementById("image-modal");
const imageModalImg = document.getElementById("image-modal-img");

// 当前选中的图片（base64 无前缀 + data URI 供预览）
let currentImage = null;  // { name, sizeKB, base64, dataUri }

let busy = false;

// ---- 设置面板切换 ----
settingsToggle.addEventListener("click", () => {
  settingsEl.classList.toggle("open");
  settingsToggle.classList.toggle("active");
});

// ---- localStorage 持久化设置 ----
function loadSettings() {
  apiKeyEl.value = localStorage.getItem("sw_api_key") || "";
  // base_url 用 HTML value 的默认（juheai 中转站）作为空值时的兜底
  const savedBase = localStorage.getItem("sw_base_url");
  if (savedBase !== null) baseUrlEl.value = savedBase;
  // Vision/Plan 模型独立记忆，缺省都用 HTML 里已 selected 的 claude-sonnet-5
  const savedVision = localStorage.getItem("sw_vision_model");
  if (savedVision) visionModelEl.value = savedVision;
  const savedPlan = localStorage.getItem("sw_plan_model");
  if (savedPlan) planModelEl.value = savedPlan;
  // 如果还没填 key，默认展开设置面板
  if (!apiKeyEl.value) {
    settingsEl.classList.add("open");
    settingsToggle.classList.add("active");
  }
}
function saveSettings() {
  localStorage.setItem("sw_api_key", apiKeyEl.value);
  localStorage.setItem("sw_base_url", baseUrlEl.value);
  localStorage.setItem("sw_vision_model", visionModelEl.value);
  localStorage.setItem("sw_plan_model", planModelEl.value);
}
apiKeyEl.addEventListener("input", saveSettings);
baseUrlEl.addEventListener("input", saveSettings);
visionModelEl.addEventListener("change", saveSettings);
planModelEl.addEventListener("change", saveSettings);

// ---- 初始化 ----
async function init() {
  loadSettings();
  renderPromptButtons("basic-buttons", BASIC_PROMPTS);
  renderPromptButtons("text-buttons", TEXT_PROMPTS);
  renderThreeViewButtons();
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

function setGearAnimationStatus(text, state = "") {
  gearAnimationStatus.textContent = text;
  gearAnimationStatus.classList.remove("ok", "fail");
  if (state) gearAnimationStatus.classList.add(state);
}

async function callGearAnimation(path) {
  const response = await fetch(`${API}/api/gear-animation/${path}`, {
    method: path === "status" ? "GET" : "POST",
    headers: { "Content-Type": "application/json" },
  });
  const data = await response.json();
  if (!response.ok || !data.success) {
    throw new Error(data.error || data.message || `HTTP ${response.status}`);
  }
  return data;
}

async function loadGearAnimation() {
  gearLoadBtn.disabled = true;
  setGearAnimationStatus("正在打开装配体并创建旋转马达…");
  try {
    const data = await callGearAnimation("load");
    gearPlayBtn.disabled = false;
    gearStopBtn.disabled = false;
    setGearAnimationStatus(data.message || "齿轮动画已就绪", "ok");
  } catch (error) {
    gearLoadBtn.disabled = false;
    setGearAnimationStatus(`加载失败：${error.message}`, "fail");
  }
}

async function refreshGearAnimationStatus() {
  try {
    const data = await callGearAnimation("status");
    const result = data.result || {};
    const time = Number(result.currentTime || 0).toFixed(2);
    const duration = Number(result.duration || 0).toFixed(2);
    if (result.isPlaying) {
      setGearAnimationStatus(`正在播放：${time}s / ${duration}s`, "ok");
      return;
    }
    setGearAnimationStatus(`已停止：${time}s / ${duration}s`, "ok");
    if (gearStatusTimer) {
      clearInterval(gearStatusTimer);
      gearStatusTimer = null;
    }
  } catch (error) {
    setGearAnimationStatus(`状态读取失败：${error.message}`, "fail");
  }
}

async function playGearAnimation() {
  gearPlayBtn.disabled = true;
  setGearAnimationStatus("正在启动 Motion Study…");
  try {
    await callGearAnimation("play");
    setGearAnimationStatus("正在播放齿轮联动动画…", "ok");
    if (gearStatusTimer) clearInterval(gearStatusTimer);
    gearStatusTimer = setInterval(refreshGearAnimationStatus, 400);
  } catch (error) {
    setGearAnimationStatus(`播放失败：${error.message}`, "fail");
  } finally {
    gearPlayBtn.disabled = false;
  }
}

async function stopGearAnimation() {
  try {
    await callGearAnimation("stop");
    await refreshGearAnimationStatus();
  } catch (error) {
    setGearAnimationStatus(`停止失败：${error.message}`, "fail");
  }
}

// ---- 发送指令 ----
async function send() {
  const text = inputEl.value.trim();
  const hasImage = currentImage !== null;
  // 文字与图片至少要有一个
  if (!text && !hasImage) return;
  if (busy) return;

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

  // 用户消息（含图片则一并展示）
  appendUser(text, hasImage ? currentImage.dataUri : null);

  // 发送后清空图片选择（保留最后一次以便查看，但下一次发送要重新选）
  const imageToSend = hasImage ? currentImage.base64 : null;
  if (hasImage) clearImage();

  // Loading
  const loadingId = appendLoading(imageToSend ? "正在识别图纸并规划建模 …" : "正在思考并执行中 …");

  try {
    const res = await fetch(`${API}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instruction: text,
        image: imageToSend,
        api_key: apiKey,
        base_url: baseUrlEl.value.trim(),
        vision_model: visionModelEl.value,
        plan_model: planModelEl.value,
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

function appendUser(text, imageDataUri) {
  const div = document.createElement("div");
  div.className = "msg";
  const imgHtml = imageDataUri
    ? `<img class="msg-user-image" src="${imageDataUri}" alt="uploaded" onclick="openImageModal('${imageDataUri}')">`
    : "";
  const textHtml = text ? esc(text) : "<em style='opacity:0.6'>（仅图片）</em>";
  // 若同时有图和文，前面加 🎯 前缀；纯图时不加
  const prefix = text ? "🎯 " : "";
  div.innerHTML = `<div class="msg-user" style="white-space: normal;">
    ${imgHtml}
    <div style="white-space: pre-wrap;">${prefix}${textHtml}</div>
  </div>`;
  chatEl.appendChild(div);
  scrollBottom();
}

let loadingCounter = 0;
function appendLoading(hint) {
  const id = "loading-" + (++loadingCounter);
  const div = document.createElement("div");
  div.className = "msg";
  div.id = id;
  div.innerHTML = `<div class="msg-ai"><div class="loading"><div class="spinner"></div>${esc(hint || "正在思考并执行中…")}</div></div>`;
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
window.useExample = useExample;

function renderPromptButtons(containerId, items) {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = "";
  for (const item of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = item.label;
    const tip = item.title || item.test || "";
    if (tip) btn.title = tip;
    btn.onclick = () => useExample(item.prompt);
    container.appendChild(btn);
  }
}

// 向后兼容旧 onclick 名称
window.fillDicePrompt = () => useExample(DICE_PROMPT);
window.fillStorageBoxPrompt = () => useExample(STORAGE_BOX_PROMPT);
window.fillHexNutPrompt = () => useExample(HEX_NUT_PROMPT);
window.fillMugPrompt = () => useExample(MUG_PROMPT);
window.fillWrenchPrompt = () => useExample(WRENCH_PROMPT);
window.fillVasePrompt = () => useExample(VASE_PROMPT);
window.fillKeyPrompt = () => useExample(KEY_PROMPT);
window.fillGearPrompt = () => useExample(GEAR_PROMPT);

// ========================================
// 图片上传：选择 / 预览 / 压缩 / base64 / 拖拽 / 查看大图
// ========================================

const MAX_EDGE = 2048;       // 最长边不超过此像素
const JPEG_QUALITY = 0.85;
const MAX_ORIGINAL_MB = 20;  // OpenAI Vision 上限

// 从 File 对象读为压缩后的 base64（无前缀）+ data URI 缩略图
async function loadAndCompressImage(file) {
  if (!file.type.startsWith("image/")) {
    throw new Error("请上传图片文件（jpg/png/webp/gif）");
  }
  if (file.size > MAX_ORIGINAL_MB * 1024 * 1024) {
    throw new Error(`图片过大（${(file.size / 1024 / 1024).toFixed(1)}MB > ${MAX_ORIGINAL_MB}MB）`);
  }

  const bitmap = await createImageBitmap(file);
  // 计算缩放
  const maxEdge = Math.max(bitmap.width, bitmap.height);
  const scale = maxEdge > MAX_EDGE ? MAX_EDGE / maxEdge : 1;
  const w = Math.round(bitmap.width * scale);
  const h = Math.round(bitmap.height * scale);

  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  // 白底：三视图 PNG 若有透明背景会导致 JPEG 变黑，先铺白
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(bitmap, 0, 0, w, h);

  const dataUri = canvas.toDataURL("image/jpeg", JPEG_QUALITY);
  const base64 = dataUri.split(",", 2)[1];
  return {
    name: file.name,
    sizeKB: Math.round((base64.length * 3 / 4) / 1024),
    base64,
    dataUri,
    width: w,
    height: h,
  };
}

async function handleFileSelected(file) {
  try {
    const img = await loadAndCompressImage(file);
    setImage(img);
  } catch (e) {
    appendError(e.message);
  }
}

function setImage(img) {
  currentImage = img;
  imgPreviewThumb.src = img.dataUri;
  imgPreviewName.textContent = img.name;
  imgPreviewSize.textContent = `${img.width}×${img.height} · ${img.sizeKB} KB`;
  imgPreviewBar.style.display = "flex";
  attachBtn.classList.add("loaded");
}

function clearImage() {
  currentImage = null;
  fileInputEl.value = "";
  imgPreviewBar.style.display = "none";
  imgPreviewThumb.src = "";
  attachBtn.classList.remove("loaded");
}

function openImageModal(dataUri) {
  imageModalImg.src = dataUri;
  imageModal.classList.add("active");
}
function closeImageModal() {
  imageModal.classList.remove("active");
}
window.openImageModal = openImageModal;
window.closeImageModal = closeImageModal;

// 点击预览缩略图放大
imgPreviewThumb.addEventListener("click", () => {
  if (currentImage) openImageModal(currentImage.dataUri);
});

// 附件按钮触发文件选择
attachBtn.addEventListener("click", () => fileInputEl.click());

// 文件选择
fileInputEl.addEventListener("change", async () => {
  const file = fileInputEl.files && fileInputEl.files[0];
  if (file) await handleFileSelected(file);
});

// 移除图片
imgPreviewRemove.addEventListener("click", clearImage);

// 拖拽上传（整个页面接收）
let dragCounter = 0;
window.addEventListener("dragenter", e => {
  if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) {
    e.preventDefault();
    dragCounter++;
    dropOverlay.classList.add("active");
  }
});
window.addEventListener("dragover", e => {
  if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) {
    e.preventDefault();
  }
});
window.addEventListener("dragleave", e => {
  if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files")) {
    dragCounter = Math.max(0, dragCounter - 1);
    if (dragCounter === 0) dropOverlay.classList.remove("active");
  }
});
window.addEventListener("drop", async e => {
  e.preventDefault();
  dragCounter = 0;
  dropOverlay.classList.remove("active");
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (file) await handleFileSelected(file);
});

// ===== 三视图示例：数据从 /api/three-view-samples 拉取 =====
// **单一数据源**：LLMPlanner/datasets/manual_three_view/samples.json
// build_all.py / test_three_view_e2e.py / 本前端三处都从这个 JSON 读，
// 之后新增样例只需要改 samples.json，三处自动同步。
let THREE_VIEW_SAMPLES = {};  // 启动时由 fetch 填充：{part_id: {subdir, label, prompt}}

// 通用：加载指定三视图样例（图片 + prompt 自动填充）
// ⚠️ 关键：**样例图片不走 canvas JPEG 压缩管线**——直接用原始 PNG bytes 的 base64。
//    原因：canvas.toDataURL("image/jpeg", ...) 在浏览器里不完全确定性
//    (同一 PNG 每次转出的 JPEG 都可能有几字节差异)，会让 image_sha256 每次不同，
//    导致端到端 cache 永远 miss——用户点样例每次都花 API 钱。
//    直接读原始 PNG bytes → 每次 hash 一致 → cache 稳定命中。
async function loadRawPngAsImage(url, filename) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`图片未找到: ${url}`);
  const blob = await res.blob();
  // 读原始 bytes 转 base64（不经过 canvas 重编码）
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  // 每 1024 字节分片调用 String.fromCharCode 避免栈溢出
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1024) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + 1024, bytes.length)));
  }
  const base64 = btoa(binary);
  // 为预览需要缩略 dataUri（用 canvas 是安全的，只影响显示不影响 base64）
  const bitmap = await createImageBitmap(blob);
  const canvas = document.createElement("canvas");
  const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  const dataUri = canvas.toDataURL("image/png");  // 缩略图仅用于预览
  return {
    name: filename,
    sizeKB: Math.round((base64.length * 3 / 4) / 1024),
    base64,  // ⭐ 原始 PNG 的 base64, 与 python 的 base64.b64encode(f.read()) 完全一致
    dataUri,
    width: bitmap.width,
    height: bitmap.height,
  };
}

window.fillThreeViewSample = async function (sampleId) {
  const s = THREE_VIEW_SAMPLES[sampleId];
  if (!s) {
    appendError(`未知三视图样例: ${sampleId}`);
    return;
  }
  try {
    const url = `/datasets/manual_three_view/${s.subdir}/drawing.png`;
    const filename = `${s.subdir}.png`;
    const img = await loadRawPngAsImage(url, filename);
    setImage(img);
    inputEl.value = s.prompt;
    inputEl.focus();
  } catch (e) {
    appendError("加载三视图示例失败：" + e.message);
  }
};

// 向后兼容：旧 HTML 缓存里可能还有 onclick="fillThreeViewExample()"
window.fillThreeViewExample = () => window.fillThreeViewSample("B01");

// 从服务端拉样例元数据，渲染成按钮
async function renderThreeViewButtons() {
  const container = document.getElementById("three-view-buttons");
  if (!container) return;
  container.innerHTML = "";
  try {
    const res = await fetch("/api/three-view-samples");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    THREE_VIEW_SAMPLES = {};  // 重置
    for (const s of data.samples || []) {
      THREE_VIEW_SAMPLES[s.part_id] = s;
      const btn = document.createElement("button");
      btn.textContent = s.label || s.part_id;
      btn.title = s.prompt || s.name || "";
      btn.onclick = () => window.fillThreeViewSample(s.part_id);
      container.appendChild(btn);
    }
    if (Object.keys(THREE_VIEW_SAMPLES).length === 0) {
      container.innerHTML = "<span style='color:var(--text-dim);font-size:12px'>（暂无三视图样例）</span>";
    }
  } catch (e) {
    container.innerHTML = `<span style='color:var(--error);font-size:12px'>加载三视图样例失败: ${e.message}</span>`;
  }
}


// ---- 事件绑定 ----
sendBtn.addEventListener("click", send);
gearLoadBtn.addEventListener("click", loadGearAnimation);
gearPlayBtn.addEventListener("click", playGearAnimation);
gearStopBtn.addEventListener("click", stopGearAnimation);
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});

init();
