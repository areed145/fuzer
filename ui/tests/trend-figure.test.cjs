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
const { buildFigure, defaults, drawn, fromQuery, names, panels, reading, short, toQuery, traceName } = exportsObject;

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
  assert.ok(f.layout.legend.title.text.includes("Lube oil")); // each panel's key is titled with it
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

test("a link carries only what differs from the defaults, and an old link still opens", () => {
  assert.deepEqual(plain(toQuery(view, defaults(view))), {});
  const on = defaults(view);
  on.add("lube"); on.delete("speed"); on.delete("lane:log");
  const q = toQuery(view, on);
  assert.deepEqual(plain(q), { show: "lube", hide: "speed,lane:log" });
  assert.deepEqual(plain([...fromQuery(view, q.show, q.hide)].sort()), plain([...on].sort()));
  const old = fromQuery(view, "lube,gone", "gone-too");
  assert.ok(old.has("lube") && !old.has("gone"));
});

test("a landed reading draws as steps with a marker per reading, its method in the hover", () => {
  const wt = trace("m:w|oil_rate|", "sm3/d", { sampled: true, methods: ["separator", null], role_label: "Oil rate" });
  const tested = { ...view, groups: [{ key: "tests", label: "Tests and samples", show: true, traces: [wt] }], other: [] };
  const [d] = buildFigure(tested, null, new Set([wt.id]), palette).data;
  assert.equal(d.mode, "lines+markers");
  assert.equal(d.line.shape, "hv");
  assert.deepEqual(plain(d.text), ["separator", ""]);
  assert.ok(d.hovertemplate.includes("%{text}"));
  const [plainLine] = buildFigure(view, null, new Set(["suction"]), palette).data;
  assert.equal(plainLine.mode, "lines");
});

test("each trace is named in its panel's key with its last value, and no panel carries more than two value axes", () => {
  const four = { ...view, groups: [{ key: "v", label: "Vibration", show: true, traces: [
    trace("a", "mm/s"), trace("b", "um"), trace("c", "mm"), trace("d", "degc", { last: 101.13, uom: "degC" })] }], other: [] };
  assert.deepEqual(plain(drawn(panels(four, defaults(four))).map((g) => [g.label, g.traces.map((t) => t.id)])),
                   [["Vibration", ["a", "b"]], ["Vibration, continued", ["c", "d"]]]);
  const f = buildFigure(four, null, defaults(four), palette);
  assert.equal(f.layout.yaxis3.domain[1] < f.layout.yaxis.domain[0], true); // the third unit starts a panel of its own
  assert.equal(Object.keys(f.layout).filter((k) => /^yaxis\d*$/.test(k) && f.layout[k].anchor === "free").length, 0);
  const d = f.data.find((t) => t.name.startsWith("d"));
  assert.equal(d.legend, "legend2");
  assert.ok(d.name.includes("<b>101</b> degC"), d.name);
  assert.equal(f.layout.legend2.title.text, "<b>Vibration, continued</b>");
  assert.equal(reading(1234.5), "1,235");
  assert.equal(reading(0.01234), "0.012");
});

test("the incident's lead-in is labelled on the chart", () => {
  const f = buildFigure(view, timeline, defaults(view), palette);
  assert.ok(f.layout.annotations.some((a) => a.text === "12 h before the incident"));
});

test("on a phone each panel's key is a list, one trace a line, with long names cut", () => {
  const f = buildFigure(view, null, defaults(view), palette, true);
  assert.equal(f.layout.legend.orientation, "v");
  assert.equal(f.layout.legend.title.side, "top");
  assert.equal(buildFigure(view, null, defaults(view), palette).layout.legend.orientation, "h");
  assert.equal(short("Journal Bearing Temperature · 20-K-2001", true), "Journal Bearing Temperature ·…");
  assert.equal(short("Journal Bearing Temperature · 20-K-2001", false), "Journal Bearing Temperature · 20-K-2001");
});
