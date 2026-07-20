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
const providerEl = document.getElementById("provider-select");
const modeEl = document.getElementById("mode-select");
const maxTurnsEl = document.getElementById("max-turns");
const maxTurnsRow = document.getElementById("max-turns-row");
const apiKeyEl = document.getElementById("api-key");
const baseUrlEl = document.getElementById("base-url");
const visionModelEl = document.getElementById("vision-model-select");
const planModelEl = document.getElementById("plan-model-select");
const PROVIDER_DEFAULTS = {
  aiping: {
    baseUrl: "https://aiping.cn/api/v1",
    visionModel: "GLM-4.6V",
    planModel: "Qwen3.5-397B-A17B",
  },
  juheai: {
    baseUrl: "https://api.juheai.top/v1",
    visionModel: "basic/claude-sonnet-5",
    planModel: "basic/claude-sonnet-5",
  },
};

// 齿轮 Motion Study 控件
const gearLoadBtn = document.getElementById("gear-load-btn");
const gearPlayBtn = document.getElementById("gear-play-btn");
const gearStopBtn = document.getElementById("gear-stop-btn");
const gearAnimationStatus = document.getElementById("gear-animation-status");
const gearboxRunBtn = document.getElementById("gearbox-run-btn");
const gearboxRunStatus = document.getElementById("gearbox-run-status");
const gearboxGearSelect = document.getElementById("gearbox-gear-select");
const gearboxMotionLoadBtn = document.getElementById("gearbox-motion-load-btn");
const gearboxMotionPlayBtn = document.getElementById("gearbox-motion-play-btn");
const gearboxMotionStopBtn = document.getElementById("gearbox-motion-stop-btn");
const engineRunBtn = document.getElementById("engine-run-btn");
const engineAngle = document.getElementById("engine-angle");
const engineAngleValue = document.getElementById("engine-angle-value");
const enginePoseBtn = document.getElementById("engine-pose-btn");
const engineRunStatus = document.getElementById("engine-run-status");
const projectorRunBtn = document.getElementById("projector-run-btn");
const projectorAngle = document.getElementById("projector-angle");
const projectorAngleValue = document.getElementById("projector-angle-value");
const projectorPoseBtn = document.getElementById("projector-pose-btn");
const projectorRunStatus = document.getElementById("projector-run-status");
let gearStatusTimer = null;
let gearboxMotionTimer = null;

