import "./styles/trend-view.css";
// One trend with event lanes (P5.13b): an entity's process values grouped by its class's template (P5.12) as panels
// on one time axis, with its alarms, log, cases, work orders and downtime beneath. Chips switch each trace, group and
// lane on or off; a marker opens its record. With `linked`, the window and what is switched on go in the page's link
// (P5.13c), so a link sent to the field opens the same chart.

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api";
import { PlotlyChart } from "./analytics/PlotlyChart";
import { entityHref, Status, useAsync } from "./lib";
import { caseHref } from "./pages/workflows/shared";
import { useTheme } from "./theme";
import { buildFigure, fromQuery, LANE_SHORT, names, OTHER, toQuery, type Palette, type TrendTrace } from "./trendFigure";
import { Button, Card, Disclosure, Input, Table } from "./ui";

const WINDOWS = [24, 72, 168, 720]; // hours: 1 d, 3 d, 7 d, 30 d
const local = (iso: string) => iso.slice(0, 16);
const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(2));

function palette(): Palette {
  const series = [1, 2, 3, 4, 5, 6].map((i) => css(`--chart-${i}`));
  return {
    series, text: css("--text"), limit: css("--warn"), shade: css("--warn-soft"),
    lanes: { alarms: css("--danger"), log: css("--muted"), cases: css("--accent"), maintenance: series[1], downtime: css("--warn") },
  };
}

/** Where a lane marker leads: the case, or the record's own page (an alarm's point). */
function recordHref(d: unknown): string | null {
  const [kind, id, item] = Array.isArray(d) ? d : [];
  if (kind === "case" && typeof item === "number") return caseHref(item);
  return typeof id === "string" ? entityHref(id) : null;
}

/** The page link's query, read once: the route re-renders on a hash change, and this view writes its own keys. */
function linkQuery(): URLSearchParams {
  return new URLSearchParams(location.hash.split("?")[1] ?? "");
}

/** The centre and width that show [start, end] with the trend's two thirds before and one third after. */
function spanning(start: string, end: string): { at: string; hours: number } {
  const a = Date.parse(`${local(start)}Z`);
  const hours = Math.max(1, Math.ceil((Date.parse(`${local(end)}Z`) - a) / 3600000));
  return { at: local(new Date(a + Math.round((hours * 2) / 3) * 3600000).toISOString()), hours };
}

/** The trend of `id`. `start` and `end` open it over a window (a case's event) instead of the latest incident; `linked`
 * keeps the window and the toggles in the page link. */
