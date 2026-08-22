import test from "node:test";
import assert from "node:assert/strict";

import {
  classifyLogLine,
  filterLogRows,
  logRowCounts,
  parseLogOutput,
  structuredFaults,
} from "./devin/ui/static/js/run_log.js";


test("raw log classification keeps actionable warnings visible", () => {
  assert.equal(classifyLogLine("[ERROR] runner failed").level, "fault");
  assert.equal(classifyLogLine("status: timeout").level, "fault");
  assert.equal(classifyLogLine("[WARNING] Rig DEVIN non disponibile").knownWarning, false);
  assert.equal(classifyLogLine("StarletteDeprecationWarning: old API").knownWarning, true);
  assert.equal(classifyLogLine("TINYFISH_API_KEY: ASSENTE").knownWarning, false);
});


test("filters hide only allowlisted warning noise", () => {
  const rows = parseLogOutput([
    "[INFO] start",
    "[WARNING] Rig DEVIN non disponibile",
    "DeprecationWarning: old API",
    "[ERROR] failed",
    "status: success",
    "",
  ].join("\n"));
  const counts = logRowCounts(rows);
  assert.deepEqual(counts, { all: 5, fault: 1, warning: 2, info: 2, knownWarning: 1 });
  assert.deepEqual(filterLogRows(rows, "warning", true).map((row) => row.text), [
    "[WARNING] Rig DEVIN non disponibile",
  ]);
  assert.equal(filterLogRows(rows, "all", true).length, 4);
});


test("fault panel is driven by structured events and terminal failure status", () => {
  const events = [
    { seq: 0, type: "warning", level: "warning", message: "fallback" },
    { seq: 1, type: "error", level: "error", message: "runner exploded" },
    { seq: 2, type: "run_finished", level: "warning", message: "done", data: { status: "failed" } },
    { seq: 3, type: "run_finished", level: "info", message: "done", data: { status: "success" } },
  ];
  assert.deepEqual(structuredFaults(events).map((event) => event.seq), [1, 2]);
});
