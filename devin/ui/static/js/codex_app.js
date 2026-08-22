import { sideBySideRows, splitManifestDiff } from "./verified_diff.js";

const $ = (id) => document.getElementById(id);

const state = {
  selectedRunId: null,
  selectedRunStatus: null,
  pipelineStage: null,
  selectedProjectPath: "",
  selectedChatId: null,
  chatLoaded: false,
  eventSource: null,
  lastEventSeq: -1,
  chatAbort: null,
  reviewedChangeRunId: null,
  reviewedChangeProjectPath: null,
  reviewedManifestDigest: null,
  reviewedManifestPayload: null,
  reviewedManifestDecision: null,
  manifestDecisionPending: false,
  manifestFiles: [],
  selectedDiffPath: null,
  trainingCases: [],
  trainingJobPoll: null,
  projects: [],
  runs: [],
  goals: [],
  goalCriteriaDraft: [],
  goalEvents: [],
  goalPoll: null,
  goalEventSource: null,
  streamedGoalRunId: null,
  lastGoalEventSeq: -1,
  projectFiles: [],
  projectTreeScope: null,
  selectedFilePath: null,
  centerView: "chat",
  commandItems: [],
};

function setText(id, text) {
  const el = $(id);
  if (el) el.textContent = text;
}

function escapeHtml(value) {
  return String(value ?? "-")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderKeyValues(container, rows) {
  container.innerHTML = rows
    .map(([key, value]) => `
      <div class="kv-row">
        <span>${escapeHtml(key)}</span>
        <span>${escapeHtml(value)}</span>
      </div>
    `)
    .join("");
}

// App nativa (2026-07-22): il frontend e' disaccoppiato dal backend. In modalita'
// web/rig la UI e' servita dallo stesso origin (API_BASE = ""). Nell'app desktop
// la UI e' bundlata come file locali e la shell Rust inietta window.__DEVIN_API_BASE__
// con l'URL del backend scoperto (rig se up, altrimenti backup locale).
const API_BASE = (typeof window !== "undefined" && window.__DEVIN_API_BASE__) || "";

function apiUrl(path) {
  if (typeof path !== "string") return path;
  if (/^https?:\/\//i.test(path)) return path;  // gia' assoluto
  return API_BASE + path;
}

async function fetchJson(url, options = {}) {
  const headers = { Accept: "application/json", ...(options.headers ?? {}) };
  const res = await fetch(apiUrl(url), { ...options, headers });
  if (!res.ok) {
    let detail = "";
    try {
      const payload = await res.json();
      detail = payload?.detail || payload?.error || "";
    } catch (_) {
      // Some infrastructure errors return an empty/non-JSON response.
    }
    const err = new Error(detail || `${url}: ${res.status}`);
    err.status = res.status;
    err.url = url;
    throw err;
  }
  return res.json();
}

async function postJson(url, body) {
  return fetchJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

// Modale interno all'app (Tauri non supporta window.prompt/confirm/dialog
// nativi). Ritorna una Promise col testo, o null se annullato. 2026-07-22.
function promptModal(message, { placeholder = "", value = "", okLabel = "OK" } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "app-modal-overlay";
    overlay.innerHTML = `
      <div class="app-modal" role="dialog" aria-modal="true">
        <p class="app-modal-msg"></p>
        <input type="text" class="app-modal-input" />
        <div class="app-modal-actions">
          <button type="button" class="tiny-button app-modal-cancel">Annulla</button>
          <button type="button" class="primary-mini-button app-modal-ok"></button>
        </div>
      </div>`;
    overlay.querySelector(".app-modal-msg").textContent = message;
    const input = overlay.querySelector(".app-modal-input");
    input.placeholder = placeholder;
    input.value = value;
    overlay.querySelector(".app-modal-ok").textContent = okLabel;
    document.body.appendChild(overlay);
    setTimeout(() => input.focus(), 20);
    const close = (val) => { overlay.remove(); resolve(val); };
    overlay.querySelector(".app-modal-ok").addEventListener("click", () => close(input.value.trim() || null));
    overlay.querySelector(".app-modal-cancel").addEventListener("click", () => close(null));
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(null); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); close(input.value.trim() || null); }
      if (e.key === "Escape") { e.preventDefault(); close(null); }
    });
  });
}

function confirmModal(message, { okLabel = "Conferma", danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "app-modal-overlay";
    overlay.innerHTML = `
      <div class="app-modal" role="dialog" aria-modal="true" aria-label="Conferma decisione">
        <p class="app-modal-msg"></p>
        <div class="app-modal-actions">
          <button type="button" class="tiny-button app-modal-cancel">Annulla</button>
          <button type="button" class="primary-mini-button app-modal-ok ${danger ? "danger" : ""}"></button>
        </div>
      </div>`;
    overlay.querySelector(".app-modal-msg").textContent = message;
    overlay.querySelector(".app-modal-ok").textContent = okLabel;
    document.body.appendChild(overlay);
    const close = (confirmed) => { overlay.remove(); resolve(confirmed); };
    overlay.querySelector(".app-modal-ok").addEventListener("click", () => close(true));
    overlay.querySelector(".app-modal-cancel").addEventListener("click", () => close(false));
    overlay.addEventListener("mousedown", (event) => { if (event.target === overlay) close(false); });
    overlay.addEventListener("keydown", (event) => {
      if (event.key === "Escape") { event.preventDefault(); close(false); }
      if (event.key === "Enter") { event.preventDefault(); close(true); }
    });
    overlay.tabIndex = -1;
    overlay.focus();
  });
}

function selectedChatFiles() {
  return Array.from($("chat-file")?.files ?? []);
}

function formatFileLabel(files) {
  if (!files.length) return "no files";
  if (files.length === 1) return files[0].name;
  const total = files.reduce((sum, file) => sum + (file.size || 0), 0);
  const mb = total / (1024 * 1024);
  return `${files.length} files · ${mb.toFixed(mb >= 10 ? 0 : 1)} MB`;
}

function activeProjectLabel() {
  return state.selectedProjectPath
    ? state.selectedProjectPath.split(/[\/]/).pop()
    : "General chat";
}

function truncateText(value, max = 140) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

function refreshActiveScope() {
  setText("active-scope-label", activeProjectLabel());
  if (state.centerView === "chat") setText("workspace-mode-context", activeProjectLabel());
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes >= 10240 ? 0 : 1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
}

function setCenterView(view) {
  const requested = ["chat", "editor", "diff"].includes(view) ? view : "chat";
  const next = requested === "editor" && state.selectedProjectPath
    ? "editor"
    : requested === "diff" && state.reviewedManifestPayload
      ? "diff"
      : "chat";
  state.centerView = next;
  const panel = document.querySelector(".workstream-panel");
  if (panel) panel.dataset.centerView = next;
  const editor = $("editor-workspace");
  if (editor) editor.hidden = next !== "editor";
  const diff = $("manifest-diff-workspace");
  if (diff) diff.hidden = next !== "diff";
  document.querySelectorAll("[data-center-view]").forEach((button) => {
    if (!button.classList.contains("workspace-mode-button")) return;
    button.classList.toggle("active", button.dataset.centerView === next);
    button.setAttribute("aria-pressed", button.dataset.centerView === next ? "true" : "false");
  });
  setText(
    "workspace-mode-context",
    next === "editor"
      ? (state.selectedFilePath || "Nessun file selezionato")
      : next === "diff"
        ? `run ${state.reviewedChangeRunId || "?"}`
        : activeProjectLabel(),
  );
}

function resetProjectEditor() {
  state.projectFiles = [];
  state.projectTreeScope = null;
  state.selectedFilePath = null;
  setText("editor-file-path", "Nessun file selezionato");
  setText("editor-file-meta", "Scegli un file di testo dalla sidebar.");
  setText("editor-content", state.selectedProjectPath
    ? "Seleziona un file dalla sidebar."
    : "Seleziona un progetto e poi un file.");
  const editorButton = $("show-editor-view");
  if (editorButton) editorButton.disabled = !state.selectedProjectPath;
}

function resetManifestReview() {
  state.reviewedChangeRunId = null;
  state.reviewedChangeProjectPath = null;
  state.reviewedManifestDigest = null;
  state.reviewedManifestPayload = null;
  state.reviewedManifestDecision = null;
  state.manifestDecisionPending = false;
  state.manifestFiles = [];
  state.selectedDiffPath = null;
  const diffButton = $("show-diff-view");
  if (diffButton) diffButton.disabled = true;
  const applyButton = $("manifest-diff-apply");
  const rejectButton = $("manifest-diff-reject");
  if (applyButton) applyButton.disabled = true;
  if (rejectButton) rejectButton.disabled = true;
  setText("manifest-diff-run", "Nessun manifest in review");
  setText("manifest-diff-digest", "Apri Diff da un run verificato in attesa di approvazione.");
  setText("manifest-diff-status", "idle");
  const statusBadge = $("manifest-diff-status");
  if (statusBadge) statusBadge.dataset.status = "idle";
  setText("manifest-diff-summary", "Il diff centrale accetta soltanto il change manifest verificato del run.");
  setText("manifest-selected-file", "Nessun file selezionato");
  setText("manifest-selected-meta", "read-only");
  const rail = $("manifest-file-rail");
  if (rail) rail.innerHTML = "";
  const rows = $("manifest-diff-rows");
  if (rows) rows.innerHTML = '<div class="manifest-diff-empty">In attesa di un manifest verificato.</div>';
}

const MANIFEST_DIFF_MAX_RENDER_ROWS = 2500;

function renderManifestDiffRows(file) {
  const container = $("manifest-diff-rows");
  if (!container) return;
  if (!file?.diffText) {
    container.innerHTML = '<div class="manifest-diff-empty">Preview non inclusa nel payload bounded.</div>';
    return;
  }
  const parsed = sideBySideRows(file.diffText);
  const rows = parsed.slice(0, MANIFEST_DIFF_MAX_RENDER_ROWS);
  container.innerHTML = rows.map((row) => {
    if (row.kind === "meta") {
      return `<div class="diff-row diff-row-meta"><span></span><code>${escapeHtml(row.text)}</code><span></span><code></code></div>`;
    }
    return `
      <div class="diff-row diff-row-${row.kind}">
        <span class="diff-line-number">${row.oldNo ?? ""}</span><code>${escapeHtml(row.oldText)}</code>
        <span class="diff-line-number">${row.newNo ?? ""}</span><code>${escapeHtml(row.newText)}</code>
      </div>`;
  }).join("") || '<div class="manifest-diff-empty">Nessuna differenza testuale.</div>';
  if (parsed.length > rows.length) {
    container.insertAdjacentHTML(
      "beforeend",
      `<div class="manifest-diff-empty">Rendering limitato a ${MANIFEST_DIFF_MAX_RENDER_ROWS} righe.</div>`,
    );
  }
  container.scrollTop = 0;
}

function selectManifestFile(path) {
  const file = state.manifestFiles.find((entry) => entry.path === path);
  if (!file) return;
  state.selectedDiffPath = file.path;
  $("manifest-file-rail")?.querySelectorAll("[data-manifest-file]").forEach((button) => {
    button.classList.toggle("active", button.dataset.manifestFile === file.path);
  });
  setText("manifest-selected-file", file.path);
  const binary = file.binary ? " · binary" : "";
  setText(
    "manifest-selected-meta",
    `${file.operation || "modify"}${binary} · ${formatBytes(file.before_size)} → ${formatBytes(file.after_size)}`,
  );
  renderManifestDiffRows(file);
}

function renderManifestWorkspace(payload, projectPath, runId) {
  state.reviewedChangeRunId = runId;
  state.reviewedChangeProjectPath = projectPath;
  state.reviewedManifestDigest = payload.entry_digest || null;
  state.reviewedManifestPayload = payload;
  state.reviewedManifestDecision = null;
  state.manifestFiles = splitManifestDiff(payload);
  state.selectedDiffPath = null;

  const diffButton = $("show-diff-view");
  if (diffButton) diffButton.disabled = false;
  const applyButton = $("manifest-diff-apply");
  const rejectButton = $("manifest-diff-reject");
  if (applyButton) applyButton.disabled = false;
  if (rejectButton) rejectButton.disabled = false;
  setText("manifest-diff-run", `Run ${runId}`);
  setText("manifest-diff-digest", `digest ${(payload.entry_digest || "missing").slice(0, 16)} · manifest verificato`);
  setText("manifest-diff-status", payload.status || "pending");
  const statusBadge = $("manifest-diff-status");
  if (statusBadge) statusBadge.dataset.status = payload.status || "pending";
  const counts = payload.counts || {};
  setText(
    "manifest-diff-summary",
    `${state.manifestFiles.length} file · ${counts.create || 0} creati · ${counts.modify || 0} modificati · ${counts.delete || 0} eliminati${payload.truncated ? " · preview bounded/troncata" : ""}`,
  );
  const rail = $("manifest-file-rail");
  if (rail) {
    rail.innerHTML = state.manifestFiles.map((file) => `
      <button class="manifest-file-button" type="button" data-manifest-file="${escapeHtml(file.path)}" title="${escapeHtml(file.path)}">
        <span>${escapeHtml(file.path)}</span>
        <small>${escapeHtml(file.operation || "modify")}${file.binary ? " · binary" : ""}</small>
      </button>`).join("");
    rail.querySelectorAll("[data-manifest-file]").forEach((button) => {
      button.addEventListener("click", () => selectManifestFile(button.dataset.manifestFile));
    });
  }
  if (state.manifestFiles.length) selectManifestFile(state.manifestFiles[0].path);
  else renderManifestDiffRows(null);
  setCenterView("diff");
}

