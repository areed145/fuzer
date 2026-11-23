// The one chart over time, as a Plotly figure: a trend spec's panels stacked on one time axis, its marks on the chart
// and the unit's event lanes beneath. Pure: it takes the spec, the timeline, what is switched on and the page's colours.
// The readability rules live here, so no page can get them wrong:
// - a series' colour runs on across panels, so the first line of every panel is not the same blue;
// - a panel carries one unit, or a pair of series on two scales (speed and power); more units start a panel of their own;
// - a series' limits draw when it is the one the chart is about, alone in its panel, or on a scale of its own; a limit
//   far from the line is named at the panel's edge instead of flattening the line;
// - only what falls in the window is drawn: an event before it is counted on its lane's name, not drawn clipped;
// - times read as the rest of Fuzer reads them.
import type { TimelineData, TimelineEvent, TimelineLane, TrendLimit, TrendMark, TrendSeries, TrendSpec } from "./spec";

export interface Palette {
  series: string[]; lanes: Record<string, string>; limit: string; danger: string; shade: string; text: string; muted: string;
}
/** A panel as switched on: its series, in the spec's order. */
export interface Panel { key: string; label: string; series: TrendSeries[] }
/** A row of the lanes as drawn: a lane, or one alarming point of the alarms lane. */
export interface LaneRow { key: string; lane: string; label: string; events: TimelineEvent[]; earlier: number; later: number }

export const LANE_SHORT: Record<string, string> = {
  alarms: "Alarms", log: "Log", cases: "Cases", maintenance: "Work orders", downtime: "Downtime",
};
const PANEL_PX = 180;
const PANEL_PX_PHONE = 140;
const ALARM_ROWS = 8;
const LANE_PX = 34;
const GAP_PX = 30;
const KEY_ROW_PX = 20;
/** One dash pattern per compared window, so three past incidents read apart in one colour. */
const COMPARE_DASH = ["dash", "dot", "dashdot"];
/** Plotly's time format for the hover and the spike label: "Nov 22, 1:20 PM", as `when` reads. */
export const HOVER_TIME = "%b %-d, %-I:%M %p";

const local = (s: string) => Date.parse(s.length === 10 ? `${s}T00:00Z` : `${s.slice(0, 16)}Z`);

export const byKey = (spec: TrendSpec): Map<string, TrendSeries> => new Map(spec.series.map((s) => [s.key, s]));

/** What is switched on at first: the series the spec shows, and every lane it lists switched on. */
export function defaults(spec: TrendSpec): Set<string> {
  return new Set([...spec.series.filter((s) => s.show).map((s) => s.key),
    ...spec.events.filter((e) => e.show).map((e) => `lane:${e.lane}`)]);
}

/** What the chart opens on: the spec's focus with every lane, else its defaults. */
export function opening(spec: TrendSpec): Set<string> {
  if (!spec.focus?.length) return defaults(spec);
  return new Set([...spec.focus, ...spec.events.filter((e) => e.show).map((e) => `lane:${e.lane}`)]);
}

/** What is switched on, as it goes in a link: what differs from the defaults (`show`, `hide`), comma-separated. */
export function toQuery(spec: TrendSpec, on: Set<string>): { show?: string; hide?: string } {
  const base = defaults(spec);
  const show = [...on].filter((k) => !base.has(k));
  const hide = [...base].filter((k) => !on.has(k));
  return { show: show.join(",") || undefined, hide: hide.join(",") || undefined };
}

/** What a link switched on: the defaults, with its `show` added and its `hide` taken away. Keys the spec no longer
 * has are dropped, so an old link still opens. */
export function fromQuery(spec: TrendSpec, show: string | null, hide: string | null): Set<string> {
  const known = new Set([...spec.series.map((s) => s.key), ...Object.keys(LANE_SHORT).map((k) => `lane:${k}`)]);
  const on = defaults(spec);
  (show ?? "").split(",").filter((k) => known.has(k)).forEach((k) => on.add(k));
  (hide ?? "").split(",").forEach((k) => on.delete(k));
  return on;
}

