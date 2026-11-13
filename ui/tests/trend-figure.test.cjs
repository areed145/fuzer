const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/trendFigure.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject, Date, Math, Set, Map, Object, JSON });
const { buildFigure, defaults, names, panels, traceName } = exportsObject;

const vals = [["2024-07-24T10:00", 1], ["2024-07-24T11:00", 2]];
const trace = (id, axis, extra = {}) => ({ id, point: `P.${id}`, description: null, uom: axis, axis, show: true, values: vals,
  min: 1, max: 2, mean: 1.5, last: 2, role_label: id, on: { id: "u", label: "K-1", relation: "self" }, ...extra });
const view = {
  provider: "synthetic", centre: "2024-07-24T12:00", start: "2024-07-24T00:00", end: "2024-07-25T00:00", step_minutes: 10,
  upsets: ["2024-07-24T13:45"], unit: "u", unit_label: "K-1", classes: ["CO"], template: { key: "CO", title: "Compressor" },
  groups: [
    { key: "pressures", label: "Pressures", show: true, traces: [trace("suction", "barg"), trace("discharge", "barg")] },
    { key: "driver", label: "Driver", show: true, traces: [trace("speed", "rpm", { on: { id: "g", label: "GT-1", relation: "driver" } }), trace("power", "kw")] },
    { key: "lube", label: "Lube oil", show: false, traces: [trace("lube", "barg", { show: false, limits: [{ type: "hi", value: 3, basis: "alarm" }] })] },
  ],
  other: [trace("spare", "degc", { show: false })], missing: [],
};
const timeline = { unit: "u", start: view.start, end: view.end, lanes: [
  { key: "alarms", label: "Alarms", events: [{ id: "p1", kind: "alarm", label: "P.1", start: "2024-07-24T13:00", end: "2024-07-24T14:00", text: "hi 5", detail: null }] },
  { key: "log", label: "Operator log", events: [{ id: "l1", kind: "log_entry", label: "op", start: "2024-07-24T13:50", end: null, text: "tripped", detail: null }] },
  { key: "cases", label: "Cases", events: [] },
] };
const plain = (x) => JSON.parse(JSON.stringify(x));
const palette = { series: ["a", "b", "c", "d", "e", "f"], lanes: {}, limit: "w", shade: "s", text: "t" };

test("a neighbour's trace names the neighbour", () => {
  assert.equal(traceName(view.groups[1].traces[0]), "speed · GT-1");
  assert.equal(traceName(view.groups[0].traces[0]), "suction");
});

test("what the template shows, and every lane, start switched on", () => {
  const on = defaults(view);
  assert.ok(on.has("suction") && on.has("speed") && on.has("lane:alarms"));
  assert.ok(!on.has("lube") && !on.has("spare"));
  assert.deepEqual(plain(panels(view, on).map((g) => g.key)), ["pressures", "driver"]);
});

test("each panel shares an axis per unit, on one time axis, with lanes beneath", () => {
  const f = buildFigure(view, timeline, defaults(view), palette);
  const lines = f.data.filter((d) => d.mode === "lines" && d.yaxis !== "y4");
  assert.deepEqual(plain(lines.map((d) => d.yaxis)), ["y", "y", "y2", "y3"]); // pressures share one; speed and power two
  assert.equal(f.layout.yaxis3.overlaying, "y2");
  assert.equal(f.layout.yaxis4.type, "category"); // the lanes
  assert.deepEqual(plain(f.layout.yaxis4.categoryarray), ["Log", "Alarms"]); // empty lanes are left out
  assert.equal(f.layout.xaxis.anchor, "y4");
  assert.ok(f.layout.yaxis.domain[0] > f.layout.yaxis4.domain[1]);
  const marker = f.data.find((d) => d.mode === "markers");
  assert.deepEqual(plain(marker.customdata[0]), ["log_entry", "l1", null]);
  assert.equal(f.layout.shapes.filter((s) => s.type === "rect").length, 1); // the incident's lead-in
});

test("switching a group off removes its panel; a lone trace draws its limits", () => {
  const on = new Set(["lube", "lane:alarms"]);
  const f = buildFigure(view, timeline, on, palette);
  assert.equal(f.data.filter((d) => d.mode === "lines" && d.yaxis === "y").length, 1);
  assert.equal(f.layout.shapes.filter((s) => s.type === "line").length, 1);
  assert.deepEqual(plain(f.layout.yaxis2.categoryarray), ["Alarms"]);
  assert.ok(f.layout.annotations.some((a) => a.text.includes("Lube oil")));
});

test("two traces that would read the same are told apart by what differs in their descriptions", () => {
  const twins = { ...view, groups: [{ key: "v", label: "V", show: true, traces: [
    trace("a", "degc", { role_label: "Bearing temperature", description: "Train B Compressor Thrust Bearing Temperature" }),
    trace("b", "degc", { role_label: "Bearing temperature", description: "Train B Compressor Journal Bearing Temperature" }),
    trace("c", "mm/s", { role_label: "Casing vibration" })] }], other: [] };
  const n = names(twins);
  assert.equal(n.get("a"), "Thrust Bearing Temperature");
  assert.equal(n.get("b"), "Journal Bearing Temperature");
  assert.equal(n.get("c"), "Casing vibration");
});
