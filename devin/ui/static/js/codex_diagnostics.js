const $ = (id) => document.getElementById(id);


const diagnosticsState = {
  activeTab: (window.location.hash || "#runs").slice(1) || "runs",
  projectPath: new URLSearchParams(window.location.search).get("project_path") || "",
};

function apiErrorMessage(error) {
  const message = error?.message || String(error || "errore sconosciuto");
  if (message.includes("project_path non consentito")) {
    return "Project path non consentito: scegli un progetto dalla Workspace oppure collega la cartella dal picker prima di usare questa azione.";
  }
  return message;
}

function currentProjectPath(inputId) {
  return inputValue(inputId) || diagnosticsState.projectPath || "";
}

function setActiveTab(tab) {
  diagnosticsState.activeTab = tab || "runs";
  document.querySelectorAll(".diagnostics-card").forEach((card) => {
    card.hidden = card.id !== diagnosticsState.activeTab;
  });
  document.querySelectorAll("[data-diagnostics-tab]").forEach((link) => {
    link.classList.toggle("active", link.dataset.diagnosticsTab === diagnosticsState.activeTab);
  });
  if (diagnosticsState.projectPath) {
    const crawl = $("crawl-project-path");
    const sandbox = $("sandbox-project-path");
    if (crawl && !crawl.value) crawl.value = diagnosticsState.projectPath;
    if (sandbox && !sandbox.value) sandbox.value = diagnosticsState.projectPath;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function fetchJson(url, fallback) {
  try {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return await res.json();
  } catch (error) {
    console.warn(`[diagnostics] ${url}`, error);
    return fallback;
  }
}

async function postJson(url, payload = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.error) {
    const detail = data.detail;
    const detailMessage = typeof detail === "string" ? detail : (detail?.error || detail?.hint || "");
    throw new Error(data.error || detailMessage || `${res.status} ${res.statusText}`);
  }
  return data;
}

async function fetchText(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return await res.text();
}

function setStatus(id, value) {
  const node = $(id);
  if (node) node.textContent = value;
}

function setActionState(message, kind = "idle") {
  const node = $("diagnostics-action-state");
  if (!node) return;
  node.textContent = message;
  node.dataset.kind = kind;
}

function renderMetrics(id, entries) {
  const node = $(id);
  if (!node) return;
  node.innerHTML = entries.map(([label, value]) => `
    <div class="metric-card"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>
  `).join("");
}

function compactDate(value) {
  if (!value) return "unknown";
  try {
    return new Date(value).toLocaleString(undefined, { dateStyle: "short", timeStyle: "short" });
  } catch {
    return value;
  }
}

function currentBenchmarkId() {
  return $("benchmark-select")?.value || "devin-mini";
}

function populateBenchmarks(benchmarks = []) {
  const select = $("benchmark-select");
  if (!select || !benchmarks.length) return;
  const previous = select.value || "devin-mini";
  select.innerHTML = benchmarks.map((item) => {
    const id = item.id || item.benchmark_id || "devin-mini";
    const label = item.name || item.title || id;
    return `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`;
  }).join("");
  select.value = benchmarks.some((item) => (item.id || item.benchmark_id) === previous) ? previous : (benchmarks[0].id || benchmarks[0].benchmark_id || "devin-mini");
}

function renderLogRetention(payload) {
  const target = $("log-retention-summary");
  if (!target) return;
  const policy = payload?.policy || {};
  const summary = payload?.summary || {};
  const enabled = policy.enabled === false ? "off" : "on";
  const wouldDelete = summary.would_delete ?? summary.deleted ?? 0;
  const scanned = summary.scanned ?? 0;
  const days = policy.retention_days ?? summary.retention_days ?? "?";
  const keep = policy.keep_recent_runs ?? summary.keep_recent_runs ?? "?";
  target.textContent = "Autoclean " + enabled + ": elimina log non aperti/usati da oltre " + days + " giorni, conservando gli ultimi " + keep + " run. Preview: " + wouldDelete + "/" + scanned + " file candidati.";
}

async function refreshLogRetention() {
  const payload = await fetchJson("/api/logs/retention", { policy: {}, summary: {} });
  renderLogRetention(payload);
  return payload;
}

async function runLogCleanup(dryRun = true) {
  if (!dryRun && !window.confirm("Pulire davvero i vecchi log fuori retention? Gli ultimi run e i run attivi restano protetti.")) {
    return;
  }
  setActionState(dryRun ? "Preview cleanup log…" : "Pulizia vecchi log…", "busy");
  try {
    const result = await postJson("/api/logs/cleanup", { dry_run: dryRun });
    renderLogRetention({ policy: result.policy || {}, summary: result.summary || {} });
    const summary = result.summary || {};
    const count = dryRun ? (summary.would_delete ?? 0) : (summary.deleted ?? 0);
    setActionState(dryRun ? "Preview cleanup: " + count + " file candidati." : "Cleanup completato: " + count + " file rimossi.", "ok");
    if (!dryRun) await loadDiagnostics();
  } catch (error) {
    setActionState("Cleanup log fallito: " + error.message, "error");
  }
}

function renderRuns(runs) {
  const list = Array.isArray(runs) ? runs : [];
  const counts = list.reduce((acc, run) => {
    const status = run.status || "unknown";
    acc[status] = (acc[status] || 0) + 1;
    return acc;
  }, {});
  renderMetrics("runs-metrics", [
    ["total", list.length],
    ["success", counts.success || 0],
    ["failed", counts.failed || 0],
    ["unknown", counts.unknown || 0],
  ]);
  setStatus("runs-status", list.length ? `${list.length} recent` : "empty");
  const target = $("runs-list");
  if (!target) return;
  if (!list.length) {
    target.innerHTML = '<div class="empty-state">Nessun run log trovato.</div>';
    return;
  }
  target.innerHTML = list.slice(0, 8).map((run) => `
    <article class="diagnostics-row">
      <div><strong>${escapeHtml(run.run_id)}</strong><span>${escapeHtml(compactDate(run.mtime))}</span></div>
      <span class="status-pill status-${escapeHtml(run.status || "unknown")}">${escapeHtml(run.status || "unknown")}</span>
      <button class="tiny-button log-button" type="button" data-run-log="${escapeHtml(run.run_id)}">Log</button>
    </article>
  `).join("");
}

function renderTraining(payload) {
  const summary = payload?.summary || {};
  const attempts = payload?.attempts || [];
  const jobs = payload?.jobs || [];
  const latestReviews = payload?.latest_reviews || {};
  populateBenchmarks(payload?.benchmarks || []);
  renderMetrics("training-metrics", [
    ["cases", summary.cases ?? payload?.cases?.length ?? 0],
    ["attempts", summary.attempts ?? attempts.length],
    ["verified", (summary.review_verified_success ?? 0) + (summary.verified_success ?? 0) + (summary.human_confirmed ?? 0)],
    ["reviewed fail", summary.review_verified_failure ?? 0],
    ["in coda", (payload?.review_queue || []).length],
  ]);
  renderReviewQueue(payload);
  // Scope ESPLICITO: con ?project_path= la pagina guarda lo store del
  // PROGETTO (.devin/training), non quello globale — senza etichetta sembrava
  // che i dati fossero spariti (visto sul campo: "0 su tutto").
  const scopeLabel = diagnosticsState.projectPath
    ? `store: progetto ${diagnosticsState.projectPath.split(/[\\/]/).pop()}`
    : "store: globale";
  const runningJob = jobs.find((job) => job.status === "running");
  setStatus("training-status", runningJob
    ? `${scopeLabel} · running ${runningJob.completed ?? 0}/${runningJob.total ?? "?"}${runningJob.current_title ? " — " + runningJob.current_title : ""}`
    : `${scopeLabel} · ready`);
  const target = $("training-list");
  if (!target) return;
  const recent = attempts.slice(0, 8);
  if (!recent.length) {
    target.innerHTML = diagnosticsState.projectPath
      ? '<div class="empty-state">Nessun attempt nello store di QUESTO progetto. Lo storico del bench sta nello store globale: apri Diagnostics senza progetto selezionato (o togli ?project_path= dall\'URL).</div>'
      : '<div class="empty-state">Nessun attempt ancora registrato. Usa Seed e poi Run mini bench quando vuoi generare un pool valutabile.</div>';
    return;
  }
  target.innerHTML = recent.map((attempt) => {
    const review = latestReviews[attempt.attempt_id] || null;
    const shownStatus = review?.status || attempt.status || "unknown";
    const subtitle = review
      ? `review: ${review.reviewer || "human"} · ${review.rationale || review.method_trace || "no rationale"}`
      : (attempt.error_reason || attempt.run_id || "needs validation if auto_*");
    return `
      <article class="diagnostics-row attempt-review-row">
        <div><strong>${escapeHtml(attempt.case_id || attempt.attempt_id)}</strong><span>${escapeHtml(subtitle)}</span></div>
        <span class="status-pill status-${escapeHtml(shownStatus)}">${escapeHtml(shownStatus)}</span>
        <div class="review-actions" data-attempt-id="${escapeHtml(attempt.attempt_id)}">
          <button class="tiny-button" type="button" data-review-status="verified_success">✓</button>
          <button class="tiny-button" type="button" data-review-status="verified_failure">✕</button>
          <button class="tiny-button" type="button" data-review-status="needs_correction">Fix</button>
        </div>
      </article>
    `;
  }).join("");
}


function renderReviewQueue(payload) {
  const target = $("review-queue-list");
  if (!target) return;
  const queue = payload?.review_queue || [];
  setStatus("review-queue-status", queue.length ? `${queue.length} da validare` : "vuota");
  if (!queue.length) {
    target.innerHTML = '<div class="empty-state">Nessun attempt in attesa di validazione: la coda Teacher è vuota.</div>';
    return;
  }
  target.innerHTML = queue.slice(0, 12).map((item) => {
    const gate = item.gate || {};
    const validators = item.validators || {};
    const signalPairs = Object.entries(validators.signals || {}).map(([sig, verdict]) => `${sig}:${verdict}`);
    const gateLine = gate.status
      ? `gate ${gate.status}${gate.test_command ? ` (${gate.test_command})` : ""}`
      : "gate n/d";
    const evidence = [gateLine, validators.overall ? `validatori ${validators.overall}` : "", signalPairs.join(" · ")]
      .filter(Boolean).join(" — ");
    const reason = item.error_reason ? `<span>${escapeHtml(item.error_reason)}</span>` : "";
    return `
      <article class="diagnostics-row attempt-review-row">
        <div>
          <strong>${escapeHtml(item.title || item.attempt_id)}</strong>
          <span>${escapeHtml(evidence)}</span>
          ${reason}
        </div>
        <span class="status-pill status-${escapeHtml(item.status || "unknown")}">${escapeHtml(item.status || "unknown")}</span>
        <div class="review-actions" data-attempt-id="${escapeHtml(item.attempt_id)}">
          <button class="tiny-button" type="button" data-review-status="verified_success" title="Conferma successo">✓</button>
          <button class="tiny-button" type="button" data-review-status="verified_failure" title="Conferma fallimento">✕</button>
          <button class="tiny-button" type="button" data-review-status="needs_correction" title="Serve correzione">Fix</button>
          <button class="tiny-button" type="button" data-review-status="runner_error" title="Errore infrastruttura, non del modello">Infra</button>
          ${item.run_id ? `<button class="tiny-button log-button" type="button" data-run-log="${escapeHtml(item.run_id)}">Log</button>` : ""}
        </div>
      </article>
    `;
  }).join("");
}


function renderExports(payload) {
  const target = $("exports-list");
  if (!target) return;
  const exports = payload?.exports || [];
  if (!exports.length) {
    target.innerHTML = '<div class="empty-state">Nessun export ancora prodotto.</div>';
    return;
  }
  target.innerHTML = exports.slice(0, 6).map((item) => `
    <article class="diagnostics-row export-row" title="${escapeHtml(item.path || "")}">
      <div><strong>${escapeHtml(item.filename)}</strong><span>${escapeHtml(item.format)} · ${escapeHtml(item.rows ?? 0)} righe · ${escapeHtml(compactDate(item.mtime))}</span></div>
      <span class="status-pill">${escapeHtml(Math.ceil((item.size || 0) / 1024))} KB</span>
    </article>
  `).join("");
}

function renderMemory(mind, training) {
  const memory = mind?.memory || {};
  const local = memory.local || {};
  const policy = training?.memory_policy || {};
  const safe = memory.recall_safe_statuses || policy.success_statuses || [];
  const reviewOnly = memory.review_only_statuses || [...(policy.auto_statuses || []), ...(policy.failure_statuses || [])];
  renderMetrics("memory-metrics", [
    ["schema", memory.schema_version || "unknown"],
    ["records", local.records ?? 0],
    ["safe statuses", safe.length],
    ["review-only", reviewOnly.length],
  ]);
  setStatus("memory-status", memory.backend || "local");
  const target = $("memory-list");
  if (!target) return;
  target.innerHTML = `
    <article class="diagnostics-row"><div><strong>Recall safe</strong><span>${escapeHtml(safe.join(", ") || "none")}</span></div></article>
    <article class="diagnostics-row"><div><strong>Review only</strong><span>${escapeHtml(reviewOnly.join(", ") || "none")}</span></div></article>
    <article class="diagnostics-row"><div><strong>Auto promote</strong><span>${escapeHtml(policy.auto_promote === false ? "disabled" : "unknown")}</span></div></article>
  `;
}

function renderDesktopReadiness(payload) {
  const host = payload?.desktop_host || {};
  const cleanup = payload?.close_cleanup || {};
  const localServers = payload?.local_model_servers || {};
  const serverNames = Object.keys(localServers);
  renderMetrics("desktop-readiness-metrics", [
    ["desktop", host.launcher ? "ready" : "unknown"],
    ["cleanup", cleanup.enabled === false ? "off" : "on"],
    ["local servers", serverNames.length],
    ["logs", host.logs ? "ready" : "unknown"],
  ]);
  const target = $("desktop-readiness-list");
  if (!target) return;
  target.innerHTML = `
    <article class="diagnostics-row"><div><strong>Launcher</strong><span>${escapeHtml(host.launcher || "unknown")}</span></div></article>
    <article class="diagnostics-row"><div><strong>Desktop host</strong><span>${escapeHtml(host.host || "unknown")}</span></div></article>
    <article class="diagnostics-row"><div><strong>Tauri log</strong><span>${escapeHtml(host.tauri_log || "unknown")}</span></div></article>
    <article class="diagnostics-row"><div><strong>Backend log</strong><span>${escapeHtml(payload?.backend?.headless_log || "unknown")}</span></div></article>
    <article class="diagnostics-row"><div><strong>Local model servers</strong><span>${escapeHtml(serverNames.length ? serverNames.join(", ") : "none")}</span></div></article>
    <article class="diagnostics-row"><div><strong>Close cleanup</strong><span>${escapeHtml(cleanup.policy || "unknown")} · rig safe: ${escapeHtml(cleanup.remote_rig_safe)}</span></div></article>
  `;
}

async function refreshDesktopReadiness() {
  const payload = await fetchJson("/api/desktop/readiness", {});
  renderDesktopReadiness(payload);
  return payload;
}

async function cleanupLocalModelsNow() {
  if (!window.confirm("Spegnere ora i model server locali DEVIN? Il rig remoto non viene toccato.")) return;
  setActionState("Cleanup modelli locali in corso…", "busy");
  try {
    const result = await postJson("/api/desktop/close_cleanup", {});
    setActionState("Cleanup locale: " + result.status + " (" + (result.count || 0) + " server).", result.status === "error" ? "error" : "ok");
    await refreshDesktopReadiness();
  } catch (error) {
    setActionState("Cleanup locale fallito: " + error.message, "error");
  }
}

function renderSettings(models, mind) {
  const target = $("settings-list");
  const modelSource = models?.source || mind?.models?.source || "unknown";
  const vram = mind?.hardware?.vram || models?.vram || null;
  setStatus("settings-status", modelSource);
  if (!target) return;
  target.innerHTML = `
    <article class="diagnostics-row"><div><strong>Model source</strong><span>${escapeHtml(modelSource)}</span></div></article>
    <article class="diagnostics-row"><div><strong>Desktop target</strong><span>${escapeHtml(mind?.agent?.desktop_shell_target || "Tauri + local FastAPI")}</span></div></article>
    <article class="diagnostics-row"><div><strong>VRAM</strong><span>${escapeHtml(vram ? `${vram.used_mb ?? "?"}/${vram.total_mb ?? "?"} MB` : "n/a")}</span></div></article>
  `;
}



function setResult(id, payload) {
  const node = $(id);
  if (!node) return;
  node.textContent = typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
}

function inputValue(id) {
  return ($(id)?.value || "").trim();
}

function checked(id) {
  return Boolean($(id)?.checked);
}

async function checkCrawlAdapter() {
  setStatus("crawl-status", "checking");
  setResult("crawl-result", "Controllo adapter…");
  const status = await fetchJson("/api/project/knowledge/crawl/status", { error: "status unavailable" });
  setStatus("crawl-status", status.available ? "crawl4ai" : "fallback");
  setResult("crawl-result", status);
}

async function crawlIntoKnowledge() {
  const projectPath = currentProjectPath("crawl-project-path");
  const url = inputValue("crawl-url");
  const mode = inputValue("crawl-mode") || "auto";
  const maxChars = Number(inputValue("crawl-max-chars") || 50000);
  if (!projectPath || !url) {
    setResult("crawl-result", "Project path e URL sono obbligatori.");
    return;
  }
  setStatus("crawl-status", "running");
  setResult("crawl-result", `Crawl in corso: ${url}`);
  try {
    const result = await postJson("/api/project/knowledge/crawl", { project_path: projectPath, url, mode, max_chars: maxChars });
    setStatus("crawl-status", result.ok ? "added" : "done");
    setResult("crawl-result", result);
  } catch (error) {
    setStatus("crawl-status", "error");
    setResult("crawl-result", `Errore crawl: ${apiErrorMessage(error)}`);
  }
}

async function prepareSandbox() {
  const projectPath = currentProjectPath("sandbox-project-path");
  if (!projectPath) {
    setResult("sandbox-result", "Project path obbligatorio.");
    return;
  }
  if (checked("sandbox-link-venv") && checked("sandbox-include-venv")) {
    setResult("sandbox-result", "Scegli link venv O copia venv, non entrambi.");
    return;
  }
  setStatus("sandbox-status", "running");
  setResult("sandbox-result", "Preparo sandbox…");
  try {
    const result = await postJson("/api/sandbox/prepare", {
      project_path: projectPath,
      link_venv: checked("sandbox-link-venv"),
      include_venv: checked("sandbox-include-venv"),
      include_secrets: false,
      include_large_binaries: false,
      max_file_size_mb: Number(inputValue("sandbox-max-file-size") || 50),
    });
    setStatus("sandbox-status", result.sandbox ? "ready" : "done");
    setResult("sandbox-result", result);
  } catch (error) {
    setStatus("sandbox-status", "error");
    setResult("sandbox-result", `Errore sandbox: ${apiErrorMessage(error)}`);
  }
}

async function recordAttemptReview(attemptId, status) {
  const rationale = window.prompt(`Esito sintetico per ${status} (${attemptId})?`, "");
  if (rationale === null) return;
  const methodTrace = window.prompt("Metodo/evidenza operativa? Esempio: ipotesi -> test eseguito -> evidenza -> correzione", "");
  if (methodTrace === null) return;
  const nextAction = window.prompt("Prossima azione o lezione candidata?", "");
  if (nextAction === null) return;
  setActionState(`Salvo review ${status}…`, "busy");
  try {
    await postJson("/api/training/reviews", {
      attempt_id: attemptId,
      status,
      rationale,
      method_trace: methodTrace,
      next_action: nextAction,
      lesson_candidate: nextAction,
      failure_mode: status === "verified_success" ? "" : rationale,
      reviewer: "human",
      confidence: 1.0,
      tags: ["diagnostics_ui", "method_trace"],
    });
    setActionState(`Review salvata: ${status}. Attempt originale non modificato.`, "ok");
    // Flywheel correzioni -> SFT: l'export SFT legge SOLO corrections.jsonl.
    // Dopo un esito negativo precompiliamo l'editor: senza correzione il
    // dataset resta vuoto anche con cento review.
    if (status === "verified_failure" || status === "needs_correction") {
      const attemptField = $("correction-attempt-id");
      if (attemptField) {
        attemptField.value = attemptId;
        setStatus("correction-status", "attempt precompilato");
        $("correction-text")?.focus();
        attemptField.closest("section")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    }
    await loadDiagnostics();
  } catch (error) {
    setActionState(`Review fallita: ${error.message}`, "error");
  }
}

async function saveCorrection() {
  const attemptId = (inputValue("correction-attempt-id") || "").trim();
  const correction = (inputValue("correction-text") || "").trim();
  const solution = inputValue("correction-solution") || "";
  if (!attemptId || !correction) {
    setActionState("Correzione: servono attempt ID e testo della correzione.", "error");
    return;
  }
  setActionState("Salvo correzione…", "busy");
  try {
    const result = await postJson("/api/training/corrections", {
      attempt_id: attemptId,
      correction,
      corrected_solution: solution,
      reviewer: "human",
      tags: ["diagnostics_ui", "sft_flywheel"],
    });
    if (result.error) throw new Error(result.error);
    setActionState("Correzione salvata: entrera' nel prossimo export SFT.", "ok");
    setStatus("correction-status", "salvata");
    const textField = $("correction-text");
    const solutionField = $("correction-solution");
    if (textField) textField.value = "";
    if (solutionField) solutionField.value = "";
    await loadDiagnostics();
  } catch (error) {
    setActionState(`Correzione fallita: ${error.message}`, "error");
  }
}

async function openRunLog(runId) {
  const viewer = $("run-log-viewer");
  if (viewer) viewer.textContent = `Carico ${runId}…`;
  try {
    const text = await fetchText(`/api/run/${encodeURIComponent(runId)}/log`);
    if (viewer) viewer.textContent = text || "Log vuoto.";
  } catch (error) {
    if (viewer) viewer.textContent = `Errore apertura log: ${error.message}`;
  }
}

async function seedBenchmark() {
  const benchmarkId = currentBenchmarkId();
  setActionState(`Seed ${benchmarkId} in corso…`, "busy");
  try {
    const result = await postJson("/api/training/seed", { benchmark_id: benchmarkId });
    setActionState(`Seed completato: ${result.count ?? 0} casi nuovi per ${benchmarkId}.`, "ok");
    await loadDiagnostics();
  } catch (error) {
    setActionState(`Seed fallito: ${error.message}`, "error");
  }
}

async function runBenchmark() {
  const benchmarkId = currentBenchmarkId();
  if (!window.confirm(`Avviare davvero il benchmark ${benchmarkId}? Partirà un job in background e nulla verrà promosso in memoria automaticamente.`)) {
    return;
  }
  // Resume per batch lunghi: OK = salta i casi che hanno gia' un attempt
  // (riprende un run interrotto), Annulla = rigira tutti i casi.
  const skipAttempted = window.confirm("Saltare i casi con un attempt già registrato? (OK = riprendi da dove eri · Annulla = rigira tutto)");
  setActionState(`Run ${benchmarkId} avviato…`, "busy");
  try {
    const result = await postJson("/api/training/run", { benchmark_id: benchmarkId, skip_attempted: skipAttempted });
    setActionState(`Job avviato: ${result.job?.job_id || "training"}. Richiede review Teacher/umana.`, "ok");
    await loadDiagnostics();
  } catch (error) {
    setActionState(`Run fallito: ${error.message}`, "error");
  }
}

async function importMbpp() {
  const countRaw = window.prompt(
    "Quanti casi MBPP importare? (max ~974, l'intero dataset; i gia' presenti vengono saltati)", "50");
  if (countRaw === null) return;
  const count = Math.max(1, Math.min(Number(countRaw) || 50, 1000));
  const offsetRaw = window.prompt("Offset di partenza nel dataset (0 = inizio)?", "0");
  if (offsetRaw === null) return;
  const offset = Math.max(0, Number(offsetRaw) || 0);
  if (!window.confirm(
    `Import esplicito MBPP: al primo uso scarica il dataset ufficiale (~5MB) nella cache training, ` +
    `poi importa ${count} casi (offset ${offset}) con i test ufficiali come gold tests. Procedere?`)) {
    return;
  }
  setActionState("Import MBPP in corso…", "busy");
  try {
    const result = await postJson("/api/training/adapters/mbpp/import", { limit: count, offset });
    if (result.error) throw new Error(result.error);
    setActionState(
      `MBPP: ${result.created} casi nuovi (saltati ${result.skipped_existing}, dataset ${result.dataset_rows} righe` +
      `${result.downloaded_now ? ", scaricato ora" : ", da cache"}). Seleziona benchmark "mbpp" per il run.`, "ok");
    await loadDiagnostics();
  } catch (error) {
    setActionState(`Import MBPP fallito: ${error.message}`, "error");
  }
}

async function exportTraining(url, label) {
  setActionState(`${label}: export in corso…`, "busy");
  try {
    const result = await postJson(url, {});
    setActionState(`${label}: ${result.rows ?? 0} righe salvate in ${result.path || "dataset"}.`, "ok");
    await loadDiagnostics();
  } catch (error) {
    setActionState(`${label} fallito: ${error.message}`, "error");
  }
}

function wireActions() {
  $("training-seed-action")?.addEventListener("click", seedBenchmark);
  $("training-run-action")?.addEventListener("click", runBenchmark);
  $("mbpp-import-action")?.addEventListener("click", importMbpp);
  $("teacher-packet-action")?.addEventListener("click", () => exportTraining("/api/training/export_teacher_packet", "Teacher packet"));
  $("sft-export-action")?.addEventListener("click", () => exportTraining("/api/training/export", "SFT export"));
  $("runs-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-run-log]");
    if (button) openRunLog(button.dataset.runLog);
  });
  $("training-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-review-status]");
    const container = event.target.closest("[data-attempt-id]");
    if (button && container) recordAttemptReview(container.dataset.attemptId, button.dataset.reviewStatus);
  });
  $("review-queue-list")?.addEventListener("click", (event) => {
    const logButton = event.target.closest("[data-run-log]");
    if (logButton) { openRunLog(logButton.dataset.runLog); return; }
    const button = event.target.closest("[data-review-status]");
    const container = event.target.closest("[data-attempt-id]");
    if (button && container) recordAttemptReview(container.dataset.attemptId, button.dataset.reviewStatus);
  });
  $("correction-save-action")?.addEventListener("click", saveCorrection);
  $("crawl-status-action")?.addEventListener("click", checkCrawlAdapter);
  $("crawl-url-action")?.addEventListener("click", crawlIntoKnowledge);
  $("sandbox-prepare-action")?.addEventListener("click", prepareSandbox);
  $("desktop-readiness-refresh")?.addEventListener("click", refreshDesktopReadiness);
  $("desktop-close-cleanup-action")?.addEventListener("click", cleanupLocalModelsNow);
  $("log-retention-dry-run")?.addEventListener("click", () => runLogCleanup(true));
  $("log-retention-cleanup")?.addEventListener("click", () => runLogCleanup(false));
}