/** The panels to draw, each with only its switched-on series. */
export function panels(spec: TrendSpec, on: Set<string>): Panel[] {
  const s = byKey(spec);
  return spec.panels.map((p) => ({ key: p.key, label: p.label, series: p.series.filter((k) => on.has(k)).map((k) => s.get(k)!).filter(Boolean) }))
    .filter((p) => p.series.length);
}

/** Each switched-on series' colour, running on across panels in the spec's order: within a panel no two series share
 * one, and across panels a colour comes back only past the palette's length. */
export function colours(spec: TrendSpec, on: Set<string>, palette: Palette): Map<string, string> {
  const out = new Map<string, string>();
  let i = 0;
  for (const p of spec.panels) for (const k of p.series) if (on.has(k)) out.set(k, palette.series[i++ % palette.series.length]);
  return out;
}

/** A series' swing, never zero, so a flat line still has a size to compare. */
const swing = (lo: number, hi: number) => Math.max(hi - lo, Math.abs((hi + lo) / 2) * 0.02, 1e-9);

/** Series in one unit that sit at very different levels get scales of their own: wellhead pressure at 166 psig on the
 * same axis as casing pressure at 1,045 is a flat line. A series joins a scale when the scale's span with it stays
 * within four times the larger of the two swings; otherwise it starts the next scale. */
export function scaled(groups: Panel[]): Panel[] {
  return groups.map((g) => {
    const scales = new Map<string, { lo: number; hi: number }[]>();
    const series = g.series.map((t) => {
      if (!Number.isFinite(t.min) || !Number.isFinite(t.max)) return t;
      const mine = scales.get(t.axis) ?? [];
      scales.set(t.axis, mine);
      let k = mine.findIndex((c) => swing(Math.min(c.lo, t.min), Math.max(c.hi, t.max)) <= 4 * Math.max(swing(c.lo, c.hi), swing(t.min, t.max)));
      if (k < 0) k = mine.push({ lo: t.min, hi: t.max }) - 1;
      else mine[k] = { lo: Math.min(mine[k].lo, t.min), hi: Math.max(mine[k].hi, t.max) };
      return k ? { ...t, axis: `${t.axis}#${k}` } : t;
    });
    return { ...g, series };
  });
}

/** A panel as drawn: one scale a panel, except a pair of series on two scales (speed and power, two pressures at
 * different levels), which share one on a wide screen. Past its first scale, a panel continues under itself. On a
 * phone, one scale a panel always. */
export function drawn(groups: Panel[], compact = false): Panel[] {
  return groups.flatMap((g) => {
    const units = [...new Set(g.series.map((t) => t.axis))];
    const per = !compact && units.length === 2 && g.series.length === 2 ? 2 : 1;
    const out: Panel[] = [];
    for (let i = 0; i < units.length; i += per) {
      const mine = new Set(units.slice(i, i + per));
      out.push({ ...g, key: i ? `${g.key}:${i / per}` : g.key, label: i ? `${g.label}, continued` : g.label,
                 series: g.series.filter((t) => mine.has(t.axis)) });
    }
    return out;
  });
}

/** A value as a label reads it: three significant figures, no exponent below a million. */
export function reading(v: number): string {
  if (!Number.isFinite(v)) return "";
  const a = Math.abs(v);
  return a >= 1000 ? Math.round(v).toLocaleString("en-US") : a >= 1 ? String(Number(v.toPrecision(3))) : String(Number(v.toPrecision(2)));
}

/** A key entry's name fits a phone's width: a long name is cut with an ellipsis, and the hover keeps it whole. */
export function short(s: string, compact: boolean, max = 30): string {
  return !compact || s.length <= max ? s : `${s.slice(0, max - 1).trimEnd()}…`;
}

