// Validation and issue building, kept apart from index.js: a Worker's entry module may only export handlers.

export const SCHEMA = 1;
export const MAX_BODY_BYTES = 64 * 1024;
const MAX_TITLE = 120;
const MAX_VERSION = 60;

export function validate(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return "body must be a JSON object";
  if (payload.schema !== SCHEMA) return `schema must be ${SCHEMA}`;
  if (typeof payload.report !== "string" || !payload.report.trim()) return "report must be a non-empty string";
  for (const key of ["title", "plugin_version", "qgis_version"]) {
    if (payload[key] !== undefined && typeof payload[key] !== "string") return `${key} must be a string`;
  }
  return null;
}

function printable(text, max) {
  return (text || "").replace(/[\u0000-\u001f\u007f]/g, " ").trim().slice(0, max);
}

export function buildIssue(payload) {
  // A fence longer than any backtick run in the report, so the report can't close it early. Inside a fence,
  // GitHub doesn't turn "@name" or "#123" into mentions or links.
  const longestRun = Math.max(0, ...(payload.report.match(/`+/g) || []).map((run) => run.length));
  const fence = "`".repeat(Math.max(3, longestRun + 1));
  return {
    title: `[report] ${printable(payload.title, MAX_TITLE) || "Problem report"}`,
    body: [
      `Plugin: ${printable(payload.plugin_version, MAX_VERSION) || "unknown"}`,
      `QGIS: ${printable(payload.qgis_version, MAX_VERSION) || "unknown"}`,
      "",
      `${fence}text`,
      payload.report,
      fence,
    ].join("\n"),
  };
}