// 连续编辑测试控件
const editSection = document.getElementById("edit-section");
const editStartBtn = document.getElementById("edit-start-btn");
const editContinueBtn = document.getElementById("edit-continue-btn");
const editResetBtn = document.getElementById("edit-reset-btn");
const editWorkflowStatus = document.getElementById("edit-workflow-status");
let pendingEditStep = null;

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
  // 首次切换到 AI Ping 时备份旧浏览器配置，并采用新的 HTML 默认值。
  const settingsVersion = "aiping-v1";
  if (localStorage.getItem("sw_settings_version") !== settingsVersion) {
    for (const key of ["sw_api_key", "sw_base_url", "sw_vision_model", "sw_plan_model"]) {
      const oldValue = localStorage.getItem(key);
      if (oldValue !== null) localStorage.setItem(`legacy_${key}`, oldValue);
      localStorage.removeItem(key);
    }
    localStorage.setItem("sw_settings_version", settingsVersion);
  }
  const agentSettingsVersion = "ledger-v2";
  if (localStorage.getItem("sw_agent_settings_version") !== agentSettingsVersion) {
    localStorage.removeItem("sw_max_turns");
    localStorage.setItem("sw_agent_settings_version", agentSettingsVersion);
  }
  providerEl.value = localStorage.getItem("sw_provider") || "aiping";
  modeEl.value = localStorage.getItem("sw_mode") || "single_turn";
  maxTurnsEl.value = localStorage.getItem("sw_max_turns") || "";
  apiKeyEl.value = localStorage.getItem("sw_api_key") || "";
  // base_url 用 HTML value 的 AI Ping 默认值作为空值时的兜底
  const savedBase = localStorage.getItem("sw_base_url");
  if (savedBase !== null) baseUrlEl.value = savedBase;
  // Vision/Plan 模型独立记忆，缺省采用 HTML 中选定的 AI Ping 模型。
  const savedVision = localStorage.getItem("sw_vision_model");
  if (savedVision) visionModelEl.value = savedVision;
  const savedPlan = localStorage.getItem("sw_plan_model");
  if (savedPlan) planModelEl.value = savedPlan;
  updateModeUI();
}
function saveSettings() {
  localStorage.setItem("sw_provider", providerEl.value);
  localStorage.setItem("sw_mode", modeEl.value);
  localStorage.setItem("sw_max_turns", maxTurnsEl.value);
  localStorage.setItem("sw_api_key", apiKeyEl.value);
  localStorage.setItem(`sw_api_key_${providerEl.value}`, apiKeyEl.value);
  localStorage.setItem("sw_base_url", baseUrlEl.value);
  localStorage.setItem("sw_vision_model", visionModelEl.value);
  localStorage.setItem("sw_plan_model", planModelEl.value);
}
function switchProvider() {
  const defaults = PROVIDER_DEFAULTS[providerEl.value];
  apiKeyEl.value = localStorage.getItem(`sw_api_key_${providerEl.value}`) || "";
  if (defaults) {
    baseUrlEl.value = defaults.baseUrl;
    visionModelEl.value = defaults.visionModel;
    planModelEl.value = defaults.planModel;
  }
  saveSettings();
}
function updateModeUI() {
  const isMultiTurn = modeEl.value === "multi_turn";
  maxTurnsRow.hidden = !isMultiTurn;
  attachBtn.disabled = isMultiTurn;
  attachBtn.title = isMultiTurn
    ? "Multi-turn MVP 暂只支持纯文本"
    : "上传三视图或参考图片";
  if (isMultiTurn && currentImage) clearImage();
}
providerEl.addEventListener("change", switchProvider);
modeEl.addEventListener("change", () => {
  updateModeUI();
  saveSettings();
});
maxTurnsEl.addEventListener("change", saveSettings);
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

async function runTwoStageGearbox() {
  if (busy) return;
  busy = true;
  gearboxRunBtn.disabled = true;
  sendBtn.disabled = true;
  gearboxRunStatus.textContent =
    "正在准备参数化零件，并由 Multi-turn Agent 完成装配与机器验收…";
  gearboxRunStatus.classList.remove("ok", "fail");
  appendUser(
    "装配参数化双级圆柱齿轮减速箱（5 个组件、两级传动、机器验收）",
    null,
  );
  const loadingId = appendLoading(
    "正在构建双级减速箱；首次运行会产生 LLM token，重复运行将优先命中状态缓存…",
  );
  try {
    const response = await fetch(
      `${API}/api/assembly-examples/two-stage-gearbox/run`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: providerEl.value,
          api_key: apiKeyEl.value.trim(),
          base_url: baseUrlEl.value.trim(),
          vision_model: visionModelEl.value,
          plan_model: planModelEl.value,
          max_turns: maxTurnsEl.value
            ? Number(maxTurnsEl.value)
            : 18,
        }),
      },
    );
    const data = await response.json();
    removeLoading(loadingId);
    if (!response.ok || !data.success) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    appendMultiTurnResponse(data.multi_turn, data.summary);
    const tokens = Number(data.token_usage?.total_tokens || 0);
    const cacheText = tokens === 0
      ? "本次 LLM 决策全部命中缓存，token 花费为 0"
      : `本次新消耗 ${tokens} tokens，决策已写入缓存`;
    const accepted = Boolean(data.summary?.all_ok);
    gearboxRunStatus.textContent =
      `${accepted ? "装配与验收成功" : "装配完成但验收未全部通过"}；${cacheText}`;
    gearboxRunStatus.classList.add(accepted ? "ok" : "fail");
    if (!accepted) {
      appendError(
        `减速箱最终验收未通过：${data.acceptance?.message || "请查看验收详情"}`,
      );
    }
  } catch (error) {
    removeLoading(loadingId);
    gearboxRunStatus.textContent = `减速箱构建失败：${error.message}`;
    gearboxRunStatus.classList.add("fail");
    appendError(`减速箱构建失败：${error.message}`);
  } finally {
    busy = false;
    gearboxRunBtn.disabled = false;
    sendBtn.disabled = false;
  }
}