async function populateProjectSelects() {
  // Dropdown progetti per Knowledge/Sandbox (2026-07-16): prima il path
  // andava incollato a mano anche coi progetti gia' in workspace. Il select
  // RIEMPIE il campo testo (che resta per cartelle esterne linkate).
  const payload = await fetchJson("/api/workspace/projects", {});
  const projects = payload?.projects || (Array.isArray(payload) ? payload : []);
  if (!projects.length) return;
  for (const [selectId, inputId] of [["crawl-project-select", "crawl-project-path"],
                                     ["sandbox-project-select", "sandbox-project-path"]]) {
    const select = $(selectId);
    if (!select) continue;
    const previous = select.value;
    select.innerHTML = '<option value="">— scegli dalla workspace —</option>'
      + projects.map((p) => `<option value="${escapeHtml(p.path || "")}">${escapeHtml(p.name || p.path || "")}</option>`).join("");
    if (previous) select.value = previous;
    if (!select.dataset.wired) {
      select.dataset.wired = "1";
      select.addEventListener("change", () => {
        const input = $(inputId);
        if (input && select.value) input.value = select.value;
      });
    }
    // se la pagina e' scoped su un progetto, preseleziona quello
    if (!select.value && diagnosticsState.projectPath) {
      select.value = diagnosticsState.projectPath;
      const input = $(inputId);
      if (input && !input.value && select.value) input.value = select.value;
    }
  }
}

