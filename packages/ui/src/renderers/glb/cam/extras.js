/**
 * A CAM job's result as `cadgen cam plan` writes it into `<stem>.cam.glb`: the scene's extras carry
 * `generator: "cadgen cam"` and `cam` (schema 1), which this reads into the one shape the viewer
 * draws (`detect.js` says whether a scene is one). A missing or malformed value takes a plain
 * default, so a damaged file still opens. Pure: no three.js, no React.
 */

import { english } from "../../../i18n/core.js";
import { camExtras, carriesCamJob } from "./detect.js";

export { carriesCamJob };

const NONE = Object.freeze([]);

const plainObject = (value) => Boolean(value) && typeof value === "object" && !Array.isArray(value);
const list = (value) => (Array.isArray(value) ? value : NONE);
const number = (value, fallback = null) => (typeof value === "number" && Number.isFinite(value) ? value : fallback);
const text = (value, fallback = "") => (typeof value === "string" ? value.trim() || fallback
  : typeof value === "number" && Number.isFinite(value) ? String(value) : fallback);

/** A duration in plain words: "35 s", "42 min", "1 h 5 min". */
export function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value === 0) return "0 s";
  if (value < 59.5) return `${Math.max(1, Math.round(value))} s`;
  const minutes = Math.round(value / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/**
 * The part the job was planned from, the file a row's references name: `document` as the extras
 * write it (relative to the GLB, or absolute), resolved against the GLB's own path. Empty when the
 * extras do not say.
 */
export function camSourcePath(glbPath, document) {
  const named = String(document || "").trim();
  if (!named) return "";
  const absolute = /^(?:[A-Za-z]:)?[\\/]/.test(named);
  const parts = absolute ? named.split("/") : [...String(glbPath || "").split("/").slice(0, -1), ...named.split("/")];
  const out = [];
  for (const part of parts) {
    if (part === "..") out.pop();
    else if (part !== ".") out.push(part);
  }
  return out.join("/");
}

/**
 * The direction a setup's tool comes from, in part (CAD) coordinates: the third row of its `matrix` (part -> WCS,
 * so WCS +Z), else the `orientation` it names ("+Z" or "-Z").
 */
function setupAxis(rawSetup) {
  const row = list(list(rawSetup.matrix)[2]).slice(0, 3).map((value) => number(value));
  const length = row.length === 3 && row.every((value) => value !== null) ? Math.hypot(...row) : 0;
  if (length > 1e-9) return row.map((value) => value / length);
  return text(rawSetup.orientation).toUpperCase() === "-Z" ? [0, 0, -1] : [0, 0, 1];
}

const point = (value) => {
  const at = list(value).map((entry) => number(entry));
  return at.length === 3 && at.every((entry) => entry !== null) ? at : null;
};
const opIndex = (value) => (Number.isInteger(number(value)) ? number(value) : null);

/**
 * A finding as the viewer shows it, from cadgen's `{ check, severity, summary, items: [{ operation, at, ... }] }`:
 * `operation` is the first operation it names, `operations` every one, `at` the first place.
 */
function readFinding(raw) {
  if (!plainObject(raw)) return null;
  const message = text(raw.summary) || text(raw.check);
  if (!message) return null;
  const items = list(raw.items).filter(plainObject);
  const operations = [...new Set(items.map((item) => opIndex(item.operation)).filter((index) => index !== null))];
  return {
    check: text(raw.check), level: raw.severity === "error" ? "error" : "warning", message,
    operation: operations.length ? operations[0] : null, operations,
    at: items.map((item) => point(item.at)).find(Boolean) || null,
  };
}

/**
 * The job a CAM result carries, or null for any other GLB.
 *
 * @returns {null | { verdict: { status: "ready" | "fix", headline: string, errors: number, warnings: number, gcode: string },
 *   setups: { id: string, plain: string, timeS: number, orientation: string, axis: number[], operations: object[] }[],
 *   operations: object[], findings: object[], posts: object[], durationS: number, post: string,
 *   tools: object[], toolChanges: number, assumed: { key: string, value: string, why: string }[], document: string, safety: string }}
 *   A setup's `axis` is the direction its tool comes from, in part coordinates. Each operation: `{ index, op, plain, name,
 *   tool, toolPlain, toolDiameterMm, toolType, rpm, feedMmMin, timeS, t0, t1, summary, faces, setup }`, `index` the one
 *   `_CAM_OP` and a finding's `operation` name. `tools` are `readTools`'s.
 */
export function readCamJob(root) {
  if (!carriesCamJob(root)) return null;
  let cam = null;
  root.traverse((object) => { if (!cam) cam = camExtras(object); });
  const tools = Object.fromEntries(list(cam.tools).filter(plainObject).map((tool) => [text(tool.id), tool]));
  const operations = [];
  const setups = [];
  for (const [setupIndex, rawSetup] of list(cam.setups).entries()) {
    if (!plainObject(rawSetup)) continue;
    const id = text(rawSetup.id, `setup-${setupIndex + 1}`);
    const own = [];
    for (const rawOp of list(rawSetup.operations)) {
      if (!plainObject(rawOp)) continue;
      const t0 = number(rawOp.t0, 0);
      const t1 = Math.max(t0, number(rawOp.t1, t0));
      const plain = text(rawOp.plain) || text(rawOp.op, "Operation");
      const tool = text(rawOp.tool);
      const op = {
        index: opIndex(rawOp.index) ?? operations.length, op: text(rawOp.op), plain, name: text(rawOp.name), tool,
        toolPlain: text(rawOp.tool_plain) || tool, toolDiameterMm: number(rawOp.tool_diameter_mm) ?? number(tools[tool]?.diameter_mm),
        toolType: text(tools[tool]?.type), rpm: number(rawOp.rpm), feedMmMin: number(rawOp.feed_mm_min),
        timeS: Math.max(0, number(rawOp.time_s, t1 - t0)), t0, t1, summary: text(rawOp.summary, plain),
        faces: list(rawOp.faces).map((face) => text(face)).filter(Boolean),
        setup: id,
      };
      own.push(op);
      operations.push(op);
    }
    setups.push({ id, plain: text(rawSetup.plain, id), timeS: number(rawSetup.time_s, own.reduce((sum, op) => sum + op.timeS, 0)),
      orientation: text(rawSetup.orientation), axis: setupAxis(rawSetup), operations: own });
  }
  const findings = list(cam.findings).map(readFinding).filter(Boolean)
    .sort((a, b) => (a.level === "error" ? 0 : 1) - (b.level === "error" ? 0 : 1));
  const rawVerdict = plainObject(cam.verdict) ? cam.verdict : {};
  const errors = number(rawVerdict.errors, findings.filter((finding) => finding.level === "error").length);
  const posts = list(cam.posts).filter(plainObject).map((entry) => ({ setup: text(entry.setup), file: text(entry.file), post: text(entry.post), tool: text(entry.tool) }));
  return {
    // A plan with errors writes no G-code; its verdict says so ("G-code not written: fix 2 errors first").
    verdict: { status: rawVerdict.status === "ready" ? "ready" : "fix", headline: text(rawVerdict.headline), errors,
      warnings: number(rawVerdict.warnings, 0), gcode: text(rawVerdict.gcode) },
    setups, operations, findings,
    // A setup may post several files (one per tool on a controller without tool changes), each naming its `tool`.
    posts,
    durationS: number(cam.duration_s, operations.reduce((end, op) => Math.max(end, op.t1), 0)),
    // The controller the G-code is written for.
    post: text(cam.machine?.post),
    tools: readTools(cam.tools, operations),
    toolChanges: countToolChanges(operations),
    // What the plan took that nobody stated (a rapid rate, a tool's catalogue flute length).
    assumed: readAssumed(cam.assumed),
    document: text(cam.document),
    // The caveat every result carries: what the plan is not, and what to check before cutting.
    safety: text(cam.safety),
  };
}

/** An assumed value in words: "grbl", "3000", "false". */
function valueWords(value) {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.map(valueWords).filter(Boolean).join(", ");
  return text(value);
}

/** What the plan assumed, as cadgen writes it (`[{ key, value, why }]`), the value in words. An entry with no key is left out. */
function readAssumed(value) {
  return list(value).filter(plainObject).map((entry) => ({ key: text(entry.key), value: valueWords(entry.value), why: text(entry.why) }))
    .filter((entry) => entry.key);
}

/** An assumption in one line: "machine.rapid_mm_min 3000". */
export const assumedWords = (entry) => [entry.key, entry.value].filter(Boolean).join(" ");

/** The operations in program order: by when each starts, the job's order where two start together. */
const programOrder = (operations) => operations.map((op, order) => ({ op, order })).sort((a, b) => a.op.t0 - b.op.t0 || a.order - b.order).map(({ op }) => op);

/** The smallest and largest of some values (finite only), or null for none. */
function span(values) {
  const finite = values.filter((value) => value !== null && Number.isFinite(value));
  return finite.length ? [Math.min(...finite), Math.max(...finite)] : null;
}

/**
 * The job's tools, each once, in the order the program first uses them (then any the job lists but no operation
 * uses): `{ id, label, plain, type, diameterMm, operations, rpm, feedMmMin }`. `label` is how the machine names it
 * ("T1"); `plain` what it is ("6 mm flat"); `type` flat, ball, bull or drill; `rpm` and `feedMmMin` the range
 * (`[min, max]`) the operations that use it run it at, or null where none says.
 */
function readTools(rawTools, operations) {
  const listed = list(rawTools).filter(plainObject);
  const byId = new Map(listed.map((tool) => [text(tool.id), tool]));
  const ids = [...new Set([...programOrder(operations).map((op) => op.tool), ...byId.keys()].filter(Boolean))];
  return ids.map((id) => {
    const raw = byId.get(id) || {};
    const uses = operations.filter((op) => op.tool === id);
    const diameterMm = number(raw.diameter_mm);
    const type = text(raw.type);
    return {
      id, label: id, plain: text(raw.plain) || [diameterMm !== null ? `${diameterMm} mm` : "", type].filter(Boolean).join(" "),
      type, diameterMm, operations: uses.map((op) => op.index),
      rpm: span(uses.map((op) => op.rpm)), feedMmMin: span(uses.map((op) => op.feedMmMin)),
    };
  });
}

/** The tool changes in the program: one wherever an operation, in program order, runs another tool than the one before it. */
function countToolChanges(operations) {
  let count = 0;
  let last = "";
  for (const op of programOrder(operations)) {
    if (!op.tool) continue;
    if (last && op.tool !== last) count += 1;
    last = op.tool;
  }
  return count;
}

const grouped = (value) => Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 });
const ranged = (range, unit) => (range ? `${range[0] === range[1] ? grouped(range[0]) : `${grouped(range[0])}–${grouped(range[1])}`} ${unit}` : "");

/** A tool's speed and feed as the operations that use it run it ("12,000 rpm · 1,440 mm/min"), a range where they differ. */
export const toolSpeeds = (tool) => [ranged(tool.rpm, "rpm"), ranged(tool.feedMmMin, "mm/min")].filter(Boolean).join(" · ");

/** The job's tool changes in words: "No tool changes", "1 tool change", "3 tool changes". */
export const toolChangeWords = (count, t = english) =>
  (count > 0 ? t(count === 1 ? "cam.toolChange" : "cam.toolChanges", { count }) : t("cam.noToolChanges"));

/** What the job takes, under its verdict: its tool changes and the controller its G-code is for ("1 tool change · grbl controller"). */
export function jobSummary(job, t = english) {
  if (!job.operations.length) return "";
  return [toolChangeWords(job.toolChanges, t), job.post ? t("cam.controller", { post: job.post }) : ""].filter(Boolean).join(" · ");
}

/** An operation row's detail: its tool and its time ("6 mm flat · 5 min"). */
export const operationDetail = (op) => [op.toolPlain, formatDuration(op.timeS)].filter(Boolean).join(" · ");
