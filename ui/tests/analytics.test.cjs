const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const { runInNewContext } = require("node:vm");
const ts = require("typescript");
const exportsObject = {};
const code = ts.transpileModule(readFileSync(resolve(__dirname, "../src/analytics/dashboard.ts"), "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
}).outputText;
runInNewContext(code, {
  exports: exportsObject,
  require: (name) => {
    assert.equal(name, "./production-efficiency.json");
    return JSON.parse(readFileSync(resolve(__dirname, "../src/analytics/production-efficiency.json"), "utf8"));
  },
});
const { defaultSpec, validateSpec, parseSpec, loadSpec, dailyPoints, constraintFor, buildDashboard, thresholdColor, queryVariables, plotText, STORAGE_KEY, rendererRegistry } = exportsObject;
const plain = (value) => JSON.parse(JSON.stringify(value));
const point = (date, changes = {}) => ({
  date, dynamic_max_sm3d: 100, allocated_sm3d: 80, gap_sm3d: 20, efficiency_pct: 80,
  active_constraint_id: "a", binding_constraint_ids: ["a"], capacity_basis: "declared_limits",
  flags: [], source_ids: ["report-1"], ...changes,
});
const response = (points = []) => ({
  end: "2026-10-14", since: "2026-10-01", until: "2026-10-15", step: "day", unit: "Sm3/d",
  facility: { id: "plant", label: "Plant" }, source: "daily report", points,
  constraints: [{ id: "a", label: "Export", category: "export" }, { id: "b", label: "Compression", category: "equipment" }],
  summary: { efficiency_pct: null, allocated_sm3: 0, dynamic_max_sm3: 0, paired_days: 0 }, warnings: [],
});
test("default JSON has registered renderers, units, fixed daily query and optional gap", () => {
  const spec = defaultSpec();
  assert.deepEqual(Object.keys(rendererRegistry).sort(), ["constraint-band", "time-series"]);
  assert.equal(spec.panels[0].axes.y, "Sm3/d");
  assert.equal(spec.panels[1].axes.y, "category");
  assert.equal(spec.panels[0].gap.visible, false);
  assert.deepEqual(plain(queryVariables(spec, { end: "2026-10-14", facility: " plant " })), { end: "2026-10-14", ref: "plant" });
  assert.deepEqual(plain(queryVariables(spec, { end: "", facility: "" })), {});
  assert.throws(() => queryVariables(spec, { end: "2026-02-30", facility: "" }), /date/);
});
test("validation rejects arbitrary sources, HTML options, fields, units and malformed grids", () => {
  const invalid = [
    (s) => s.dataSource = "https://example.com",
    (s) => s.query.end = "2026-10-14",
    (s) => s.query.samples = 30,
    (s) => s.layout.columns = 2,
    (s) => s.layout.rowHeights = [3],
    (s) => s.layout.height = 1e9,
    (s) => s.panels[1].row = 0,
    (s) => s.panels[1].visualization = "iframe",
    (s) => s.panels[0].axes.y = "bbl/d",
    (s) => s.panels[0].series[0].field = "arbitrary",
    (s) => s.panels[0].series[0].color = "url(javascript:alert(1))",
    (s) => s.panels[0].hovertemplate = "<img src=x>",
    (s) => s.panels[0].thresholds.good = 79,
    (s) => s.panels[0].thresholds.colors.extra = "ok",
    (s) => s.panels[1].series = [],
  ];
  for (const change of invalid) { const s = defaultSpec(); change(s); assert.throws(() => validateSpec(s)); }
  assert.throws(() => parseSpec("{"));
  assert.throws(() => parseSpec(" ".repeat(32_769)), /32 KB/);
});
test("saved specs load, invalid/blocked storage safely restores default", () => {
  const spec = defaultSpec(); spec.title = "Saved dashboard";
  assert.equal(loadSpec({ getItem: (key) => { assert.equal(key, STORAGE_KEY); return JSON.stringify(spec); } }).spec.title, "Saved dashboard");
  for (const storage of [{ getItem: () => "bad JSON" }, { getItem: () => { throw Error("blocked"); } }]) {
    const loaded = loadSpec(storage);
    assert.equal(loaded.spec.title, "Production efficiency");
    assert.ok(loaded.error);
  }
});
test("daily transform produces exactly 14 inclusive UTC dates, preserving zero and explicit null gaps", () => {
  const d = response([point("2026-10-01", { allocated_sm3d: 0 }), point("2026-10-14", { dynamic_max_sm3d: NaN, allocated_sm3d: Infinity })]);
  const points = dailyPoints(d);
  assert.equal(points.length, 14);
  assert.equal(points[0].date, "2026-10-01");
  assert.equal(points[13].date, "2026-10-14");
  assert.equal(points[0].allocated_sm3d, 0);
  assert.equal(points[1].dynamic_max_sm3d, null);
  assert.equal(points[1].allocated_sm3d, null);
  assert.equal(points[1].capacity_basis, "missing");
  assert.deepEqual(plain(points[1].flags), ["missing_day"]);
  assert.equal(points[13].dynamic_max_sm3d, null);
  assert.equal(points[13].allocated_sm3d, null);
  assert.equal(dailyPoints({ ...d, end: "2026-03-05" })[0].date, "2026-02-20");
});
test("constraints label ties deterministically, preserve categories, and show unknown days explicitly", () => {
  const d = response();
  const tied = constraintFor(point("2026-10-01", { binding_constraint_ids: ["b", "a", "b"] }), d);
  assert.equal(tied.label, "Export [export] + Compression [equipment] (tied)");
  assert.equal(tied.key, constraintFor(point("2026-10-01", { binding_constraint_ids: ["a", "b"] }), d).key);
  for (const p of [
    point("2026-10-01", { dynamic_max_sm3d: null }),
    point("2026-10-01", { binding_constraint_ids: ["unregistered"] }),
    point("2026-10-01", { active_constraint_id: null, binding_constraint_ids: [] }),
    point("2026-10-01", { capacity_basis: "missing" }),
  ]) assert.equal(constraintFor(p, d).label, "Unknown");
  const fallback = constraintFor(point("2026-10-01", { active_constraint_id: null, binding_constraint_ids: [], capacity_basis: "reported_potential" }), d);
  assert.equal(fallback.label, "Reported potential · physical constraint unknown");
  assert.equal(fallback.unknown, true);
});
test("registry builds shared-axis linked hover/zoom, null line gaps, optional gap and categorical legend", () => {
  const spec = defaultSpec();
  const d = response([point("2026-10-01"), point("2026-10-02", { binding_constraint_ids: ["a", "b"] })]);
  const chart = buildDashboard(spec, d, (token) => token);
  assert.equal(chart.layout.hovermode, "x unified");
  assert.equal(chart.layout.hoversubplots, "axis");
  assert.deepEqual(plain(chart.layout.xaxis.range), ["2026-10-01", "2026-10-15"]);
  assert.ok(chart.data.every((trace) => trace.xaxis === "x"));
  assert.equal(chart.data[0].y[2], null);
  assert.equal(chart.data[0].connectgaps, false);
  assert.equal(chart.data[2].visible, "legendonly");
  const bands = chart.data.filter((t) => t.type === "bar");
  assert.equal(bands.length, 3);
  assert.ok(bands.some((t) => t.name === "Unknown"));
  assert.ok(bands.some((t) => t.name.includes("(tied)")));
  assert.ok(chart.data[0].text[2].includes("Dynamic maximum: Unknown"));
  assert.equal(chart.data[0].x[0], bands[0].x[0]);
  assert.equal(bands[0].width, 86_400_000);
  spec.panels[0].gap.visible = true;
  assert.equal(buildDashboard(spec, d, (t) => t).data[2].visible, undefined);
  spec.panels[0].row = 1; spec.panels[1].row = 0;
  assert.ok(buildDashboard(spec, d, (t) => t).data.filter((t) => t.type === "scatter").every((t) => t.yaxis === "y2"));
});
test("threshold boundaries and escaped Plotly strings are safe and unit aware", () => {
  const spec = defaultSpec(), t = spec.panels[0].thresholds;
  assert.equal(thresholdColor(null, t), "muted");
  assert.equal(thresholdColor(0, t), "danger");
  assert.equal(thresholdColor(80, t), "warn");
  assert.equal(thresholdColor(95, t), "ok");
  assert.equal(thresholdColor(125, t), "ok");
  spec.panels[0].title = "<b>not HTML</b>";
  const d = response([point("2026-10-01")]);
  d.constraints[0].label = '<img src=x onerror="alert(1)">';
  const chart = buildDashboard(spec, d, (t) => t);
  assert.ok(chart.layout.annotations[0].text.includes("&lt;b&gt;"));
  assert.ok(chart.data.find((t) => t.type === "bar").text[0].includes("&lt;img"));
  assert.ok(chart.data[0].text[0].includes("100 Sm3/d"));
  assert.equal(plotText("<>&"), "&lt;&gt;&amp;");
});
test("reported-potential fallback never invents constraints and above-100 efficiency is preserved", () => {
  const spec = defaultSpec();
  const d = response([point("2026-10-01", {
    capacity_basis: "reported_potential", active_constraint_id: null, binding_constraint_ids: [],
    dynamic_max_sm3d: 100, allocated_sm3d: 125, gap_sm3d: -25, efficiency_pct: 125,
  })]);
  d.constraints = [];
  const chart = buildDashboard(spec, d, (token) => token);
  const fallback = chart.data.find((trace) => trace.name === "Reported potential · physical constraint unknown");
  assert.ok(fallback);
  assert.equal(fallback.marker.color, spec.panels[1].unknownColor);
  assert.match(fallback.text[0], /Efficiency: 125 %/);
  assert.match(fallback.text[0], /Binding: Reported potential · physical constraint unknown/);
  assert.equal(chart.data[1].y[0], 125);
  assert.match(chart.data[1].text[0], /Efficiency: 125 %/);
  assert.equal(chart.layout.yaxis.range, undefined, "Rate axis is autoranged rather than capped at 100");
  assert.equal(chart.layout.yaxis.title.text, "Oil rate (Sm3/d)");
});