const LIMIT_WORD: Record<TrendLimit["type"], string> = { hihi: "HiHi", hi: "Hi", lo: "Lo", lolo: "LoLo" };
/** A limit as its edge label reads: "HiHi 275", "learned Hi 260", "IOW high 300". */
export function limitName(l: TrendLimit): string {
  if (l.basis === "iow") return `${l.iow ?? "IOW"} ${l.type.startsWith("hi") ? "high" : "low"} ${reading(l.value)}`;
  return `${l.basis === "learned" ? "learned " : ""}${LIMIT_WORD[l.type]} ${reading(l.value)}`;
}

/** A series' limits, split: near enough to its line to draw without flattening it, and the rest. */
export function nearLimits(t: TrendSeries): { near: TrendLimit[]; far: TrendLimit[] } {
  const mid = (t.min + t.max) / 2;
  const reach = swing(t.min, t.max) * 1.5;
  const near = (t.limits ?? []).filter((l) => Math.abs(l.value - mid) <= reach);
  return { near, far: (t.limits ?? []).filter((l) => !near.includes(l)) };
}

/** Rows a panel's key needs at this width: its title and each series' name and last value, wrapped. */
function keyRows(g: Panel, compact: boolean): number {
  if (compact) return 1 + g.series.length;
  const chars = g.label.length + g.series.reduce((n, t) => n + t.label.length + 12, 0);
  return Math.max(1, Math.ceil((chars * 6.6 + g.series.length * 34) / 860));
}

/** How urgent an alarm reads: its priority where the record gives one, else its limit (hihi and lolo before hi and lo). */
function urgency(e: TimelineEvent): number {
  const level = ({ critical: 0, emergency: 0, urgent: 1, high: 1, medium: 2, low: 3 } as Record<string, number>)[(e.detail ?? "").toLowerCase()];
  if (level !== undefined) return level;
  const type = (e.text ?? "").split(" ")[0].toLowerCase();
  return type === "hihi" || type === "lolo" ? 0.5 : type === "hi" || type === "lo" ? 1.5 : 4;
}

/** A point as a person reads it when no series names it: its tag, without the historian's prefix and suffix. */
export function pointTag(point: string): string {
  return point.replace(/^[^:]*:/, "").replace(/\.(PV|CV|OUT|SP|MV)$/i, "");
}

/** Events in the window, and how many fall before and after it. A span in the window by any part is in it. */
export function inWindow(events: TimelineEvent[], start: string, end: string): { events: TimelineEvent[]; earlier: number; later: number } {
  const a = local(start);
  const b = local(end);
  let earlier = 0;
  let later = 0;
  const kept = events.filter((e) => {
    const s = local(e.start);
    const f = e.end ? local(e.end) : s;
    if (f < a) earlier += 1;
    else if (s > b) later += 1;
    else return true;
    return false;
  });
  return { events: kept, earlier, later };
}

/** The lanes as rows: every lane one row, except alarms, one row per point, the most urgent first. Past `ALARM_ROWS`
 * points the rest share a row that says how many. Only events in [start, end] are kept; the rest are counted. */
export function laneRows(lanes: TimelineLane[], start: string, end: string, compact = false,
                         pointNames: Map<string, string> = new Map()): LaneRow[] {
  return lanes.flatMap((l) => {
    const name = LANE_SHORT[l.key] ?? l.label;
    const w = inWindow(l.events, start, end);
    if (l.key !== "alarms") return w.events.length || w.earlier || w.later ? [{ key: l.key, lane: l.key, label: name, ...w }] : [];
    const by = new Map<string, TimelineEvent[]>();
    for (const e of w.events) by.set(e.label ?? e.id ?? "?", [...(by.get(e.label ?? e.id ?? "?") ?? []), e]);
    const points = [...by.entries()].sort(([a, x], [b, y]) =>
      Math.min(...x.map(urgency)) - Math.min(...y.map(urgency)) || x[0].start.localeCompare(y[0].start) || a.localeCompare(b));
    const own = points.length > ALARM_ROWS ? points.slice(0, ALARM_ROWS - 1) : points;
    const rest = points.slice(own.length);
    const label = (point: string) => short(`${pointNames.get(point) ?? pointTag(point)} alarm`, compact, 28);
    const rows: LaneRow[] = [
      ...own.map(([point, events]) => ({ key: `alarms:${point}`, lane: "alarms", label: label(point), events, earlier: 0, later: 0 })),
      ...(rest.length ? [{ key: "alarms:more", lane: "alarms", label: `${rest.length} more alarming points`,
                           events: rest.flatMap(([, events]) => events), earlier: 0, later: 0 }] : []),
    ];
    // alarms before or after the window: said once, on the lane's first row, or a row of their own when none is in it
    if (w.earlier || w.later) {
      if (rows.length) Object.assign(rows[0], { earlier: w.earlier, later: w.later });
      else rows.push({ key: "alarms", lane: "alarms", label: name, events: [], earlier: w.earlier, later: w.later });
    }
    return rows;
  });
}

