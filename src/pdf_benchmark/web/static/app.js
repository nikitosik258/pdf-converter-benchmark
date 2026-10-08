const API = "/api/v1";

const state = {
  converters: [],
  selectedFile: null,
  document: null,
  job: null,
  result: null,
  activeTab: "text",
  pollTimer: null,
  pollStartedAt: null,
  busy: false,
};

const ui = {
  apiState: document.querySelector("#api-state"),
  file: document.querySelector("#pdf-file"),
  fileLabel: document.querySelector("#file-label"),
  form: document.querySelector("#conversion-form"),
  select: document.querySelector("#converter-select"),
  convert: document.querySelector("#convert-button"),
  converterNote: document.querySelector("#converter-note"),
  statusPanel: document.querySelector("#status-panel"),
  statusTitle: document.querySelector("#status-title"),
  statusMessage: document.querySelector("#status-message"),
  statusValue: document.querySelector("#status-value"),
  processingTime: document.querySelector("#processing-time"),
  errorPanel: document.querySelector("#error-panel"),
  errorTitle: document.querySelector("#error-title"),
  errorMessage: document.querySelector("#error-message"),
  workspace: document.querySelector("#workspace"),
  documentTitle: document.querySelector("#document-title"),
  documentMeta: document.querySelector("#document-meta"),
  pdfViewer: document.querySelector("#pdf-viewer"),
  downloadTxt: document.querySelector("#download-txt"),
  downloadJson: document.querySelector("#download-json"),
  tabContent: document.querySelector("#tab-content"),
  tabs: [...document.querySelectorAll(".tab")],
};

function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MiB`;
}

function formatSeconds(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  const seconds = Number(value);
  return seconds < 60 ? `${seconds.toFixed(seconds < 10 ? 2 : 1)} s` : `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, options);
  if (response.ok) return response;
  let payload = null;
  try { payload = await response.json(); } catch (_) { /* non-JSON server failure */ }
  const error = payload?.error ?? { code: "NETWORK_ERROR", message: `HTTP ${response.status}` };
  throw Object.assign(new Error(error.message), { apiError: error });
}

function setBusy(busy) {
  state.busy = busy;
  ui.file.disabled = busy;
  ui.select.disabled = busy || !state.converters.some(item => item.available);
  updateConvertButton();
}

function updateConvertButton() {
  // The user must always receive feedback after pressing Convert. Only an
  // active job disables controls; missing input is explained in the handler.
  ui.convert.disabled = state.busy;
}

function clearError() {
  ui.errorPanel.hidden = true;
  ui.errorMessage.textContent = "";
}

function showError(error) {
  const details = error?.apiError;
  ui.errorPanel.hidden = false;
  ui.errorTitle.textContent = details?.code || "Ошибка";
  ui.errorMessage.textContent = details?.message || error?.message || "Не удалось выполнить запрос.";
  ui.statusPanel.className = "status-panel is-error";
}

function setStatus(kind, title, message, value, processingTime = null) {
  ui.statusPanel.className = `status-panel is-${kind}`;
  ui.statusTitle.textContent = title;
  ui.statusMessage.textContent = message;
  ui.statusValue.textContent = value;
  ui.processingTime.textContent = formatSeconds(processingTime);
}

function converterNote() {
  const selected = state.converters.find(item => item.id === ui.select.value);
  if (!selected) return "";
  const categories = selected.capabilities.join(", ");
  if (!selected.available) return selected.unavailable_reason || "Инструмент недоступен.";
  return `${selected.deployment} · ${selected.technology} · outputs: ${categories}`;
}

function renderConverters() {
  ui.select.replaceChildren();
  for (const item of state.converters) {
    const option = document.createElement("option");
    option.value = item.id;
    option.textContent = `${item.name} — ${item.deployment}${item.available ? "" : " (недоступен)"}`;
    option.disabled = !item.available;
    ui.select.append(option);
  }
  const firstAvailable = state.converters.find(item => item.available);
  if (firstAvailable) ui.select.value = firstAvailable.id;
  ui.select.disabled = !firstAvailable;
  ui.converterNote.textContent = converterNote();
  updateConvertButton();
}

async function loadConverters() {
  try {
    const health = await request("/health");
    const healthPayload = await health.json();
    ui.apiState.textContent = healthPayload.status === "ok" ? "API доступен" : "API работает с ограничениями";
    ui.apiState.className = `api-state ${healthPayload.status === "ok" ? "is-ready" : "is-error"}`;
    const response = await request("/converters");
    state.converters = await response.json();
    renderConverters();
  } catch (error) {
    ui.apiState.textContent = "API недоступен";
    ui.apiState.className = "api-state is-error";
    showError(error);
  }
}

