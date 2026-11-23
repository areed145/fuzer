const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");

// The one chart over time (trends/figure.ts): a trend spec drawn as a Plotly figure.
const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/trends/figure.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
runInNewContext(code, { exports: exportsObject, Date, Math, Set, Map, Object, JSON, String, Number });
const { buildFigure, colours, crowded, defaults, drawn, fromQuery, inWindow, laneRows, limitName, nearLimits, opening, panels,
  reading, rowLabel, scaled, short, toQuery, valueAt, HOVER_TIME } = exportsObject;

const vals = [["2024-07-24T10:00", 1], ["2024-07-24T11:00", 2]];
const series = (key, unit, extra = {}) => ({ key, point: `P.${key}`, tag: key, label: key, description: null, unit, uom: unit,
  role: null, on: null, link: key, as: "line", axis: unit, values: vals, min: 1, max: 2, mean: 1.5, last: 2, limits: [], show: true, ...extra });
const lanes = ["alarms", "log", "cases", "maintenance", "downtime"].map((lane) => ({ lane, label: lane, show: true }));
const spec = (panelList, seriesList, extra = {}) => ({
  version: 1, subject: "k", unit: "u", unit_label: "K-1", title: "K-1 · Compressor", template: { key: "CO", title: "Compressor" },
  provider: "synthetic", step_minutes: 10, time: { start: "2024-07-24T00:00", end: "2024-07-25T00:00", centre: "2024-07-24T16:00" },
  panels: panelList, series: seriesList, focus: null, events: lanes,
  marks: [{ kind: "window", start: "2024-07-24T01:45", end: "2024-07-24T13:45", label: "12 h before the incident", tone: "warn" }],
  missing: [], ...extra,
});
const compressor = spec([
  { key: "pressures", label: "Pressures", show: true, axis: "auto", series: ["suction", "discharge"] },
  { key: "driver", label: "Driver", show: true, axis: "auto", series: ["speed", "power"] },
  { key: "lube", label: "Lube oil", show: false, axis: "auto", series: ["lube"] },
], [series("suction", "psig"), series("discharge", "psig"), series("speed", "rpm"), series("power", "hp"),
    series("lube", "psig", { show: false, limits: [{ type: "hi", value: 3, basis: "alarm" }] })]);
const timeline = { unit: "u", start: "2024-07-24T00:00", end: "2024-07-25T00:00", lanes: [
  { key: "alarms", label: "Alarms", events: [{ id: "p1", kind: "alarm", label: "P.suction", start: "2024-07-24T13:00", end: "2024-07-24T14:00", text: "hi 5", detail: null },
    { id: "p0", kind: "alarm", label: "P.speed", start: "2024-07-23T20:00", end: null, text: "hi 5", detail: null }] },
  { key: "log", label: "Operator log", events: [{ id: "l1", kind: "log_entry", label: "op", start: "2024-07-24T13:50", end: null, text: "tripped", detail: null }] },
  { key: "cases", label: "Cases", events: [] },
] };
const plain = (x) => JSON.parse(JSON.stringify(x));
const palette = { series: ["a", "b", "c", "d", "e", "f"], lanes: {}, limit: "w", danger: "r", shade: "s", text: "t", muted: "m" };
const yaxes = (f) => Object.keys(f.layout).filter((k) => /^yaxis\d*$/.test(k));

test("what the spec shows and every lane start switched on; a focus opens on its series with the lanes", () => {
  const on = defaults(compressor);
  assert.ok(on.has("suction") && on.has("speed") && on.has("lane:alarms") && !on.has("lube"));
  assert.deepEqual(plain(panels(compressor, on).map((p) => p.key)), ["pressures", "driver"]);
  const focused = opening({ ...compressor, focus: ["discharge"] });
  assert.deepEqual(plain([...focused].filter((k) => !k.startsWith("lane:"))), ["discharge"]);
  assert.ok(focused.has("lane:cases"));
  assert.deepEqual(plain([...opening(compressor)].sort()), plain([...on].sort()));
});

test("a link carries only what differs from the defaults, and an old link still opens", () => {
  assert.deepEqual(plain(toQuery(compressor, defaults(compressor))), {});
  const on = defaults(compressor);
  on.add("lube"); on.delete("speed"); on.delete("lane:log");
  const q = toQuery(compressor, on);
  assert.deepEqual(plain(q), { show: "lube", hide: "speed,lane:log" });
  assert.deepEqual(plain([...fromQuery(compressor, q.show, q.hide)].sort()), plain([...on].sort()));
  assert.ok(!fromQuery(compressor, "lube,gone", null).has("gone"));
});

