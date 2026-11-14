// One trend with event lanes (P5.13b): the template's groups (P5.12) as panels stacked on one time axis, traces in a
// panel sharing an axis per unit, and the unit's alarms, log, cases, work orders and downtime as lanes beneath. Pure:
// it takes the trend view, the timeline, what is switched on and the page's colours, and returns a Plotly figure.

export interface TrendLimit { type: "hihi" | "hi" | "lo" | "lolo"; value: number; basis: "alarm" | "iow" | "learned"; iow?: string }
export interface TrendTrace {
  id: string;
  point: string;
  description: string | null;
  uom: string | null;
  role?: string | null;
  role_label?: string | null;
  on?: { id: string; label: string | null; relation: string } | null;
  axis: string;
  show: boolean;
  values: [string, number][];
  min: number;
  max: number;
  mean: number;
  last: number;
  limits?: TrendLimit[];
  modelled?: boolean;
  /** A landed reading (a well test, a lab sample): drawn as steps with a marker per reading (P5.13d). */
  sampled?: boolean;
  methods?: (string | null)[];
  /** The record the trace's table row opens, when it is not the trace's own id. */
  link?: string;
}
export interface TrendGroup { key: string; label: string; show: boolean; traces: TrendTrace[] }
export interface TrendViewData {
  provider: string;
  centre: string;
  start: string;
  end: string;
  step_minutes: number;
  upsets: string[];
  unit: string;
  unit_label: string | null;
  classes: string[];
  template: { key: string; title: string; source?: "platform" | "bundle" };
  groups: TrendGroup[];
  other: TrendTrace[];
  missing: { role: string; role_label: string; relation: string }[];
  errors?: string[];
  error?: string;
}
export interface TimelineEvent {
  id: string | null;
  item_id?: number;
  kind: string;
  label: string | null;
  start: string;
  end: string | null;
  text: string | null;
  detail: string | null;
  status?: string | null;
}
export interface TimelineLane { key: string; label: string; events: TimelineEvent[] }
/** A row of the lanes as drawn: a lane, or one alarming point of the alarms lane (P5.15c). */
export interface LaneRow { key: string; lane: string; label: string; events: TimelineEvent[] }
export interface TimelineData { unit: string; start: string; end: string; lanes: TimelineLane[] }

export interface Palette { series: string[]; lanes: Record<string, string>; limit: string; shade: string; text: string }

export const OTHER = "other";
export const LANE_SHORT: Record<string, string> = {
  alarms: "Alarms", log: "Log", cases: "Cases", maintenance: "Work orders", downtime: "Downtime",
};
const PANEL_PX = 170;
const PANEL_PX_PHONE = 120; // shorter on a phone, so the chart starts in the first screen (P5.15f)
const ALARM_ROWS = 8; // alarming points with a row of their own; the rest share one
const LANE_PX = 30;
const GAP_PX = 30;
const KEY_ROW_PX = 20;
const LEAD_IN_MS = 12 * 3600 * 1000;

/** What a trace is called on a chip and in the hover: its role, and the neighbour it is on when not the unit. */
export function traceName(t: TrendTrace): string {
  const what = t.role_label ?? t.description ?? t.point;
  return t.on && t.on.relation !== "self" && t.on.label ? `${what} · ${t.on.label}` : what;
}

/** Every trace's name: its role, and where two traces in a group would read the same (two bearing temperatures, four
 * exhaust thermocouples), its description instead. */
export function names(view: TrendViewData): Map<string, string> {
  const out = new Map<string, string>();
  for (const g of [...view.groups, { traces: view.other }]) {
    const base = g.traces.map(traceName);
    g.traces.forEach((t, i) => {
      const twins = g.traces.filter((_, j) => base[j] === base[i]);
      const twin = twins.length > 1;
      const own = twin ? distinct(t.description ?? t.point, twins.map((x) => x.description ?? x.point)) : "";
      out.set(t.id, twin ? (t.on && t.on.relation !== "self" && t.on.label ? `${own} · ${t.on.label}` : own) : base[i]);
    });
  }
  return out;
}

/** A description less the words every twin starts with: "Train B Compressor Thrust Bearing Temperature" beside
 * "Train B Compressor Journal Bearing Temperature" reads "Thrust Bearing Temperature". */
function distinct(own: string, all: string[]): string {
  const words = all.map((x) => x.split(" "));
  let k = 0;
  while (words.every((w) => w.length > k + 1 && w[k] === words[0][k])) k += 1;
  return own.split(" ").slice(k).join(" ");
}

/** The panels to draw: the template's groups, then the unit's other points, each with only its switched-on traces. */
export function panels(view: TrendViewData, on: Set<string>): TrendGroup[] {
  const all = [...view.groups, { key: OTHER, label: "Other points", show: false, traces: view.other }];
  return all.map((g) => ({ ...g, traces: g.traces.filter((t) => on.has(t.id)) })).filter((g) => g.traces.length);
}

