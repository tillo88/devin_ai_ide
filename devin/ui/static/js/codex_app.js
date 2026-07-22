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
  diffPreviewOk: false,
  reviewedChangeRunId: null,
  trainingCases: [],
  trainingJobPoll: null,
  projects: [],
  runs: [],
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
    const err = new Error(`${url}: ${res.status}`);
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

function renderMind(status) {
  if (!state.selectedRunId) setText("mind-state", "ready");
  const launcherSource = status.models?.launcher_source ?? "unavailable";
  const sourceLabels = {
    rig: `rig attivo${status.models?.rig_host ? ` · ${status.models.rig_host}` : ""}`,
    local: "locale attivo",
    unavailable: "offline · nessun modello",
  };
  setText("model-source", sourceLabels[launcherSource] ?? launcherSource);

  const localMemory = status.memory?.local ?? {};
  setText("memory-count", `memory: ${localMemory.records ?? 0}`);

  const vram = status.models?.vram;
  setText(
    "vram-pill",
    vram ? `vram: ${vram.used_mb}/${vram.total_mb} MB` : "vram: n/a",
  );

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
    return;
  }
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
        <button class="project-card ${project.path === state.selectedProjectPath ? "active" : ""}" data-project-path="${escapeHtml(project.path)}">
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
        <button class="chat-card" data-chat-id="${escapeHtml(chat.chat_id)}">
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

  const full = await fetchJson(`/api/project/overview?${new URLSearchParams({ project_path: projectPath }).toString()}`, {});

  // Cartella di lavoro
  const wd = full.work_dir || "";
  if (wd) {
    workBox.innerHTML = `<i class="folder-ico">📁</i> <span title="${escapeHtml(wd)}">${escapeHtml(wd.split(/[\\/]/).pop())}</span>`;
    workBox.classList.add("linked");
  } else {
    workBox.textContent = "Nessuna cartella collegata: i run girano nel progetto.";
    workBox.classList.remove("linked");
  }
  const files = full.files || [];
  if (filesEl) {
    filesEl.innerHTML = files.length
      ? files.slice(0, 12).map((f) => `<span class="file-row"><i class="ti">·</i>${escapeHtml(String(f).split(/[\\/]/).pop())}</span>`).join("")
      : "";
  }

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
      } else {
        runEl.textContent = "Nessun run recente in questo progetto.";
        if (!state.selectedRunId) setText("mind-state", "ready");
      }
    } catch (_) {
      runEl.textContent = "Nessun run recente in questo progetto.";
    }
  }
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
  refreshActiveScope();
  state.selectedChatId = null;
  state.chatLoaded = true;
  document.querySelectorAll(".project-card").forEach((card) => {
    card.classList.toggle("active", (card.dataset.projectPath || "") === state.selectedProjectPath);
  });

  try {
    await loadProjectOverview(state.selectedProjectPath);
    await loadTrainingOverview();
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
  appendChatMessage("assistant", "Apro il picker cartelle Windows: scegli la cartella progetto da collegare.");
  const result = await postJson("/api/workspace/pick_folder", {});
  if (result.error) throw new Error(result.error);
  if (!result.path) {
    appendChatMessage("assistant", "Nessuna cartella collegata.");
    return;
  }
  await refresh();
  await selectProject(result.path);
  appendChatMessage("assistant", `Cartella collegata e autorizzata: ${result.path}. Ora crawl/sandbox possono usarla in sicurezza.`);
}

async function createWorkspaceProject() {
  const name = window.prompt("Nome del nuovo progetto DEVIN:");
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
  applyRunEventToActivity(events[events.length - 1]);
}

