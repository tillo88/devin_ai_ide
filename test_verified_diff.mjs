import test from "node:test";
import assert from "node:assert/strict";

import {
  normalizedDiffHeaderPath,
  sideBySideRows,
  splitManifestDiff,
} from "./devin/ui/static/js/verified_diff.js";


test("manifest diff is split only across declared relative entries", () => {
  const payload = {
    entries: [
      { path: "src/a.py", operation: "modify" },
      { path: "src/b.py", operation: "create" },
    ],
    unified_diff: [
      "--- a/src/a.py",
      "+++ b/src/a.py",
      "@@ -1 +1 @@",
      "-old",
      "+new",
      "--- a/src/b.py",
      "+++ b/src/b.py",
      "@@ -0,0 +1 @@",
      "+created",
      "--- a/not-declared.txt",
      "+++ b/not-declared.txt",
      "@@ -0,0 +1 @@",
      "+ignored",
      "",
    ].join("\n"),
  };

  const files = splitManifestDiff(payload);

  assert.equal(files.length, 2);
  assert.match(files[0].diffText, /-old\n\+new/);
  assert.match(files[1].diffText, /\+created/);
  assert.ok(files.every((file) => !file.diffText.includes("ignored")));
  assert.equal(normalizedDiffHeaderPath("b/src/a.py"), "src/a.py");
});


test("side-by-side rows align replacement blocks and preserve line numbers", () => {
  const rows = sideBySideRows([
    "--- a/src/a.py",
    "+++ b/src/a.py",
    "@@ -10,3 +10,3 @@",
    " context",
    "-before one",
    "-before two",
    "+after one",
    "+after two",
    " tail",
    "",
  ].join("\n"));

  assert.deepEqual(rows.map((row) => row.kind), ["meta", "context", "change", "change", "context"]);
  assert.deepEqual(
    rows.slice(1).map((row) => [row.oldNo, row.newNo]),
    [[10, 10], [11, 11], [12, 12], [13, 13]],
  );
  assert.equal(rows[2].oldText, "before one");
  assert.equal(rows[2].newText, "after one");
});


test("binary and truncated markers remain bounded metadata rows", () => {
  const rows = sideBySideRows([
    "--- a/logo.png",
    "+++ b/logo.png",
    "Binary file changed (modify)",
    "... diff preview truncated ...",
  ].join("\n"));

  assert.deepEqual(rows, [
    { kind: "meta", text: "Binary file changed (modify)" },
    { kind: "meta", text: "... diff preview truncated ..." },
  ]);
});