test("a colour runs on across panels, so the first line of each panel is not the same colour", () => {
  const c = colours(compressor, defaults(compressor), palette);
  assert.deepEqual(plain([...c.values()]), ["a", "b", "c", "d"]);
  const f = buildFigure(compressor, null, defaults(compressor), palette);
  assert.deepEqual(plain(f.data.map((d) => d.line.color)), ["a", "b", "c", "d"]);
});

test("a panel is one scale, or a pair of series on two; more units continue in a panel of their own, and one a panel on a phone", () => {
  const exhaust = spec([{ key: "x", label: "Exhaust and fuel", show: true, axis: "auto", series: ["t1", "t2", "t3", "fuel"] }],
    [series("t1", "°F"), series("t2", "°F"), series("t3", "°F"), series("fuel", "scfh")]);
  assert.deepEqual(plain(drawn(scaled(panels(exhaust, defaults(exhaust)))).map((p) => [p.label, p.series.map((s) => s.key)])),
                   [["Exhaust and fuel", ["t1", "t2", "t3"]], ["Exhaust and fuel, continued", ["fuel"]]]);
  const f = buildFigure(compressor, null, defaults(compressor), palette);
  assert.equal(f.layout.yaxis3.overlaying, "y2"); // speed and power: a pair on two scales
  assert.deepEqual(plain(f.data.map((d) => d.yaxis)), ["y", "y", "y2", "y3"]);
  const phone = buildFigure(compressor, null, defaults(compressor), palette, true);
  assert.equal(yaxes(phone).filter((k) => phone.layout[k].overlaying).length, 0);
  assert.equal(yaxes(phone).length, 3);
});

test("a series' limits draw when it is the one the chart is about; one far from its line is named at the edge", () => {
  const well = spec([{ key: "p", label: "Pressures", show: true, axis: "auto", series: ["whp", "chp"] }],
    [series("whp", "psig", { min: 188, max: 194, limits: [{ type: "lo", value: 190, basis: "alarm" }, { type: "hi", value: 258, basis: "alarm" },
      { type: "hihi", value: 275, basis: "alarm" }] }), series("chp", "psig", { min: 185, max: 200 })], { focus: ["whp"] });
  const near = nearLimits(well.series[0]);
  assert.deepEqual(plain(near.near.map((l) => l.type)), ["lo"]);
  assert.deepEqual(plain(near.far.map((l) => l.type)), ["hi", "hihi"]);
  const f = buildFigure(well, null, opening(well), palette);
  const lines = f.layout.shapes.filter((s) => s.type === "line" && s.yref === "y");
  assert.deepEqual(plain(lines.map((s) => s.y0)), [190]); // the Lo it crossed, not Hi 258 flattening the line
  assert.ok(f.layout.annotations.some((a) => a.text === "Lo 190"));
  assert.ok(f.layout.annotations.some((a) => a.text === "Hi 258, HiHi 275 ↑"));
  // two series on one scale, neither focused: no limits, they'd be mistaken for each other's
  const both = buildFigure({ ...well, focus: null }, null, defaults(well), palette);
  assert.equal(both.layout.shapes.filter((s) => s.type === "line" && s.yref !== "paper").length, 0);
  assert.equal(limitName({ type: "hi", value: 300, basis: "iow", iow: "Critical" }), "Critical high 300");
  assert.equal(limitName({ type: "lolo", value: 12.345, basis: "learned" }), "learned LoLo 12.3");
});

test("only events in the window are drawn; those before or after are counted on their lane", () => {
  const w = inWindow(timeline.lanes[0].events, "2024-07-24T00:00", "2024-07-25T00:00");
  assert.equal(w.events.length, 1);
  assert.equal(w.earlier, 1);
  const rows = laneRows(timeline.lanes, "2024-07-24T00:00", "2024-07-25T00:00", false, new Map([["P.suction", "Suction"]]));
  assert.deepEqual(plain(rows.map(rowLabel)), ["Suction alarm · 1 earlier", "Log"]);
  const f = buildFigure(compressor, timeline, defaults(compressor), palette);
  const lane = f.layout[yaxes(f).find((k) => f.layout[k].type === "category")];
  assert.deepEqual(plain(lane.categoryarray), ["Log", "suction alarm · 1 earlier"]);
  assert.ok(f.layout.annotations.some((a) => a.text === "suction alarm · 1 earlier"));
  const marker = f.data.find((d) => d.mode === "markers");
  assert.deepEqual(plain(marker.customdata[0]), ["events", 1]);
  assert.equal(f.events[1].id, "l1");
});