function appendTimelineEvent(event) {
  if (!event || event.run_id !== state.selectedRunId) return;
  state.lastEventSeq = Math.max(state.lastEventSeq, Number(event.seq ?? state.lastEventSeq));
  applyRunEventToActivity(event);

  const timeline = $("timeline");
  if (!timeline) return;
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
  if (action === "apply" && state.reviewedChangeRunId !== runId) {
    appendChatMessage("assistant", "Apri prima Diff e controlla le modifiche verificate.");
    return;
  }
  const labels = { apply: "applicare", reject: "rifiutare", rollback: "ripristinare" };
  if (!window.confirm(`Confermi di ${labels[action] || action} le modifiche verificate del run ${runId}?`)) return;
  try {
    const result = await postJson(`/api/run/changes/${action}`, {
      path: projectPath,
      run_id: runId,
      commit: action === "apply",
    });
    if (result?.error) {
      appendChatMessage("assistant", `Decisione non applicata: ${result.error}`);
      return;
    }
    appendChatMessage("assistant", `Run ${runId}: ${result.status}.`);
    await renderActivityRail(projectPath);
    await loadRunLog(runId);
  } catch (err) {
    console.error(err);
    appendChatMessage("assistant", `Decisione non applicata: ${err.message || err}`);
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
    const input = $("diff-input");
    if (input) input.value = payload.unified_diff || "(nessuna differenza testuale)";
    const panel = document.querySelector(".diff-preview-panel");
    if (panel) panel.open = true;
    const result = $("diff-result");
    if (result) {
      result.innerHTML = `<div class="diff-summary">Manifest verificato · ${escapeHtml(payload.entries?.length || 0)} file · digest ${escapeHtml((payload.entry_digest || "").slice(0, 12))}${payload.truncated ? " · preview troncata" : ""}</div>`;
    }
    setText("diff-preview-status", "verified manifest");
    state.diffPreviewOk = false;
    state.reviewedChangeRunId = runId;
    panel?.scrollIntoView({ behavior: "smooth", block: "nearest" });
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
      formData.append("use_web_search", Boolean($("chat-web")?.checked) ? "true" : "false");
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
          use_web_search: Boolean($("chat-web")?.checked),
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

function renderDiffPreview(payload) {
  const result = $("diff-result");
  if (!result) return;

  if (payload.error || payload.message) {
    result.innerHTML = `<div class="empty-card">${escapeHtml(payload.error || payload.message)}</div>`;
    return;
  }

  const files = Object.entries(payload.files_affected ?? {});
  if (!files.length) {
    result.innerHTML = '<div class="empty-card">Nessun file rilevato nella diff.</div>';
    return;
  }

  result.innerHTML = `
    <div class="diff-summary">
      ${escapeHtml(payload.total_files)} file - ${escapeHtml(payload.total_additions)} additions - ${escapeHtml(payload.total_deletions)} deletions - ${escapeHtml(payload.patch_lines)} lines
    </div>
    <div class="diff-file-list">
      ${files.map(([path, info]) => `
        <article class="diff-file-card">
          <strong>${escapeHtml(path)}</strong>
          <span>${info.is_new ? "new" : "existing"} - +${escapeHtml(info.additions)} / -${escapeHtml(info.deletions)}</span>
        </article>
      `).join("")}
    </div>
  `;
}

async function previewDiff() {
  const patchText = $("diff-input")?.value.trim() ?? "";
  if (!patchText) {
    setText("diff-preview-status", "empty");
    state.diffPreviewOk = false;
    renderDiffPreview({ error: "Incolla una unified diff prima di fare preview." });
    return;
  }

  if (!state.selectedProjectPath) {
    setText("diff-preview-status", "no project");
    state.diffPreviewOk = false;
    renderDiffPreview({ error: "Seleziona un progetto nella sidebar prima della preview." });
    return;
  }

  setText("diff-preview-status", "checking");
  try {
    const payload = await postJson("/api/diff/preview", {
      project_path: state.selectedProjectPath,
      patch_text: patchText,
    });
    state.diffPreviewOk = Boolean(payload.success && !payload.error);
    renderDiffPreview(payload);
    setText("diff-preview-status", payload.error ? "error" : "ready");
  } catch (err) {
    state.diffPreviewOk = false;
    renderDiffPreview({ error: err.message });
    setText("diff-preview-status", "error");
  }
}


async function applyDiffWithConfirmation() {
  const patchText = $("diff-input")?.value.trim() ?? "";
  if (!patchText) {
    renderDiffPreview({ error: "Incolla una unified diff prima di applicarla." });
    return;
  }
  if (!state.selectedProjectPath) {
    renderDiffPreview({ error: "Seleziona un progetto nella sidebar prima di applicare la diff." });
    return;
  }
  if (!state.diffPreviewOk) {
    renderDiffPreview({ error: "Esegui prima Preview diff e verifica il risultato." });
    return;
  }

  const projectName = activeProjectLabel();
  const ok = window.confirm(`Applicare questa diff al progetto ${projectName}? Operazione reale su file.`);
  if (!ok) return;

  setText("diff-preview-status", "applying");
  try {
    const payload = await postJson("/api/diff/apply", {
      project_path: state.selectedProjectPath,
      patch_text: patchText,
    });

    if (payload.error || payload.success === false) {
      renderDiffPreview({ error: payload.error || "apply failed" });
      setText("diff-preview-status", "apply error");
      return;
    }

    renderDiffPreview({ message: `Diff applicata: ${payload.message || payload.method || "ok"}` });
    setText("diff-preview-status", "applied");
    state.diffPreviewOk = false;
  } catch (err) {
    renderDiffPreview({ error: err.message });
    setText("diff-preview-status", "apply error");
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
    {
      id: "dashboard",
      title: "Legacy dashboard",
      description: "Fallback tecnico della vecchia dashboard",
      icon: "↗",
      group: "Navigation",
      run: () => { window.location.href = "/"; },
    },
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


  $("diff-preview-button")?.addEventListener("click", () => {
    previewDiff().catch((err) => {
      console.error(err);
      renderDiffPreview({ error: err.message });
    });
  });


  $("diff-apply-button")?.addEventListener("click", () => {
    applyDiffWithConfirmation().catch((err) => {
      console.error(err);
      renderDiffPreview({ error: err.message });
    });
  });
}

function diagnosticsUrl(section = "") {
  const params = new URLSearchParams();
  if (state.selectedProjectPath) params.set("project_path", state.selectedProjectPath);
  const query = params.toString();
  return `/app/diagnostics${query ? `?${query}` : ""}${section ? `#${section}` : ""}`;
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
  } catch (err) {
    el.innerHTML = "";  // fail-soft: niente Steward, nessun impatto sulla UI
  }
}

async function refresh() {
  if (!state.selectedRunId) setText("mind-state", "loading");

  try {
    const [mind, workspace] = await Promise.all([
      fetchJson("/api/mind/status"),
      fetchJson("/api/workspace/projects").catch(() => ({ projects: [] })),
    ]);

    renderMind(mind);
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
setupChatComposer();
setupCommandPalette();
refresh();
setInterval(refresh, 15000);