/** What is switched on at first: the traces the template shows, and every lane. */
export function defaults(view: TrendViewData): Set<string> {
  const ids = view.groups.flatMap((g) => g.traces.filter((t) => t.show).map((t) => t.id));
  return new Set([...ids, ...Object.keys(LANE_SHORT).map((k) => `lane:${k}`)]);
}

/** What is switched on, as it goes in a link: the traces and lanes switched on beyond the defaults (`show`) and
 * the defaults switched off (`hide`), comma-separated, so a link to the default view carries neither. */
export function toQuery(view: TrendViewData, on: Set<string>): { show?: string; hide?: string } {
  const base = defaults(view);
  const show = [...on].filter((k) => !base.has(k));
  const hide = [...base].filter((k) => !on.has(k));
  return { show: show.join(",") || undefined, hide: hide.join(",") || undefined };
}

/** What a link switched on: the defaults, with its `show` added and its `hide` taken away. Ids the view no longer has
 * are dropped, so an old link still opens. */
export function fromQuery(view: TrendViewData, show: string | null, hide: string | null): Set<string> {
  const known = new Set([...view.groups, { traces: view.other }].flatMap((g) => g.traces.map((t) => t.id)));
  Object.keys(LANE_SHORT).forEach((k) => known.add(`lane:${k}`));
  const on = defaults(view);
  (show ?? "").split(",").filter((k) => known.has(k)).forEach((k) => on.add(k));
  (hide ?? "").split(",").forEach((k) => on.delete(k));
  return on;
}

/** Each trace's colour, by its place in its group, so a colour means the same trace whatever else is switched on. */
export function colours(view: TrendViewData, palette: Palette): Map<string, string> {
  const out = new Map<string, string>();
  for (const g of [...view.groups, { traces: view.other }]) {
    g.traces.forEach((t, i) => out.set(t.id, palette.series[i % palette.series.length]));
  }
  return out;
}

/** A panel as drawn: a template group, or the part of one past its first two units (P5.15b), so no panel carries
 * more than two value axes. On a phone, where a second axis has no room for its scale, one unit a panel. */