export function TrendView({ id, start, end, linked = false }: { id: string; start?: string; end?: string; linked?: boolean }) {
  const q = useMemo(() => (linked ? linkQuery() : new URLSearchParams()), [linked, id]);
  const around = start && end ? spanning(start, end) : null;
  const [hours, setHours] = useState(() => Number(q.get("w")) || around?.hours || 72);
  const [at, setAt] = useState<string | undefined>(() => q.get("at") || around?.at);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [zoomed, setZoomed] = useState(false);
  const [redraw, setRedraw] = useState(0);
  const theme = useTheme();
  const before = Math.round((hours * 2) / 3);
  const v = useAsync(() => api.trendView(id, at, before, hours - before), [id, at, hours]);
  const d = v.data;
  const t = useAsync(() => (d && !d.error ? api.timeline(id, d.start, d.end) : Promise.resolve(null)), [id, d?.start, d?.end]);
  useEffect(() => setZoomed(false), [d]); // a new window draws unzoomed
  const on = useMemo(() => picked ?? (d ? fromQuery(d, q.get("show"), q.get("hide")) : new Set<string>()), [picked, d, q]);
  const first = around?.at;
  useEffect(() => {
    if (!linked || !d || d.error) return;
    const [path, query] = location.hash.split("?");
    const next = new URLSearchParams(query ?? "");
    const keep = { ...toQuery(d, on), at: at !== first ? at : undefined, w: hours !== (around?.hours ?? 72) ? String(hours) : undefined };
    for (const [k, v] of Object.entries(keep)) (v ? next.set(k, v) : next.delete(k));
    const hash = `${path}${next.size ? `?${next}` : ""}`;
    // replaceState: a toggle is not a page to go back to, and it fires no hashchange to re-render the route
    if (hash !== location.hash) history.replaceState(history.state, "", hash);
  }, [linked, d, on, at, hours]);
  const toggle = (keys: string[], value: boolean) => {
    const next = new Set(on);
    keys.forEach((k) => (value ? next.add(k) : next.delete(k)));
    setPicked(next);
  };
  const compact = typeof matchMedia === "function" && matchMedia("(max-width: 640px)").matches;
  const figure = useMemo(() => (d && !d.error ? buildFigure(d, t.data ?? null, on, palette(), compact) : null),
                         [d, t.data, on, theme, compact]);
  const name = useMemo(() => (d ? names(d) : new Map<string, string>()), [d]);
  const open = useCallback((data: unknown) => { const h = recordHref(data); if (h) location.assign(h); }, []);
  const shift = (sign: number) =>
    d && setAt(local(new Date(Date.parse(d.centre + "Z") + (sign * hours * 3600 * 1000) / 2).toISOString()));
  const groups = d ? [...d.groups, { key: OTHER, label: "Other points", show: false, traces: d.other }] : [];
  const shownTraces: TrendTrace[] = groups.flatMap((g) => g.traces.filter((x) => on.has(x.id)));
  const lanes = (t.data?.lanes ?? []).filter((l) => l.events.length);
  // Nothing measured here (a document, a pipeline without points): no card at all, the page's other views carry on.
  if (d && !d.error && !at && groups.every((g) => !g.traces.length)) return null;
  return (
    <Card as="section" className="trend-view">
      <h2>
        {d?.unit_label ? `${d.unit_label} · ` : ""}{d?.template.title ?? "Trend"}{" "}
        {d && !d.error && (
          <span className="muted small">
            {d.provider === "synthetic" ? "synthetic values: no PI / ADX connection configured" : `from ${d.provider.toUpperCase()}`}
            {" "}· every {d.step_minutes} min{d.upsets.length > 0 && " · shaded: the 12 h leading into each incident"}
          </span>
        )}
      </h2>
      <div className="row trend-controls">
        {WINDOWS.map((h) => (
          <Button key={h} variant={h === hours ? "primary" : "secondary"} size="sm" onClick={() => setHours(h)}>{h / 24} d</Button>
        ))}
        <span className="spacer" />
        {zoomed && <Button size="sm" onClick={() => { setZoomed(false); setRedraw((r) => r + 1); }}>Reset zoom</Button>}
        <Button size="sm" onClick={() => shift(-1)} disabled={!d}>← Earlier</Button>
        <Input type="datetime-local" aria-label="Centre the window on" value={d && !d.error ? local(d.centre) : ""}
               onChange={(e) => e.target.value && setAt(e.target.value)} />
        <Button size="sm" onClick={() => shift(1)} disabled={!d}>Later →</Button>
        <Button variant="link" size="sm" onClick={() => setAt(undefined)} disabled={at === undefined}>Latest incident</Button>
      </div>
      <Status loading={v.loading && !d} error={v.error ?? d?.error} empty={!!d && !d.error && groups.every((g) => !g.traces.length)}>
        No values between {d?.start.replace("T", " ")} and {d?.end.replace("T", " ")}{d?.errors?.length ? `: ${d.errors[0]}` : "."}
      </Status>
      {d && !d.error && (
        <div className="trend-view-chips">
          {groups.filter((g) => g.traces.length).map((g) => {
            const ids = g.traces.map((x) => x.id);
            const all = ids.every((i) => on.has(i));
            const any = ids.some((i) => on.has(i));
            return (
              <div key={g.key} className="chips">
                <Button className="chip trend-view-group" aria-pressed={all} onClick={() => toggle(ids, !all)}>{g.label}</Button>
                {/* a group that is off is one chip; its traces show once it is on */}
                {any && g.traces.map((x, i) => (
                  <Button key={x.id} className="chip" aria-pressed={on.has(x.id)} title={`${x.point}${x.description ? `: ${x.description}` : ""}`}
                          onClick={() => toggle([x.id], !on.has(x.id))}>
                    {on.has(x.id) && <i className={`dot c${(i % 6) + 1}`} />} {name.get(x.id)}
                  </Button>
                ))}
              </div>
            );
          })}
          {lanes.length > 0 && (
            <div className="chips">
              <span className="muted small trend-view-group">Events</span>
              {lanes.map((l) => (
                <Button key={l.key} className="chip lane" aria-pressed={on.has(`lane:${l.key}`)} title={l.label}
                        onClick={() => toggle([`lane:${l.key}`], !on.has(`lane:${l.key}`))}>
                  {LANE_SHORT[l.key] ?? l.label} <span className="muted">{l.events.length}</span>
                </Button>
              ))}
            </div>
          )}
          {d.missing.length > 0 && (
            <p className="small">Not on this unit: {d.missing.map((m) => m.role_label.toLowerCase()).join(", ")}.</p>
          )}
        </div>
      )}
      {figure && (shownTraces.length > 0 || lanes.length > 0) && (
        <PlotlyChart key={redraw} spec={figure as never} onPoint={open} onZoom={setZoomed} bare
                     label={`${d?.unit_label ?? id}: ${shownTraces.length} traces and ${lanes.length} event lanes from ${d?.start} to ${d?.end}`} />
      )}
      {t.error && <p className="small error">Events unavailable: {t.error}</p>}
      {shownTraces.length > 0 && (
        <Disclosure className="small" label={<>Table</>}>
          <Table variant="list" density="compact">
            <thead><tr><th>Trace</th><th>Point</th><th>Min</th><th>Max</th><th>Last</th><th>Units</th></tr></thead>
            <tbody>
              {shownTraces.map((x) => (
                <tr key={x.id}>
                  <td><a href={entityHref(x.link ?? x.id)}>{name.get(x.id)}</a></td><td className="mono">{x.point}</td>
                  <td>{fmt(x.min)}</td><td>{fmt(x.max)}</td><td>{fmt(x.last)}</td><td>{x.uom}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Disclosure>
      )}
    </Card>
  );
}