function onFileChange() {
  state.selectedFile = ui.file.files?.[0] || null;
  ui.fileLabel.textContent = state.selectedFile
    ? `${state.selectedFile.name} · ${formatBytes(state.selectedFile.size)}`
    : "Выберите файл";
  clearError();
  updateConvertButton();
}

function stopPolling() {
  if (state.pollTimer) window.clearTimeout(state.pollTimer);
  state.pollTimer = null;
  state.pollStartedAt = null;
}

async function uploadAndStart() {
  if (!state.selectedFile) {
    setStatus("error", "Файл не выбран", "Выберите PDF-файл перед запуском.", "waiting_input");
    showError(new Error("Выберите PDF-файл перед запуском."));
    return;
  }
  const selected = state.converters.find(item => item.id === ui.select.value);
  if (!selected?.available) {
    setStatus("error", "Converter недоступен", "Выберите доступный converter из списка.", "waiting_input");
    showError(new Error(selected?.unavailable_reason || "Выберите доступный converter из списка."));
    return;
  }
  clearError();
  ui.workspace.hidden = true;
  setBusy(true);
  setStatus("running", "Загрузка PDF", "Файл передаётся в временное хранилище.", "uploading");
  try {
    const form = new FormData();
    form.append("file", state.selectedFile, state.selectedFile.name);
    const upload = await request("/documents", { method: "POST", body: form });
    state.document = await upload.json();

    setStatus("running", "Задание поставлено в очередь", "Converter ожидает или начинает обработку.", "queued");
    const created = await request("/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ document_id: state.document.id, converter_id: ui.select.value }),
    });
    state.job = await created.json();
    state.pollStartedAt = Date.now();
    await pollJob();
  } catch (error) {
    stopPolling();
    setBusy(false);
    setStatus("error", "Конвертация не запущена", "Проверьте сообщение об ошибке.", "failed");
    showError(error);
  }
}

async function pollJob() {
  if (!state.job) return;
  try {
    const response = await request(`/jobs/${encodeURIComponent(state.job.id)}`);
    state.job = await response.json();
    const job = state.job;
    const elapsed = job.processing_time_seconds ?? (state.pollStartedAt ? (Date.now() - state.pollStartedAt) / 1000 : null);
    const running = !["succeeded", "failed", "cancelled", "expired"].includes(job.status);
    setStatus(
      running ? "running" : job.status === "succeeded" ? "success" : "error",
      job.status === "succeeded" ? "Конвертация завершена" : `Статус: ${job.stage}`,
      job.status === "succeeded" ? "Результат готов к просмотру и скачиванию." : "Выполняется обработка PDF.",
      job.status,
      elapsed,
    );
    if (running) {
      state.pollTimer = window.setTimeout(pollJob, 1000);
      return;
    }
    stopPolling();
    setBusy(false);
    if (job.status === "succeeded") {
      await loadResult();
    } else {
      const error = new Error(job.error?.message || "Конвертация не завершилась.");
      error.apiError = job.error;
      showError(error);
    }
  } catch (error) {
    stopPolling();
    setBusy(false);
    setStatus("error", "Не удалось получить статус", "Проверьте подключение к API.", "unknown");
    showError(error);
  }
}

async function loadResult() {
  const response = await request(`/jobs/${encodeURIComponent(state.job.id)}/result`);
  state.result = await response.json();
  const document = state.result.document;
  ui.documentTitle.textContent = document.source_pdf || "Результат конвертации";
  const pages = document.pages?.length ?? 0;
  ui.documentMeta.textContent = `${state.job.converter_id} · ${pages} стр. · ${formatSeconds(state.result.processing_time_seconds)}`;
  ui.pdfViewer.src = state.document.content_url;
  ui.downloadTxt.href = state.job.text_download_url;
  ui.downloadJson.href = state.job.json_download_url;
  ui.workspace.hidden = false;
  renderActiveTab();
}

function pages() { return state.result?.document?.pages ?? []; }
function allObjects(key) { return pages().flatMap(page => (page[key] || []).map(item => ({ ...item, page_number: page.page_number }))); }

function empty(message) {
  const node = document.createElement("p");
  node.className = "empty";
  node.textContent = message;
  return node;
}

function renderText() {
  const blocks = allObjects("text_blocks");
  if (!blocks.length) return empty("Текстовые блоки не извлечены.");
  const output = document.createElement("pre");
  output.className = "text-output";
  output.textContent = blocks.map(block => block.raw_text || block.normalized_text || "").filter(Boolean).join("\n\n");
  return output;
}