async function loadGearboxMotion() {
  gearboxMotionLoadBtn.disabled = true;
  gearboxMotionPlayBtn.disabled = true;
  gearboxMotionStopBtn.disabled = true;
  gearboxRunStatus.textContent =
    "正在打开双级减速箱并为所选齿轮创建确定性旋转马达…";
  gearboxRunStatus.classList.remove("ok", "fail");
  try {
    const response = await fetch(`${API}/api/gearbox-animation/load`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gear_role: gearboxGearSelect.value,
        rpm: 60,
        turns: 5,
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    gearboxMotionPlayBtn.disabled = false;
    gearboxMotionStopBtn.disabled = false;
    gearboxRunStatus.textContent =
      `${data.message}；该过程不调用 LLM`;
    gearboxRunStatus.classList.add("ok");
  } catch (error) {
    gearboxRunStatus.textContent = `加载旋转马达失败：${error.message}`;
    gearboxRunStatus.classList.add("fail");
  } finally {
    gearboxMotionLoadBtn.disabled = false;
  }
}

async function refreshGearboxMotionStatus() {
  try {
    const data = await callGearAnimation("status");
    const result = data.result || {};
    const time = Number(result.currentTime || 0).toFixed(2);
    const duration = Number(result.duration || 0).toFixed(2);
    if (result.isPlaying) {
      gearboxRunStatus.textContent =
        `所选齿轮正在旋转：${time}s / ${duration}s（纯 SW API）`;
      return;
    }
    gearboxRunStatus.textContent =
      `旋转已停止：${time}s / ${duration}s（纯 SW API）`;
    if (gearboxMotionTimer) {
      clearInterval(gearboxMotionTimer);
      gearboxMotionTimer = null;
    }
  } catch (error) {
    gearboxRunStatus.textContent = `旋转状态读取失败：${error.message}`;
    gearboxRunStatus.classList.add("fail");
  }
}

async function playGearboxMotion() {
  gearboxMotionPlayBtn.disabled = true;
  try {
    await callGearAnimation("play");
    gearboxRunStatus.textContent = "正在旋转所选齿轮并通过配合联动其余齿轮…";
    gearboxRunStatus.classList.add("ok");
    if (gearboxMotionTimer) clearInterval(gearboxMotionTimer);
    gearboxMotionTimer = setInterval(refreshGearboxMotionStatus, 400);
  } catch (error) {
    gearboxRunStatus.textContent = `旋转失败：${error.message}`;
    gearboxRunStatus.classList.add("fail");
  } finally {
    gearboxMotionPlayBtn.disabled = false;
  }
}

async function stopGearboxMotion() {
  try {
    await callGearAnimation("stop");
    await refreshGearboxMotionStatus();
  } catch (error) {
    gearboxRunStatus.textContent = `停止失败：${error.message}`;
    gearboxRunStatus.classList.add("fail");
  }
}

async function runSingleCylinderEngine() {
  if (busy) return;
  busy = true;
  engineRunBtn.disabled = true;
  sendBtn.disabled = true;
  engineRunStatus.textContent =
    "正在准备 10 个参数化零件，并由 Multi-turn Agent 装配、检查正时和完整行程…";
  engineRunStatus.classList.remove("ok", "fail");
  appendUser(
    "装配带 2:1 正时齿轮的单缸曲柄滑块发动机，并验收完整运动周期",
    null,
  );
  const loadingId = appendLoading(
    "正在构建复杂发动机机构；重复运行将复用成功的 Agent 状态缓存…",
  );
  try {
    const response = await fetch(
      `${API}/api/assembly-examples/single-cylinder-engine/run`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: providerEl.value,
          api_key: apiKeyEl.value.trim(),
          base_url: baseUrlEl.value.trim(),
          vision_model: visionModelEl.value,
          plan_model: planModelEl.value,
          max_turns: maxTurnsEl.value
            ? Number(maxTurnsEl.value)
            : 24,
        }),
      },
    );
    const data = await response.json();
    removeLoading(loadingId);
    if (!response.ok || !data.success) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    appendMultiTurnResponse(data.multi_turn, data.summary);
    const tokens = Number(data.token_usage?.total_tokens || 0);
    const cacheText = tokens === 0
      ? "Agent 决策全部命中缓存，token 花费为 0"
      : `本次新消耗 ${tokens} tokens`;
    const accepted = Boolean(data.summary?.all_ok);
    const stroke = Number(
      data.kinematic_acceptance?.actual_stroke_mm || 0,
    ).toFixed(3);
    engineRunStatus.textContent = accepted
      ? `装配及五角度验收成功，实测行程 ${stroke} mm；${cacheText}`
      : `装配或运动学验收未通过；${cacheText}`;
    engineRunStatus.classList.add(accepted ? "ok" : "fail");
  } catch (error) {
    removeLoading(loadingId);
    engineRunStatus.textContent = `发动机构建失败：${error.message}`;
    engineRunStatus.classList.add("fail");
    appendError(`发动机构建失败：${error.message}`);
  } finally {
    busy = false;
    engineRunBtn.disabled = false;
    sendBtn.disabled = false;
  }
}