/** A row's name with what lies outside the window: "Suction flow alarm · 2 earlier". */
export function rowLabel(r: LaneRow): string {
  return [r.label, r.earlier ? `${r.earlier} earlier` : "", r.later ? `${r.later} later` : ""].filter(Boolean).join(" · ");
}

/** Markers too close to tell apart at this width, as groups: each starts at its first marker and takes every marker
 * within `gap` ms of it. */
export function crowded(events: TimelineEvent[], gap: number): TimelineEvent[][] {
  const out: TimelineEvent[][] = [];
  for (const e of [...events].sort((a, b) => a.start.localeCompare(b.start))) {
    const last = out[out.length - 1];
    if (last && local(e.start) - local(last[0].start) <= gap) last.push(e);
    else out.push([e]);
  }
  return out;
}

/** A series' value at a time: the reading nearest it, if one is within two steps. */
export function valueAt(t: TrendSeries, at: string, stepMinutes: number): number | null {
  const want = local(at);
  let best: [string, number] | null = null;
  for (const v of t.values) if (!best || Math.abs(local(v[0]) - want) < Math.abs(local(best[0]) - want)) best = v;
  return best && Math.abs(local(best[0]) - want) <= 2 * stepMinutes * 60000 ? best[1] : null;
}

const MARK_TONE = (palette: Palette, tone?: TrendMark["tone"]) =>
  tone === "danger" ? palette.danger : tone === "accent" ? palette.lanes.cases ?? palette.text : palette.limit;

/** The chart. `pins` are the times pinned on it, drawn as lines marked A and B. The figure carries `events`, every
 * lane event once: a lane marker's customdata is ["events", ...its indexes in that list], so a click can say what it
 * was without leaving the page. */
