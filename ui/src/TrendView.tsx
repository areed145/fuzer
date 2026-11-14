import "./styles/trend-view.css";
// One trend with event lanes (P5.13b): an entity's process values grouped by its class's template (P5.12) as panels
// on one time axis, with its alarms, log, cases, work orders and downtime beneath. Chips switch each trace, group and
// lane on or off; a marker opens its record. With `linked`, the window and what is switched on go in the page's link
// (P5.13c), so a link sent to the field opens the same chart.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { PlotlyChart } from "./analytics/PlotlyChart";
import { entityHref, Status, useAsync } from "./lib";
import { caseHref } from "./pages/workflows/shared";
import { useTheme } from "./theme";
import { buildFigure, fromQuery, LANE_SHORT, names, OTHER, reading, toQuery, valueAt, type Palette, type TimelineEvent,
  type TrendTrace } from "./trendFigure";
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

/** Where a lane event leads: the case, or the record's own page (an alarm's point). */
function recordHref(e: TimelineEvent): string | null {
  if (e.kind === "case" && typeof e.item_id === "number") return caseHref(e.item_id);
  return e.id ? entityHref(e.id) : null;
}

const KIND: Record<string, string> = { alarm: "Alarm", log_entry: "Log entry", case: "Case", notification: "Notification",
  work_order: "Work order", downtime: "Downtime", incident: "Incident" };