async function setEnginePose() {
  enginePoseBtn.disabled = true;
  engineRunStatus.textContent =
    `正在通过确定性 SW API 设置 ${engineAngle.value}° 姿态…`;
  engineRunStatus.classList.remove("ok", "fail");
  try {
    const response = await fetch(`${API}/api/engine-animation/pose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ angle_deg: Number(engineAngle.value) }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    engineRunStatus.textContent =
      `${data.message}，活塞销位置 ${Number(data.slider_mm).toFixed(3)} mm；该过程不调用 LLM`;
    engineRunStatus.classList.add("ok");
  } catch (error) {
    engineRunStatus.textContent = `机构姿态设置失败：${error.message}`;
    engineRunStatus.classList.add("fail");
  } finally {
    enginePoseBtn.disabled = false;
  }
}

async function runFilmProjector() {
  if (busy) return;
  busy = true;
  projectorRunBtn.disabled = true;
  sendBtn.disabled = true;
  projectorRunStatus.textContent =
    "正在准备 15 个精密零件，并由 Multi-turn Agent 装配放映、送片和快门机构…";
  projectorRunStatus.classList.remove("ok", "fail");
  appendUser(
    "装配带四槽间歇送片和同步快门的复古 16mm 电影放映机",
    null,
  );
  const loadingId = appendLoading(
    "正在构建电影放映机；重复运行将复用成功的 Agent 状态缓存…",
  );
  try {
    const response = await fetch(
      `${API}/api/assembly-examples/film-projector/run`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider: providerEl.value,
          api_key: apiKeyEl.value.trim(),
          base_url: baseUrlEl.value.trim(),
          vision_model: visionModelEl.value,
          plan_model: planModelEl.value,
          max_turns: maxTurnsEl.value
            ? Number(maxTurnsEl.value)
            : 28,
        }),
      },
    );
    const data = await response.json();
    removeLoading(loadingId);
    if (!response.ok || !data.success) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    appendMultiTurnResponse(data.multi_turn, data.summary);
    const tokens = Number(data.token_usage?.total_tokens || 0);
    const cacheText = tokens === 0
      ? "Agent 决策全部命中缓存，token 花费为 0"
      : `本次新消耗 ${tokens} tokens`;
    const accepted = Boolean(data.summary?.all_ok);
    const output = Number(
      data.indexing_acceptance?.four_input_cycles_output_deg || 0,
    ).toFixed(3);
    projectorRunStatus.textContent = accepted
      ? `装配及间歇分度验收成功，四周期输出 ${output}°；${cacheText}`
      : `装配或分度验收未通过；${cacheText}`;
    projectorRunStatus.classList.add(accepted ? "ok" : "fail");
  } catch (error) {
    removeLoading(loadingId);
    projectorRunStatus.textContent = `放映机构建失败：${error.message}`;
    projectorRunStatus.classList.add("fail");
    appendError(`放映机构建失败：${error.message}`);
  } finally {
    busy = false;
    projectorRunBtn.disabled = false;
    sendBtn.disabled = false;
  }
}

async function setProjectorPose() {
  projectorPoseBtn.disabled = true;
  projectorRunStatus.textContent =
    `正在通过确定性 SW API 设置 ${projectorAngle.value}° 输入姿态…`;
  projectorRunStatus.classList.remove("ok", "fail");
  try {
    const response = await fetch(`${API}/api/projector-animation/pose`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_angle_deg: Number(projectorAngle.value),
      }),
    });
    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    const state = data.engaged ? "驱动销啮合、正在分度" : "停歇锁止";
    projectorRunStatus.textContent =
      `${data.message}，输出轮 ${Number(data.output_angle_deg).toFixed(3)}°，${state}；不调用 LLM`;
    projectorRunStatus.classList.add("ok");
  } catch (error) {
    projectorRunStatus.textContent = `分度姿态设置失败：${error.message}`;
    projectorRunStatus.classList.add("fail");
  } finally {
    projectorPoseBtn.disabled = false;
  }
}

function setEditWorkflowStatus(text, state = "") {
  editWorkflowStatus.textContent = text;
  editWorkflowStatus.classList.remove("ok", "fail");
  if (state) editWorkflowStatus.classList.add(state);
}

function resetEditWorkflow() {
  pendingEditStep = null;
  editContinueBtn.disabled = true;
  setEditWorkflowStatus("请先执行第 1 步；成功后将自动解锁第 2 步。");
}

function fillEditStep(step) {
  if (step === "base") {
    useExample(EDIT_BASE_PROMPT, { editStep: step });
    setEditWorkflowStatus("第 1 步已填入输入框，请发送并等待执行成功。");
  } else {
    useExample(EDIT_CONTINUE_PROMPT, { editStep: step });
    setEditWorkflowStatus("第 2 步已填入输入框，请发送以编辑当前活动零件。");
  }
}

function updateEditWorkflowAfterRun(step, succeeded) {
  if (!step) return;
  if (step === "base") {
    editContinueBtn.disabled = !succeeded;
    setEditWorkflowStatus(
      succeeded
        ? "第 1 步成功：当前活动零件已创建，可以执行第 2 步。"
        : "第 1 步失败：请修正后重试，第 2 步仍保持锁定。",
      succeeded ? "ok" : "fail"
    );
    return;
  }
  setEditWorkflowStatus(
    succeeded
      ? "Edit 流程完成：已在当前活动零件上继续拉伸 50mm。"
      : "第 2 步失败：当前零件未完成续拉伸，请检查执行结果。",
    succeeded ? "ok" : "fail"
  );
}

// ---- 发送指令 ----
async function send() {
  const text = inputEl.value.trim();
  const hasImage = currentImage !== null;
  const editStep = pendingEditStep;
  // 文字与图片至少要有一个
  if (!text && !hasImage) return;
  if (busy) return;

  const apiKey = apiKeyEl.value.trim();

  busy = true;
  pendingEditStep = null;
  sendBtn.disabled = true;
  inputEl.value = "";

  // 移除欢迎区
  const welcome = document.querySelector(".welcome");
  if (welcome) {
    // 只有从 Edit 控件发起请求时才保留跨请求操作区。
    if (editStep && editSection && welcome.contains(editSection)) {
      chatEl.insertBefore(editSection, welcome.nextSibling);
    }
    welcome.remove();
  }

  // 用户消息（含图片则一并展示）
  appendUser(text, hasImage ? currentImage.dataUri : null);

  // 发送后清空图片选择（保留最后一次以便查看，但下一次发送要重新选）
  const imageToSend = hasImage ? currentImage.base64 : null;
  if (hasImage) clearImage();

  // Loading
  const loadingHint = modeEl.value === "multi_turn"
    ? "Multi-turn Agent 正在反复检查并调整模型 …"
    : imageToSend
      ? "正在识别图纸并规划建模 …"
      : "正在思考并执行中 …";
  const loadingId = appendLoading(loadingHint);

  try {
    const res = await fetch(`${API}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        instruction: text,
        image: imageToSend,
        mode: modeEl.value,
        max_turns: maxTurnsEl.value ? Number(maxTurnsEl.value) : null,
        provider: providerEl.value,
        api_key: apiKey,
        base_url: baseUrlEl.value.trim(),
        vision_model: visionModelEl.value,
        plan_model: planModelEl.value,
      }),
    });
    const data = await res.json();
    removeLoading(loadingId);

    if (data.success) {
      if (data.mode === "multi_turn") {
        appendMultiTurnResponse(data.multi_turn, data.summary);
      } else {
        appendAIResponse(data.plan, data.results, data.summary);
      }
      updateEditWorkflowAfterRun(editStep, Boolean(data.summary?.all_ok));
    } else {
      appendError(data.error || "未知错误");
      updateEditWorkflowAfterRun(editStep, false);
    }
  } catch (e) {
    removeLoading(loadingId);
    appendError("请求失败: " + e.message);
    updateEditWorkflowAfterRun(editStep, false);
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

function observationHtml(observation) {
  if (!observation) return "<span class='muted'>无状态快照</span>";
  const bbox = observation.bounding_box_mm;
  const bboxText = bbox
    ? `${Number(bbox.sizeX || 0).toFixed(2)} × ${Number(bbox.sizeY || 0).toFixed(2)} × ${Number(bbox.sizeZ || 0).toFixed(2)} mm`
    : "无有效包围盒";
  const volumeText = observation.volume_mm3 == null
    ? "N/A"
    : `${Number(observation.volume_mm3).toFixed(2)} mm³`;
  const errorText = observation.errors && Object.keys(observation.errors).length
    ? `<div class="agent-errors">${esc(JSON.stringify(observation.errors))}</div>`
    : "";
  return `<div>特征 ${observation.features?.length || 0} 个；包围盒 ${esc(bboxText)}；体积 ${esc(volumeText)}</div>${errorText}`;
}

function appendMultiTurnResponse(result, summary) {
  const div = document.createElement("div");
  div.className = "msg";
  const milestonesHtml = (result.milestones || []).map(item =>
    `<li class="${item.status === "done" ? "ok" : item.status === "failed" ? "fail" : ""}">
      ${esc(item.id)} · ${esc(item.title)} — ${esc(item.status)}
      ${item.failure_count ? `（失败 ${item.failure_count} 次）` : ""}
    </li>`
  ).join("");

  const turnsHtml = (result.turns || []).map(turn => {
    const decision = turn.decision || {};
    const actions = (decision.actions || []).map((action, index) => {
      const params = action.params && Object.keys(action.params).length
        ? ` ${esc(JSON.stringify(action.params))}`
        : "";
      return `<li><span class="step-num">${index + 1}</span><span class="step-desc"><span class="step-op">${esc(action.operation)}</span>${params}${action.description ? " — " + esc(action.description) : ""}</span></li>`;
    }).join("") || "<li>本轮没有建模操作</li>";
    const executions = (turn.execution_results || []).map(item => {
      const ok = Boolean(item.success);
      return `<li><span class="icon">${ok ? "✅" : "❌"}</span>${esc(item.operation || "")} — ${esc(item.message || "")}</li>`;
    }).join("") || "<li>无执行结果</li>";
    const after = turn.observation_after || turn.observation_before;
    const validation = turn.validation;
    const validationHtml = validation
      ? `<div class="agent-validation ${validation.passed ? "ok" : "fail"}">
          机器验收：${validation.passed ? "通过" : "未通过"} —
          ${esc(validation.message || "")}
          <div>${esc(JSON.stringify(validation.details || {}))}</div>
        </div>`
      : "";
    return `<div class="phase agent-turn">
      <div class="phase-header" onclick="togglePhase(this)">第 ${turn.turn} 轮 · ${esc(decision.milestone_id || "未指定里程碑")} — ${esc(decision.decision || "")}: ${esc(decision.progress_summary || "")}</div>
      <div class="phase-body">
        <div class="agent-stage"><strong>Observe</strong>${observationHtml(turn.observation_before)}</div>
        <div class="agent-stage"><strong>Action</strong><ol class="step-list">${actions}</ol></div>
        <div class="agent-stage"><strong>Result</strong><ul class="exec-list">${executions}</ul></div>
        <div class="agent-stage"><strong>Check</strong>
          <div>${esc(decision.check_summary || "等待下一轮检查")}</div>
          ${observationHtml(after)}
          ${validationHtml}
        </div>
      </div>
    </div>`;
  }).join("");

  const completed = result.status === "completed";
  const usage = summary.token_usage || {};
  const tokenLine = usage.total_tokens
    ? `；tokens ${usage.prompt_tokens || 0}+${usage.completion_tokens || 0}=${usage.total_tokens}`
    : "";
  div.innerHTML = `<div class="msg-ai">
    <div class="multi-turn-title">Multi-turn Agent：${esc(result.user_goal || "")}</div>
    ${milestonesHtml ? `<div class="agent-ledger"><strong>里程碑账本</strong><ul>${milestonesHtml}</ul></div>` : ""}
    ${turnsHtml}
    <div class="summary ${completed ? "ok" : "fail"}">
      ${completed ? "✅" : "⚠️"} 状态: ${esc(result.status)}；
      ${summary.succeeded}/${summary.total} 个操作成功；
      ${summary.turn_count} 轮，${summary.llm_calls} 次 LLM 调用${tokenLine}
      ${result.message ? " — " + esc(result.message) : ""}
    </div>
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

function useExample(text, { editStep = null } = {}) {
  pendingEditStep = editStep;
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
editStartBtn.addEventListener("click", () => fillEditStep("base"));
editContinueBtn.addEventListener("click", () => fillEditStep("continue"));
editResetBtn.addEventListener("click", resetEditWorkflow);
gearLoadBtn.addEventListener("click", loadGearAnimation);
gearPlayBtn.addEventListener("click", playGearAnimation);
gearStopBtn.addEventListener("click", stopGearAnimation);
gearboxRunBtn.addEventListener("click", runTwoStageGearbox);
gearboxMotionLoadBtn.addEventListener("click", loadGearboxMotion);
gearboxMotionPlayBtn.addEventListener("click", playGearboxMotion);
gearboxMotionStopBtn.addEventListener("click", stopGearboxMotion);
engineRunBtn.addEventListener("click", runSingleCylinderEngine);
enginePoseBtn.addEventListener("click", setEnginePose);
engineAngle.addEventListener("input", () => {
  engineAngleValue.textContent = `${engineAngle.value}°`;
});
projectorRunBtn.addEventListener("click", runFilmProjector);
projectorPoseBtn.addEventListener("click", setProjectorPose);
projectorAngle.addEventListener("input", () => {
  projectorAngleValue.textContent = `${projectorAngle.value}°`;
});
gearboxGearSelect.addEventListener("change", () => {
  gearboxMotionPlayBtn.disabled = true;
  gearboxMotionStopBtn.disabled = true;
  gearboxRunStatus.textContent = "齿轮选择已改变，请重新加载旋转马达";
  gearboxRunStatus.classList.remove("ok", "fail");
});
inputEl.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
});
inputEl.addEventListener("input", () => {
  const expected = pendingEditStep === "base"
    ? EDIT_BASE_PROMPT
    : pendingEditStep === "continue" ? EDIT_CONTINUE_PROMPT : null;
  if (expected !== null && inputEl.value !== expected) pendingEditStep = null;
});

init();