export function drawn(groups: TrendGroup[], per = 2): TrendGroup[] {
  return groups.flatMap((g) => {
    const units = [...new Set(g.traces.map((t) => t.axis))];
    const out: TrendGroup[] = [];
    for (let i = 0; i < units.length; i += per) {
      const mine = new Set(units.slice(i, i + per));
      out.push({ ...g, key: i ? `${g.key}:${i / per}` : g.key, label: i ? `${g.label}, continued` : g.label,
                 traces: g.traces.filter((t) => mine.has(t.axis)) });
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

/** Rows a panel's key needs at this width: its title and each trace's name and last value, wrapped. */
function keyRows(g: TrendGroup, name: Map<string, string>, compact: boolean): number {
  // On a phone the key is a list: the panel's name, then one trace a line.
  if (compact) return 1 + g.traces.length;
  const chars = g.label.length + g.traces.reduce((n, t) => n + (name.get(t.id) ?? "").length + 12, 0);
  return Math.max(1, Math.ceil((chars * 6.6 + g.traces.length * 34) / 860));
}

/** A key entry's name fits a phone's width: a long name is cut with an ellipsis, and the hover keeps it whole. */
export function short(s: string, compact: boolean, max = 30): string {
  return !compact || s.length <= max ? s : `${s.slice(0, max - 1).trimEnd()}…`;
}

/** How urgent an alarm reads: its priority where the record gives one, else its limit (hihi and lolo before hi and lo). */
function urgency(e: TimelineEvent): number {
  const level = ({ critical: 0, emergency: 0, urgent: 1, high: 1, medium: 2, low: 3 } as Record<string, number>)[(e.detail ?? "").toLowerCase()];
  if (level !== undefined) return level;
  const type = (e.text ?? "").split(" ")[0].toLowerCase();
  return type === "hihi" || type === "lolo" ? 0.5 : type === "hi" || type === "lo" ? 1.5 : 4;
}

/** The lanes as rows (P5.15c): every lane one row, except alarms, one row per point, the most urgent first (a point's
 * most urgent alarm, then its first). Past `ALARM_ROWS` points, the rest share a row that says how many. */
export function laneRows(lanes: TimelineLane[], compact = false): LaneRow[] {
  return lanes.flatMap((l) => {
    const name = LANE_SHORT[l.key] ?? l.label;
    if (l.key !== "alarms") return [{ key: l.key, lane: l.key, label: name, events: l.events }];
    const by = new Map<string, TimelineEvent[]>();
    for (const e of l.events) by.set(e.label ?? e.id ?? "?", [...(by.get(e.label ?? e.id ?? "?") ?? []), e]);
    const points = [...by.entries()].sort(([a, x], [b, y]) =>
      Math.min(...x.map(urgency)) - Math.min(...y.map(urgency)) || x[0].start.localeCompare(y[0].start) || a.localeCompare(b));
    const own = points.length > ALARM_ROWS ? points.slice(0, ALARM_ROWS - 1) : points;
    const rest = points.slice(own.length);
    const label = (point: string) => (compact ? short(point, true, 14) : `Alarm · ${point}`);
    return [
      ...own.map(([point, events]) => ({ key: `alarms:${point}`, lane: "alarms", label: label(point), events })),
      ...(rest.length ? [{ key: "alarms:more", lane: "alarms", label: `${rest.length} more alarming points`,
                           events: rest.flatMap(([, events]) => events) }] : []),
    ];
  });
}

/** Markers too close to tell apart at this width, as groups (P5.15c): each group starts at its first marker and
 * takes every marker within `gap` ms of that one. */
export function crowded(events: TimelineEvent[], gap: number): TimelineEvent[][] {
  const out: TimelineEvent[][] = [];
  for (const e of [...events].sort((a, b) => a.start.localeCompare(b.start))) {
    const last = out[out.length - 1];
    if (last && local(e.start) - local(last[0].start) <= gap) last.push(e);
    else out.push([e]);
  }
  return out;
}

/** A trace's value at a time: the reading nearest it, if one is within two steps (P5.15d). */
export function valueAt(t: TrendTrace, at: string, stepMinutes: number): number | null {
  const want = local(at);
  let best: [string, number] | null = null;
  for (const v of t.values) if (!best || Math.abs(local(v[0]) - want) < Math.abs(local(best[0]) - want)) best = v;
  return best && Math.abs(local(best[0]) - want) <= 2 * stepMinutes * 60000 ? best[1] : null;
}

const iso = (ms: number) => new Date(ms).toISOString().slice(0, 16);
const local = (s: string) => Date.parse(s.length === 10 ? `${s}T00:00Z` : `${s.slice(0, 16)}Z`);

/** The chart. `pins` are the times pinned on it (P5.15d), drawn as lines marked A and B. The figure carries `events`,
 * every lane event once: a lane marker's customdata is ["events", ...its indexes in that list], so a click can say
 * what it was without leaving the page (P5.15c). */
export function buildFigure(view: TrendViewData, timeline: TimelineData | null, on: Set<string>, palette: Palette,
                            compact = false, pins: string[] = []) {
  const shown = drawn(panels(view, on), compact ? 1 : 2);
  const name = names(view);
  const panelPx = compact ? PANEL_PX_PHONE : PANEL_PX;
  const lanes = laneRows((timeline?.lanes ?? []).filter((l) => l.events.length && on.has(`lane:${l.key}`)), compact);
  const events: TimelineEvent[] = [];
  const indexes = (es: TimelineEvent[]) => ["events", ...es.map((e) => events.push(e) - 1)];
  const gap = Math.max(1, local(view.end) - local(view.start)) / (compact ? 24 : 60);
  const colour = colours(view, palette);
  // Above each panel, its key: the panel's name, then each trace's name and last value in its colour (P5.15b).
  const keyPx = shown.map((g) => 8 + KEY_ROW_PX * keyRows(g, name, compact));
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
    const units = [...new Set(g.traces.map((t) => t.axis))];
    const first = n + 1;
    units.forEach((u, j) => {
      n += 1;
      const axis = n === 1 ? "yaxis" : `yaxis${n}`;
      const ref = n === 1 ? "y" : `y${n}`;
      const traces = g.traces.filter((t) => t.axis === u);
      const label = traces[0].uom ?? "";
      layout[axis] = j === 0
        ? { domain, anchor: "x", title: { text: label, font: { size: 11 } }, fixedrange: true, zeroline: false }
        : { overlaying: first === 1 ? "y" : `y${first}`, side: "right", fixedrange: true, zeroline: false, showgrid: false,
            title: { text: label, font: { size: 11 } }, anchor: "x" };
      if (j === 0) lowest = ref;
      for (const t of traces) {
        data.push({
          type: "scatter", mode: t.sampled ? "lines+markers" : "lines", x: t.values.map(([at]) => at),
          y: t.values.map(([, v]) => v), yaxis: ref, legend, showlegend: true,
          name: `${short(name.get(t.id) ?? "", compact)} <b>${reading(t.last)}</b>${t.uom ? ` ${t.uom}` : ""}`,
          line: { width: 1.6, color: colour.get(t.id), ...(t.sampled ? { shape: "hv", dash: "dot" } : {}) },
          ...(t.sampled ? { marker: { size: 7, color: colour.get(t.id) }, text: (t.methods ?? []).map((m) => m ?? "") } : {}),
          hovertemplate: `%{y:.4~g} ${t.uom ?? ""}${t.sampled ? " %{text}" : ""}<extra>${name.get(t.id)}</extra>`,
        });
      }
      // A limit draws only where it can't be mistaken for another trace's: the panel's one trace.
      if (g.traces.length === 1) {
        for (const l of traces[0].limits ?? []) {
          shapes.push({ type: "line", xref: "x", yref: ref, x0: view.start, x1: view.end, y0: l.value, y1: l.value,
                        line: { color: palette.limit, width: 1, dash: l.basis === "learned" ? "dot" : "dash" }, layer: "below" });
        }
      }
    });
    top = bottom;
  });
  if (lanes.length) top -= frac(GAP_PX);
  if (lanes.length) {
    n += 1;
    const axis = n === 1 ? "yaxis" : `yaxis${n}`;
    const ref = n === 1 ? "y" : `y${n}`;
    const order = lanes.map((l) => l.label);
    layout[axis] = { domain: [0, Math.max(0, frac(lanesPx))], anchor: "x", type: "category", categoryorder: "array",
                     categoryarray: [...order].reverse(), fixedrange: true, showgrid: false, zeroline: false,
                     range: [-0.6, lanes.length - 0.4], ...(compact ? { tickfont: { size: 10 } } : {}) };
    lowest = ref;
    const hover = (e: TimelineEvent) =>
      [e.label, e.text, e.detail, e.status].filter(Boolean).join(" · ") + `<br>${e.start.replace("T", " ")}` +
      (e.end && e.end !== e.start ? ` to ${e.end.replace("T", " ")}` : "");
    for (const l of lanes) {
      const y = l.label;
      const c = palette.lanes[l.lane] ?? palette.text;
      const spans = l.events.filter((e) => e.end && e.end !== e.start);
      const moments = l.events.filter((e) => !e.end || e.end === e.start);
      if (spans.length) {
        const ids = spans.map((e) => indexes([e]));
        data.push({
          type: "scatter", mode: "lines", yaxis: ref, showlegend: false, line: { width: 10, color: c },
          x: spans.flatMap((e) => [e.start, e.end, null]), y: spans.flatMap(() => [y, y, null]),
          text: spans.flatMap((e) => [hover(e), hover(e), null]), hovertemplate: "%{text}<extra></extra>",
          customdata: ids.flatMap((d) => [d, d, null]), opacity: 0.75, name: y,
        });
      }
      if (moments.length) {
        // Markers too close to tell apart are one, with how many it holds (P5.15c).
        const groups = crowded(moments, gap);
        data.push({
          type: "scatter", mode: groups.some((g) => g.length > 1) ? "markers+text" : "markers", yaxis: ref, showlegend: false, name: y,
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
  for (const u of view.upsets) {
    const end = local(u);
    shapes.push({ type: "rect", xref: "x", yref: "paper", x0: iso(end - LEAD_IN_MS), x1: iso(end), y0: 0, y1: 1,
                  fillcolor: palette.shade, line: { width: 0 }, layer: "below" });
    // said on the chart, not only in the heading (P5.15b)
    if (end > local(view.start) && end <= local(view.end)) {
      annotations.push({ text: "12 h before the incident", xref: "x", yref: "paper", x: iso(end - LEAD_IN_MS), y: 1,
                         xanchor: "left", yanchor: "top", showarrow: false, font: { size: 10, color: palette.text },
                         opacity: 0.7 });
    }
  }
  pins.forEach((at, i) => {
    shapes.push({ type: "line", xref: "x", yref: "paper", x0: at, x1: at, y0: 0, y1: 1, line: { color: palette.text, width: 1.5 } });
    annotations.push({ text: `<b>${"AB"[i]}</b>`, xref: "x", yref: "paper", x: at, y: 1, yanchor: "bottom", showarrow: false,
                       font: { size: 11, color: palette.text } });
  });
  layout.xaxis = { type: "date", range: [view.start, view.end], anchor: lowest, domain: [0, 1],
                   showspikes: true, spikemode: "across", spikesnap: "cursor", spikethickness: 1, spikedash: "solid",
                   spikecolor: palette.text, ...(compact ? { nticks: 4 } : {}) };
  return {
    data,
    events,
    layout: {
      ...layout, shapes, annotations, height: plotPx + (keyPx[0] ?? 0) + 60,
      margin: compact ? { t: (keyPx[0] ?? 0) + 4, r: 12, b: 44, l: 44 } : { t: (keyPx[0] ?? 0) + 4, r: 56, b: 44, l: 56 },
      hovermode: "x unified", hoversubplots: "axis", showlegend: true, dragmode: "zoom",
    },
  };
}