function projectTreeModel(files) {
  const root = { directories: new Map(), files: [] };
  files.forEach((file) => {
    const parts = String(file.path || "").split("/").filter(Boolean);
    const filename = parts.pop();
    if (!filename) return;
    let node = root;
    parts.forEach((part) => {
      if (!node.directories.has(part)) {
        node.directories.set(part, { directories: new Map(), files: [] });
      }
      node = node.directories.get(part);
    });
    node.files.push(file);
  });
  return root;
}

function renderProjectTreeNode(node, depth = 0) {
  const directories = [...node.directories.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, child]) => `
      <details class="project-tree-folder" ${depth < 1 ? "open" : ""}>
        <summary><span class="project-tree-icon">▸</span>${escapeHtml(name)}</summary>
        <div class="project-tree-children">${renderProjectTreeNode(child, depth + 1)}</div>
      </details>`);
  const files = [...node.files]
    .sort((left, right) => String(left.name).localeCompare(String(right.name)))
    .map((file) => {
      const readable = Boolean(file.is_text);
      const selected = file.path === state.selectedFilePath;
      return `
        <button class="project-tree-file ${selected ? "active" : ""}" type="button"
          data-project-file="${escapeHtml(file.path)}" title="${escapeHtml(file.path)}"
          ${readable ? "" : 'disabled aria-label="File binario non visualizzabile"'}>
          <span class="project-tree-icon">${readable ? "·" : "◇"}</span>
          <span>${escapeHtml(file.name)}</span>
        </button>`;
    });
  return [...directories, ...files].join("");
}

function renderWorkdirFileSummary() {
  const filesEl = $("workdir-files");
  if (!filesEl) return;
  filesEl.innerHTML = state.projectFiles.length
    ? state.projectFiles.slice(0, 12).map((file) => `
        <button class="file-row" type="button" data-rail-project-file="${escapeHtml(file.path)}"
          ${file.is_text ? "" : "disabled"} title="${escapeHtml(file.path)}">
          <i class="ti">·</i>${escapeHtml(file.name)}
        </button>`).join("")
    : "";
  filesEl.querySelectorAll("[data-rail-project-file]").forEach((button) => {
    button.addEventListener("click", () => openProjectFile(button.dataset.railProjectFile));
  });
}

function renderProjectTree(payload) {
  const tree = $("project-file-tree");
  const status = $("project-tree-status");
  state.projectFiles = Array.isArray(payload?.files) ? payload.files : [];
  state.projectTreeScope = payload?.scope || null;
  if (!tree || !status) return;
  if (!state.projectFiles.length) {
    tree.innerHTML = '<div class="project-tree-empty">Nessun file trovato.</div>';
  } else {
    tree.innerHTML = renderProjectTreeNode(projectTreeModel(state.projectFiles));
    tree.querySelectorAll("[data-project-file]").forEach((button) => {
      button.addEventListener("click", () => openProjectFile(button.dataset.projectFile));
    });
  }
  const scopeLabel = payload?.scope === "work_dir" ? "work_dir" : "progetto";
  status.textContent = `${scopeLabel} · ${payload?.count ?? 0} file${payload?.truncated ? " · vista limitata" : ""}`;
  renderWorkdirFileSummary();
}

async function loadProjectTree() {
  const refreshButton = $("refresh-project-tree");
  const tree = $("project-file-tree");
  if (refreshButton) refreshButton.disabled = !state.selectedProjectPath;
  if (!state.selectedProjectPath) {
    if (tree) tree.innerHTML = "";
    setText("project-tree-status", "Seleziona un progetto.");
    renderWorkdirFileSummary();
    return;
  }
  const projectAtRequest = state.selectedProjectPath;
  setText("project-tree-status", "Lettura file…");
  const params = new URLSearchParams({ project_path: projectAtRequest });
  const payload = await fetchJson(`/api/project/tree?${params.toString()}`);
  if (state.selectedProjectPath !== projectAtRequest) return;
  renderProjectTree(payload);
}

async function openProjectFile(relativePath) {
  if (!state.selectedProjectPath || !relativePath) return;
  const projectAtRequest = state.selectedProjectPath;
  state.selectedFilePath = relativePath;
  setCenterView("editor");
  setText("editor-file-path", relativePath);
  setText("editor-file-meta", "Lettura in corso…");
  setText("editor-content", "");
  $("project-file-tree")?.querySelectorAll("[data-project-file]").forEach((button) => {
    button.classList.toggle("active", button.dataset.projectFile === relativePath);
  });
  const params = new URLSearchParams({ project_path: projectAtRequest, path: relativePath });
  try {
    const payload = await fetchJson(`/api/project/file?${params.toString()}`);
    if (state.selectedProjectPath !== projectAtRequest || state.selectedFilePath !== relativePath) return;
    setText("editor-content", payload.content || "");
    const truncation = payload.truncated ? " · anteprima troncata a 256 KiB" : "";
    setText("editor-file-meta", `${payload.language || "text"} · ${formatBytes(payload.size)}${truncation}`);
  } catch (err) {
    setText("editor-content", `File non visualizzabile: ${err.message || err}`);
    setText("editor-file-meta", "lettura non disponibile");
  }
}


function renderTrainingOverview(payload) {
  const summary = payload?.summary ?? {};
  state.trainingCases = payload?.cases ?? [];
  setText("training-status", "ready");
  setText("training-cases-count", summary.cases ?? 0);
  setText("training-attempts-count", summary.attempts ?? 0);
  setText("training-auto-success-count", summary.auto_success ?? 0);
  setText("training-auto-failure-count", summary.auto_failure ?? 0);
  setText("training-runner-error-count", summary.runner_error ?? 0);
  setText("training-success-count", summary.verified_success ?? 0);
  setText("training-failure-count", summary.verified_failure ?? 0);
  const activeJob = (payload?.jobs ?? []).find((job) => ["queued", "running"].includes(job.status));
  if (activeJob) {
    setText("training-status", `${activeJob.status} ${activeJob.completed ?? 0}/${activeJob.total ?? "?"}`);
  }

  const list = $("training-case-list");
  if (!list) return;
  const cases = state.trainingCases;
  if (!cases.length) {
    list.innerHTML = `<div class="empty-card">Nessun caso training. Premi “Seed mini bench” per creare i primi esercizi locali.</div>`;
    return;
  }
  list.innerHTML = cases.slice(-5).reverse().map((item) => `
    <div class="training-case-card" data-training-case-id="${escapeHtml(item.case_id)}">
      <strong>${escapeHtml(item.title || item.case_id)}</strong>
      <span>${escapeHtml(item.kind || "custom")} · ${escapeHtml(item.source || "manual")}</span>
      <p class="training-case-task">${escapeHtml(truncateText(item.task || item.prompt || "", 170))}</p>
      <div class="training-case-actions">
        <button class="tiny-button ghost-button" type="button" data-load-training-case="${escapeHtml(item.case_id)}">Load prompt</button>
      </div>
    </div>
  `).join("");
  list.querySelectorAll("[data-load-training-case]").forEach((button) => {
    button.addEventListener("click", () => loadTrainingCaseToChat(button.dataset.loadTrainingCase));
  });
}

async function loadTrainingOverview() {
  const params = new URLSearchParams();
  if (state.selectedProjectPath) params.set("project_path", state.selectedProjectPath);
  setText("training-status", "loading");
  const overview = await fetchJson(`/api/training/overview?${params.toString()}`);
  renderTrainingOverview(overview);
}

async function seedTrainingMiniBench() {
  const result = await postJson("/api/training/seed", {
    project_path: state.selectedProjectPath || "",
    benchmark_id: "devin-mini",
  });
  const created = result.created ?? [];
  const createdList = created.length
    ? `\nCreati:\n${created.map((item) => `- ${item.title || item.case_id}`).join("\n")}`
    : "\nNessun duplicato creato: i casi DEVIN Mini erano già presenti.";
  appendChatMessage(
    "assistant",
    `Training seed completato: ${result.count} nuovi casi DEVIN Mini.${createdList}\n\nOra puoi premere “Run mini bench”: DEVIN proverà i casi in sandbox, registrando auto_success/auto_failure. Teacher o umano validano dopo: niente promozione automatica in memoria buona.`,
  );
  await loadTrainingOverview();
}

function startTrainingJobPolling() {
  if (state.trainingJobPoll) window.clearInterval(state.trainingJobPoll);
  state.trainingJobPoll = window.setInterval(async () => {
    try {
      await loadTrainingOverview();
      const overview = await fetchJson(`/api/training/overview?${new URLSearchParams(state.selectedProjectPath ? { project_path: state.selectedProjectPath } : {}).toString()}`);
      const active = (overview.jobs ?? []).some((job) => ["queued", "running"].includes(job.status));
      if (!active && state.trainingJobPoll) {
        window.clearInterval(state.trainingJobPoll);
        state.trainingJobPoll = null;
        appendChatMessage("assistant", "Training bench completato. Controlla auto ok/auto fail, poi valida con Teacher o correzione umana prima di esportare SFT.");
        await loadTrainingOverview();
      }
    } catch (err) {
      console.error(err);
    }
  }, 3000);
}

async function runTrainingMiniBench() {
  const ok = window.confirm("Avviare DEVIN Mini Bench in sandbox? Può richiedere diversi minuti e userà i modelli locali.");
  if (!ok) return;
  const result = await postJson("/api/training/run", {
    project_path: state.selectedProjectPath || "",
    benchmark_id: "devin-mini",
  });
  if (result.error) throw new Error(result.error);
  const job = result.job ?? {};
  appendChatMessage(
    "assistant",
    `Training bench avviato: ${job.job_id || "job"} · ${job.total || 0} casi. Registro auto_success/auto_failure, poi serve validazione Teacher/umana.`,
  );
  await loadTrainingOverview();
  startTrainingJobPolling();
}

function buildTrainingCasePrompt(item) {
  const expected = (item.expected_signals ?? []).length
    ? `\n\nCriteri attesi: ${(item.expected_signals ?? []).join(", ")}`
    : "";
  return [
    `TRAINING CASE: ${item.title || item.case_id}`,
    "",
    item.task || item.prompt || "",
    expected,
    "",
    "Lavora come coding agent locale: spiega brevemente il piano, modifica solo i file necessari, poi indica come verificare con test o controlli ripetibili.",
  ].join("\n").trim();
}

function loadTrainingCaseToChat(caseId) {
  const item = state.trainingCases.find((entry) => entry.case_id === caseId);
  if (!item) {
    appendChatMessage("assistant", "[training] Caso non trovato: ricarica la pagina o premi di nuovo Seed/Overview.");
    return;
  }
  const input = $("chat-input");
  if (!input) return;
  input.value = buildTrainingCasePrompt(item);
  input.focus();
  appendChatMessage(
    "assistant",
    `Caso training caricato nel prompt manuale: ${item.title || item.case_id}. Per il flusso vero da benchmark usa “Run mini bench”, che registra automaticamente auto_success/auto_failure in sandbox.`,
  );
}

async function createTrainingCaseFromPrompt() {
  const task = window.prompt("Task/esercizio da aggiungere alla training queue:");
  if (!task || !task.trim()) return;
  const result = await postJson("/api/training/cases", {
    project_path: state.selectedProjectPath || "",
    title: task.trim().slice(0, 80),
    task: task.trim(),
    kind: "manual",
    tags: ["manual", "devin-training"],
  });
  if (result.error) throw new Error(result.error);
  appendChatMessage("assistant", "Caso training aggiunto. Ora puoi farlo tentare a DEVIN e registrare esito/correzione.");
  await loadTrainingOverview();
}

async function recordTrainingFailure() {
  const reason = window.prompt("Motivo del fallimento da salvare come negativo verificato:");
  if (!reason || !reason.trim()) return;
  const result = await postJson("/api/training/attempts", {
    project_path: state.selectedProjectPath || "",
    case_id: "manual",
    prompt: activeProjectLabel(),
    status: "verified_failure",
    error_reason: reason.trim(),
    tests: { source: "human_review", passed: false },
  });
  appendChatMessage("assistant", `Failure salvato: ${result.attempt?.attempt_id || "ok"}. Non verrà promosso in memoria buona.`);
  await loadTrainingOverview();
}

async function exportTrainingDataset() {
  const result = await postJson("/api/training/export", {
    project_path: state.selectedProjectPath || "",
  });
  appendChatMessage("assistant", `Dataset SFT esportato: ${result.rows} righe → ${result.path}`);
  await loadTrainingOverview();
}

