/** Pure, bounded helpers for the C3.2 verified change-manifest viewer. */

export function normalizedDiffHeaderPath(header) {
  const value = String(header || "").trim().split("\t", 1)[0];
  if (!value || value === "/dev/null") return "";
  return value.replace(/^[ab]\//, "");
}

export function splitManifestDiff(payload) {
  const entries = Array.isArray(payload?.entries) ? payload.entries : [];
  const files = entries.map((entry) => ({ ...entry, diffText: "" }));
  const byPath = new Map(files.map((entry) => [entry.path, entry]));
  const lines = String(payload?.unified_diff || "").split("\n");
  let current = null;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.startsWith("--- ") && lines[index + 1]?.startsWith("+++ ")) {
      const beforePath = normalizedDiffHeaderPath(line.slice(4));
      const afterPath = normalizedDiffHeaderPath(lines[index + 1].slice(4));
      const path = afterPath || beforePath;
      current = byPath.get(path) || null;
      if (current) current.diffText += `${line}\n${lines[index + 1]}\n`;
      index += 1;
      continue;
    }
    if (current) current.diffText += `${line}\n`;
  }
  return files;
}

export function sideBySideRows(diffText) {
  const lines = String(diffText || "").split("\n");
  const rows = [];
  let oldLine = null;
  let newLine = null;
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.startsWith("--- ") || line.startsWith("+++ ") || line === "") {
      index += 1;
      continue;
    }
    if (line.startsWith("@@")) {
      const match = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (match) {
        oldLine = Number(match[1]);
        newLine = Number(match[2]);
      }
      rows.push({ kind: "meta", text: line });
      index += 1;
      continue;
    }
    if (line.startsWith("Binary file changed") || line.startsWith("... diff preview truncated")) {
      rows.push({ kind: "meta", text: line });
      index += 1;
      continue;
    }
    if (line.startsWith("-") && !line.startsWith("---")) {
      const deletions = [];
      while (index < lines.length && lines[index].startsWith("-") && !lines[index].startsWith("---")) {
        deletions.push(lines[index].slice(1));
        index += 1;
      }
      const additions = [];
      while (index < lines.length && lines[index].startsWith("+") && !lines[index].startsWith("+++")) {
        additions.push(lines[index].slice(1));
        index += 1;
      }
      const count = Math.max(deletions.length, additions.length);
      for (let offset = 0; offset < count; offset += 1) {
        const hasOld = offset < deletions.length;
        const hasNew = offset < additions.length;
        rows.push({
          kind: hasOld && hasNew ? "change" : hasOld ? "delete" : "add",
          oldNo: hasOld ? oldLine++ : null,
          newNo: hasNew ? newLine++ : null,
          oldText: hasOld ? deletions[offset] : "",
          newText: hasNew ? additions[offset] : "",
        });
      }
      continue;
    }
    if (line.startsWith("+") && !line.startsWith("+++")) {
      rows.push({ kind: "add", oldNo: null, newNo: newLine++, oldText: "", newText: line.slice(1) });
      index += 1;
      continue;
    }
    if (line.startsWith(" ")) {
      rows.push({
        kind: "context",
        oldNo: oldLine++,
        newNo: newLine++,
        oldText: line.slice(1),
        newText: line.slice(1),
      });
      index += 1;
      continue;
    }
    if (line.startsWith("\\ No newline")) {
      index += 1;
      continue;
    }
    rows.push({ kind: "meta", text: line });
    index += 1;
  }
  return rows;
}