/** A time as the chart's hover reads it, to the minute: "2024-03-14 03:20". */
const minute = (x: string) => x.replace(" ", "T").slice(0, 16).replace("T", " ");

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
  const [zoom, setZoom] = useState<[string, string] | null>(null);
  const [pins, setPins] = useState<string[]>([]);
  const [chosen, setChosen] = useState<number[] | null>(null);
  const [redraw, setRedraw] = useState(0);
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const theme = useTheme();
  const before = Math.round((hours * 2) / 3);
  const v = useAsync(() => api.trendView(id, at, before, hours - before), [id, at, hours]);
  const d = v.data;
  const t = useAsync(() => (d && !d.error ? api.timeline(id, d.start, d.end) : Promise.resolve(null)), [id, d?.start, d?.end]);
  useEffect(() => { setZoom(null); setPins([]); setChosen(null); }, [d]); // a new window draws unzoomed, unpinned
  // Zoomed in, the values are read again for the range at the finest step that keeps it to a chart's worth (P5.15e).
  const zoomHours = zoom ? Math.max(1, Math.ceil((Date.parse(`${zoom[1]}Z`) - Date.parse(`${zoom[0]}Z`)) / 3600000)) : 0;
  const fine = useAsync(() => (zoom && d && zoomHours < hours ? api.trendView(id, zoom[0], 0, zoomHours) : Promise.resolve(null)),
                        [id, zoom?.[0], zoomHours]);
  const shown = fine.data && !fine.data.error && zoom ? fine.data : d;
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
  const figure = useMemo(() => {
    if (!shown || shown.error) return null;
    const f = buildFigure(shown, t.data ?? null, on, palette(), compact, pins);
    // the finer read keeps the range zoomed to
    return zoom ? { ...f, layout: { ...f.layout, xaxis: { ...(f.layout as Record<string, any>).xaxis, range: zoom } } } : f;
  }, [shown, t.data, on, theme, compact, pins, zoom]);
  const name = useMemo(() => (d ? names(d) : new Map<string, string>()), [d]);
  // A lane marker says what it was under the chart, without leaving the page (P5.15c).
  const open = useCallback((data: unknown) => {
    if (Array.isArray(data) && data[0] === "events") setChosen(data.slice(1) as number[]);
  }, []);
  // A click on the chart pins its time; a second pins before and after; a third starts again (P5.15d).
  const pin = useCallback((x: string) => setPins((p) => (p.length >= 2 ? [minute(x).replace(" ", "T")] : [...p, minute(x).replace(" ", "T")])), []);
  const zoomed = useCallback((z: boolean, range?: [string, string]) =>
    setZoom(z && range ? [minute(range[0]).replace(" ", "T"), minute(range[1]).replace(" ", "T")] : null), []);
  const shift = (sign: number) =>
    d && setAt(local(new Date(Date.parse(d.centre + "Z") + (sign * hours * 3600 * 1000) / 2).toISOString()));
  const groups = d ? [...d.groups, { key: OTHER, label: "Other points", show: false, traces: d.other }] : [];
  const shownTraces: TrendTrace[] = groups.flatMap((g) => g.traces.filter((x) => on.has(x.id)));
  const lanes = (t.data?.lanes ?? []).filter((l) => l.events.length);
  // On a phone the chips go under the chart, so the chart starts in the first screen (P5.15f).
  const chipsBlock = d && !d.error && (
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
      );
  const events = (figure as { events?: TimelineEvent[] } | null)?.events ?? [];
  const selected = (chosen ?? []).map((i) => events[i]).filter(Boolean);
  // Nothing measured here (a document, a pipeline without points): no card at all, the page's other views carry on.
  if (d && !d.error && !at && groups.every((g) => !g.traces.length)) return null;
  return (
    <Card as="section" className="trend-view">
      <h2>
        {d?.unit_label ? `${d.unit_label} · ` : ""}{d?.template.title ?? "Trend"}{" "}
        {d && !d.error && (
          <span className="muted small">
            {d.provider === "synthetic" ? "synthetic values: no PI / ADX connection configured" : `from ${d.provider.toUpperCase()}`}
            {" "}· every {shown?.step_minutes ?? d.step_minutes} min{d.upsets.length > 0 && " · shaded: the 12 h leading into each incident"}
          </span>
        )}
      </h2>
      <div className="row trend-controls">
        {WINDOWS.map((h) => (
          <Button key={h} variant={h === hours ? "primary" : "secondary"} size="sm" onClick={() => setHours(h)}>{h / 24} d</Button>
        ))}
        <span className="spacer" />
        {zoom && <Button size="sm" onClick={() => { setZoom(null); setRedraw((r) => r + 1); }}>Reset zoom</Button>}
        <Button size="sm" onClick={() => shift(-1)} disabled={!d}>← Earlier</Button>
        <Input type="datetime-local" aria-label="Centre the window on" value={d && !d.error ? local(d.centre) : ""}
               onChange={(e) => e.target.value && setAt(e.target.value)} />
        <Button size="sm" onClick={() => shift(1)} disabled={!d}>Later →</Button>
        <Button variant="link" size="sm" onClick={() => setAt(undefined)} disabled={at === undefined}>Latest incident</Button>
      </div>
      <Status loading={v.loading && !d} error={v.error ?? d?.error} empty={!!d && !d.error && groups.every((g) => !g.traces.length)}>
        No values between {d?.start.replace("T", " ")} and {d?.end.replace("T", " ")}{d?.errors?.length ? `: ${d.errors[0]}` : "."}
      </Status>
      {!compact && chipsBlock}
      {figure && (shownTraces.length > 0 || lanes.length > 0) && (
        // On a phone a sideways swipe moves the window (P5.15f); a drag there scrolls the page, not the chart.
        <div className="trend-view-plot"
             onTouchStart={(e) => { const p = e.touches[0]; swipe.current = { x: p.clientX, y: p.clientY }; }}
             onTouchEnd={(e) => {
               const s0 = swipe.current;
               swipe.current = null;
               const p = e.changedTouches[0];
               if (!s0 || !p) return;
               const dx = p.clientX - s0.x;
               if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(p.clientY - s0.y)) shift(dx < 0 ? 1 : -1);
             }}>
          <PlotlyChart key={redraw} spec={figure as never} onPoint={open} onZoom={zoomed} onTime={pin}
                       label={`${d?.unit_label ?? id}: ${shownTraces.length} traces and ${lanes.length} event lanes from ${shown?.start} to ${shown?.end}`} />
        </div>
      )}
      {selected.length > 0 && (
        <Card as="section" className="trend-view-detail" aria-label="Selected events">
          <div className="row">
            <h3>{selected.length === 1 ? (KIND[selected[0].kind] ?? "Event") : `${selected.length} events`}</h3>
            <span className="spacer" />
            <Button size="sm" variant="link" onClick={() => setChosen(null)}>Close</Button>
          </div>
          <ul className="plain">
            {selected.map((e, i) => {
              const h = recordHref(e);
              return (
                <li key={`${e.kind}-${e.id ?? e.item_id}-${i}`}>
                  <b>{minute(e.start)}{e.end && e.end !== e.start ? ` to ${minute(e.end)}` : ""}</b>{" "}
                  {selected.length > 1 && <span className="muted">{KIND[e.kind] ?? e.kind} · </span>}
                  {[e.label, e.text, e.detail, e.status].filter(Boolean).join(" · ")}
                  {h && <> · <a href={h}>{e.kind === "case" ? "Open case" : "Open record"}</a></>}
                </li>
              );
            })}
          </ul>
        </Card>
      )}
      {pins.length > 0 && shown && !shown.error && (
        <Card as="section" className="trend-view-pins" aria-label="Pinned times">
          <div className="row">
            <h3>{pins.length === 1 ? `At ${minute(pins[0])}` : `${minute(pins[0])} to ${minute(pins[1])}`}</h3>
            <span className="spacer" />
            <Button size="sm" variant="link" onClick={() => setPins([])}>Unpin</Button>
          </div>
          <Table variant="list" density="compact">
            <thead><tr><th>Trace</th><th>A</th>{pins.length > 1 && <><th>B</th><th>Change</th></>}</tr></thead>
            <tbody>
              {shownTraces.map((x) => {
                const tr = [...shown.groups.flatMap((g) => g.traces), ...shown.other].find((y) => y.id === x.id) ?? x;
                const [a, b] = pins.map((at) => valueAt(tr, at, shown.step_minutes));
                const unit = x.uom ? ` ${x.uom}` : "";
                return (
                  <tr key={x.id}>
                    <td>{name.get(x.id)}</td>
                    <td>{a === null || a === undefined ? "no value" : `${reading(a)}${unit}`}</td>
                    {pins.length > 1 && <>
                      <td>{b === null || b === undefined ? "no value" : `${reading(b)}${unit}`}</td>
                      <td>{a == null || b == null ? "" : `${b - a >= 0 ? "+" : "−"}${reading(Math.abs(b - a))}${unit}`
                        + (a ? ` (${b - a >= 0 ? "+" : "−"}${Math.abs(((b - a) / a) * 100).toFixed(0)}%)` : "")}</td>
                    </>}
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Card>
      )}
      {compact && chipsBlock}
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