function renderMind(status, health = {}) {
  if (!state.selectedRunId) setText("mind-state", "ready");
  const launcherSource = status.models?.launcher_source ?? "unavailable";
  const sourceLabels = {
    rig: `rig attivo${status.models?.rig_host ? ` · ${status.models.rig_host}` : ""}`,
    local: "locale attivo",
    unavailable: "offline · nessun modello",
  };
  setText("model-source", sourceLabels[launcherSource] ?? launcherSource);

  const localRunning = status.models?.local_running ?? {};
  const activeModel = health.remote_model || Object.keys(localRunning)[0] ||
    (launcherSource === "rig" ? "DEVIN model-slot" : "nessun modello");
  setText("active-model-label", activeModel);

  const remoteReady = Boolean(health.remote_coder && health.remote_reasoning);
  const gpuCard = $("gpu-slot-card");
  const gpuFill = $("gpu-slot-fill");
  const slotState = remoteReady ? "ready" : (launcherSource === "rig" ? "loading" : "offline");
  setText("gpu-slot-status", remoteReady ? "DEVIN ready" : (launcherSource === "rig" ? "preparazione" : "offline"));
  if (gpuCard) gpuCard.dataset.state = slotState;
  if (gpuFill) gpuFill.style.width = remoteReady ? "100%" : (launcherSource === "rig" ? "58%" : "0%");

  const localMemory = status.memory?.local ?? {};
  setText("memory-count", `memory: ${localMemory.records ?? 0}`);

  const vram = status.models?.vram;
  const sampledVram = vram && Number.isFinite(Number(vram.used_mb)) && Number.isFinite(Number(vram.total_mb));
  setText("vram-pill", sampledVram
    ? `VRAM: ${vram.used_mb}/${vram.total_mb} MB`
    : "NVML: off · lifecycle safe");

  const agentCard = $("agent-card");
  if (agentCard) {
    renderKeyValues(agentCard, [
      ["name", status.agent?.name],
      ["role", status.agent?.role],
      ["target", status.agent?.target_experience],
      ["shell", status.agent?.desktop_shell_target],
    ]);
  }

  const loopList = $("loop-list");
  if (loopList) {
    loopList.innerHTML = (status.loop ?? [])
      .map((step) => `<span class="loop-chip">${escapeHtml(step)}</span>`)
      .join("");
  }

  const memoryCard = $("memory-card");
  if (memoryCard) {
    memoryCard.innerHTML = `
      <div class="memory-line"><strong>schema</strong> ${escapeHtml(status.memory?.schema_version ?? "unknown")}</div>
      <div class="memory-line"><strong>local records</strong> ${escapeHtml(localMemory.records ?? 0)}</div>
      <div class="memory-line"><strong>safe</strong> ${escapeHtml((status.memory?.recall_safe_statuses ?? []).join(", "))}</div>
      <div class="memory-line"><strong>review-only</strong> ${escapeHtml((status.memory?.review_only_statuses ?? []).slice(0, 5).join(", "))}</div>
    `;
  }

  const evalList = $("eval-list");
  if (evalList) {
    evalList.innerHTML = (status.evals?.active_detectors ?? [])
      .map((detector) => `<span class="tag">${escapeHtml(detector)}</span>`)
      .join("");
  }
}

const activeGoalStatuses = new Set(["starting", "running", "stopping", "awaiting_approval"]);
const liveGoalStatuses = new Set(["starting", "running", "stopping"]);

function goalCriterionLabel(criterion) {
  if (criterion?.label) return criterion.label;
  const params = criterion?.params ?? {};
  return {
    tests_pass: "Suite test verde",
    file_exists: `File presente: ${params.path || "?"}`,
    absence_of_pattern: `Pattern assente: ${params.pattern || "?"}`,
    contains_text: `Contenuto verificato: ${params.path || "?"}`,
    command_succeeds: `Comando con exit 0: ${(params.argv || []).join(" ")}`,
  }[criterion?.type] || criterion?.type || "Criterio";
}

function setMeter(id, ratio) {
  const meter = $(id);
  if (meter) meter.style.width = `${Math.max(0, Math.min(1, ratio || 0)) * 100}%`;
}

function setGoalFeedback(message, kind = "") {
  const feedback = $("goal-form-feedback");
  if (!feedback) return;
  feedback.textContent = message;
  feedback.dataset.kind = kind;
}

function goalCriterionPlaceholder(type) {
  return {
    tests_pass: "Nessun parametro richiesto",
    file_exists: "es. src/main.py",
    absence_of_pattern: "es. TODO|FIXME",
    contains_text: "percorso :: testo atteso",
  }[type] || "Valore";
}

function renderGoalCriteriaDraft() {
  const draft = $("goal-criteria-draft");
  if (!draft) return;
  if (!state.goalCriteriaDraft.length) {
    draft.innerHTML = '<div class="goal-draft-item"><span>Nessun criterio aggiunto.</span></div>';
    return;
  }
  draft.innerHTML = state.goalCriteriaDraft.map((criterion, index) => `
    <div class="goal-draft-item">
      <span>${escapeHtml(goalCriterionLabel(criterion))}</span>
      <button type="button" data-remove-goal-criterion="${index}" title="Rimuovi criterio">×</button>
    </div>`).join("");
}

function addGoalCriterion(type, rawValue = "") {
  const value = String(rawValue || "").trim();
  let criterion;
  if (type === "tests_pass") {
    criterion = { type, params: {} };
  } else if (type === "file_exists") {
    if (!value) throw new Error("Indica il percorso del file atteso.");
    criterion = { type, params: { path: value } };
  } else if (type === "absence_of_pattern") {
    if (!value) throw new Error("Indica il pattern che non deve comparire.");
    criterion = { type, params: { pattern: value } };
  } else if (type === "contains_text") {
    const separator = value.indexOf("::");
    const path = separator >= 0 ? value.slice(0, separator).trim() : "";
    const text = separator >= 0 ? value.slice(separator + 2).trim() : "";
    if (!path || !text) throw new Error("Usa il formato: percorso :: testo atteso.");
    criterion = { type, params: { path, text } };
  } else {
    throw new Error("Tipo di criterio non supportato dal form.");
  }

  const signature = JSON.stringify(criterion);
  if (!state.goalCriteriaDraft.some((item) => JSON.stringify(item) === signature)) {
    state.goalCriteriaDraft.push(criterion);
  }
  renderGoalCriteriaDraft();
  setGoalFeedback("Criterio aggiunto.", "ok");
}

function currentLiveGoal() {
  return [...state.goals].reverse().find((goal) => liveGoalStatuses.has(goal.status));
}

function syncGoalLaunchState() {
  const projectLabel = $("goal-project-label");
  if (projectLabel) {
    projectLabel.textContent = state.selectedProjectPath
      ? (state.selectedProjectPath.split(/[\\/]/).filter(Boolean).at(-1) || state.selectedProjectPath)
      : "Seleziona un progetto";
    projectLabel.title = state.selectedProjectPath || "";
  }
  const start = $("goal-start-button");
  if (start) {
    const live = currentLiveGoal();
    start.disabled = !state.selectedProjectPath || Boolean(live);
    start.title = live
      ? "Un Goal è già in esecuzione"
      : (!state.selectedProjectPath ? "Seleziona prima un progetto" : "Avvia sul progetto selezionato");
  }
}

async function refreshGoals() {
  const goals = await fetchJson("/api/goal").catch(() => ({ goal_runs: [] }));
  renderGoalPanel(goals);
}

function updateGoalPolling(isLive) {
  if (isLive && !state.goalEventSource && !state.goalPoll) {
    state.goalPoll = window.setInterval(refreshGoals, 5000);
    setText("goal-stream-status", "poll fallback");
  } else if (!isLive && state.goalPoll) {
    window.clearInterval(state.goalPoll);
    state.goalPoll = null;
  }
}

function closeGoalEventStream() {
  if (state.goalEventSource) state.goalEventSource.close();
  state.goalEventSource = null;
  if (state.goalPoll) {
    window.clearInterval(state.goalPoll);
    state.goalPoll = null;
  }
}

function goalEventSummary(event) {
  const data = event?.data ?? {};
  if (event?.type === "goal_attempt") {
    return `${data.strategy || "executor"} · ${data.status || "step"}`;
  }
  return data.status || data.role || event?.level || "";
}

function renderGoalEvents(events = []) {
  state.goalEvents = Array.isArray(events) ? events.slice(-12) : [];
  const feed = $("goal-event-feed");
  if (!feed) return;
  if (!state.goalEvents.length) {
    feed.innerHTML = '<div class="goal-event-empty">In attesa del primo evento strutturato.</div>';
    return;
  }
  feed.innerHTML = state.goalEvents.map((event) => `
    <div class="goal-event level-${escapeHtml(event.level || "info")}" data-goal-event-seq="${escapeHtml(event.seq)}">
      <span>${escapeHtml(event.type || "event")}</span>
      <div>
        <strong>${escapeHtml(event.message || event.type || "Evento Goal")}</strong>
        <small>${escapeHtml(goalEventSummary(event))} · #${escapeHtml(event.seq)} ${escapeHtml(formatEventTime(event))}</small>
      </div>
    </div>`).join("");
  feed.scrollTop = feed.scrollHeight;
}

function appendGoalEvent(event) {
  if (!event || event.goal_run_id !== state.streamedGoalRunId) return;
  const seq = Number(event.seq ?? -1);
  if (state.goalEvents.some((item) => Number(item.seq) === seq)) return;
  state.lastGoalEventSeq = Math.max(state.lastGoalEventSeq, seq);
  renderGoalEvents([...state.goalEvents, event]);
}

function startGoalEventStream(goalRunId) {
  if (!window.EventSource || !goalRunId || state.goalEventSource) {
    updateGoalPolling(Boolean(currentLiveGoal()));
    return;
  }
  const url = `/api/goal/${encodeURIComponent(goalRunId)}/events/stream?after_seq=${state.lastGoalEventSeq}`;
  const source = new EventSource(apiUrl(url));
  state.goalEventSource = source;
  if (state.goalPoll) {
    window.clearInterval(state.goalPoll);
    state.goalPoll = null;
  }
  setText("goal-stream-status", "connessione…");

  source.onopen = () => setText("goal-stream-status", "stream live");
  source.onmessage = (message) => {
    try {
      const event = JSON.parse(message.data);
      appendGoalEvent(event);
      if (TERMINAL_GOAL_EVENT_TYPES.has(event.type)) {
        source.close();
        if (state.goalEventSource === source) state.goalEventSource = null;
        setText("goal-stream-status", "completo");
      }
      refreshGoals().catch(() => {});
    } catch (err) {
      console.warn("Invalid Goal event", err);
    }
  };
  source.addEventListener("done", () => {
    source.close();
    if (state.goalEventSource === source) state.goalEventSource = null;
    setText("goal-stream-status", "completo");
    refreshGoals().catch(() => {});
  });
  source.onerror = () => {
    source.close();
    if (state.goalEventSource === source) state.goalEventSource = null;
    updateGoalPolling(Boolean(currentLiveGoal()));
  };
}

async function loadGoalEvents(goalRunId, isLive) {
  const payload = await fetchJson(`/api/goal/${encodeURIComponent(goalRunId)}/events?limit=100`);
  if (state.streamedGoalRunId !== goalRunId) return;
  const events = payload.events ?? [];
  state.lastGoalEventSeq = events.length ? Number(events.at(-1).seq ?? -1) : -1;
  renderGoalEvents(events);
  if (isLive) startGoalEventStream(goalRunId);
  else setText("goal-stream-status", "completo");
}

function ensureGoalEvents(goal) {
  const goalRunId = goal?.goal_run_id || "";
  const isLive = liveGoalStatuses.has(goal?.status);
  if (state.streamedGoalRunId === goalRunId) {
    if (isLive && !state.goalEventSource) startGoalEventStream(goalRunId);
    if (!isLive && state.goalEventSource) {
      state.goalEventSource.close();
      state.goalEventSource = null;
      setText("goal-stream-status", "completo");
    }
    return;
  }
  closeGoalEventStream();
  state.streamedGoalRunId = goalRunId || null;
  state.lastGoalEventSeq = -1;
  renderGoalEvents([]);
  if (!goalRunId) {
    setText("goal-stream-status", "idle");
    return;
  }
  setText("goal-stream-status", "caricamento…");
  loadGoalEvents(goalRunId, isLive).catch(() => {
    setText("goal-stream-status", isLive ? "poll fallback" : "non disponibile");
    updateGoalPolling(isLive);
  });
}

const TERMINAL_GOAL_EVENT_TYPES = new Set(["goal_finished", "goal_error"]);

async function startGoal() {
  if (!state.selectedProjectPath) throw new Error("Seleziona prima un progetto.");
  if (currentLiveGoal()) throw new Error("Un Goal è già in esecuzione.");
  const objective = $("goal-objective-input")?.value.trim() || "";
  if (!objective) throw new Error("Descrivi l'obiettivo da raggiungere.");
  if (!state.goalCriteriaDraft.length) throw new Error("Aggiungi almeno un criterio verificabile.");

  const mode = $("goal-mode-select")?.value || "maintenance";
  const approval = mode === "scaffold" ? "auto" : ($("goal-approval-select")?.value || "manual");
  const budgetSteps = Number.parseInt($("goal-budget-steps")?.value || "12", 10);
  const budgetMinutes = Number.parseInt($("goal-budget-minutes")?.value || "30", 10);
  if (!(budgetSteps >= 1 && budgetSteps <= 100)) throw new Error("Il budget step deve essere tra 1 e 100.");
  if (!(budgetMinutes >= 1 && budgetMinutes <= 480)) throw new Error("Il budget tempo deve essere tra 1 e 480 minuti.");

  const start = $("goal-start-button");
  if (start) start.disabled = true;
  setGoalFeedback("Avvio del Goal in corso…");
  const result = await postJson("/api/goal/run", {
    project_path: state.selectedProjectPath,
    objective,
    acceptance: state.goalCriteriaDraft,
    mode,
    approval_policy: approval,
    budget_steps: budgetSteps,
    budget_seconds: budgetMinutes * 60,
    role: $("goal-role-select")?.value || "scaffolder",
  });
  if (result.error) throw new Error(result.error);
  setGoalFeedback(`Goal avviato: ${result.goal_run_id}`, "ok");
  const launcher = $("goal-launcher");
  if (launcher) launcher.open = false;
  appendChatMessage("assistant", `Goal Mode avviata sul progetto ${activeProjectLabel()}.`);
  await refreshGoals();
}

