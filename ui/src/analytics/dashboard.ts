import type { ProductionEfficiency, ProductionEfficiencyPoint } from "../api";
import initialSpec from "./production-efficiency.json";

export const STORAGE_KEY = "fuzer.analytics.production-efficiency.v1";
const COLOR_TOKENS = ["chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "chart-6", "ok", "warn", "danger", "muted"] as const;
type Color = typeof COLOR_TOKENS[number];
type SeriesField = "dynamic_max_sm3d" | "allocated_sm3d" | "gap_sm3d";
interface Series { field: SeriesField; label: string; color: Color }
interface PanelBase { id: string; title: string; row: number; axes: { x: "date"; y: "Sm3/d" | "category" } }
export interface TimeSeriesPanel extends PanelBase {
  visualization: "time-series";
  series: Series[];
  gap?: Series & { visible: boolean };
  thresholds?: {
    field: "efficiency_pct"; warning: number; good: number;
    colors: { good: Color; warning: Color; low: Color; unknown: Color };
  };
}
export interface ConstraintPanel extends PanelBase {
  visualization: "constraint-band"; colors: Color[]; unknownColor: Color;
}
export type Panel = TimeSeriesPanel | ConstraintPanel;
export interface DashboardSpec {
  version: 1;
  title: string;
  dataSource: "production-efficiency";
  query: { end: "$end"; ref: "$facility"; step: "day"; samples: 14 };
  layout: { columns: 1; rowHeights: number[]; gap: number; height: number };
  panels: Panel[];
}
type Obj = Record<string, unknown>;
function object(value: unknown, keys: string[], at: string): Obj {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw Error(`${at} must be an object`);
  const obj = value as Obj;
  if (Object.keys(obj).some((key) => !keys.includes(key))) throw Error(`${at} contains an unsupported property`);
  return obj;
}
function check(ok: unknown, message: string): asserts ok { if (!ok) throw Error(message); }
const text = (value: unknown) => typeof value === "string" && value.trim().length > 0 && value.length <= 160;
const color = (value: unknown) => COLOR_TOKENS.includes(value as Color);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

/** Whitelist the entire spec: imported JSON cannot supply URLs, Plotly HTML or arbitrary options. */
export function validateSpec(value: unknown): DashboardSpec {
  const d = object(value, ["version", "title", "dataSource", "query", "layout", "panels"], "Dashboard");
  check(d.version === 1 && text(d.title), "Use version 1 and a nonempty title (up to 160 characters)");
  check(d.dataSource === "production-efficiency", "Only the production-efficiency data source is registered");
  const q = object(d.query, ["end", "ref", "step", "samples"], "Query");
  check(q.end === "$end" && q.ref === "$facility" && q.step === "day" && q.samples === 14, "Query must use $end, $facility and 14 daily samples");
  const l = object(d.layout, ["columns", "rowHeights", "gap", "height"], "Layout");
  check(l.columns === 1 && Array.isArray(l.rowHeights) && l.rowHeights.length === 2 &&
    l.rowHeights.every((n) => finite(n) && n >= 1 && n <= 5), "Layout must have one column and two row weights (1–5)");
  check(finite(l.gap) && l.gap >= 0.08 && l.gap <= 0.25 && finite(l.height) && l.height >= 450 && l.height <= 900, "Layout gap must be 0.08–0.25; height must be 450–900");
  check(Array.isArray(d.panels) && d.panels.length === 2, "Use exactly two panels");
  const ids = new Set(), rows = new Set(), renderers = new Set();
  for (const value of d.panels) {
    const p = object(value, ["id", "title", "row", "axes", "visualization", "series", "gap", "thresholds", "colors", "unknownColor"], "Panel");
    check(typeof p.id === "string" && /^[a-z][a-z0-9-]{0,39}$/.test(p.id) && !ids.has(p.id), "Panel IDs must be unique lowercase identifiers");
    check(text(p.title) && (p.row === 0 || p.row === 1) && !rows.has(p.row), "Panels need titles and distinct rows 0 and 1");
    ids.add(p.id); rows.add(p.row);
    const a = object(p.axes, ["x", "y"], "Axes");
    check(a.x === "date", "The shared x axis must be date");
    check(p.visualization === "time-series" || p.visualization === "constraint-band", "Unknown visualization");
    check(!renderers.has(p.visualization), "Use one time-series and one constraint-band panel");
    renderers.add(p.visualization);
    if (p.visualization === "time-series") {
      check(a.y === "Sm3/d", "Production y axis must use Sm3/d");
      check(p.colors === undefined && p.unknownColor === undefined, "Band options do not belong on a time series");
      check(Array.isArray(p.series) && p.series.length === 2, "Time series requires dynamic maximum and allocation");
      const fields = new Set();
      for (const entry of p.series) {
        const s = object(entry, ["field", "label", "color"], "Series");
        check((s.field === "dynamic_max_sm3d" || s.field === "allocated_sm3d") && !fields.has(s.field) && text(s.label) && color(s.color), "Use distinct maximum/allocation fields, labels and registered colors");
        fields.add(s.field);
      }
      if (p.gap !== undefined) {
        const g = object(p.gap, ["field", "label", "color", "visible"], "Gap");
        check(g.field === "gap_sm3d" && text(g.label) && color(g.color) && typeof g.visible === "boolean", "Gap requires gap_sm3d, label, color and visible");
      }
      if (p.thresholds !== undefined) {
        const t = object(p.thresholds, ["field", "warning", "good", "colors"], "Thresholds");
        check(t.field === "efficiency_pct" && finite(t.warning) && finite(t.good) && t.warning >= 0 && t.good <= 100 && t.warning < t.good, "Efficiency thresholds must satisfy 0 ≤ warning < good ≤ 100");
        const c = object(t.colors, ["good", "warning", "low", "unknown"], "Threshold colors");
        check(["good", "warning", "low", "unknown"].every((k) => color(c[k])), "Use registered threshold colors");
      }
    } else {
      check(a.y === "category", "Constraint y axis must be category");
      check(p.series === undefined && p.gap === undefined && p.thresholds === undefined, "Time-series options do not belong on a band");
      check(Array.isArray(p.colors) && p.colors.length > 0 && p.colors.length <= 10 && p.colors.every(color) && color(p.unknownColor), "Band requires a registered palette and unknownColor");
    }
  }
  return JSON.parse(JSON.stringify(d)) as DashboardSpec;
}
export const defaultSpec = (): DashboardSpec => validateSpec(initialSpec);
export function parseSpec(json: string): DashboardSpec {
  check(json.length <= 32_768, "Dashboard JSON must be at most 32 KB");
  return validateSpec(JSON.parse(json));
}
export function loadSpec(storage: Pick<Storage, "getItem">): { spec: DashboardSpec; error?: string } {
  try { const saved = storage.getItem(STORAGE_KEY); return { spec: saved ? parseSpec(saved) : defaultSpec() }; }
  catch { return { spec: defaultSpec(), error: "Saved dashboard unavailable or invalid; using the default." }; }
}
export function queryVariables(spec: DashboardSpec, variables: { end: string; facility: string }) {
  validateSpec(spec);
  check(!variables.end || isDate(variables.end), "End must be a valid YYYY-MM-DD date");
  return { end: variables.end || undefined, ref: variables.facility.trim() || undefined };
}
export function isDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
}
const DAY = 86_400_000;
export function dailyPoints(response: ProductionEfficiency): ProductionEfficiencyPoint[] {
  check(isDate(response.end), "Analytics response has an invalid end date");
  const byDate = new Map(response.points.map((point) => [point.date, point]));
  return Array.from({ length: 14 }, (_, index) => {
    const date = new Date(Date.parse(response.end) - (13 - index) * DAY).toISOString().slice(0, 10);
    const p = byDate.get(date);
    return {
      date, dynamic_max_sm3d: finite(p?.dynamic_max_sm3d) ? p.dynamic_max_sm3d : null,
      allocated_sm3d: finite(p?.allocated_sm3d) ? p.allocated_sm3d : null,
      gap_sm3d: finite(p?.gap_sm3d) ? p.gap_sm3d : null,
      efficiency_pct: finite(p?.efficiency_pct) ? p.efficiency_pct : null,
      active_constraint_id: p?.active_constraint_id ?? null,
      binding_constraint_ids: p?.binding_constraint_ids ?? [],
      capacity_basis: p?.capacity_basis ?? "missing",
      flags: p?.flags ?? ["missing_day"], source_ids: p?.source_ids ?? [],
    };
  });
}
export function constraintFor(point: ProductionEfficiencyPoint, response: ProductionEfficiency) {
  const ids = [...new Set([...point.binding_constraint_ids, ...(point.active_constraint_id ? [point.active_constraint_id] : [])])].sort();
  const lookup = new Map(response.constraints.map((c) => [c.id, c]));
  if (point.dynamic_max_sm3d === null || point.capacity_basis === "missing" || ids.some((id) => !lookup.has(id))) {
    return { key: "unknown", label: "Unknown", unknown: true };
  }
  if (point.capacity_basis === "reported_potential") {
    return { key: "unknown-potential", label: "Reported potential · physical constraint unknown", unknown: true };
  }
  if (ids.length === 0) return { key: "unknown", label: "Unknown", unknown: true };
  return {
    key: JSON.stringify(ids),
    label: ids.map((id) => { const c = lookup.get(id)!; return `${c.label} [${c.category}]`; }).join(" + ") + (ids.length > 1 ? " (tied)" : ""),
    unknown: false,
  };
}
// Plotly understands a small HTML subset. Escape every API/spec string before passing it to Plotly.
export const plotText = (value: string) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
const valueText = (value: number | null, unit: string) => value === null ? "Unknown" : `${value.toLocaleString("en", { maximumFractionDigits: 1 })} ${unit}`;
type PlotObject = Record<string, any>;
type RenderContext = {
  response: ProductionEfficiency; points: ProductionEfficiencyPoint[]; yaxis: string;
  color: (token: Color) => string;
};
function pointHover(p: ProductionEfficiencyPoint, response: ProductionEfficiency) {
  return [
    p.date, `Dynamic maximum: ${valueText(p.dynamic_max_sm3d, "Sm3/d")}`,
    `Allocated: ${valueText(p.allocated_sm3d, "Sm3/d")}`,
    `Gap: ${valueText(p.gap_sm3d, "Sm3/d")}`,
    `Efficiency: ${valueText(p.efficiency_pct, "%")}`,
    `Binding: ${constraintFor(p, response).label}`,
    `Capacity basis: ${p.capacity_basis.replaceAll("_", " ")}`,
    `Flags: ${p.flags.join(", ") || "none"}`, `Sources: ${p.source_ids.join(", ") || "none"}`,
  ].map(plotText).join("<br>");
}
export function thresholdColor(value: number | null, thresholds: NonNullable<TimeSeriesPanel["thresholds"]>): Color {
  return value === null ? thresholds.colors.unknown : value >= thresholds.good ? thresholds.colors.good :
    value >= thresholds.warning ? thresholds.colors.warning : thresholds.colors.low;
}
function timeSeries(panel: TimeSeriesPanel, c: RenderContext): PlotObject[] {
  const series = [...panel.series, ...(panel.gap ? [panel.gap] : [])];
  return series.map((s) => ({
    type: "scatter", mode: "lines+markers", name: plotText(`${s.label} (Sm3/d)`),
    x: c.points.map((p) => p.date), y: c.points.map((p) => p[s.field]), xaxis: "x", yaxis: c.yaxis,
    connectgaps: false, line: { color: c.color(s.color), width: 2, ...(s.field === "gap_sm3d" ? { dash: "dot" } : {}) },
    marker: { size: 6, color: c.points.map((p) => c.color(s.field === "allocated_sm3d" && panel.thresholds ? thresholdColor(p.efficiency_pct, panel.thresholds) : s.color)) },
    text: c.points.map((p) => `${plotText(s.label)}: ${plotText(valueText(p[s.field], "Sm3/d"))}${s.field === "allocated_sm3d" ? `<br>Efficiency: ${plotText(valueText(p.efficiency_pct, "%"))}` : ""}`),
    hovertemplate: "%{text}<extra></extra>",
    ...(s.field === "gap_sm3d" && panel.gap && !panel.gap.visible ? { visible: "legendonly" } : {}),
  }));
}
function constraintBand(panel: ConstraintPanel, c: RenderContext): PlotObject[] {
  const categories = c.points.map((p) => constraintFor(p, c.response));
  const unique = [...new Map(categories.map((entry) => [entry.key, entry])).values()];
  // One bar series per category gives a categorical legend and exact full-day bands, including gaps.
  return unique.map((category, index) => ({
    type: "bar", orientation: "v", name: plotText(category.label), xaxis: "x", yaxis: c.yaxis,
    x: c.points.map((p) => p.date),
    y: categories.map((entry) => entry.key === category.key ? 1 : null),
    width: DAY, offset: 0, base: 0,
    marker: { color: c.color(category.unknown ? panel.unknownColor : panel.colors[index % panel.colors.length]), line: { width: 1, color: c.color(panel.unknownColor) } },
    text: c.points.map((p) => pointHover(p, c.response)),
    hovertemplate: "%{text}<extra></extra>", legendgroup: `constraint-${category.key}`,
  }));
}
export const rendererRegistry = { "time-series": timeSeries, "constraint-band": constraintBand };
export function buildDashboard(spec: DashboardSpec, response: ProductionEfficiency, colorValue: (token: Color) => string): { data: PlotObject[]; layout: PlotObject } {
  validateSpec(spec);
  const points = dailyPoints(response);
  const total = spec.layout.rowHeights.reduce((a, b) => a + b, 0);
  const available = 1 - spec.layout.gap;
  const data: PlotObject[] = [], axes: PlotObject = {}, annotations: PlotObject[] = [];
  let top = 1;
  for (const panel of [...spec.panels].sort((a, b) => a.row - b.row)) {
    const bottom = top - available * spec.layout.rowHeights[panel.row] / total;
    const key = panel.row === 0 ? "yaxis" : `yaxis${panel.row + 1}`;
    const yaxis = panel.row === 0 ? "y" : `y${panel.row + 1}`;
    axes[key] = { domain: [bottom, top], anchor: "x", title: { text: panel.axes.y === "Sm3/d" ? "Oil rate (Sm3/d)" : "" },
      ...(panel.visualization === "constraint-band" ? { range: [0, 1], showticklabels: false, showgrid: false, zeroline: false, fixedrange: true } : { rangemode: "tozero" }) };
    annotations.push({ x: 0, y: top, xref: "paper", yref: "paper", yshift: 20, text: plotText(panel.title), showarrow: false, xanchor: "left", font: { size: 13 } });
    const context = { response, points, yaxis, color: colorValue };
    data.push(...(panel.visualization === "time-series" ? rendererRegistry[panel.visualization](panel, context) : rendererRegistry[panel.visualization](panel, context)));
    top = bottom - spec.layout.gap;
  }
  return { data, layout: {
    ...axes, annotations, height: spec.layout.height, margin: { t: 45, r: 20, b: 50, l: 75 },
    grid: { rows: 2, columns: 1, subplots: [["xy"], ["xy2"]], roworder: "top to bottom" },
    xaxis: { type: "date", anchor: "y2", title: { text: "Production day (UTC)" },
      range: [points[0].date, new Date(Date.parse(response.end) + DAY).toISOString().slice(0, 10)],
      tickformat: "%d %b", dtick: DAY, showspikes: true, spikemode: "across", spikesnap: "cursor", spikecolor: colorValue("muted"), spikethickness: 1 },
    hovermode: "x unified", hoversubplots: "axis", barmode: "overlay",
    legend: { orientation: "h", y: -0.18, x: 0, font: { size: 11 } },
    uirevision: `${response.facility.id}:${response.end}:${JSON.stringify(spec)}`,
  } };
}