async function loadDiagnostics(tab = diagnosticsState.activeTab) {
  setActiveTab(tab);
  populateProjectSelects().catch(() => {});
  setStatus("diagnostics-refresh-state", `refreshing ${tab}`);
  const projectQuery = diagnosticsState.projectPath ? `?${new URLSearchParams({ project_path: diagnosticsState.projectPath }).toString()}` : "";

  if (tab === "runs") {
    const [runs, retention] = await Promise.all([
      fetchJson("/api/runs", []),
      fetchJson("/api/logs/retention", { policy: {}, summary: {} }),
    ]);
    renderRuns(runs);
    renderLogRetention(retention);
  } else if (tab === "training") {
    const [training, exportsPayload] = await Promise.all([
      fetchJson(`/api/training/overview${projectQuery}`, {}),
      fetchJson("/api/training/exports", { exports: [] }),
    ]);
    renderTraining(training);
    renderExports(exportsPayload);
  } else if (tab === "memory") {
    const [mind, training] = await Promise.all([
      fetchJson("/api/mind/status", {}),
      fetchJson(`/api/training/overview${projectQuery}`, {}),
    ]);
    renderMemory(mind, training);
  } else if (tab === "settings") {
    const [mind, models, readiness] = await Promise.all([
      fetchJson("/api/mind/status", {}),
      fetchJson("/api/models/info", {}),
      fetchJson("/api/desktop/readiness", {}),
    ]);
    renderSettings(models, mind);
    renderDesktopReadiness(readiness);
  } else if (tab === "knowledge") {
    await checkCrawlAdapter();
  }

  setStatus("diagnostics-refresh-state", `${tab} · ${new Date().toLocaleTimeString()}`);
}

function setupDiagnosticsTabs() {
  setActiveTab(diagnosticsState.activeTab);
  window.addEventListener("hashchange", () => {
    loadDiagnostics((window.location.hash || "#runs").slice(1) || "runs");
  });
  document.querySelectorAll("[data-diagnostics-tab]").forEach((link) => {
    link.addEventListener("click", () => {
      const tab = link.dataset.diagnosticsTab || "runs";
      setActiveTab(tab);
      loadDiagnostics(tab);
    });
  });
}

wireActions();
setupDiagnosticsTabs();
loadDiagnostics();
setInterval(() => loadDiagnostics(diagnosticsState.activeTab), 30000);