test("the spec's marks are on the chart: a window shaded and named, a moment a labelled line", () => {
  const stepped = { ...compressor, marks: [...compressor.marks, { kind: "time", at: "2024-07-24T13:20", label: "Step change", tone: "danger" },
    { kind: "time", at: "2024-07-30T00:00", label: "Outside" }] };
  const f = buildFigure(stepped, null, defaults(stepped), palette);
  assert.equal(f.layout.shapes.filter((s) => s.type === "rect").length, 1);
  assert.ok(f.layout.annotations.some((a) => a.text === "12 h before the incident"));
  const step = f.layout.shapes.find((s) => s.type === "line" && s.x0 === "2024-07-24T13:20");
  assert.equal(step.line.color, "r");
  assert.ok(f.layout.annotations.some((a) => a.text === "Step change"));
  assert.ok(!f.layout.annotations.some((a) => a.text === "Outside"));
  assert.equal(f.layout.xaxis.hoverformat, HOVER_TIME);
});

test("a landed reading draws as steps with a marker per reading, its method in the hover", () => {
  const wt = series("wt", "bbl/d", { as: "step", methods: ["separator", null] });
  const s = spec([{ key: "tests", label: "Tests", show: true, axis: "auto", series: ["wt"] }], [wt]);
  const [d] = buildFigure(s, null, defaults(s), palette).data;
  assert.equal(d.mode, "lines+markers");
  assert.equal(d.line.shape, "hv");
  assert.deepEqual(plain(d.text), ["separator", ""]);
});

test("each series is named in its panel's key with its last value and unit; long names cut on a phone", () => {
  const s = spec([{ key: "v", label: "Vibration", show: true, axis: "auto", series: ["d"] }],
    [series("d", "°C", { label: "Journal Bearing Temperature · 20-K-2001", last: 101.13 })]);
  const f = buildFigure(s, null, defaults(s), palette);
  assert.ok(f.data[0].name.includes("<b>101</b> °C"), f.data[0].name);
  assert.equal(f.layout.legend.title.text, "<b>Vibration</b>");
  assert.ok(buildFigure(s, null, defaults(s), palette, true).data[0].name.startsWith("Journal Bearing Temperature ·…"));
  assert.equal(short("Journal Bearing Temperature · 20-K-2001", false), "Journal Bearing Temperature · 20-K-2001");
  assert.equal(reading(1234.5), "1,235");
});

test("markers too close to tell apart are one, counted; a pinned time reads each series and draws as A or B", () => {
  const at = (t) => ({ id: t, kind: "log_entry", label: "op", start: `2024-07-24T${t}`, end: null, text: t, detail: null });
  assert.deepEqual(plain(crowded([at("10:00"), at("10:10"), at("10:20"), at("14:00")], 30 * 60000).map((g) => g.length)), [3, 1]);
  const t = series("x", "psig");
  assert.equal(valueAt(t, "2024-07-24T10:05", 10), 1);
  assert.equal(valueAt(t, "2024-07-24T18:00", 10), null);
  const f = buildFigure(compressor, timeline, defaults(compressor), palette, false, ["2024-07-24T10:00", "2024-07-24T11:00"]);
  assert.deepEqual(plain(f.layout.annotations.filter((a) => a.text.startsWith("<b>") && a.text.length < 10).map((a) => a.text)), ["<b>A</b>", "<b>B</b>"]);
});

test("panels are shorter on a phone", () => {
  const desk = buildFigure(compressor, null, defaults(compressor), palette);
  const phone = buildFigure(compressor, null, defaults(compressor), palette, true);
  const px = (f) => (f.layout.yaxis.domain[1] - f.layout.yaxis.domain[0]) * (f.layout.height - f.layout.margin.t - f.layout.margin.b);
  assert.ok(px(phone) < px(desk) - 20, `${px(phone)} vs ${px(desk)}`);
});

test("what the window is compared with draws in the series' colour, thin and dashed, named once in the key", () => {
  const earlier = [["2024-07-24T10:00", 1.5], ["2024-07-24T11:00", 1.7]];
  const s = { ...compressor, series: compressor.series.map((x) => (x.key === "lube" ? x : { ...x,
    compare: [{ label: "Mar 3 incident", values: earlier }, { label: "Feb 9 incident", values: earlier }] })) };
  const f = buildFigure(s, null, defaults(s), palette);
  const overlays = f.data.filter((d) => d.legendgroup?.startsWith("compare:"));
  assert.equal(overlays.length, 8); // four series, two windows each
  assert.deepEqual(plain(overlays.slice(0, 2).map((d) => [d.line.color, d.line.dash])), [["a", "dash"], ["a", "dot"]]);
  assert.deepEqual(plain(overlays.filter((d) => d.showlegend).map((d) => d.name)), ["Mar 3 incident", "Feb 9 incident"]);
  assert.ok(overlays[0].hovertemplate.includes("Mar 3 incident · suction"));
});
