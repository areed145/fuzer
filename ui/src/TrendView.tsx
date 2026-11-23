import { kindWord } from "./wording";
import "./styles/trend-view.css";
// One trend with event lanes: an entity's trend spec (analysis/trend_spec.py) drawn by the one chart (TrendChart), with
// the controls around it. Chips switch each series, panel and lane on or off; a marker says what it was under the
// chart. With `linked`, the window and what is switched on go in the page's link, so a link sent to the field opens
// the same chart.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { when } from "./format";
import { entityHref, Status, useAsync } from "./lib";
import { caseHref } from "./pages/workflows/shared";
import { drawn, fromQuery, LANE_SHORT, opening, panels, reading, scaled, toQuery, valueAt } from "./trends/figure";
import type { TimelineEvent, TrendSeries, TrendSpec } from "./trends/spec";
import { isCompact, TrendChart } from "./trends/TrendChart";
import { Badge, Button, Card, Disclosure, Input, Table, ErrorText, Text, List, Tabs } from "./ui";

const WINDOWS = [24, 72, 168, 720]; // hours: 1 d, 3 d, 7 d, 30 d
const PHONE_PANELS = 3; // panels a phone opens on; the rest are one tap away
const local = (iso: string) => iso.slice(0, 16);
const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(2));

/** Where a lane event leads: the case, or the record's own page (an alarm's point). */
function recordHref(e: TimelineEvent): string | null {
  if (e.kind === "case" && typeof e.item_id === "number") return caseHref(e.item_id);
  return e.id ? entityHref(e.id) : null;
}

/** A lane event's kind as words: an entity kind as the ontology labels it, and the two that aren't entities. */
const KIND: Record<string, string> = { alarm: "Alarm", case: "Case" };
const kindOf = (kind: string) => KIND[kind] ?? kindWord(kind);
/** A chart time to the minute, as a pin holds it: "2024-03-14T03:20". */
const minute = (x: string) => x.replace(" ", "T").slice(0, 16);

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

/** The series a page's words name (a case's title), by label or point: a fallback for a page that has no keys. */
function named(spec: TrendSpec, words: string): string[] {
  const said = words.toLowerCase();
  const hit = spec.series.map((s) => s.label.toLowerCase()).filter((n) => n.length > 3 && said.includes(n));
  return spec.series.filter((s) => hit.some((m) => s.label.toLowerCase().startsWith(m)) || said.includes(s.point.toLowerCase())
    || said.includes(s.tag.toLowerCase())).map((s) => s.key);
}

/** The trend of `id`. `start` and `end` open it over a window (a case's event) instead of the latest incident; `linked`
 * keeps the window and the toggles in the page link. */
