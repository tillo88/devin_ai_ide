/** Pure helpers for the bounded, read-only C3.3 run-log workspace. */

const KNOWN_WARNING_PATTERNS = [
  /(?:DeprecationWarning|FutureWarning|ResourceWarning)\b/i,
  /sentence-transformers unavailable:/i,
  /sklearn unavailable:/i,
  /verify_requirements argument is now a no-op/i,
];

const FAULT_STATUSES = new Set(["failed", "timeout", "stalled"]);


export function classifyLogLine(line, index = 0) {
  const text = String(line ?? "").replace(/\r$/, "");
  const severity = text.match(/^\s*\[(DEBUG|INFO|SUCCESS|WARNING|WARN|ERROR|FATAL|CRITICAL)\]\s*/i);
  const status = text.match(/^\s*status:\s*([a-z_]+)\s*$/i)?.[1]?.toLowerCase() || null;
  const matchesKnownWarning = KNOWN_WARNING_PATTERNS.some((pattern) => pattern.test(text));
  let level = "info";
  if (severity) {
    const normalized = severity[1].toLowerCase();
    if (["error", "fatal", "critical"].includes(normalized)) level = "fault";
    else if (["warning", "warn"].includes(normalized)) level = "warning";
    else if (normalized === "success") level = "success";
    else if (normalized === "debug") level = "debug";
  } else if (status && FAULT_STATUSES.has(status)) {
    level = "fault";
  } else if (/^\s*(?:Traceback \(most recent call last\):|[\w.]+(?:Error|Exception):)/.test(text)) {
    level = "fault";
  } else if (matchesKnownWarning) {
    level = "warning";
  } else if (/\bwarn(?:ing)?\b/i.test(text) || text.includes("⚠")) {
    level = "warning";
  } else if (status && ["success", "verified_success", "awaiting_approval"].includes(status)) {
    level = "success";
  }
  return {
    index: Number(index),
    text,
    level,
    status,
    knownWarning: level === "warning" && matchesKnownWarning,
  };
}


export function parseLogOutput(output) {
  const text = String(output ?? "");
  if (!text) return [];
  const lines = text.split("\n");
  if (lines.at(-1) === "") lines.pop();
  return lines.map((line, index) => classifyLogLine(line, index));
}


export function filterLogRows(rows, filter = "all", hideKnownWarnings = true) {
  const selected = ["all", "fault", "warning", "info"].includes(filter) ? filter : "all";
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (hideKnownWarnings && row.knownWarning) return false;
    if (selected === "all") return true;
    if (selected === "info") return ["info", "debug", "success"].includes(row.level);
    return row.level === selected;
  });
}


export function logRowCounts(rows) {
  const counts = { all: 0, fault: 0, warning: 0, info: 0, knownWarning: 0 };
  for (const row of Array.isArray(rows) ? rows : []) {
    counts.all += 1;
    if (row.level === "fault") counts.fault += 1;
    else if (row.level === "warning") counts.warning += 1;
    else counts.info += 1;
    if (row.knownWarning) counts.knownWarning += 1;
  }
  return counts;
}


export function structuredFaults(events) {
  return (Array.isArray(events) ? events : []).filter((event) => {
    const level = String(event?.level || "").toLowerCase();
    const status = String(event?.data?.status || "").toLowerCase();
    return ["error", "fatal", "critical"].includes(level)
      || (event?.type === "run_finished" && FAULT_STATUSES.has(status));
  });
}
