import assert from "node:assert/strict";
import { projectFlowSnapshot } from "./devin/ui/static/js/project_flow.js";

const empty = projectFlowSnapshot();
assert.equal(empty.stages.project.status, "idle");
assert.equal(empty.stages.run.label, "nessun run");
assert.equal(empty.nextAction, "Seleziona o crea un progetto");

const active = projectFlowSnapshot({
  projectSelected: true,
  projectLabel: "compiler",
  goalStatus: "running",
  run: { run_id: "run_1", status: "running" },
  runLookup: "loaded",
  centerView: "goal",
});
assert.deepEqual(active.stages.project, { status: "done", label: "compiler", active: false });
assert.equal(active.stages.goal.status, "active");
assert.equal(active.stages.goal.active, true);
assert.equal(active.stages.evidence.label, "in raccolta");
assert.equal(active.nextAction, "Segui l’esecuzione in corso");

const approval = projectFlowSnapshot({
  projectSelected: true,
  goalStatus: "needs_approval",
  run: { run_id: "run_2", status: "awaiting_approval" },
  runLookup: "loaded",
});
assert.equal(approval.stages.goal.status, "pending");
assert.equal(approval.stages.run.status, "pending");
assert.equal(approval.stages.review.status, "pending");
assert.equal(approval.stages.review.label, "diff da decidere");
assert.equal(approval.nextAction, "Apri il diff e decidi");

const applied = projectFlowSnapshot({
  projectSelected: true,
  goalStatus: "success",
  run: { run_id: "run_3", status: "verified_success" },
  runLookup: "loaded",
  manifestStatus: "applied",
  centerView: "log",
});
assert.equal(applied.stages.goal.status, "done");
assert.equal(applied.stages.run.status, "done");
assert.equal(applied.stages.review.label, "applicata");
assert.equal(applied.stages.evidence.active, true);
assert.equal(applied.stages.evidence.label, "run registrato");

const unavailable = projectFlowSnapshot({ projectSelected: true, runLookup: "error" });
assert.equal(unavailable.stages.run.label, "stato non disponibile");
assert.equal(unavailable.stages.evidence.label, "stato non disponibile");
assert.equal(unavailable.nextAction, "Riprova lo stato del progetto");

const exhausted = projectFlowSnapshot({ projectSelected: true, goalStatus: "budget_exhausted" });
assert.equal(exhausted.stages.goal.status, "failed");
assert.equal(exhausted.nextAction, "Apri log ed evidenze");

console.log("project flow: 6 scenari verdi");
