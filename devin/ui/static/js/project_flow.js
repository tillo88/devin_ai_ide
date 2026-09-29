const terminalRunStatuses = new Set([
  "success", "verified_success", "syntax_only", "failed", "timeout", "stopped",
  "stalled", "awaiting_approval", "rejected", "rolled_back", "applied_uncommitted",
]);
const successfulRunStatuses = new Set(["success", "verified_success"]);
const failedRunStatuses = new Set(["failed", "timeout", "stalled", "stopped"]);
const successfulGoalStatuses = new Set(["success"]);
const failedGoalStatuses = new Set(["blocked", "budget_exhausted", "stopped", "error"]);

export function projectFlowSnapshot({
  projectSelected = false,
  projectLabel = "da scegliere",
  goalStatus = "",
  run = null,
  runLookup = "idle",
  manifestStatus = "",
  centerView = "chat",
} = {}) {
  const runStatus = run?.status || "";
  const stages = {
    project: {
      status: projectSelected ? "done" : "idle",
      label: projectSelected ? projectLabel : "da scegliere",
      active: ["chat", "editor"].includes(centerView),
    },
    goal: {
      status: "idle",
      label: projectSelected ? "da definire" : "in attesa",
      active: centerView === "goal",
    },
    run: {
      status: "idle",
      label: "nessun run",
      active: centerView === "runs",
    },
    review: {
      status: "idle",
      label: "non richiesta",
      active: centerView === "diff",
    },
    evidence: {
      status: "idle",
      label: "non disponibili",
      active: centerView === "log",
    },
  };

  if (goalStatus) {
    stages.goal.label = goalStatus;
    if (["starting", "running", "stopping"].includes(goalStatus)) stages.goal.status = "active";
    else if (["awaiting_approval", "needs_approval"].includes(goalStatus)) stages.goal.status = "pending";
    else if (successfulGoalStatuses.has(goalStatus)) stages.goal.status = "done";
    else if (failedGoalStatuses.has(goalStatus)) stages.goal.status = "failed";
    else stages.goal.status = "ready";
  }

  if (!run && runLookup === "loading") {
    stages.run.status = "active";
    stages.run.label = "caricamento…";
  } else if (!run && runLookup === "error") {
    stages.run.status = "ready";
    stages.run.label = "stato non disponibile";
  } else if (run) {
    stages.run.label = runStatus || "stato ignoto";
    if (["starting", "running"].includes(runStatus)) stages.run.status = "active";
    else if (runStatus === "awaiting_approval") stages.run.status = "pending";
    else if (successfulRunStatuses.has(runStatus)) stages.run.status = "done";
    else if (failedRunStatuses.has(runStatus)) stages.run.status = "failed";
    else stages.run.status = "ready";
  }

  if (runStatus === "awaiting_approval" || manifestStatus === "pending") {
    stages.review.status = "pending";
    stages.review.label = "diff da decidere";
  } else if (["applied", "applied_uncommitted"].includes(manifestStatus)) {
    stages.review.status = "done";
    stages.review.label = manifestStatus === "applied" ? "applicata" : "applicata, non commit";
  } else if (manifestStatus === "rejected") {
    stages.review.status = "ready";
    stages.review.label = "rifiutata";
  } else if (manifestStatus === "rolled_back") {
    stages.review.status = "ready";
    stages.review.label = "rollback eseguito";
  }

  stages.evidence.status = !run
    ? (runLookup === "loading" ? "active" : "idle")
    : terminalRunStatuses.has(runStatus) ? "done" : "active";
  stages.evidence.label = !run
    ? (runLookup === "error" ? "stato non disponibile" : runLookup === "loading" ? "caricamento…" : "non disponibili")
    : terminalRunStatuses.has(runStatus) ? "run registrato" : "in raccolta";

  let nextAction = "Seleziona o crea un progetto";
  if (runLookup === "error") nextAction = "Riprova lo stato del progetto";
  else if (projectSelected && !goalStatus && !run) nextAction = "Definisci il Goal e i criteri";
  else if (["starting", "running", "stopping"].includes(goalStatus) || ["starting", "running"].includes(runStatus)) nextAction = "Segui l’esecuzione in corso";
  else if (runStatus === "awaiting_approval" || manifestStatus === "pending") nextAction = "Apri il diff e decidi";
  else if (goalStatus === "needs_approval") nextAction = "Controlla il Goal in attesa";
  else if (failedRunStatuses.has(runStatus) || failedGoalStatuses.has(goalStatus)) nextAction = "Apri log ed evidenze";
  else if (run) nextAction = "Controlla criteri ed evidenze";
  else if (projectSelected) nextAction = "Avvia un Goal verificabile";

  return { stages, nextAction };
}