async function stopGoal(goalRunId) {
  if (!goalRunId) return;
  const button = $("goal-stop-button");
  if (button) button.disabled = true;
  const result = await postJson(`/api/goal/${encodeURIComponent(goalRunId)}/stop`, {});
  if (result.error) throw new Error(result.error);
  setGoalFeedback(result.reason || "Stop richiesto.", "ok");
  await refreshGoals();
}

function renderGoalPanel(payload) {
  const goals = Array.isArray(payload?.goal_runs) ? payload.goal_runs : [];
  state.goals = goals;
  const active = [...goals].reverse().find((goal) => activeGoalStatuses.has(goal.status));
  const goal = active || goals.at(-1);
  const empty = $("goal-empty");
  const summary = $("goal-summary");
  if (!goal) {
    if (empty) empty.hidden = false;
    if (summary) summary.hidden = true;
    setText("goal-status-badge", "idle");
    const badge = $("goal-status-badge");
    if (badge) badge.dataset.status = "idle";
    const reason = $("goal-reason");
    if (reason) reason.hidden = true;
    const stop = $("goal-stop-button");
    if (stop) stop.hidden = true;
    ensureGoalEvents(null);
    updateGoalPolling(false);
    syncGoalLaunchState();
    return;
  }

  if (empty) empty.hidden = true;
  if (summary) summary.hidden = false;
  const badge = $("goal-status-badge");
  if (badge) {
    badge.textContent = goal.status || "unknown";
    badge.dataset.status = goal.status || "unknown";
  }
  setText("goal-objective", goal.objective || "Goal senza descrizione");
  const policy = goal.requires_checkpoint ? "supervisione" : "autonomo";
  setText("goal-meta", `${goal.role || "scaffolder"} · ${goal.mode || "maintenance"} · ${policy}`);
  const reason = $("goal-reason");
  if (reason) {
    reason.textContent = goal.reason || "";
    reason.hidden = !goal.reason;
  }

  const stop = $("goal-stop-button");
  if (stop) {
    stop.hidden = !liveGoalStatuses.has(goal.status);
    stop.disabled = goal.status === "stopping";
    stop.dataset.goalRunId = goal.goal_run_id || "";
    stop.textContent = goal.status === "stopping" ? "Stop richiesto…" : "Ferma dopo lo step corrente";
  }

  const attempts = Array.isArray(goal.attempts) ? goal.attempts : [];
  const latestEvaluation = goal.evaluation
    || [...attempts].reverse().find((attempt) => attempt?.evaluation)?.evaluation;
  const results = Array.isArray(latestEvaluation?.results) ? latestEvaluation.results : [];
  const checklist = $("goal-checklist");
  if (checklist) {
    checklist.innerHTML = (goal.acceptance || []).map((criterion, index) => {
      const result = results[index];
      const stateClass = result ? (result.passed ? "passed" : "failed") : "pending";
      const icon = result ? (result.passed ? "✓" : "×") : "·";
      const evidenceLabel = result ? "Apri evidenza" : "In attesa di valutazione";
      const evidence = result?.detail || "Il criterio non è ancora stato valutato.";
      const verdict = result ? (result.passed ? "PASS" : "FAIL") : "PENDING";
      return `<details class="goal-check ${stateClass}">
        <summary><span>${icon}</span><div><strong>${escapeHtml(goalCriterionLabel(criterion))}</strong><small>${evidenceLabel}</small></div></summary>
        <div class="goal-evidence"><div><code>${escapeHtml(criterion?.type || "criterion")}</code><strong>${verdict}</strong></div><p>${escapeHtml(evidence)}</p></div>
      </details>`;
    }).join("") || '<div class="goal-check pending"><strong>Checklist non disponibile</strong></div>';
  }

  const budgetSteps = Number(goal.budget_steps || 0);
  const stepCount = attempts.length;
  setText("goal-step-count", `${stepCount} / ${budgetSteps || "—"}`);
  setMeter("goal-step-fill", budgetSteps ? stepCount / budgetSteps : 0);

  const started = Date.parse(goal.started_at || "");
  const ended = Date.parse(goal.finished_at || "");
  const elapsedSeconds = Number.isFinite(started)
    ? Math.max(0, ((Number.isFinite(ended) ? ended : Date.now()) - started) / 1000)
    : 0;
  const budgetSeconds = Number(goal.budget_seconds || 0);
  setText("goal-time-count", `${Math.round(elapsedSeconds / 60)}m / ${budgetSeconds ? Math.round(budgetSeconds / 60) : "—"}m`);
  setMeter("goal-time-fill", budgetSeconds ? elapsedSeconds / budgetSeconds : 0);
  ensureGoalEvents(goal);
  updateGoalPolling(liveGoalStatuses.has(goal.status));
  syncGoalLaunchState();
}

function renderGovernanceStatus(knowledge, council, routing) {
  const knowledgeCard = $("knowledge-exchange-card");
  if (knowledgeCard) {
    const counts = knowledge?.counts ?? {};
    knowledgeCard.innerHTML = `
      <strong>Knowledge exchange</strong>
      <span>${escapeHtml(counts.promoted ?? 0)} promosse · ${escapeHtml(counts.quarantine ?? 0)} in quarantena</span>
      <small>store separato · raw memory non condivisa</small>`;
  }
  const councilCard = $("council-card");
  if (councilCard) {
    const covered = council?.covered_axes?.length ?? 0;
    const total = council?.axes?.length ?? 5;
    const missing = council?.missing_axes ?? [];
    councilCard.innerHTML = `
      <strong>Evidence Council</strong>
      <span>${escapeHtml(covered)}/${escapeHtml(total)} assi disponibili</span>
      <small>${missing.length ? `attende: ${escapeHtml(missing.join(", "))}` : "copertura completa"} · no auto-promozione</small>`;
  }
  const routingCard = $("routing-card");
  if (routingCard) {
    const roles = Object.entries(routing?.roles ?? {});
    const active = roles.filter(([, value]) => value.enabled).map(([role]) => role);
    const future = roles.filter(([, value]) => value.future && !value.enabled).map(([role]) => role);
    routingCard.innerHTML = `
      <strong>Routing ruoli</strong>
      <span>attivi: ${escapeHtml(active.join(", ") || "nessuno")}</span>
      <small>futuri disabilitati: ${escapeHtml(future.join(", ") || "nessuno")} · switch manuale</small>`;
  }
}

async function previewCapabilityRoute() {
  const output = $("routing-preview-result");
  if (!output) return;
  output.textContent = "calcolo…";
  try {
    const result = await postJson("/api/routing/plan", {
      capability: $("routing-capability")?.value || "quick_question",
      resident_role: null,
    });
    if (result.error) throw new Error(result.error);
    const target = result.target_role || "non disponibile";
    output.textContent = `${target} · ${result.status} · nessuno switch eseguito`;
  } catch (err) {
    output.textContent = `routing non disponibile: ${err.message || err}`;
  }
}

const terminalRunStatuses = new Set([
  "success", "verified_success", "syntax_only", "failed", "timeout", "stopped",
  "stalled", "awaiting_approval", "rejected", "rolled_back", "applied_uncommitted",
]);

function runStatusIcon(status) {
  return {
    starting: "🟡", running: "🔵", success: "✅", verified_success: "✅",
    syntax_only: "⚠️", failed: "❌", timeout: "⏱️", stopped: "🛑",
    stalled: "⏸️", awaiting_approval: "👁", rejected: "🚫", rolled_back: "↩",
    applied_uncommitted: "⚠️",
  }[status] || "⏸️";
}

function setPipelineStage(index = null, completed = false) {
  state.pipelineStage = index;
  document.querySelectorAll("#pipeline-steps .pipe-step").forEach((step, position) => {
    step.classList.toggle("active", !completed && index === position);
    step.classList.toggle("complete", completed ? position <= 3 : index !== null && position < index);
  });
}

// Stati terminali di un run: una volta raggiunti, gli eventi intermedi ancora
// in coda o ri-consegnati dallo stream non devono riportare il badge a running.
const RUN_TERMINAL_STATES = new Set([
  "awaiting_approval", "success", "verified_success", "failed",
  "rejected", "rolled_back", "applied_uncommitted", "stopped", "timeout",
]);

function showRunStatus(runId, status, { updateBadge = true, completed = false } = {}) {
  if (!runId) return;
  state.selectedRunId = runId;
  state.selectedRunStatus = status || "running";
  setText("mind-state", state.selectedRunStatus);
  if (completed) setPipelineStage(3, true);
  if (updateBadge) {
    const runEl = $("activity-run");
    if (runEl) {
      runEl.innerHTML = `<span class="run-badge">${runStatusIcon(state.selectedRunStatus)} ${escapeHtml(state.selectedRunStatus)}</span> <span class="run-id">${escapeHtml(runId)}</span>`;
    }
  }
}

function applyRunEventToActivity(event) {
  if (!event || event.run_id !== state.selectedRunId) return;
  const stages = {
    run_started: 0, run_resumed: 0, models: 0, context: 0, plan: 0,
    act: 1, patch: 1, verify: 2,
    quality_gate: 3, quality_gate_passed: 3, quality_gate_failed: 3,
    memory: 3, commit: 3,
  };
  if (Object.hasOwn(stages, event.type)) setPipelineStage(stages[event.type]);
  if (event.type === "run_finished") {
    const status = event.data?.status || "failed";
    showRunStatus(event.run_id, status, { completed: ["success", "verified_success", "awaiting_approval"].includes(status) });
    loadRunLog(event.run_id).catch(() => {});
    if (state.selectedProjectPath) renderActivityRail(state.selectedProjectPath).catch(() => {});
    // A fine run ricarico l'overview del progetto: e' li' che vengono
    // renderizzati i bottoni decisione (Diff / Applica / Rifiuta) quando lo
    // stato e' awaiting_approval. showRunStatus() sopra scrive solo il badge in
    // #activity-run, quindi senza questo refresh l'utente vede solo testo.
    loadProjectOverview().catch(() => {});
    return;
  }
  // Guard: se il run e' gia' in uno stato TERMINALE (es. awaiting_approval,
  // success, failed), un evento intermedio ri-consegnato dallo stream (es. dopo
  // una riconnessione SSE) NON deve riportare il badge a "running". Senza questo
  // guard un run in attesa di approvazione poteva "tornare running" da solo.
  if (RUN_TERMINAL_STATES.has(state.selectedRunStatus)) return;
  showRunStatus(event.run_id, "running");
}

function renderProjects(payload) {
  refreshActiveScope();
  const list = $("project-list");
  if (!list) return;

  const projects = payload?.projects ?? [];
  state.projects = projects;
  const cards = [
    `
      <button class="project-card ${state.selectedProjectPath === "" ? "active" : ""}" data-project-path="">
        <strong>General chat</strong>
        <span>Nessun progetto selezionato</span>
      </button>
    `,
    ...projects.map((project) => `
      <div class="chat-card-row ${project.path === state.selectedProjectPath ? "active" : ""}">
        <button class="project-card ${project.path === state.selectedProjectPath ? "active" : ""}" data-project-path="${escapeHtml(project.path)}" title="${escapeHtml(project.name)}${project.path ? ` — ${escapeHtml(project.path)}` : ""}">
          <strong>${escapeHtml(project.name)}</strong>
          <span>${project.linked ? "linked · " : ""}${escapeHtml(project.chats ?? 0)} chat - ${escapeHtml(project.knowledge ?? 0)} knowledge</span>
          ${project.work_dir ? `<span class="project-workdir" title="${escapeHtml(project.work_dir)}">📁 ${escapeHtml(project.work_dir.split(/[\\/]/).pop())}</span>` : ""}
        </button>
        <button class="chat-delete-button" data-remove-project-path="${escapeHtml(project.path)}" data-remove-project-linked="${project.linked ? "1" : ""}" title="${project.linked ? "Scollega progetto (i file restano)" : "Sposta il progetto nel cestino"}">×</button>
      </div>
    `),
  ];

  // Empty-state: nessun progetto ancora -> guida l'utente con CTA dirette
  // (funzionano SENZA modello attivo, a differenza dei prompt in chat).
  if (projects.length === 0) {
    cards.push(`
      <div class="project-empty">
        <p>Nessun progetto ancora.</p>
        <div class="project-empty-actions">
          <button type="button" class="tiny-button" data-empty-new>+ Crea progetto</button>
          <button type="button" class="tiny-button" data-empty-link>📁 Collega cartella</button>
        </div>
      </div>
    `);
  }

  list.innerHTML = cards.join("");
  list.querySelectorAll("[data-project-path]").forEach((button) => {
    button.addEventListener("click", () => selectProject(button.dataset.projectPath ?? ""));
  });
  list.querySelector("[data-empty-new]")?.addEventListener("click", () => {
    createWorkspaceProject().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`));
  });
  list.querySelector("[data-empty-link]")?.addEventListener("click", () => {
    linkWorkspaceFolder().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`));
  });
  list.querySelectorAll("[data-remove-project-path]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      removeProject(button.dataset.removeProjectPath, button.dataset.removeProjectLinked === "1");
    });
  });
}

async function removeProject(projectPath, linked) {
  if (!projectPath) return;
  const name = projectPath.split(/[\\/]/).pop();
  const message = linked
    ? `Scollegare il progetto "${name}"? I file restano al loro posto, sparisce solo dalla sidebar.`
    : `Spostare il progetto "${name}" nel cestino (workspace/_trash)? Recuperabile a mano, nessuna cancellazione permanente.`;
  if (!window.confirm(message)) return;
  try {
    const result = await postJson("/api/workspace/projects/remove", { path: projectPath });
    if (result?.error) throw new Error(result.error);
    if (state.selectedProjectPath === projectPath) await selectProject("");
    await refresh();
  } catch (err) {
    console.error(err);
    window.alert(`Rimozione fallita: ${err.message || err}`);
  }
}