export function buildFigure(spec: TrendSpec, timeline: TimelineData | null, on: Set<string>, palette: Palette,
                            compact = false, pins: string[] = []) {
  const shown = drawn(scaled(panels(spec, on)), compact);
  const focus = new Set(spec.focus ?? []);
  const panelPx = compact ? PANEL_PX_PHONE : PANEL_PX;
  const { start, end } = spec.time;
  const pointNames = new Map(spec.series.map((s) => [s.point, s.label]));
  const lanes = laneRows((timeline?.lanes ?? []).filter((l) => l.events.length && on.has(`lane:${l.key}`)), start, end, compact, pointNames);
  const events: TimelineEvent[] = [];
  const indexes = (es: TimelineEvent[]) => ["events", ...es.map((e) => events.push(e) - 1)];
  const gap = Math.max(1, local(end) - local(start)) / (compact ? 24 : 60);
  const colour = colours(spec, on, palette);
  const keyPx = shown.map((g) => 8 + KEY_ROW_PX * keyRows(g, compact));
  const lanesPx = lanes.length ? lanes.length * LANE_PX + 12 : 0;
  const plotPx = shown.length * panelPx + keyPx.reduce((a, b) => a + b, 0) - (keyPx[0] ?? 0) + (lanesPx ? GAP_PX + lanesPx : 0);
  const frac = (px: number) => px / Math.max(1, plotPx);
  const data: Record<string, unknown>[] = [];
  const shapes: Record<string, unknown>[] = [];
  const annotations: Record<string, unknown>[] = [];
  const layout: Record<string, any> = {};
  let n = 0;
  let top = 1;
  let lowest = "y";
  shown.forEach((g, k) => {
    if (k) top -= frac(keyPx[k]);
    const bottom = Math.max(0, top - frac(panelPx));
    const legend = k ? `legend${k + 1}` : "legend";
    layout[legend] = { visible: true, x: 0, y: top, xref: "paper", yref: "paper", xanchor: "left", yanchor: "bottom", orientation: compact ? "v" : "h",
                       bgcolor: "rgba(0,0,0,0)", font: { size: 11, color: palette.text }, itemclick: false,
                       itemdoubleclick: false, itemwidth: 30, tracegroupgap: 0,
                       title: { text: `<b>${g.label}</b>`, side: compact ? "top" : "left", font: { size: 12, color: palette.text } } };
    const domain = [bottom, top];
    const units = [...new Set(g.series.map((t) => t.axis))];
    const first = n + 1;
    units.forEach((u, j) => {
      n += 1;
      const axis = n === 1 ? "yaxis" : `yaxis${n}`;
      const ref = n === 1 ? "y" : `y${n}`;
      const series = g.series.filter((t) => t.axis === u);
      const label = series[0].unit;
      // A position or opening in % reads on its whole 0 to 100 scale, so a shut valve is a line on the floor.
      const whole = label === "%" && series.every((t) => t.min >= 0 && t.max <= 100) ? { range: [-2, 102], autorange: false } : {};
      // A scale with one series takes its colour when the panel has two scales, so each line is read on its own.
      const own = series.length === 1 && units.length > 1;
      const tint = own ? colour.get(series[0].key) : undefined;
      const title = { text: label, font: { size: 11, ...(tint ? { color: tint } : {}) } };
      const ticks = tint ? { tickfont: { color: tint } } : {};
      layout[axis] = j === 0
        ? { domain, anchor: "x", title, fixedrange: true, zeroline: false, ...ticks, ...whole }
        : { overlaying: first === 1 ? "y" : `y${first}`, side: "right", fixedrange: true, zeroline: false, showgrid: false,
            tickmode: "auto", nticks: 5, title, anchor: "x", ...ticks, ...whole };
      if (j === 0) lowest = ref;
      for (const t of series) {
        const step = t.as === "step";
        const marked = step || t.as === "points";
        data.push({
          type: "scatter", mode: marked ? "lines+markers" : "lines", x: t.values.map(([at]) => at),
          y: t.values.map(([, v]) => v), yaxis: ref, legend, showlegend: true,
          name: `${short(t.label, compact)} <b>${reading(t.last)}</b>${t.unit ? ` ${t.unit}` : ""}`,
          line: { width: t.as === "points" ? 1 : focus.has(t.key) ? 2.2 : 1.6, color: colour.get(t.key),
                  ...(step ? { shape: "hv", dash: "dot" } : {}) },
          ...(marked ? { marker: { size: 7, color: colour.get(t.key) }, text: (t.methods ?? []).map((m) => m ?? "") } : {}),
          hovertemplate: `%{y:.4~g} ${t.unit}${marked ? " %{text}" : ""}<extra>${t.label}</extra>`,
        });
        // What it is compared with: the same colour, thin and dashed, a dash pattern per window, on the same scale.
        (t.compare ?? []).forEach((c, i) => data.push({
          type: "scatter", mode: "lines", x: c.values.map(([at]) => at), y: c.values.map(([, v]) => v), yaxis: ref,
          legend, showlegend: k === 0 && j === 0 && t === series[0], legendgroup: `compare:${c.label}`, name: c.label,
          line: { width: 1.2, color: colour.get(t.key), dash: COMPARE_DASH[i % COMPARE_DASH.length] }, opacity: 0.6,
          hovertemplate: `%{y:.4~g} ${t.unit}<extra>${c.label} · ${t.label}</extra>`,
        }));
      }
      // Limits draw where they can't be mistaken for another line's: the series the chart is about, the panel's one
      // series, or a series on a scale of its own. Near ones are lines; far ones are named at the panel's edge.
      const limited = series.filter((t) => focus.has(t.key) || g.series.length === 1 || own);
      for (const t of limited) {
        const { near, far } = nearLimits(t);
        const urgent = (l: TrendLimit) => l.type === "hihi" || l.type === "lolo";
        for (const l of near) {
          shapes.push({ type: "line", xref: "x", yref: ref, x0: start, x1: end, y0: l.value, y1: l.value, layer: "below",
                        line: { color: l.basis === "learned" ? palette.muted : urgent(l) ? palette.danger : palette.limit,
                                width: 1, dash: l.basis === "learned" ? "dot" : "dash" } });
          annotations.push({ text: limitName(l), xref: "paper", yref: ref, x: 1, y: l.value, xanchor: "right", yanchor: "bottom",
                             showarrow: false, font: { size: 10, color: urgent(l) ? palette.danger : palette.limit } });
        }
        const above = far.filter((l) => l.value > t.max).sort((a, b) => a.value - b.value);
        const below = far.filter((l) => l.value < t.min).sort((a, b) => b.value - a.value);
        if (above.length) annotations.push({ text: `${above.map(limitName).join(", ")} ↑`, xref: "paper", yref: "paper", x: 1, y: top,
                                             xanchor: "right", yanchor: "top", showarrow: false, font: { size: 10, color: palette.limit } });
        if (below.length) annotations.push({ text: `${below.map(limitName).join(", ")} ↓`, xref: "paper", yref: "paper", x: 1, y: bottom,
                                             xanchor: "right", yanchor: "bottom", showarrow: false, font: { size: 10, color: palette.limit } });
      }
    });
    top = bottom;
  });
  if (lanes.length) top -= frac(GAP_PX);
  if (lanes.length) {
    n += 1;
    const axis = n === 1 ? "yaxis" : `yaxis${n}`;
    const ref = n === 1 ? "y" : `y${n}`;
    const order = lanes.map(rowLabel);
    layout[axis] = { domain: [0, Math.max(0, frac(lanesPx))], anchor: "x", type: "category", categoryorder: "array",
                     categoryarray: [...order].reverse(), fixedrange: true, showgrid: false, zeroline: false,
                     range: [-0.6, lanes.length - 0.4], showticklabels: false };
    // Each row is named inside the chart, above its markers.
    for (const l of lanes) {
      annotations.push({ text: rowLabel(l), xref: "paper", yref: ref, x: 0, y: rowLabel(l), xanchor: "left", yanchor: "bottom",
                         yshift: 5, showarrow: false, font: { size: 10, color: palette.text }, opacity: 0.85 });
    }
    lowest = ref;
    const hover = (e: TimelineEvent) =>
      [e.label, e.text, e.detail, e.status].filter(Boolean).join(" · ") + `<br>${e.start.replace("T", " ")}` +
      (e.end && e.end !== e.start ? ` to ${e.end.replace("T", " ")}` : "");
    for (const l of lanes) {
      const y = rowLabel(l);
      const c = palette.lanes[l.lane] ?? palette.text;
      const spans = l.events.filter((e) => e.end && e.end !== e.start);
      const moments = l.events.filter((e) => !e.end || e.end === e.start);
      if (spans.length) {
        const ids = spans.map((e) => indexes([e]));
        data.push({
          type: "scatter", mode: "lines", yaxis: ref, showlegend: false, line: { width: 10, color: c },
          x: spans.flatMap((e) => [e.start, e.end, null]), y: spans.flatMap(() => [y, y, null]),
          text: spans.flatMap((e) => [hover(e), hover(e), null]), hovertemplate: "%{text}<extra></extra>",
          customdata: ids.flatMap((d) => [d, d, null]), opacity: 0.75, name: l.label,
        });
      }
      if (moments.length) {
        const groups = crowded(moments, gap);
        data.push({
          type: "scatter", mode: groups.some((g) => g.length > 1) ? "markers+text" : "markers", yaxis: ref, showlegend: false, name: l.label,
          marker: { size: groups.map((g) => (g.length > 1 ? 14 : 10)), symbol: "diamond", color: c, line: { width: 1, color: palette.text } },
          x: groups.map((g) => g[0].start), y: groups.map(() => y),
          text: groups.map((g) => (g.length > 1 ? String(g.length) : "")), textposition: "top center",
          textfont: { size: 10, color: palette.text },
          hovertext: groups.map((g) => g.length > 1
            ? `${g.length} ${LANE_SHORT[l.lane]?.toLowerCase() ?? "events"} entries: ` +
              g.slice(0, 4).map((e) => e.text ?? e.label).join("; ") + (g.length > 4 ? "; …" : "") + "<br>click to list them"
            : hover(g[0])),
          hovertemplate: "%{hovertext}<extra></extra>", customdata: groups.map(indexes),
        });
      }
    }
  }
  // The spec's marks, on the chart itself: a window shaded and named at its start, a moment a labelled line.
  for (const m of spec.marks ?? []) {
    const c = MARK_TONE(palette, m.tone);
    if (m.kind === "window" && m.start && m.end) {
      shapes.push({ type: "rect", xref: "x", yref: "paper", x0: m.start, x1: m.end, y0: 0, y1: 1,
                    fillcolor: m.tone === "warn" || !m.tone ? palette.shade : c, opacity: m.tone === "warn" || !m.tone ? 1 : 0.12,
                    line: { width: 0 }, layer: "below" });
      const x = local(m.start) < local(start) ? start : m.start;
      if (local(m.end) > local(start) && local(m.start) <= local(end)) {
        annotations.push({ text: m.label, xref: "x", yref: "paper", x, y: 1, xanchor: "left", yanchor: "top", showarrow: false,
                           font: { size: 10, color: palette.text }, opacity: 0.75 });
      }
    } else if (m.kind === "time" && m.at && local(m.at) >= local(start) && local(m.at) <= local(end)) {
      shapes.push({ type: "line", xref: "x", yref: "paper", x0: m.at, x1: m.at, y0: 0, y1: 1, line: { color: c, width: 1.5, dash: "dot" } });
      annotations.push({ text: m.label, xref: "x", yref: "paper", x: m.at, y: 1, xanchor: "left", yanchor: "top", xshift: 3,
                         showarrow: false, font: { size: 10, color: c } });
    }
  }
  pins.forEach((at, i) => {
    shapes.push({ type: "line", xref: "x", yref: "paper", x0: at, x1: at, y0: 0, y1: 1, line: { color: palette.text, width: 1.5 } });
    annotations.push({ text: `<b>${"AB"[i]}</b>`, xref: "x", yref: "paper", x: at, y: 1, yanchor: "bottom", showarrow: false,
                       font: { size: 11, color: palette.text } });
  });
  layout.xaxis = { type: "date", range: [start, end], anchor: lowest, domain: [0, 1], hoverformat: HOVER_TIME,
                   showspikes: true, spikemode: "across", spikesnap: "cursor", spikethickness: 1, spikedash: "solid",
                   spikecolor: palette.text, ...(compact ? { nticks: 4 } : {}) };
  return {
    data,
    events,
    layout: {
      ...layout, shapes, annotations, height: plotPx + (keyPx[0] ?? 0) + (compact ? 104 : 60),
      // On a phone the full screen button (44 px) sits above the first key, not over its first line.
      margin: compact ? { t: (keyPx[0] ?? 0) + 48, r: 12, b: 44, l: 44 } : { t: (keyPx[0] ?? 0) + 4, r: 56, b: 44, l: 56 },
      hovermode: "x unified", hoversubplots: "axis", showlegend: true, dragmode: "zoom",
    },
  };
}
