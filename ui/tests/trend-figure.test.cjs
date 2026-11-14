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
const { buildFigure, crowded, defaults, drawn, fromQuery, laneRows, names, panels, reading, short, toQuery, traceName, valueAt } = exportsObject;

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
  assert.deepEqual(plain(f.layout.yaxis4.categoryarray), ["Log", "Alarm · P.1"]); // empty lanes are left out
  assert.equal(f.layout.xaxis.anchor, "y4");
  assert.ok(f.layout.yaxis.domain[0] > f.layout.yaxis4.domain[1]);
  const marker = f.data.find((d) => d.mode === "markers");
  assert.deepEqual(plain(marker.customdata[0]), ["events", 1]); // the figure's second event: the log entry
  assert.equal(f.events[1].id, "l1");
  assert.equal(f.layout.shapes.filter((s) => s.type === "rect").length, 1); // the incident's lead-in
});

test("switching a group off removes its panel; a lone trace draws its limits", () => {
  const on = new Set(["lube", "lane:alarms"]);
  const f = buildFigure(view, timeline, on, palette);
  assert.equal(f.data.filter((d) => d.mode === "lines" && d.yaxis === "y").length, 1);
  assert.equal(f.layout.shapes.filter((s) => s.type === "line").length, 1);
  assert.deepEqual(plain(f.layout.yaxis2.categoryarray), ["Alarm · P.1"]);
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

test("on a phone each panel carries one unit, so no line is read against another's scale", () => {
  const loop = { ...view, groups: [{ key: "loop", label: "Control loop", show: true, traces: [
    trace("mode", ""), trace("pv", "%"), trace("sp", "%")] }], other: [] };
  loop.groups[0].traces[1].axis = loop.groups[0].traces[2].axis = "pv";
  loop.groups[0].traces[0].axis = "mode";
  assert.deepEqual(plain(drawn(panels(loop, defaults(loop)), 1).map((g) => g.traces.map((t) => t.id))), [["mode"], ["pv", "sp"]]);
  const f = buildFigure(loop, null, defaults(loop), palette, true);
  assert.equal(Object.keys(f.layout).filter((k) => /^yaxis\d*$/.test(k) && f.layout[k].overlaying).length, 0);
  assert.equal(Object.keys(f.layout).filter((k) => /^yaxis\d*$/.test(k)).length, 2);
});

test("P5.15c alarms get a row per point, the most urgent first, and the rest share one row", () => {
  const alarm = (point, type, start) => ({ id: point, kind: "alarm", label: point, start, end: null, text: `${type} 5`, detail: null });
  const lane = { key: "alarms", label: "Alarms", events: [alarm("A", "hi", "2024-07-24T01:00"), alarm("B", "hihi", "2024-07-24T02:00"),
    alarm("A", "hi", "2024-07-24T03:00"), ...Array.from({ length: 9 }, (_, i) => alarm(`Z${i}`, "lo", `2024-07-24T0${4 + (i % 5)}:00`))] };
  const rows = laneRows([lane, timeline.lanes[1]]);
  assert.deepEqual(plain(rows.slice(0, 2).map((r) => r.label)), ["Alarm · B", "Alarm · A"]); // hihi before hi
  assert.equal(rows.filter((r) => r.lane === "alarms").length, 8);
  assert.equal(rows.find((r) => r.key === "alarms:more").label, "4 more alarming points");
  assert.equal(rows[0].events.length, 1);
  assert.equal(rows[1].events.length, 2);
  assert.deepEqual(plain(rows.map((r) => r.lane).slice(-1)), ["log"]);
  assert.equal(laneRows([lane], true)[0].label, "B"); // a phone names the point alone
});

test("P5.15c markers too close to tell apart are one, counted", () => {
  const at = (t) => ({ id: t, kind: "log_entry", label: "op", start: `2024-07-24T${t}`, end: null, text: t, detail: null });
  const groups = crowded([at("10:00"), at("10:10"), at("10:20"), at("14:00")], 30 * 60000);
  assert.deepEqual(plain(groups.map((g) => g.length)), [3, 1]);
  const busy = { ...timeline, lanes: [{ key: "log", label: "Operator log", events: [at("10:00"), at("10:05"), at("10:10"), at("20:00")] }] };
  const f = buildFigure(view, busy, defaults(view), palette);
  const marker = f.data.find((d) => d.mode === "markers+text");
  assert.deepEqual(plain(marker.text), ["3", ""]);
  assert.deepEqual(plain(marker.customdata[0]), ["events", 0, 1, 2]);
});

test("P5.15d a pinned time reads each trace there, and draws as a line marked A or B", () => {
  const t = trace("x", "barg");
  assert.equal(valueAt(t, "2024-07-24T10:05", 10), 1);
  assert.equal(valueAt(t, "2024-07-24T10:50", 10), 2);
  assert.equal(valueAt(t, "2024-07-24T18:00", 10), null); // nothing within two steps
  const f = buildFigure(view, timeline, defaults(view), palette, false, ["2024-07-24T10:00", "2024-07-24T11:00"]);
  const pins = f.layout.shapes.filter((s) => s.type === "line" && s.yref === "paper");
  assert.deepEqual(plain(pins.map((s) => s.x0)), ["2024-07-24T10:00", "2024-07-24T11:00"]);
  assert.deepEqual(plain(f.layout.annotations.filter((a) => a.text.startsWith("<b>")).map((a) => a.text)), ["<b>A</b>", "<b>B</b>"]);
});

test("P5.15f panels are shorter on a phone", () => {
  const desk = buildFigure(view, null, defaults(view), palette);
  const phone = buildFigure(view, null, defaults(view), palette, true);
  // a panel's height in pixels: its share of the plot area (the figure less its top and bottom margins)
  const px = (f) => (f.layout.yaxis.domain[1] - f.layout.yaxis.domain[0]) * (f.layout.height - f.layout.margin.t - f.layout.margin.b);
  assert.ok(px(phone) < px(desk) - 30, `${px(phone)} vs ${px(desk)}`);
});