function renderChatList(chats = []) {
  const list = $("chat-list");
  if (!list) return;

  const baseTitle = state.selectedProjectPath ? "Project default" : "General chat";
  const cards = [
    `
      <div class="chat-card-row ${state.selectedChatId ? "" : "active"}">
        <button class="chat-card" data-chat-id="">
          <strong>${escapeHtml(baseTitle)}</strong>
          <span>Storico compatibile legacy</span>
        </button>
        <button class="chat-delete-button" data-delete-chat-id="" title="Svuota questa chat">×</button>
      </div>
    `,
    ...chats.map((chat) => `
      <div class="chat-card-row ${chat.chat_id === state.selectedChatId ? "active" : ""}">
        <button class="chat-card" data-chat-id="${escapeHtml(chat.chat_id)}" title="${escapeHtml(chat.title || "Nuova chat")}">
          <strong>${escapeHtml(chat.title || "Nuova chat")}</strong>
          <span>${escapeHtml(chat.messages ?? 0)} messaggi</span>
        </button>
        <button class="chat-delete-button" data-delete-chat-id="${escapeHtml(chat.chat_id)}" title="Cancella chat">×</button>
      </div>
    `),
  ];

  list.innerHTML = cards.join("");
  list.querySelectorAll("[data-chat-id]").forEach((button) => {
    button.addEventListener("click", () => selectChat(button.dataset.chatId || null));
  });
  list.querySelectorAll("[data-delete-chat-id]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteChat(button.dataset.deleteChatId || null).catch((err) => {
        console.error(err);
        appendChatMessage("assistant", `[error] ${err.message}`);
      });
    });
  });
}

async function loadProjectOverview(projectPath = state.selectedProjectPath) {
  if (!projectPath) {
    state.selectedChatId = null;
    renderChatList([]);
    await loadChatHistory();
    const workBox = $("workdir-box");
    if (workBox) {
      workBox.textContent = "Nessun progetto selezionato.";
      workBox.classList.remove("linked");
    }
    const tags = $("context-tags");
    if (tags) tags.innerHTML = '<span class="context-empty">Seleziona un progetto.</span>';
    setText("activity-run", "Nessun run selezionato.");
    renderApprovalBanner("", null);
    return;
  }

  const params = new URLSearchParams({ project_path: projectPath, lite: "true" });
  const overview = await fetchJson(`/api/project/overview?${params.toString()}`);
  const chats = overview.chats ?? [];
  if (state.selectedChatId && !chats.some((chat) => chat.chat_id === state.selectedChatId)) {
    state.selectedChatId = null;
  }
  renderChatList(chats);
  await loadChatHistory();
  // Pannello Attività (destra): overview COMPLETO (files/pins/work_dir) +
  // ultimo run. Non-bloccante: se fallisce la chat resta usabile.
  renderActivityRail(projectPath).catch(() => {});
}

async function renderActivityRail(projectPath) {
  const workBox = $("workdir-box");
  const filesEl = $("workdir-files");
  const tagsEl = $("context-tags");
  const runEl = $("activity-run");
  if (!workBox) return;

  const full = await fetchJson(`/api/project/overview?${new URLSearchParams({
    project_path: projectPath,
    include_files: "false",
  }).toString()}`, {});

  // Cartella di lavoro
  const wd = full.work_dir || "";
  if (wd) {
    workBox.innerHTML = `<i class="folder-ico">📁</i> <span title="${escapeHtml(wd)}">${escapeHtml(wd.split(/[\\/]/).pop())}</span>`;
    workBox.classList.add("linked");
  } else {
    workBox.textContent = "Nessuna cartella collegata: i run girano nel progetto.";
    workBox.classList.remove("linked");
  }
  if (filesEl) renderWorkdirFileSummary();

  // Contesto attivo: cosa entra nel prompt (pin, knowledge, docs cache)
  if (tagsEl) {
    const tags = [];
    if ((full.pins || []).length) tags.push(`★ ${full.pins.length} pin`);
    if ((full.knowledge || []).length) tags.push(`📎 ${full.knowledge.length} knowledge`);
    if (full.description) tags.push("descrizione");
    if (full.instructions) tags.push("istruzioni");
    tagsEl.innerHTML = tags.length
      ? tags.map((t) => `<span class="context-tag">${escapeHtml(t)}</span>`).join("")
      : '<span class="context-empty">Nessun contesto extra: solo la chat.</span>';
  }

  // Ultimo run del progetto (avanzamento)
  if (runEl) {
    try {
      const lr = await fetchJson(`/api/project/last_run?${new URLSearchParams({ project_path: projectPath }).toString()}`, {});
      if (lr && lr.run_id) {
        const icon = runStatusIcon(lr.status);
        const resumeBtn = lr.resumable
          ? ` <button class="run-resume-btn" data-resume-run="${escapeHtml(lr.run_id)}" title="Riprendi il run interrotto da dove era arrivato">▶ Riprendi</button>`
          : "";
        const reviewBtns = lr.status === "awaiting_approval"
          ? ` <button class="run-decision-btn" data-review-change-run="${escapeHtml(lr.run_id)}">👁 Diff</button><button class="run-decision-btn approve" data-change-action="apply" data-change-run="${escapeHtml(lr.run_id)}">✓ Applica</button><button class="run-decision-btn reject" data-change-action="reject" data-change-run="${escapeHtml(lr.run_id)}">× Rifiuta</button>`
          : "";
        const rollbackBtn = lr.change_manifest_status === "applied"
          ? ` <button class="run-decision-btn" data-change-action="rollback" data-change-run="${escapeHtml(lr.run_id)}">↩ Rollback</button>`
          : "";
        runEl.innerHTML = `<span class="run-badge">${icon} ${escapeHtml(lr.status || "?")}</span> <span class="run-id">${escapeHtml(lr.run_id)}</span>${resumeBtn}${reviewBtns}${rollbackBtn}`;
        if (!state.selectedRunId || state.selectedRunId === lr.run_id) {
          state.selectedRunStatus = lr.status || null;
          setText("mind-state", lr.status || "ready");
        }
        if (!state.selectedRunId) selectRun(lr.run_id).catch(() => {});
        const btn = runEl.querySelector("[data-resume-run]");
        if (btn) btn.addEventListener("click", () => resumeRun(projectPath, btn.dataset.resumeRun));
        runEl.querySelectorAll("[data-change-action]").forEach((decision) => {
          decision.addEventListener("click", () => decideRunChanges(
            projectPath, decision.dataset.changeRun, decision.dataset.changeAction,
          ));
        });
        const review = runEl.querySelector("[data-review-change-run]");
        if (review) review.addEventListener("click", () => reviewRunChanges(
          projectPath, review.dataset.reviewChangeRun,
        ));
        renderApprovalBanner(projectPath, lr);
      } else {
        runEl.textContent = "Nessun run recente in questo progetto.";
        if (!state.selectedRunId) setText("mind-state", "ready");
        renderApprovalBanner(projectPath, null);
      }
    } catch (_) {
      runEl.textContent = "Nessun run recente in questo progetto.";
      renderApprovalBanner(projectPath, null);
    }
  }
}

// Banner di approvazione ben visibile al centro (sopra il composer). I bottoni
// nel pannello destro sono piccoli e vengono persi quando un run piu' recente
// rimpiazza lo stato: questo banner rende l'azione impossibile da mancare e
// resta finche' non decidi (Applica/Rifiuta) o non apri il Diff.
function renderApprovalBanner(projectPath, lr) {
  const banner = $("approval-banner");
  if (!banner) return;
  const actions = $("approval-banner-actions");
  const isAwaiting = lr && lr.run_id && lr.status === "awaiting_approval";
  if (!isAwaiting) {
    banner.hidden = true;
    if (actions) actions.innerHTML = "";
    return;
  }
  setText("approval-banner-run", lr.run_id);
  if (actions) {
    actions.innerHTML = `
      <button class="run-decision-btn" data-review-change-run="${escapeHtml(lr.run_id)}">👁 Diff</button>
      <button class="run-decision-btn approve" data-change-action="apply" data-change-run="${escapeHtml(lr.run_id)}">✓ Applica</button>
      <button class="run-decision-btn reject" data-change-action="reject" data-change-run="${escapeHtml(lr.run_id)}">× Rifiuta</button>`;
    actions.querySelectorAll("[data-change-action]").forEach((decision) => {
      decision.addEventListener("click", () => decideRunChanges(
        projectPath, decision.dataset.changeRun, decision.dataset.changeAction,
      ));
    });
    const review = actions.querySelector("[data-review-change-run]");
    if (review) review.addEventListener("click", () => reviewRunChanges(
      projectPath, review.dataset.reviewChangeRun,
    ));
  }
  banner.hidden = false;
}

async function selectProject(projectPath) {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.selectedRunId = null;
  state.selectedRunStatus = null;
  state.lastEventSeq = -1;
  setPipelineStage(null);
  setText("mind-state", "ready");
  renderTimeline([]);
  renderRunLog(null);
  state.selectedProjectPath = projectPath || "";
  setCenterView("chat");
  resetProjectEditor();
  resetManifestReview();
  refreshActiveScope();
  syncGoalLaunchState();
  state.selectedChatId = null;
  state.chatLoaded = true;
  document.querySelectorAll(".project-card").forEach((card) => {
    card.classList.toggle("active", (card.dataset.projectPath || "") === state.selectedProjectPath);
  });

  try {
    await Promise.all([
      loadProjectOverview(state.selectedProjectPath),
      loadTrainingOverview(),
      loadProjectTree().catch((treeError) => {
        console.error(treeError);
        setText("project-tree-status", `File non disponibili: ${treeError.message || treeError}`);
      }),
    ]);
  } catch (err) {
    console.error(err);
    renderChatHistory([]);
    appendChatMessage("assistant", `Contesto attivo: ${activeProjectLabel()} (overview non disponibile)`);
  }
}

async function selectChat(chatId) {
  state.selectedChatId = chatId || null;
  document.querySelectorAll(".chat-card-row").forEach((card) => {
    const button = card.querySelector("[data-chat-id]");
    card.classList.toggle("active", ((button?.dataset.chatId || "") === (state.selectedChatId || "")));
  });
  await loadChatHistory();
}

async function deleteChat(chatId) {
  const label = chatId ? "questa chat" : (state.selectedProjectPath ? "la chat default del progetto" : "la chat generale");
  if (!window.confirm(`Cancellare ${label}? Operazione non reversibile.`)) return;

  if (chatId && state.selectedProjectPath) {
    const result = await postJson("/api/project/chats/delete", {
      project_path: state.selectedProjectPath,
      chat_id: chatId,
    });
    if (result.status !== "deleted") throw new Error("chat non trovata");
    if (state.selectedChatId === chatId) state.selectedChatId = null;
    await loadProjectOverview(state.selectedProjectPath);
    return;
  }

  await postJson("/api/chat/history/clear", {
    project_path: state.selectedProjectPath || "",
    chat_id: "",
  });
  state.selectedChatId = null;
  await loadProjectOverview(state.selectedProjectPath);
  renderChatHistory([]);
}

async function createProjectChat(continueCurrent = false) {
  if (!state.selectedProjectPath) {
    appendChatMessage("assistant", "Seleziona un progetto prima di creare una chat multipla.");
    return;
  }

  const result = await postJson("/api/project/chats/new", {
    project_path: state.selectedProjectPath,
    title: continueCurrent ? "Continuazione" : "Nuova chat",
    continue_from_chat_id: continueCurrent ? (state.selectedChatId || "") : "",
  });
  state.selectedChatId = result.chat_id || null;
  await loadProjectOverview(state.selectedProjectPath);
}


async function linkWorkspaceFolder() {
  // Prima prova il picker nativo (funziona solo se il backend gira sulla
  // stessa macchina con display, es. dev su Windows). Se non disponibile
  // (app Tauri o backend headless sul rig), chiede il path e lo registra.
  let result = await postJson("/api/workspace/pick_folder", {}).catch(() => ({ error: "picker non disponibile" }));
  if (result.error || !result.path) {
    const path = await promptModal(
      "Incolla il percorso della cartella (sulla macchina del backend)",
      { placeholder: "/home/tillo/progetti/mio-progetto", okLabel: "Collega" });
    if (!path) return;
    result = await postJson("/api/workspace/link_path", { path });
    if (result.error) throw new Error(result.error);
  }
  await refresh();
  await selectProject(result.path);
  appendChatMessage("assistant", `Cartella collegata e autorizzata: ${result.path}.`);
}

async function createWorkspaceProject() {
  const name = await promptModal("Nome del nuovo progetto DEVIN", { placeholder: "es. calcolatrice", okLabel: "Crea" });
  if (!name || !name.trim()) return;
  const result = await postJson("/api/workspace/projects/new", { name: name.trim() });
  if (result.error) throw new Error(result.error);
  await refresh();
  await selectProject(result.path || "");
  appendChatMessage("assistant", `Progetto creato: ${result.name}. Puoi allegare file, aggiungere knowledge o chiedermi di scaffoldare il codice.`);
}