export function TrendView({ id, start, end, linked = false, focus, focusKeys, caseId }: {
  id: string; start?: string; end?: string; linked?: boolean;
  /** What the page is about, in words (a case's title): the chart opens on the series it names. */
  focus?: string;
  /** The series the page is about, by key, point or role: the chart opens on them, the rest a tap away. */
  focusKeys?: string[];
  /** The case the chart is on: it opens on what the case's event was seen on, with the event shaded and marked. */
  caseId?: number;
}) {
  const q = useMemo(() => (linked ? linkQuery() : new URLSearchParams()), [linked, id]);
  const around = start && end ? spanning(start, end) : null;
  const [hours, setHours] = useState(() => Number(q.get("w")) || around?.hours || 72);
  const [at, setAt] = useState<string | undefined>(() => q.get("at") || around?.at);
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [zoom, setZoom] = useState<[string, string] | null>(null);
  const [pins, setPins] = useState<string[]>([]);
  const [chosen, setChosen] = useState<TimelineEvent[] | null>(null);
  const [morePanels, setMorePanels] = useState(false);
  const [redraw, setRedraw] = useState(0);
  const swipe = useRef<{ x: number; y: number } | null>(null);
  const before = Math.round((hours * 2) / 3);
  const keys = focusKeys?.join(",") ?? "";
  const v = useAsync(() => api.trendSpec(id, at, before, hours - before, focusKeys, caseId), [id, at, hours, keys, caseId]);
  const d = v.data;
  const t = useAsync(() => (d && !d.error ? api.timeline(id, d.time.start, d.time.end) : Promise.resolve(null)), [id, d?.time.start, d?.time.end]);
  // The chart is drawn once its event lanes are read too, in the space it takes, so the page moves once.
  const pending = !d ? !v.error : !d.error && t.data === undefined && !t.error;
  useEffect(() => { setZoom(null); setPins([]); setChosen(null); }, [d]); // a new window draws unzoomed, unpinned
  // Zoomed in, the values are read again for the range at the finest step that keeps it to a chart's worth.
  const zoomHours = zoom ? Math.max(1, Math.ceil((Date.parse(`${zoom[1]}Z`) - Date.parse(`${zoom[0]}Z`)) / 3600000)) : 0;
  const fine = useAsync(() => (zoom && d && zoomHours < hours ? api.trendSpec(id, zoom[0], 0, zoomHours, focusKeys, caseId) : Promise.resolve(null)),
                        [id, zoom?.[0], zoomHours]);
  const shown = fine.data && !fine.data.error && zoom ? fine.data : d;
  const all = useMemo(() => (d && !d.error ? fromQuery(d, q.get("show"), q.get("hide")) : new Set<string>()), [d, q]);
  // What the page is about: the spec's focus, else the series the page's words name; nothing named, the defaults.
  const focused = useMemo(() => {
    if (!d || d.error) return null;
    if (d.focus?.length) return opening(d);
    const ids = focus ? named(d, focus) : [];
    return ids.length ? opening({ ...d, focus: ids }) : null;
  }, [d, focus]);
  const compact = isCompact();
  const base = picked ?? focused ?? all;
  // On a phone the chart opens on its first few panels; the rest are one tap away.
  const drawnPanels = useMemo(() => (d && !d.error ? drawn(scaled(panels(d, base)), compact) : []), [d, base, compact]);
  const capped = compact && !picked && !morePanels && drawnPanels.length > PHONE_PANELS;
  const on = useMemo(() => {
    if (!capped) return base;
    const keep = new Set(drawnPanels.slice(0, PHONE_PANELS).flatMap((p) => p.series.map((s) => s.key)));
    return new Set([...base].filter((k) => k.startsWith("lane:") || keep.has(k)));
  }, [capped, base, drawnPanels]);
  const first = around?.at;
  useEffect(() => {
    if (!linked || !d || d.error) return;
    const [path, query] = location.hash.split("?");
    const next = new URLSearchParams(query ?? "");
    const keep = { ...toQuery(d, base), at: at !== first ? at : undefined, w: hours !== (around?.hours ?? 72) ? String(hours) : undefined };
    for (const [k, v] of Object.entries(keep)) (v ? next.set(k, v) : next.delete(k));
    const hash = `${path}${next.size ? `?${next}` : ""}`;
    // replaceState: a toggle is not a page to go back to, and it fires no hashchange to re-render the route
    if (hash !== location.hash) history.replaceState(history.state, "", hash);
  }, [linked, d, base, at, hours]);
  const toggle = (keys: string[], value: boolean) => {
    const next = new Set(on);
    keys.forEach((k) => (value ? next.add(k) : next.delete(k)));
    setPicked(next);
  };
  // A click on the chart pins its time; a second pins before and after; a third starts again.
  const pin = useCallback((x: string) => setPins((p) => (p.length >= 2 ? [minute(x)] : [...p, minute(x)])), []);
  const zoomed = useCallback((z: boolean, range?: [string, string]) => setZoom(z && range ? [minute(range[0]), minute(range[1])] : null), []);
  const shift = (sign: number) =>
    d && !d.error && setAt(local(new Date(Date.parse(d.time.centre + "Z") + (sign * hours * 3600 * 1000) / 2).toISOString()));
  const series = useMemo(() => new Map((d?.series ?? []).map((s) => [s.key, s])), [d]);
  const groups = (d?.panels ?? []).map((p) => ({ ...p, items: p.series.map((k) => series.get(k)!).filter(Boolean) }));
  const shownSeries: TrendSeries[] = groups.flatMap((g) => g.items.filter((x) => on.has(x.key)));
  const lanes = (t.data?.lanes ?? []).filter((l) => l.events.length);
  const colour = useMemo(() => {
    const out = new Map<string, number>();
    let i = 0;
    for (const p of d?.panels ?? []) for (const k of p.series) if (on.has(k)) out.set(k, (i++ % 6) + 1);
    return out;
  }, [d, on]);
  const showAll = () => { setPicked(all); setMorePanels(true); };
  // On a phone the chips go under the chart, so the chart starts in the first screen.
  const chipsBlock = d && !d.error && (
        <div className="trend-view-chips">
          {groups.filter((g) => g.items.length).map((g) => {
            const ids = g.items.map((x) => x.key);
            const every = ids.every((i) => on.has(i));
            const any = ids.some((i) => on.has(i));
            return (
              <div key={g.key} className="chips">
                {/* A panel that is off says how many series it holds, so it doesn't read as empty. */}
                <Button className="chip trend-view-group" aria-pressed={every} onClick={() => toggle(ids, !every)}
                        title={any ? undefined : `Show ${ids.length} point${ids.length === 1 ? "" : "s"}`}>
                  {g.label}{!any && <Text muted> · {ids.length} hidden</Text>}
                </Button>
                {any && g.items.map((x) => (
                  <Button key={x.key} className="chip" aria-pressed={on.has(x.key)} title={`${x.point}${x.description ? `: ${x.description}` : ""}`}
                          onClick={() => toggle([x.key], !on.has(x.key))}>
                    {on.has(x.key) && <i className={`dot c${colour.get(x.key)}`} />} {x.label}
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
                  {LANE_SHORT[l.key] ?? l.label} <Text muted>{l.events.length}</Text>
                </Button>
              ))}
            </div>
          )}
          {d.missing.length > 0 && (
            <Text as="p" small>Not on this unit: {d.missing.map((m) => m.role_label.toLowerCase()).join(", ")}.</Text>
          )}
        </div>
      );
  // Nothing measured here (a document, a pipeline without points): no card at all, the page's other views carry on.
  if (d && !d.error && !at && !d.series.length) return null;
  const hiddenSeries = [...all].filter((k) => !k.startsWith("lane:") && !on.has(k)).length;
  const opened = focused && !picked; // opened on what the page is about: the chips wait behind Show all
  return (
    <Card as="section" className={pending ? "trend-view trend-view-pending" : "trend-view"}
      title={d && !d.error ? d.title : "Trend"}
      detail={d && !d.error && <>
        {d.provider === "synthetic" ? <Badge title="Generated values: no PI or ADX historian is connected">Demo data</Badge> : `from ${d.provider.toUpperCase()}`}
        {" "}every {shown && !shown.error ? shown.step_minutes : d.step_minutes} min
      </>}>
      <div className="row trend-controls">
        <Tabs small label="How much time the chart shows" items={WINDOWS.map((h) => ({ key: String(h), label: `${h / 24} d`, current: h === hours, onSelect: () => setHours(h) }))} />
        <span className="spacer" />
        {zoom && <Button size="sm" onClick={() => { setZoom(null); setRedraw((r) => r + 1); }}>Reset zoom</Button>}
        <Button size="sm" onClick={() => shift(-1)} disabled={!d} aria-label="Earlier">{compact ? "←" : "← Earlier"}</Button>
        {!compact && <Input type="datetime-local" aria-label="Centre the window on" value={d && !d.error ? local(d.time.centre) : ""}
               onChange={(e) => e.target.value && setAt(e.target.value)} />}
        <Button size="sm" onClick={() => shift(1)} disabled={!d} aria-label="Later">{compact ? "→" : "Later →"}</Button>
        {/* The window opens on the latest incident, else the latest values; once moved, one press returns there. */}
        {at === undefined
          ? !compact && d && !d.error && <Text small muted>{d.marks.some((m) => m.tone === "warn") ? "Latest incident" : "Latest values"}</Text>
          : <Button variant="link" size="sm" onClick={() => setAt(undefined)}>{d?.marks.some((m) => m.tone === "warn") ? "Back to latest incident" : "Back to latest"}</Button>}
      </div>
      <Status loading={pending} error={v.error ?? d?.error} empty={!!d && !d.error && !d.series.length}>
        No values between {d && !d.error ? when(d.time.start) : ""} and {d && !d.error ? when(d.time.end) : ""}{d?.errors?.length ? `: ${d.errors[0]}` : "."}
      </Status>
      {!compact && !pending && !opened && chipsBlock}
      {shown && !shown.error && !pending && (shownSeries.length > 0 || lanes.length > 0) && (
        // On a phone a sideways swipe moves the window; a drag there scrolls the page, not the chart.
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
          <TrendChart key={redraw} spec={shown} timeline={t.data ?? null} on={on} pins={pins} zoom={zoom} compact={compact}
                      onEvents={setChosen} onZoom={zoomed} onTime={pin}
                      label={`${d?.title ?? id}: ${shownSeries.length} series and ${lanes.length} event lanes from ${when(shown.time.start)} to ${when(shown.time.end)}`} />
        </div>
      )}
      {!pending && (opened || capped) && hiddenSeries > 0 && (
        <Button size="sm" variant="link" onClick={showAll}>
          {capped && !opened ? `Show ${drawnPanels.length - PHONE_PANELS} more panels` : `Show all ${[...all].filter((k) => !k.startsWith("lane:")).length} values`}
        </Button>
      )}
      {chosen && chosen.length > 0 && (
        <Card as="section" className="trend-view-detail" aria-label="Selected events"
          title={chosen.length === 1 ? kindOf(chosen[0].kind) : `${chosen.length} events`}
          actions={<Button size="sm" variant="link" onClick={() => setChosen(null)}>Close</Button>}>
          <List plain>
            {chosen.map((e, i) => {
              const h = recordHref(e);
              return (
                <li key={`${e.kind}-${e.id ?? e.item_id}-${i}`}>
                  <b>{when(e.start)}{e.end && e.end !== e.start ? ` to ${when(e.end)}` : ""}</b>{" "}
                  {chosen.length > 1 && <Text muted>{kindOf(e.kind)} · </Text>}
                  {[e.label, e.text, e.detail, e.status].filter(Boolean).join(" · ")}
                  {h && <> · <a href={h}>{e.kind === "case" ? "Open case" : "Open entity"}</a></>}
                </li>
              );
            })}
          </List>
        </Card>
      )}
      {pins.length > 0 && shown && !shown.error && (
        <Card as="section" className="trend-view-pins" aria-label="Pinned times"
          title={pins.length === 1 ? `At ${when(pins[0])}` : `${when(pins[0])} to ${when(pins[1])}`}
          actions={<Button size="sm" variant="link" onClick={() => setPins([])}>Unpin</Button>}>
          <Table variant="list" density="compact">
            <thead><tr><th>Series</th><th>A</th>{pins.length > 1 && <><th>B</th><th>Change</th></>}</tr></thead>
            <tbody>
              {shownSeries.map((x) => {
                const tr = shown.series.find((y) => y.key === x.key) ?? x;
                const [a, b] = pins.map((p) => valueAt(tr, p, shown.step_minutes));
                const unit = x.unit ? ` ${x.unit}` : "";
                return (
                  <tr key={x.key}>
                    <td>{x.label}</td>
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
      {compact && !pending && !opened && chipsBlock}
      {t.error && <ErrorText>Events unavailable: {t.error}</ErrorText>}
      {!pending && shownSeries.length > 0 && (
        <Disclosure className="small" label={<>Table</>}>
          <Table variant="list" density="compact">
            <thead><tr><th>Series</th><th>Point</th><th>Min</th><th>Max</th><th>Last</th><th>Units</th></tr></thead>
            <tbody>
              {shownSeries.map((x) => (
                <tr key={x.key}>
                  <td><a href={entityHref(x.link)}>{x.label}</a></td><td className="mono">{x.tag}</td>
                  <td>{fmt(x.min)}</td><td>{fmt(x.max)}</td><td>{fmt(x.last)}</td><td>{x.unit}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Disclosure>
      )}
    </Card>
  );
}