function tableGrid(table) {
  const rows = Math.max(table.rows || 0, ...table.cells.map(cell => cell.row_index + 1), 0);
  const columns = Math.max(table.columns || 0, ...table.cells.map(cell => cell.column_index + 1), 0);
  const grid = Array.from({ length: rows }, () => Array.from({ length: columns }, () => null));
  for (const cell of table.cells) grid[cell.row_index][cell.column_index] = cell;
  const element = document.createElement("table");
  element.className = "data-table";
  const body = document.createElement("tbody");
  grid.forEach((row, index) => {
    const tr = document.createElement("tr");
    row.forEach(cell => {
      const part = cell?.is_header || index === 0 ? document.createElement("th") : document.createElement("td");
      part.textContent = cell?.text || "";
      if (cell?.row_span > 1) part.rowSpan = cell.row_span;
      if (cell?.column_span > 1) part.colSpan = cell.column_span;
      tr.append(part);
    });
    body.append(tr);
  });
  element.append(body);
  return element;
}

function renderTables() {
  const tables = allObjects("tables");
  if (!tables.length) return empty("Таблицы не извлечены.");
  const list = document.createElement("div");
  list.className = "object-list";
  tables.forEach((table, index) => {
    const card = document.createElement("article");
    card.className = "object-card";
    const title = document.createElement("h4");
    title.textContent = `Table ${index + 1}`;
    const page = document.createElement("span");
    page.className = "badge";
    page.textContent = `стр. ${table.page_number}`;
    title.append(page);
    card.append(title);
    if (table.caption?.text) { const caption = document.createElement("p"); caption.textContent = table.caption.text; card.append(caption); }
    card.append(tableGrid(table));
    list.append(card);
  });
  return list;
}

function renderFormulas() {
  const formulas = allObjects("formulas");
  if (!formulas.length) return empty("Формулы не извлечены.");
  const list = document.createElement("div");
  list.className = "object-list";
  formulas.forEach((formula, index) => {
    const card = document.createElement("article");
    card.className = "object-card";
    const title = document.createElement("h4");
    title.textContent = `Formula ${index + 1}`;
    const page = document.createElement("span"); page.className = "badge"; page.textContent = `стр. ${formula.page_number}`; title.append(page);
    const code = document.createElement("pre"); code.className = "formula-code";
    code.textContent = formula.latex || formula.raw_text || formula.plain_text || "[empty formula]";
    card.append(title, code);
    list.append(card);
  });
  return list;
}

function assetUrl(assetPath) {
  const safePath = String(assetPath || "").split("/").filter(part => part && part !== "." && part !== "..").map(encodeURIComponent).join("/");
  return `${API}/jobs/${encodeURIComponent(state.job.id)}/assets/${safePath}`;
}

function renderImages() {
  const images = [...allObjects("images"), ...allObjects("diagrams")];
  if (!images.length) return empty("Изображения и диаграммы не извлечены.");
  const list = document.createElement("div"); list.className = "object-list";
  images.forEach((image, index) => {
    const card = document.createElement("article"); card.className = "object-card";
    const title = document.createElement("h4"); title.textContent = `Image ${index + 1}`;
    const page = document.createElement("span"); page.className = "badge"; page.textContent = `стр. ${image.page_number}`; title.append(page);
    card.append(title);
    if (image.asset_path) {
      const preview = document.createElement("img"); preview.className = "image-preview"; preview.loading = "lazy";
      preview.alt = image.caption?.text || `Extracted image ${index + 1}`; preview.src = assetUrl(image.asset_path);
      preview.addEventListener("error", () => { preview.replaceWith(empty("Файл изображения недоступен для preview.")); });
      card.append(preview);
    }
    const caption = image.caption?.text || image.diagram_type || "Подпись отсутствует.";
    const text = document.createElement("p"); text.textContent = caption; card.append(text);
    list.append(card);
  });
  return list;
}

function renderRaw() {
  const output = document.createElement("pre");
  output.className = "raw-output";
  output.textContent = JSON.stringify(state.result?.document ?? {}, null, 2);
  return output;
}

function renderActiveTab() {
  ui.tabs.forEach(tab => {
    const selected = tab.dataset.tab === state.activeTab;
    tab.classList.toggle("is-active", selected);
    tab.setAttribute("aria-selected", String(selected));
  });
  ui.tabContent.replaceChildren();
  const renderer = { text: renderText, tables: renderTables, formulas: renderFormulas, images: renderImages, raw: renderRaw }[state.activeTab];
  ui.tabContent.append(renderer());
}

ui.file.addEventListener("change", onFileChange);
ui.select.addEventListener("change", () => { ui.converterNote.textContent = converterNote(); updateConvertButton(); });
function startConversionFromForm(event) {
  event.preventDefault();
  void uploadAndStart();
}

// This explicit browser entry point makes the form submit path independent of
// module listener registration in embedded or extension-modified browsers.
window.pdfBenchmarkStartConversion = startConversionFromForm;
ui.tabs.forEach(tab => tab.addEventListener("click", () => { state.activeTab = tab.dataset.tab; renderActiveTab(); }));
window.addEventListener("beforeunload", stopPolling);

setStatus("idle", "Готово к загрузке", "Выберите PDF и converter.", "idle");
loadConverters();