function renderRuns(runs) {
  const list = $("run-list");
  state.runs = runs ?? [];
  if (!list) return;

  if (!runs?.length) {
    list.innerHTML = '<div class="empty-card">Nessun run recente.</div>';
    renderTimeline([]);
    return;
  }

  if (!state.selectedRunId || !runs.some((run) => run.run_id === state.selectedRunId)) {
    state.selectedRunId = runs[0].run_id;
  }

  list.innerHTML = runs
    .slice(0, 8)
    .map((run) => `
      <button class="run-card ${run.run_id === state.selectedRunId ? "active" : ""}" data-run-id="${escapeHtml(run.run_id)}">
        <strong>${escapeHtml(run.run_id)}</strong>
        <span>${escapeHtml(run.status)} - ${escapeHtml(new Date(run.mtime).toLocaleString())}</span>
      </button>
    `)
    .join("");

  list.querySelectorAll("[data-run-id]").forEach((button) => {
    button.addEventListener("click", () => selectRun(button.dataset.runId));
  });
}

function formatEventTime(event) {
  if (!event?.ts) return "";
  try {
    return new Date(event.ts).toLocaleTimeString();
  } catch {
    return "";
  }
}

function renderTimeline(events) {
  const timeline = $("timeline");
  if (!timeline) return;

  if (!events?.length) {
    timeline.innerHTML = `
      <article class="timeline-item timeline-empty">
        <span class="timeline-kind">idle</span>
        <div>
          <h3>Nessun evento strutturato per questo run</h3>
          <p>I nuovi run scriveranno plan, act, verify, memory e finish qui dentro.</p>
        </div>
      </article>
    `;
    return;
  }

  timeline.innerHTML = events
    .map((event) => `
      <article class="timeline-item event-${escapeHtml(event.type)} level-${escapeHtml(event.level)}" data-event-seq="${escapeHtml(event.seq)}">
        <span class="timeline-kind">${escapeHtml(event.type)}</span>
        <div>
          <h3>${escapeHtml(event.message || event.type)}</h3>
          <p>${escapeHtml(event.data?.status ?? event.data?.mode ?? event.level ?? "")}</p>
          <span class="timeline-time">#${escapeHtml(event.seq)} ${escapeHtml(formatEventTime(event))}</span>
        </div>
      </article>
    `)
    .join("");
  // Al (ri)caricamento di un run porta la timeline in fondo, sull'ultimo evento.
  timeline.scrollTop = timeline.scrollHeight;
  applyRunEventToActivity(events[events.length - 1]);
}

function appendTimelineEvent(event) {
  if (!event || event.run_id !== state.selectedRunId) return;
  state.lastEventSeq = Math.max(state.lastEventSeq, Number(event.seq ?? state.lastEventSeq));
  applyRunEventToActivity(event);

  const timeline = $("timeline");
  if (!timeline) return;
  // Autoscroll "intelligente": segue gli eventi nuovi solo se sei gia' in fondo,
  // cosi' se stai leggendo piu' su non ti strappa via.
  const nearBottom = timeline.scrollHeight - timeline.scrollTop - timeline.clientHeight < 48;
  const existing = new Set(
    Array.from(timeline.querySelectorAll("[data-event-seq]")).map((el) => el.dataset.eventSeq),
  );
  if (existing.has(String(event.seq))) return;

  const wrapper = document.createElement("div");
  wrapper.innerHTML = `
    <article class="timeline-item event-${escapeHtml(event.type)} level-${escapeHtml(event.level)}" data-event-seq="${escapeHtml(event.seq)}">
      <span class="timeline-kind">${escapeHtml(event.type)}</span>
      <div>
        <h3>${escapeHtml(event.message || event.type)}</h3>
        <p>${escapeHtml(event.data?.status ?? event.data?.mode ?? event.level ?? "")}</p>
        <span class="timeline-time">#${escapeHtml(event.seq)} ${escapeHtml(formatEventTime(event))}</span>
      </div>
    </article>
  `;

  if (timeline.querySelector(".timeline-empty")) timeline.innerHTML = "";
  timeline.appendChild(wrapper.firstElementChild);
  if (nearBottom) timeline.scrollTop = timeline.scrollHeight;
}

async function loadRunEvents(runId) {
  if (!runId) return;
  const payload = await fetchJson(`/api/run/${encodeURIComponent(runId)}/events?limit=100`);
  const events = payload.events ?? [];
  state.lastEventSeq = events.length ? Number(events[events.length - 1].seq ?? -1) : -1;
  renderTimeline(events);
  startEventStream(runId);
}

function startEventStream(runId) {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }

  if (!window.EventSource || !runId) return;

  const url = `/api/run/${encodeURIComponent(runId)}/events/stream?after_seq=${state.lastEventSeq}`;
  const source = new EventSource(apiUrl(url));
  state.eventSource = source;

  source.onmessage = (message) => {
    try {
      appendTimelineEvent(JSON.parse(message.data));
    } catch (err) {
      console.warn("Invalid run event", err);
    }
  };

  source.onerror = () => {
    source.close();
    if (state.eventSource === source) state.eventSource = null;
  };
}


function renderRunLog(payload) {
  const output = $("run-log-output");
  if (!output) return;

  if (!state.selectedRunId) {
    output.textContent = "Seleziona un run nella sidebar per vedere il log.";
    return;
  }

  if (payload?.error) {
    output.textContent = `[error] ${payload.error}`;
    return;
  }

  const header = `run: ${payload.run_id ?? state.selectedRunId} - lines ${payload.lines_returned ?? 0}/${payload.total_lines ?? 0}`;
  output.textContent = `${header}\n\n${payload.output || "(log vuoto)"}`;
  output.scrollTop = output.scrollHeight;
}

async function loadRunLog(runId = state.selectedRunId) {
  if (!runId) {
    renderRunLog(null);
    return;
  }

  try {
    const params = new URLSearchParams({ run_id: runId, lines: "160" });
    const payload = await fetchJson(`/api/terminal/output?${params.toString()}`);
    renderRunLog(payload);
  } catch (err) {
    renderRunLog({ error: err.message });
  }
}

async function selectRun(runId) {
  if (!runId) return;
  state.selectedRunId = runId;
  document.querySelectorAll(".run-card").forEach((card) => {
    card.classList.toggle("active", card.dataset.runId === runId);
  });

  try {
    await loadRunEvents(runId);
    await loadRunLog(runId);
  } catch (err) {
    console.error(err);
    renderTimeline([]);
  }
}

async function resumeRun(projectPath, runId) {
  // Riprende ESPLICITAMENTE un run interrotto (crash/restart del backend):
  // stesso run_id, log e timeline continuano, l'orchestratore riparte
  // dall'attempt salvato in .devin_state.
  try {
    const result = await postJson("/api/run/resume", { path: projectPath, run_id: runId });
    if (result?.error) {
      appendChatMessage("assistant", `Ripresa del run fallita: ${result.error}`);
      return;
    }
    appendChatMessage("assistant", `Run ${result.run_id} ripreso dall'attempt ${Number(result.attempt ?? 0) + 1}. Seguo la timeline.`);
    setPipelineStage(0);
    showRunStatus(result.run_id, "starting");
    await selectRun(result.run_id);
  } catch (err) {
    console.error(err);
    appendChatMessage("assistant", `Ripresa del run fallita: ${err.message || err}`);
  }
}

async function decideRunChanges(projectPath, runId, action) {
  const reviewedManifestMatches = (
    state.reviewedChangeRunId === runId
    && state.reviewedChangeProjectPath === projectPath
    && /^[0-9a-f]{64}$/i.test(state.reviewedManifestDigest || "")
  );
  if (["apply", "reject"].includes(action) && !reviewedManifestMatches) {
    appendChatMessage("assistant", "Apro prima il manifest verificato: Applica/Rifiuta richiedono la review del digest.");
    await reviewRunChanges(projectPath, runId);
    return;
  }
  const labels = { apply: "applicare", reject: "rifiutare", rollback: "ripristinare" };
  const confirmed = await confirmModal(
    `Confermi di ${labels[action] || action} le modifiche verificate del run ${runId}?`,
    { okLabel: action === "apply" ? "Applica manifest" : action === "reject" ? "Rifiuta manifest" : "Ripristina", danger: action !== "apply" },
  );
  if (!confirmed || state.manifestDecisionPending) return;
  state.manifestDecisionPending = true;
  if (reviewedManifestMatches) {
    const applyButton = $("manifest-diff-apply");
    const rejectButton = $("manifest-diff-reject");
    if (applyButton) applyButton.disabled = true;
    if (rejectButton) rejectButton.disabled = true;
  }
  try {
    const result = await postJson(`/api/run/changes/${action}`, {
      path: projectPath,
      run_id: runId,
      commit: action === "apply",
      expected_entry_digest: reviewedManifestMatches ? state.reviewedManifestDigest : null,
    });
    if (result?.error) {
      appendChatMessage("assistant", `Decisione non applicata: ${result.error}`);
      return;
    }
    appendChatMessage("assistant", `Run ${runId}: ${result.status}.`);
    if (reviewedManifestMatches) {
      state.reviewedManifestDecision = result.status || action;
      setText("manifest-diff-status", result.status || action);
      const statusBadge = $("manifest-diff-status");
      if (statusBadge) statusBadge.dataset.status = result.status || action;
      setText(
        "manifest-diff-digest",
        `digest ${state.reviewedManifestDigest.slice(0, 16)} · decisione registrata`,
      );
      const applyButton = $("manifest-diff-apply");
      const rejectButton = $("manifest-diff-reject");
      if (applyButton) applyButton.disabled = true;
      if (rejectButton) rejectButton.disabled = true;
    }
    await renderActivityRail(projectPath);
    await loadRunLog(runId);
    if (["apply", "rollback"].includes(action)) loadProjectTree().catch(() => {});
  } catch (err) {
    console.error(err);
    appendChatMessage("assistant", `Decisione non applicata: ${err.message || err}`);
  } finally {
    state.manifestDecisionPending = false;
    if (reviewedManifestMatches && !state.reviewedManifestDecision) {
      const applyButton = $("manifest-diff-apply");
      const rejectButton = $("manifest-diff-reject");
      if (applyButton) applyButton.disabled = false;
      if (rejectButton) rejectButton.disabled = false;
    }
  }
}

async function reviewRunChanges(projectPath, runId) {
  try {
    const params = new URLSearchParams({ path: projectPath });
    const payload = await fetchJson(`/api/run/changes/${encodeURIComponent(runId)}?${params.toString()}`);
    if (payload?.error) {
      appendChatMessage("assistant", `Preview non disponibile: ${payload.error}`);
      return;
    }
    if (
      payload.schema !== "change_manifest_v1"
      || payload.status !== "pending"
      || !/^[0-9a-f]{64}$/i.test(payload.entry_digest || "")
    ) {
      throw new Error("manifest preview incompleto o non verificabile");
    }
    if (state.selectedProjectPath !== projectPath) {
      throw new Error("il progetto selezionato è cambiato durante la review");
    }
    renderManifestWorkspace(payload, projectPath, runId);
  } catch (err) {
    appendChatMessage("assistant", `Preview non disponibile: ${err.message || err}`);
  }
}

function appendChatMessage(role, content = "", options = {}) {
  const thread = $("chat-thread");
  // La hero di benvenuto vive solo finche' la chat e' vuota: al primo
  // messaggio (utente o assistant) sparisce, come nelle home dei desktop
  // Claude/Codex.
  thread.querySelector(".chat-hero")?.remove();
  const article = document.createElement("article");
  article.className = `chat-message ${role}`;
  const deleteButton = Number.isInteger(options.historyIndex)
    ? `<button class="message-delete-button" data-message-index="${options.historyIndex}" title="Cancella questo messaggio">×</button>`
    : "";
  article.innerHTML = `
    <div class="chat-message-topline">
      <span class="chat-role">${role === "user" ? "TU" : "DEVIN"}</span>
      ${deleteButton}
    </div>
    <p>${escapeHtml(content)}</p>
  `;
  const btn = article.querySelector("[data-message-index]");
  if (btn) {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      deleteChatMessage(Number(btn.dataset.messageIndex)).catch((err) => {
        console.error(err);
        appendChatMessage("assistant", `[error] ${err.message}`);
      });
    });
  }
  thread.appendChild(article);
  thread.scrollTop = thread.scrollHeight;
  return article.querySelector("p");
}

function renderChatHistory(history = []) {
  const thread = $("chat-thread");
  thread.innerHTML = "";

  if (!history.length) {
    // Home stile Claude/Codex desktop: saluto + composer come protagonisti,
    // niente finto messaggio dell'assistente.
    thread.innerHTML = `
      <div class="chat-hero">
        <div class="chat-hero-mark">&#129504;</div>
        <h1>Ciao Alessandro.</h1>
        <p class="chat-hero-sub"></p>
        <div class="chat-hero-suggestions">
          <button type="button" class="hero-chip" data-hero-prompt="Fai il punto del progetto: struttura, stato attuale e prossimi passi consigliati.">📋 Punto del progetto</button>
          <button type="button" class="hero-chip" data-hero-action="new-project">✨ Nuovo progetto</button>
          <button type="button" class="hero-chip" data-hero-prompt="Analizza il progetto selezionato e trova eventuali bug o fragilità, poi proponi i fix.">🐛 Caccia ai bug</button>
        </div>
      </div>`;
    const sub = thread.querySelector(".chat-hero-sub");
    if (sub) {
      sub.textContent = state.selectedProjectPath
        ? `Su cosa lavoriamo in ${activeProjectLabel()}?`
        : "Su cosa lavoriamo oggi?";
    }
    thread.querySelectorAll("[data-hero-prompt]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const input = $("chat-input");
        if (input) {
          input.value = btn.dataset.heroPrompt;
          input.focus();
        }
      });
    });
    // Azioni dirette (non prompt): creano/collegano senza modello attivo.
    thread.querySelector('[data-hero-action="new-project"]')?.addEventListener("click", () => {
      createWorkspaceProject().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`));
    });
    return;
  }

  history.forEach((message, index) => {
    appendChatMessage(message.role === "user" ? "user" : "assistant", message.content ?? "", { historyIndex: index });
  });
}

async function deleteChatMessage(index) {
  if (!window.confirm("Cancellare questo messaggio dalla chat?")) return;
  let result;
  try {
    result = await postJson("/api/chat/history/delete_message", {
      project_path: state.selectedProjectPath || "",
      chat_id: state.selectedChatId || "",
      index,
    });
  } catch (err) {
    if (err.status === 404) {
      throw new Error("delete_message non è caricato nel backend attivo. Riavvia DEVIN backend e ricarica /app: venv/bin/python devin/ui/fast_app.py");
    }
    throw err;
  }
  if (result.error) throw new Error(result.error);
  await loadChatHistory();
}

async function loadChatHistory() {
  const params = new URLSearchParams();
  if (state.selectedProjectPath) params.set("project_path", state.selectedProjectPath);
  if (state.selectedChatId) params.set("chat_id", state.selectedChatId);
  const payload = await fetchJson(`/api/chat/history?${params.toString()}`);
  renderChatHistory(payload.history ?? []);
  const continueButton = $("continue-chat-button");
  if (continueButton) {
    continueButton.hidden = !(payload.continuity_ready && state.selectedProjectPath && state.selectedChatId);
    continueButton.title = payload.continuity_ready
      ? `Continue with ${payload.continuity_summarized_messages ?? 0} summarized messages`
      : "Continuity checkpoint not ready";
  }
}

function setChatBusy(isBusy) {
  $("chat-send").disabled = isBusy;
  $("chat-input").disabled = isBusy;
  setText("chat-send", isBusy ? "..." : "Invia");
}

function parseSseBlock(block) {
  const event = { type: "message", data: "" };
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event.type = line.slice(6).trim();
    if (line.startsWith("data:")) event.data += line.slice(5).trim();
  }
  return event;
}

function applyChatEvent(event, assistantNode) {
  if (!event.data) return;

  let payload;
  try {
    payload = JSON.parse(event.data);
  } catch {
    payload = { token: event.data };
  }

  if (event.type === "message" && payload.token) {
    assistantNode.textContent += payload.token;
    $("chat-thread").scrollTop = $("chat-thread").scrollHeight;
    return;
  }

  if (event.type === "meta") {
    assistantNode.textContent += `[model: ${payload.model ?? "unknown"}]\n`;
    return;
  }

  if (event.type === "info" || event.type === "warning") {
    assistantNode.textContent += `[${event.type}] ${payload.message ?? ""}\n`;
    return;
  }

  if (event.type === "error") {
    assistantNode.textContent += `[error] ${payload.error ?? "stream failed"}`;
  }
}

async function sendChatMessage(message) {
  appendChatMessage("user", message);
  const assistantNode = appendChatMessage("assistant", "");
  setChatBusy(true);

  state.chatAbort = new AbortController();

  try {
    const selectedFiles = selectedChatFiles();
    let response;
    if (selectedFiles.length) {
      const formData = new FormData();
      formData.append("message", message);
      formData.append("mode", $("chat-mode")?.value ?? "auto");
      formData.append("use_web_search", "true");  // ricerca web sempre attiva
      formData.append("project_path", state.selectedProjectPath || "");
      formData.append("chat_id", state.selectedChatId || "");
      selectedFiles.forEach((file) => formData.append("files", file));
      response = await fetch(apiUrl("/api/chat/document"), {
        method: "POST",
        body: formData,
        signal: state.chatAbort.signal,
      });
    } else {
      response = await fetch(apiUrl("/api/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          mode: $("chat-mode")?.value ?? "auto",
          use_web_search: true,  // ricerca web sempre attiva
          project_path: state.selectedProjectPath || null,
          chat_id: state.selectedChatId || null,
        }),
        signal: state.chatAbort.signal,
      });
    }

    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      const payload = await response.json();
      if (payload.error) throw new Error(payload.error);
      if (payload.run_id && ["started", "queued", "running"].includes(payload.status)) {
        const mode = payload.mode === "scaffold" ? "scaffold" : "manutenzione";
        assistantNode.textContent = `Run ${payload.run_id} avviato in modalità ${mode}. Seguo la timeline.`;
        setPipelineStage(0);
        showRunStatus(payload.run_id, "starting");
        await selectRun(payload.run_id);
        if (state.selectedProjectPath) {
          renderActivityRail(state.selectedProjectPath).catch(() => {});
        }
        return;
      }
      throw new Error(payload.message || `chat returned JSON: ${response.status}`);
    }

    if (!response.ok || !response.body) {
      throw new Error(`chat failed: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) applyChatEvent(parseSseBlock(block), assistantNode);
    }

    if (buffer.trim()) applyChatEvent(parseSseBlock(buffer), assistantNode);
  } catch (err) {
    if (err.name !== "AbortError") assistantNode.textContent += `\n[error] ${err.message}`;
  } finally {
    state.chatAbort = null;
    if ($("chat-file")) $("chat-file").value = "";
    setText("chat-file-label", "no files");
    const labelNode = $("chat-file-label");
    if (labelNode) labelNode.title = "No files attached";
    setChatBusy(false);
    $("chat-input")?.focus();
  }
}

async function crawlUrlIntoKnowledge() {
  if (!state.selectedProjectPath) {
    appendChatMessage("assistant", "Seleziona un progetto prima di aggiungere knowledge da URL.");
    return;
  }
  const url = window.prompt("URL da leggere con Crawl4AI/fallback e salvare nella knowledge del progetto?", "https://");
  if (!url || url === "https://") return;
  appendChatMessage("assistant", `[knowledge] Crawl URL in corso: ${url}`);
  try {
    const result = await postJson("/api/project/knowledge/crawl", {
      project_path: state.selectedProjectPath,
      url,
      mode: "auto",
      max_chars: 50000,
    });
    if (result.error) throw new Error(result.error);
    appendChatMessage("assistant", `[knowledge] Aggiunta fonte ${result.filename || url} (${result.chars ?? 0} chars, source: ${result.adapter?.source || "unknown"}).`);
    await loadProjectOverview(state.selectedProjectPath);
  } catch (err) {
    appendChatMessage("assistant", `[knowledge error] ${err.message}`);
  }
}

async function setProjectWorkDir() {
  // Epic "Progetti come Claude": lega la cartella su cui i run lavorano.
  // La cartella deve essere in allowlist (workspace o linkata col picker).
  if (!state.selectedProjectPath) {
    appendChatMessage("assistant", "Seleziona prima un progetto dalla sidebar.");
    return;
  }
  const current = (state.projects || []).find((p) => p.path === state.selectedProjectPath)?.work_dir || "";
  const value = window.prompt(
    "Cartella di lavoro per questo progetto (path assoluto consentito; vuoto = scollega):", current);
  if (value === null) return;
  const result = await postJson("/api/project/workdir", {
    project_path: state.selectedProjectPath,
    work_dir: value.trim(),
  });
  if (result.error) {
    appendChatMessage("assistant", `[workdir] ${result.error}`);
    return;
  }
  appendChatMessage("assistant", result.status === "linked"
    ? `📁 Cartella di lavoro collegata: ${result.work_dir}. I run di questo progetto lavoreranno lì (sempre via sandbox).`
    : "Cartella di lavoro scollegata: i run tornano sulla cartella del progetto.");
  await refresh();
}

function commandActions() {
  return [
    {
      id: "focus-chat",
      title: "Focus composer",
      description: "Scrivi subito a DEVIN nel workspace corrente",
      icon: "⌨",
      group: "Workspace",
      run: () => $("chat-input")?.focus(),
    },
    {
      id: "new-chat",
      title: "Nuova chat progetto",
      description: state.selectedProjectPath ? "Crea una chat nel progetto selezionato" : "Seleziona un progetto per creare chat multiple",
      icon: "+",
      group: "Workspace",
      run: () => createProjectChat().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`)),
    },
    {
      id: "link-folder",
      title: "Collega cartella progetto",
      description: "Autorizza una cartella esterna per chat, crawl e sandbox",
      icon: "↧",
      group: "Workspace",
      run: () => linkWorkspaceFolder().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`)),
    },
    {
      id: "set-workdir",
      title: "Cartella di lavoro del progetto",
      description: state.selectedProjectPath
        ? "I run del progetto lavoreranno su questa cartella (vuoto = scollega)"
        : "Seleziona prima un progetto",
      icon: "📁",
      group: "Workspace",
      run: () => setProjectWorkDir().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`)),
    },
    {
      id: "new-project",
      title: "Nuovo progetto workspace",
      description: "Crea una cartella progetto gestita da DEVIN",
      icon: "□",
      group: "Workspace",
      run: () => createWorkspaceProject().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`)),
    },
    {
      id: "refresh",
      title: "Refresh workspace",
      description: "Ricarica stato, progetti, run e memoria visibile",
      icon: "↻",
      group: "Workspace",
      run: () => refresh(),
    },
    {
      id: "crawl-url-knowledge",
      title: "Crawl URL nella knowledge",
      description: state.selectedProjectPath ? "Usa Crawl4AI/fallback e salva la fonte nel progetto" : "Seleziona prima un progetto",
      icon: "◎",
      group: "Knowledge",
      run: () => crawlUrlIntoKnowledge(),
    },
    {
      id: "diagnostics",
      title: "Apri Diagnostics",
      description: "Run, training, memory audit e settings",
      icon: "⌁",
      group: "Diagnostics",
      run: () => { window.location.href = diagnosticsUrl(); },
    },
    {
      id: "training",
      title: "Apri Training review",
      description: "Seed, attempt, review append-only e Teacher packet",
      icon: "◇",
      group: "Diagnostics",
      run: () => { window.location.href = diagnosticsUrl("training"); },
    },
    {
      id: "memory",
      title: "Apri Memory audit",
      description: "Recall-safe, review-only e policy anti-contaminazione",
      icon: "◌",
      group: "Diagnostics",
      run: () => { window.location.href = diagnosticsUrl("memory"); },
    },
    // Legacy dashboard: route "/" ancora viva (magazzino Monaco/explorer) ma
    // link nascosto dalla UI nuova finche' la Fase 2 non assorbe editor+explorer.
    ...state.projects.slice(0, 12).map((project) => ({
      id: `project:${project.path}`,
      title: `Progetto: ${project.name}`,
      description: `${project.chats ?? 0} chat · ${project.knowledge ?? 0} knowledge`,
      icon: "P",
      group: "Projects",
      run: () => selectProject(project.path),
    })),
    ...state.runs.slice(0, 8).map((run) => ({
      id: `run:${run.run_id}`,
      title: `Run: ${run.run_id}`,
      description: `${run.status || "unknown"} · ${run.mtime ? new Date(run.mtime).toLocaleString() : "no date"}`,
      icon: "R",
      group: "Runs",
      run: () => selectRun(run.run_id),
    })),
  ];
}

function commandMatches(item, query) {
  if (!query) return true;
  const haystack = `${item.title} ${item.description} ${item.group}`.toLowerCase();
  return query.toLowerCase().split(/\s+/).every((part) => haystack.includes(part));
}

function renderCommandPalette() {
  const list = $("command-list");
  const input = $("command-search");
  if (!list) return;
  const query = input?.value.trim() || "";
  const items = commandActions().filter((item) => commandMatches(item, query)).slice(0, 30);
  state.commandItems = items;
  if (!items.length) {
    list.innerHTML = '<div class="command-empty">Nessun comando trovato.</div>';
    return;
  }
  list.innerHTML = items.map((item, index) => `
    <button class="command-item ${index === 0 ? "active" : ""}" type="button" data-command-index="${index}">
      <span class="command-icon">${escapeHtml(item.icon)}</span>
      <span class="command-main"><strong>${escapeHtml(item.title)}</strong><span>${escapeHtml(item.description)}</span></span>
      <span class="command-badge">${escapeHtml(item.group)}</span>
    </button>
  `).join("");
}

function openCommandPalette() {
  const overlay = $("command-overlay");
  const input = $("command-search");
  if (!overlay) return;
  overlay.hidden = false;
  if (input) input.value = "";
  renderCommandPalette();
  setTimeout(() => input?.focus(), 0);
}

function closeCommandPalette() {
  const overlay = $("command-overlay");
  if (overlay) overlay.hidden = true;
}

function runCommand(index = 0) {
  const item = state.commandItems[index];
  if (!item) return;
  closeCommandPalette();
  item.run();
}

function setupCommandPalette() {
  $("open-command-palette")?.addEventListener("click", openCommandPalette);
  $("close-command-palette")?.addEventListener("click", closeCommandPalette);
  $("command-overlay")?.addEventListener("click", (event) => {
    if (event.target === $("command-overlay")) closeCommandPalette();
  });
  $("command-search")?.addEventListener("input", renderCommandPalette);
  $("command-search")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runCommand(0);
    }
    if (event.key === "Escape") {
      event.preventDefault();
      closeCommandPalette();
    }
  });
  $("command-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-command-index]");
    if (button) runCommand(Number(button.dataset.commandIndex || 0));
  });
  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openCommandPalette();
      return;
    }
    if (event.key === "Escape" && !$("command-overlay")?.hidden) {
      event.preventDefault();
      closeCommandPalette();
    }
  });
}

function setupChatComposer() {
  $("chat-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const input = $("chat-input");
    const message = input.value.trim();
    const files = selectedChatFiles();
    if (!message && !files.length) return;
    input.value = "";
    sendChatMessage(message || `Analizza ${files.length} allegat${files.length === 1 ? "o" : "i"}.`);
  });

  $("chat-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      $("chat-form")?.requestSubmit();
    }
  });

  $("chat-file")?.addEventListener("change", () => {
    const files = selectedChatFiles();
    const label = formatFileLabel(files);
    setText("chat-file-label", label);
    const labelNode = $("chat-file-label");
    if (labelNode) labelNode.title = files.map((file) => file.name).join("\n") || "No files attached";
  });

  $("link-folder-button")?.addEventListener("click", () => {
    linkWorkspaceFolder().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[error] ${err.message}`);
    });
  });

  $("new-project-button")?.addEventListener("click", () => {
    createWorkspaceProject().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[error] ${err.message}`);
    });
  });

  $("workdir-set-button")?.addEventListener("click", () => {
    setProjectWorkDir().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[error] ${err.message}`);
    });
  });

  $("new-chat-button")?.addEventListener("click", () => {
    createProjectChat().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[error] ${err.message}`);
    });
  });

  $("continue-chat-button")?.addEventListener("click", () => {
    createProjectChat(true).catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[continuity error] ${err.message}`);
    });
  });


  $("training-seed-button")?.addEventListener("click", () => {
    seedTrainingMiniBench().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[training error] ${err.message}`);
    });
  });

  $("training-run-button")?.addEventListener("click", () => {
    runTrainingMiniBench().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[training error] ${err.message}`);
    });
  });

  $("training-new-case-button")?.addEventListener("click", () => {
    createTrainingCaseFromPrompt().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[training error] ${err.message}`);
    });
  });

  $("training-record-failure-button")?.addEventListener("click", () => {
    recordTrainingFailure().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[training error] ${err.message}`);
    });
  });

  $("training-export-button")?.addEventListener("click", () => {
    exportTrainingDataset().catch((err) => {
      console.error(err);
      appendChatMessage("assistant", `[training error] ${err.message}`);
    });
  });

  // Link Diagnostics/Knowledge: gli <a href="/app/diagnostics"> sono relativi e
  // nell'app nativa (bundle su origin locale) punterebbero dentro il bundle, dove
  // la pagina non esiste. Li instradiamo sull'URL del BACKEND via diagnosticsUrl().
  document.querySelectorAll("[data-diag-link]").forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      window.location.href = diagnosticsUrl(link.dataset.diagLink || "");
    });
  });

  // Strumenti del composer: File / Skill / Goal. Ogni bottone apre il proprio
  // popover verso l'alto-sinistra (niente clip dal pannello destro). Un solo
  // popover aperto per volta; chiusura su click fuori o Esc.
  const toolButtons = Array.from(document.querySelectorAll(".composer-tools .tool-btn"));
  const closeAllTools = () => {
    document.querySelectorAll(".composer-tools .tool-pop").forEach((pop) => { pop.hidden = true; });
    toolButtons.forEach((btn) => btn.setAttribute("aria-expanded", "false"));
  };
  toolButtons.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      const pop = btn.parentElement.querySelector(".tool-pop");
      const wasOpen = pop && !pop.hidden;
      closeAllTools();
      if (pop && !wasOpen) {
        pop.hidden = false;
        btn.setAttribute("aria-expanded", "true");
      }
    });
  });
  document.querySelectorAll(".composer-tools .tool-pop").forEach((pop) => {
    pop.addEventListener("click", (event) => {
      const item = event.target.closest(".pop-item");
      if (!item) return;
      const prompt = item.dataset.plusPrompt;
      const action = item.dataset.plus;
      if (prompt) {
        const input = $("chat-input");
        if (input) {
          input.value = input.value ? `${input.value.replace(/\s+$/, "")} ${prompt}` : prompt;
          input.focus();
        }
      } else if (action === "attach") {
        $("chat-file")?.click();
      } else if (action === "link-folder") {
        linkWorkspaceFolder().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`));
      } else if (action === "new-project") {
        createWorkspaceProject().catch((err) => appendChatMessage("assistant", `[error] ${err.message}`));
      }
      closeAllTools();
    });
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".composer-tools")) closeAllTools();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeAllTools();
  });
}

function diagnosticsUrl(section = "") {
  const params = new URLSearchParams();
  if (state.selectedProjectPath) params.set("project_path", state.selectedProjectPath);
  const query = params.toString();
  // App nativa: la pagina diagnostics e' servita dal BACKEND (non e' nel bundle
  // locale), quindi va aperta sull'URL del backend scoperto (rig o locale).
  return apiUrl(`/app/diagnostics${query ? `?${query}` : ""}${section ? `#${section}` : ""}`);
}

async function renderSteward() {
  const el = $("steward-line");
  if (!el) return;
  try {
    const params = new URLSearchParams();
    if (state.selectedProjectPath) params.set("project_path", state.selectedProjectPath);
    if (state.selectedChatId) params.set("chat_id", state.selectedChatId);
    const q = params.toString();
    const snap = await fetchJson(`/api/steward/status${q ? `?${q}` : ""}`);
    const stateLabels = {
      IDLE: "riposo", WATCHING: "osserva", PREPARING: "prepara checkpoint",
      COMPACTING: "compatta", CHECKPOINT_REQUIRED: "checkpoint richiesto",
      CONTROLLED_CONTINUATION: "nuovo slot",
    };
    const label = stateLabels[snap.state] ?? snap.state;
    const pct = snap.pressure_pct ?? 0;
    el.innerHTML = `<span class="steward-badge steward-${(snap.state || "IDLE").toLowerCase()}">🧭 contesto ${pct}% · ${escapeHtml(label)}</span>`;
    setText("context-meter-label", `${pct}%`);
    setMeter("context-meter-fill", Number(pct) / 100);
  } catch (err) {
    el.innerHTML = "";  // fail-soft: niente Steward, nessun impatto sulla UI
    setText("context-meter-label", "n/d");
    setMeter("context-meter-fill", 0);
  }
}

async function refresh() {
  // Solo al PRIMO caricamento mostra "loading": sui poll periodici (ogni 15s)
  // aggiorna i dati in silenzio, senza il flicker da pagina web (2026-07-22).
  if (!state.selectedRunId && !state.mindLoaded) setText("mind-state", "loading");

  try {
    const projectQuery = state.selectedProjectPath
      ? `?project_path=${encodeURIComponent(state.selectedProjectPath)}` : "";
    const [mind, health, workspace, knowledge, council, routing, goals] = await Promise.all([
      fetchJson("/api/mind/status"),
      fetchJson("/api/health").catch(() => ({})),
      fetchJson("/api/workspace/projects").catch(() => ({ projects: [] })),
      fetchJson(`/api/knowledge-exchange/status${projectQuery}`).catch(() => ({})),
      fetchJson("/api/council/status").catch(() => ({})),
      fetchJson("/api/routing/status").catch(() => ({})),
      fetchJson("/api/goal").catch(() => ({ goal_runs: [] })),
    ]);

    state.mindLoaded = true;
    renderMind(mind, health);
    renderGoalPanel(goals);
    renderGovernanceStatus(knowledge, council, routing);
    renderProjects(workspace);
    renderSteward();  // fail-soft, non blocca il refresh
    if (!state.chatLoaded) {
      state.chatLoaded = true;
      await loadProjectOverview(state.selectedProjectPath);
    }
  } catch (err) {
    console.error(err);
    if (!state.selectedRunId) setText("mind-state", "error");
  }
}

$("refresh-app")?.addEventListener("click", refresh);
$("routing-preview-button")?.addEventListener("click", previewCapabilityRoute);
$("show-chat-view")?.addEventListener("click", () => setCenterView("chat"));
$("show-editor-view")?.addEventListener("click", () => setCenterView("editor"));
$("show-diff-view")?.addEventListener("click", () => setCenterView("diff"));
$("manifest-diff-apply")?.addEventListener("click", () => {
  if (!state.reviewedChangeRunId || !state.reviewedChangeProjectPath) return;
  decideRunChanges(state.reviewedChangeProjectPath, state.reviewedChangeRunId, "apply");
});
$("manifest-diff-reject")?.addEventListener("click", () => {
  if (!state.reviewedChangeRunId || !state.reviewedChangeProjectPath) return;
  decideRunChanges(state.reviewedChangeProjectPath, state.reviewedChangeRunId, "reject");
});
$("refresh-project-tree")?.addEventListener("click", () => {
  loadProjectTree().catch((err) => {
    console.error(err);
    setText("project-tree-status", `Refresh fallito: ${err.message || err}`);
  });
});

function setupGoalControls() {
  const type = $("goal-criterion-type");
  const value = $("goal-criterion-value");
  const syncCriterionInput = () => {
    if (!type || !value) return;
    value.placeholder = goalCriterionPlaceholder(type.value);
    value.disabled = type.value === "tests_pass";
    if (value.disabled) value.value = "";
  };
  type?.addEventListener("change", syncCriterionInput);
  syncCriterionInput();

  $("goal-add-criterion")?.addEventListener("click", () => {
    try {
      addGoalCriterion(type?.value || "tests_pass", value?.value || "");
      if (value) value.value = "";
    } catch (err) {
      setGoalFeedback(err.message || String(err), "error");
    }
  });
  value?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    $("goal-add-criterion")?.click();
  });

  document.querySelectorAll("[data-goal-preset]").forEach((button) => {
    button.addEventListener("click", () => {
      try {
        if (button.dataset.goalPreset === "tests") addGoalCriterion("tests_pass");
        if (button.dataset.goalPreset === "no-todos") addGoalCriterion("absence_of_pattern", "TODO|FIXME");
      } catch (err) {
        setGoalFeedback(err.message || String(err), "error");
      }
    });
  });

  $("goal-criteria-draft")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-goal-criterion]");
    if (!button) return;
    const index = Number.parseInt(button.dataset.removeGoalCriterion, 10);
    if (Number.isInteger(index)) state.goalCriteriaDraft.splice(index, 1);
    renderGoalCriteriaDraft();
    setGoalFeedback("Criterio rimosso.");
  });

  const mode = $("goal-mode-select");
  const approval = $("goal-approval-select");
  const syncApproval = () => {
    if (!approval) return;
    const scaffold = mode?.value === "scaffold";
    if (scaffold) approval.value = "auto";
    approval.disabled = scaffold;
    approval.title = scaffold ? "Lo scaffold applica automaticamente per contratto" : "Policy di approvazione";
  };
  mode?.addEventListener("change", syncApproval);
  syncApproval();

  $("goal-start-button")?.addEventListener("click", () => {
    startGoal().catch((err) => {
      setGoalFeedback(err.message || String(err), "error");
      syncGoalLaunchState();
    });
  });
  $("goal-stop-button")?.addEventListener("click", (event) => {
    stopGoal(event.currentTarget.dataset.goalRunId).catch((err) => {
      setGoalFeedback(err.message || String(err), "error");
      event.currentTarget.disabled = false;
    });
  });

  renderGoalCriteriaDraft();
  syncGoalLaunchState();
}

// 2026-07-18 (PWA slice): toggle dei pannelli laterali come overlay ai
// breakpoint mobile. Nessuna logica di toggle preesistente da riusare
// (verificato): minimo indispensabile qui; i bottoni sono nascosti su
// desktop via CSS (.panel-toggle), quindi il comportamento desktop non
// cambia. Le classi body pwa-show-* sono lette solo dentro media query.
function setupPanelToggles() {
  const bind = (buttonId, bodyClass) => {
    const button = $(buttonId);
    if (!button) return;
    button.addEventListener("click", () => {
      document.body.classList.toggle(bodyClass);
    });
  };
  bind("toggle-workspace-panel", "pwa-show-workspace");
  bind("toggle-mind-panel", "pwa-show-mind");

  // Collasso sidebar su desktop: i bottoni "comprimi" nei pannelli e i due
  // pulsanti-rail per riaprirli condividono data-collapse=left|right.
  const applyCollapseState = () => {
    const leftCollapsed = document.body.classList.contains("left-collapsed");
    const rightCollapsed = document.body.classList.contains("right-collapsed");
    const railLeft = document.querySelector(".rail-reopen-left");
    const railRight = document.querySelector(".rail-reopen-right");
    if (railLeft) railLeft.hidden = !leftCollapsed;
    if (railRight) railRight.hidden = !rightCollapsed;
  };
  document.querySelectorAll("[data-collapse]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const side = btn.dataset.collapse === "right" ? "right" : "left";
      document.body.classList.toggle(`${side}-collapsed`);
      applyCollapseState();
    });
  });
  applyCollapseState();

  // Chiudi l'overlay workspace dopo una selezione (progetto/chat/azione).
  document.querySelector(".workspace-panel")?.addEventListener("click", (event) => {
    if (event.target.closest("button")) {
      document.body.classList.remove("pwa-show-workspace");
    }
  });

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      document.body.classList.remove("pwa-show-workspace", "pwa-show-mind");
    }
  });
}

setupPanelToggles();
resetProjectEditor();
resetManifestReview();
setCenterView("chat");

document.querySelectorAll("[data-workspace-target]").forEach((button) => {
  button.addEventListener("click", () => {
    const target = document.getElementById(button.dataset.workspaceTarget || "");
    if (!target) return;
    if (button.dataset.workspaceTarget === "governance-section") {
      document.body.classList.remove("right-collapsed");
      target.open = true;
    }
    target.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

setupChatComposer();
setupCommandPalette();
setupGoalControls();
refresh();
setInterval(refresh, 15000);
