/** The integrated plan (Turnaround is a window on it, #554): every planned activity from every source on one timeline, by area. SAP work orders,
 * turnaround work, inspections and the exceptions the IOC chose to plan sit side by side, so two jobs on one asset,
 * a person booked twice, and work that could ride along with a shutdown all show before they happen. */
import { acronyms, say } from "../wording";
import { money, whenDay, whenHour, estimate } from "../format";
import { type CSSProperties, type ReactNode, useCallback, useEffect, useMemo, useState } from "react";
import { api, type AtRisk, type PlanItem, type ResourceLoad } from "../api";
import { Badge, Button, Card, Input, PageHeader, Select, Empty, ErrorText, Line, Text, List, Field, confirmDialog, promptDialog } from "../ui";
import { Status, entityHref, errorMessage, href, useAsync, useAction } from "../lib";
import { AssignPicker } from "./workflows/shared";
import { TurnaroundPanel } from "./Turnaround";
import { SchedulePage } from "./Schedule";
import { ResourcesPage } from "./Resources";
import { LogisticsPage } from "./Logistics";
import { SchedulesPage } from "./Schedules";
import { PortfolioPage } from "./Portfolio";
import { usePerson } from "../work";
import { useEntityNames } from "../useEntityNames";
import { commonName } from "../entityRefs";

const HOUR = 3_600_000, DAY = 24 * HOUR;
// What Plan can show from From: a planner's day first, the longer ranges as choices.
const SPANS: [number, string][] = [[1, "1 day"], [7, "1 week"], [14, "2 weeks"], [28, "4 weeks"], [56, "8 weeks"], [84, "12 weeks"],
                                   [182, "26 weeks"]];
const STATUSES = ["proposed", "scheduled", "in_progress", "done", "cancelled"];
const day = (d: Date) => d.toISOString().slice(0, 10);
const short = (at: string) => whenDay(at);

/** An area's rows, with the exceptions about one piece of equipment together: an item alone, or a list of two or more
 * exceptions on the same equipment where the first of them would be (the field raises one per condition, so a machine
 * that needs action four ways made four rows saying so). */
function byEquipment(list: PlanItem[]): (PlanItem | PlanItem[])[] {
  const on = (i: PlanItem) => (i.kind === "exception" && i.subjects.length ? i.subjects[0] : null);
  const same = new Map<string, PlanItem[]>();
  for (const i of list) { const k = on(i); if (k) same.set(k, [...(same.get(k) ?? []), i]); }
  const out: (PlanItem | PlanItem[])[] = [];
  for (const i of list) {
    const k = on(i), all = k ? same.get(k)! : [i];
    if (all.length < 2) out.push(i);
    else if (all[0] === i) out.push(all);
  }
  return out;
}

/** The axis labels that fit a track `width` px wide: a label is left out where it would run into the one before it, off
 * the track's right end, or under the Today mark, so no two read as one ("Jan 4, 2027Jan 11, 2027"). */
function fitLabels(ticks: { at: number; text: string }[], width: number, today: { at: number; text: string } | null): string[] {
  const wide = (text: string) => text.length * 6.5 + 10;  // the 11px axis font, with a gap either side
  let right = 0;
  return ticks.map(({ at, text }) => {
    const x = (at / 100) * width, half = wide(text) / 2;
    if (!width || x - half < right || x + half > width
        || (today && Math.abs(x - (today.at / 100) * width) < half + wide(today.text) / 2)) return "";
    right = x + half;
    return text;
  });
}

export function PlanPage({ query }: { query: URLSearchParams }) {
  const [person] = usePerson();
  // `?schedule=OK-TAR-2026` opens that schedule's activities, logic and resources (P5.18).
  const schedule = query.get("schedule");
  // `?resources=1` is the resource owners' view: demand from every source against each resource's capacity.
  if (query.has("resources")) return <ResourcesPage query={query} person={person} />;
  // `?logistics=1` is what the plan asks to be sent each day: crews to jobs, who travels, seats, beds and lifts.
  if (query.has("logistics")) return <LogisticsPage />;
  // `?schedules=1` lists the schedules (Plan's Schedules page).
  if (query.has("schedules")) return <SchedulesPage />;
  // `?portfolio=1` is every container from the business plan down (`&container=` opens one, `&tab=plan` its plan).
  if (query.has("portfolio")) return <PortfolioPage query={query} />;
  return schedule ? <SchedulePage code={schedule} person={person} view={query.get("view")} /> : <PlanTimeline query={query} person={person} />;
}

/** An entity page's Schedule tab: the plan's timeline, only the jobs about it or anything beneath it. */
export function PlanWithin({ within }: { within: string }) {
  const [person] = usePerson();
  return <PlanTimeline query={new URLSearchParams()} person={person} within={within} />;
}

function PlanTimeline({ query, person, within }: { query: URLSearchParams; person: string | null; within?: string }) {
  // `?revision=TA26` opens that turnaround beside the timeline; `?revision=` opens the next one.
  const revision = !within && query.has("revision") ? query.get("revision") ?? "" : null;
  const scope = useAsync(() => (within ? Promise.resolve(undefined) : api.turnaround(revision || undefined)), [revision, within]);
  const turnaround = revision !== null ? scope.data : undefined;
  const pick = (r: string) => { location.hash = r ? `#/plan?revision=${encodeURIComponent(r)}` : "#/plan"; };
  // Plan opens on today's day; `?item=12&from=…` comes from a case's "planned" link: its bar opens, on its first day.
  const [from, setFrom] = useState(() => query.get("from") ?? day(new Date()));
  const [days, setDays] = useState(1);
  const to = day(new Date(new Date(from).getTime() + days * DAY));
  // The axis is labelled to fit the track's width, which only the browser knows.
  const [axisWidth, setAxisWidth] = useState(0);
  const measure = useCallback((el: HTMLDivElement | null) => {
    if (!el) return;
    const read = () => setAxisWidth(Math.round(el.getBoundingClientRect().width));
    read();
    const seen = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(read);
    seen?.observe(el);
    return () => seen?.disconnect();
  }, []);
  const [openGroup, setOpenGroup] = useState<string | null>(null);
  const plan = useAsync(() => api.plan({ start: from, end: to, within }), [from, to, within]);
  const { busy, error, run } = useAction();
  const [open, setOpen] = useState<number | null>(() => Number(query.get("item")) || null);
  const askedItem = Number(query.get("item")) || null, askedFrom = query.get("from");
  useEffect(() => {
    if (askedItem) setOpen(askedItem);
    if (askedFrom) setFrom(askedFrom);
  }, [askedItem, askedFrom]);
  useEffect(() => {
    if (open && plan.data) document.getElementById(`plan-item-${open}`)?.scrollIntoView({ block: "center" });
  }, [plan.data]);  // eslint-disable-line react-hooks/exhaustive-deps
  const groups = useMemo(() => {
    const out = new Map<string, PlanItem[]>();
    for (const i of plan.data ?? []) {
      const k = i.area_label ?? (i.area_id ? i.area_id : "No area");
      out.set(k, [...(out.get(k) ?? []), i]);
    }
    return [...out.entries()].sort(([a], [b]) => (a === "No area" ? 1 : b === "No area" ? -1 : a.localeCompare(b)));
  }, [plan.data]);
  // An area heading names the area, not only its code: "OKAN-10 · Okan Production Platform" (#1078).
  const areaIds = [...new Set((plan.data ?? []).map((i) => i.area_id).filter((x): x is string => !!x))];
  const names = useEntityNames(areaIds);
  const areaTitle = (area: string, list: PlanItem[]) => {
    const id = list[0]?.area_id;
    const n = id ? names?.[id] : undefined;
    const name = n ? commonName(n.label, n.description) : null;
    return name ? `${area} · ${name}` : area;
  };
  if (!plan.data) return <Status of={plan} />;
  const t0 = new Date(from).getTime(), t1 = new Date(to).getTime();
  const pos = (at: string) => Math.min(100, Math.max(0, ((new Date(at).getTime() - t0) / (t1 - t0)) * 100));
  const clashes = plan.data.filter((i) => i.clashes.some((c) => c.kind !== "bundle")).length;
  const bundles = plan.data.filter((i) => i.clashes.some((c) => c.kind === "bundle")).length;
  const waiting = plan.data.filter((i) => i.parts_short?.length).length;
  const today = pos(new Date().toISOString());
  // Lines on every track and a time axis above them (#1078): every three hours on a day, every day over two weeks or
  // less, every Monday beyond.
  const step = days === 1 ? 3 * HOUR : days <= 14 ? DAY : 7 * DAY;
  const ticks: number[] = [];
  for (let t = step === 7 * DAY ? t0 + ((8 - new Date(t0).getUTCDay()) % 7) * DAY : t0 + step; t < t1; t += step) ticks.push(t);
  const weekAt = (t: number) => ((t - t0) / (t1 - t0)) * 100;
  // Today and the lines, set once for every track: one tile a step wide, placed so a line falls on each tick.
  const week = (step * 100) / (t1 - t0);
  const lines = { "--today": `${today}%`, "--week": `${week}%`,
                  "--week-at": `${week < 100 && ticks.length ? (100 * weekAt(ticks[0])) / (100 - week) : 0}%` } as CSSProperties;
  const nowMark = today > 0 && today < 100 ? { at: today, text: days === 1 ? "Now" : "Today" } : null;
  const labels = fitLabels(ticks.map((t) => ({ at: weekAt(t), text: days === 1 ? whenHour(t) : short(new Date(t).toISOString()) })),
                           axisWidth, nowMark);
  const taWindow = turnaround?.window ? { left: pos(turnaround.window.start), right: pos(turnaround.window.end) } : null;
  const changed = () => { plan.reload(); scope.reload(); };
  const bar = (i: PlanItem) => <span key={i.id} className={`plan-bar kind-${i.kind}${i.needs_shutdown ? " shutdown" : ""}`}
    style={{ left: `${pos(i.start_at)}%`, width: `${Math.max(0.8, pos(i.end_at) - pos(i.start_at))}%` }} />;
  const row = (i: PlanItem) => <li key={i.id} id={`plan-item-${i.id}`} className={`plan-row status-${i.status}`}>
    <button type="button" className="plan-label" aria-expanded={open === i.id} onClick={() => setOpen(open === i.id ? null : i.id)}>
      <strong>{acronyms(i.title)}</strong>
      <Text small muted> {i.kind.replace(/_/g, " ")} · {short(i.start_at)}–{short(i.end_at)}{i.crew ? ` · ${i.crew}` : ""}</Text>
      {i.clashes.filter((c) => c.kind !== "bundle").length > 0 && <Badge tone="danger">clash</Badge>}
      {i.clashes.some((c) => c.kind === "bundle") && <Badge tone="ok">bundle</Badge>}
      {i.needs_shutdown && <Badge tone="warn">shutdown</Badge>}
      {i.place === "desk" && <Badge>desk</Badge>}
      {i.stage === "released" && <Badge tone="ok">released</Badge>}
      {!!i.parts_short?.length && <Badge tone="warn">parts short</Badge>}
    </button>
    <div className="plan-track" aria-hidden="true">
      {taWindow && <span className="plan-window" style={{ left: `${taWindow.left}%`, width: `${Math.max(0.8, taWindow.right - taWindow.left)}%` }} />}
      {today > 0 && today < 100 && <span className="plan-today" />}
      {bar(i)}
    </div>
    {open === i.id && <Detail item={i} person={person} onChange={changed} />}
  </li>;
  const axis = (
    <div className="plan-row plan-axis" aria-hidden="true">
      <span />
      <div className="plan-axis-track" ref={measure}>
        {ticks.map((t, n) => <span key={t} className="plan-axis-week" style={{ left: `${weekAt(t)}%` }}>{labels[n]}</span>)}
        {nowMark && <span className="plan-axis-today">{nowMark.text}</span>}
      </div>
    </div>
  );
  return (
    <section>
      {!within && <PageHeader
        actions={<Button disabled={!!busy} onClick={() => run("Refreshing from SAP", () => api.planSync(), plan.reload)}>Refresh from SAP</Button>}>
        Plan</PageHeader>}
      <Line>
        <Field inline label="From"><Input type="date" value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} /></Field>
        <Field inline label="Showing"><Select aria-label="Showing" value={String(days)} onChange={(e) => setDays(Number(e.target.value))}>
          {SPANS.map(([n, label]) => <option key={n} value={n}>{label}</option>)}
        </Select></Field>
        {!within && !!scope.data?.revisions?.length && <Field inline label="Turnaround"><Select aria-label="Turnaround" value={turnaround?.revision ?? ""} onChange={(e) => pick(e.target.value)}>
          <option value="">None shown</option>
          {scope.data.revisions.map((r) => <option key={r} value={r}>{r}</option>)}
        </Select></Field>}
        <Badge>{plan.data.length} items</Badge>
        {clashes > 0 && <Badge tone="danger">{clashes} with a clash</Badge>}
        {bundles > 0 && <Badge tone="ok">{bundles} could ride along with a shutdown</Badge>}
        {waiting > 0 && <Badge tone="warn">{waiting} waiting on parts</Badge>}
      </Line>
      {busy && <p role="status">{busy}…</p>}
      <ErrorText>{error}</ErrorText>
      <div className={revision !== null ? "plan-layout with-panel" : "plan-layout"}>
      <div>
      {/* The number a manager wants comes first: production at risk from the planned shutdowns (#1078). */}
      {!within && <AtRiskCard from={from} />}
      {!within && <CrewLoadCard from={from} days={days} pos={pos} lines={lines} axis={axis} />}
      {!plan.data.length && <Empty>Nothing planned on these dates.</Empty>}
      {groups.map(([area, list]) => (
        <Card key={area} title={areaTitle(area, list)} className="plan-area">
          {/* Each area's timeline carries its own date axis, so a row is read against dates, not a bare track (#1078). */}
          <div className="plan-timeline" style={lines}>
          {axis}
          <ul className="plan-rows">{byEquipment(list).map((entry) => {
            if (!Array.isArray(entry)) return row(entry);
            // Field exceptions on one piece of equipment are one row; it opens to each of them.
            const [first] = entry, key = `${area} ${first.subjects[0]}`;
            const shown = openGroup === key || entry.some((i) => i.id === open);
            const ends = entry.map((i) => i.end_at).sort();
            return [<li key={key} className="plan-row plan-group">
              <Button variant="link" className="plan-label" aria-expanded={shown} onClick={() => setOpenGroup(shown ? null : key)}>
                <strong>{first.assets[0]?.label ?? acronyms(first.title)}</strong>
                <Text small muted> {entry.length} exceptions · {short(first.start_at)}–{short(ends[ends.length - 1])}</Text>
                {entry.some((i) => i.clashes.some((c) => c.kind !== "bundle")) && <Badge tone="danger">clash</Badge>}
                {entry.some((i) => i.parts_short?.length) && <Badge tone="warn">parts short</Badge>}
              </Button>
              <div className="plan-track" aria-hidden="true">
                {today > 0 && today < 100 && <span className="plan-today" />}
                {entry.map(bar)}
              </div>
            </li>, ...(shown ? entry.map(row) : [])];
          })}</ul>
          </div>
        </Card>
      ))}
      </div>
      {revision !== null && (turnaround ? <TurnaroundPanel scope={turnaround} person={person} onChange={changed} />
        : <aside className="plan-panel"><Status of={scope} /></aside>)}
      </div>
    </section>
  );
}

function Detail({ item, person, onChange }: { item: PlanItem; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const [start, setStart] = useState(item.start_at.slice(0, 10));
  const [end, setEnd] = useState(item.end_at.slice(0, 10));
  const [crew, setCrew] = useState(item.crew ?? "");
  const change = (c: Parameters<typeof api.planChange>[2]) => person && run("Saving", () => api.planChange(item.id, person, c), onChange);
  const resources = useAsync(() => api.planResources(), []);
  const crews = (resources.data ?? []).filter((r) => r.kind === "crew");
  const [resource, setResource] = useState("");
  const [people, setPeople] = useState("1");
  // a booking over the crew's capacity is refused with what it clashes with; it goes ahead only with a reason
  const book = () => person && resource && run("Booking", async () => {
    const b = { resource, quantity: Number(people) || 1, person: crew.trim() || undefined };
    try { return await api.planBook(item.id, person, b); }
    catch (e) {
      if ((e as { status?: number }).status !== 409) throw e;
      const why = await promptDialog(errorMessage(e), { confirm: "Book anyway" });
      return why ? api.planBook(item.id, person, { ...b, reason: why }) : null;
    }
  }, onChange);
  const asked = useAsync(() => api.planPrerequisites(item.id), [item.id, item.status, item.released_at, item.start_at]);
  const pre = Array.isArray(asked.data?.prerequisites) ? asked.data : null;  // nothing shown until it reads
  const fieldJob = (item.place ?? "field") === "field" && ["proposed", "scheduled", "in_progress"].includes(item.status);
  // field work reaches the field through Release; with a prerequisite open it goes only with a reason
  const release = () => person && run("Releasing", async () => {
    try { return await api.planRelease(item.id, person); }
    catch (e) {
      if ((e as { status?: number }).status !== 409) throw e;
      const why = await promptDialog(errorMessage(e), { confirm: "Release anyway" });
      return why ? api.planRelease(item.id, person, why) : null;
    }
  }, onChange);
  // moving a released job says why, and it waits for release again on its new dates
  const reschedule = async () => {
    if (!person) return;
    const to = { start, end: end < start ? start : end, crew };
    if (!item.released_at) return change(to);
    const why = await promptDialog("It was released. Why does it move? It waits for release again on the new dates.",
                                   { confirm: "Reschedule" });
    if (why) run("Rescheduling", async () => {
      await api.planReschedule(item.id, person, to.start, to.end, why);
      return to.crew !== (item.crew ?? "") ? api.planChange(item.id, person, { crew: to.crew }) : null;
    }, onChange);
  };
  return (
    <div className="plan-detail">
      {fieldJob && pre && <Text as="div" small>
        {item.released_at
          ? <Badge tone="ok">Released {whenDay(item.released_at)}{item.released_by ? ` by ${item.released_by}` : ""}</Badge>
          : <Badge tone={pre.ready ? "ok" : "warn"}>{pre.ready ? "Ready" : "Not ready"}</Badge>}
        {item.release_reason && <Text as="span" small muted> Why it went anyway: {item.release_reason}</Text>}
        <List>{pre.prerequisites.map((p) => <li key={p.kind} className={p.satisfied ? "" : "error"}>
          {p.satisfied ? "✓" : "✗"} {p.label}{p.detail.length ? `: ${p.detail.join("; ")}` : ""}</li>)}</List>
      </Text>}
      {item.clashes.map((c, n) => <p key={n} className={`small ${c.kind === "bundle" ? "" : "error"}`}>
        {c.kind === "bundle" ? "Bundle: " : c.kind === "simops" ? "Simultaneous operations: " : "Clash: "}{say(c.say)}</p>)}
      {!!item.parts_short?.length && <Text as="p" small>
        Parts with no stock on record (check with stores before it starts): {item.parts_short.map((p) =>
          `${p.label ?? p.id}${p.description ? ` (${p.description})` : ""}`).join(", ")}</Text>}
      <Text as="p" small>{item.assets.map((a) => <a key={a.fuzer_id} href={entityHref(a.fuzer_id)}>{a.label ?? a.fuzer_id} </a>)}</Text>
      {item.item_id != null ? <Text as="p" small><a href={href(`/cases/${item.item_id}`)}>Open its case</a></Text>
        : item.exception_id != null && <Text as="p" small><a href={href("/list", { q: item.title })}>Open this work in All work</a></Text>}
      <Text as="p" small muted>{item.source}{item.source_key ? ` ${item.source_key}` : ""}{item.notes ? ` · ${item.notes}` : ""}
        {item.deferment_usd != null ? ` · ${money(item.deferment_usd)} at risk` : ""}</Text>
      {person ? <Line>
        <Field inline label="Start"><Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
        <Field inline label="End"><Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
        {/* who goes, from the roster: a person or a role's queue, never typed as "role:…" (P5.28g) */}
        <Field inline label="Who"><AssignPicker value={crew} onChange={setCrew} label="Who" /></Field>
        <Field inline label="Done"><Select aria-label="Where it is done" value={item.place ?? "field"} disabled={!!busy}
          onChange={(e) => change({ place: e.target.value === "desk" ? "desk" : "field" })}>
          <option value="field">in the field</option><option value="desk">at a desk</option>
        </Select></Field>
        <Button size="sm" disabled={!!busy} onClick={reschedule}>Reschedule</Button>
        {fieldJob && !item.released_at && <Button size="sm" variant="primary" disabled={!!busy} onClick={release}>Release</Button>}
        {crews.length > 0 && <>
          <Field inline label="Crew"><Select aria-label="Crew" value={resource} onChange={(e) => setResource(e.target.value)}>
            <option value="">choose a crew</option>
            {crews.map((r) => <option key={r.code} value={r.code}>{r.name} ({r.capacity})</option>)}
          </Select></Field>
          <Field inline label="People"><Input type="number" min={1} value={people} onChange={(e) => setPeople(e.target.value)} /></Field>
          <Button size="sm" disabled={!!busy || !resource} onClick={book}>Book</Button>
        </>}
        <Select aria-label="Status" value={item.status} disabled={!!busy} onChange={async (e) => {
          const status = e.target.value;
          // taking planned work off the plan leaves the work itself open, for the IOC to decide again
          if (status === "cancelled" && item.exception_id != null
              && !(await confirmDialog("Take this off the plan? The work itself stays open on All work.",
                                       { confirm: "Take it off", danger: true }))) return;
          change({ status });
        }}>
          {STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
        </Select>
      </Line> : <Text as="p" small>Choose who you are to change the plan.</Text>}
      <ErrorText>{error}</ErrorText>
    </div>
  );
}

const num = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** Crew load on the plan itself (red team #25): each shared crew or resource the plan uses in these weeks, day by day
 * against its capacity, on the same dates as the rows below, so an overloaded day shows beside the jobs that cause it.
 * Resources owns the detail: who is in each overload and the move that clears it. */
function CrewLoadCard({ from, days, pos, lines, axis }: { from: string; days: number; pos: (at: string) => number;
                                                         lines: CSSProperties; axis: ReactNode }) {
  const load = useAsync(() => api.resourceLoad({ start: from, days }), [from, days]);
  const all = load.data?.resources ?? [];
  const used = all.filter((r) => r.days.some((d) => d.demand > 0))
    .sort((a, b) => Number(b.kind === "crew") - Number(a.kind === "crew") || a.name.localeCompare(b.name));
  const idle = all.filter((r) => !used.includes(r));
  const over = (r: ResourceLoad) => r.days.filter((d) => d.demand > d.capacity + 1e-6).length;
  const overloaded = used.filter((r) => over(r) > 0).length;
  return (
    <Card title="Crew load" className="plan-area plan-load"
      actions={<a href="#/plan?resources=1">Resources</a>}>
      <Status of={load} />
      {load.data && (used.length ? <>
        <Text as="p" small>{overloaded ? `${overloaded} of ${used.length} over capacity on some days.` : `All ${used.length} within capacity.`}
          {" "}Each column is a day's demand at its busiest hour: full height is the capacity, red is over it.</Text>
        <div className="plan-timeline" style={lines}>
          {axis}
          <ul className="plan-rows">{used.map((r) => {
            const n = over(r);
            const peak = Math.max(...r.days.map((d) => d.demand));
            return <li key={r.code} className="plan-row">
              <a className="plan-label" href={`#/plan?resources=1&resource=${encodeURIComponent(r.code)}`}>
                <strong>{r.name}</strong>
                <Text small muted> peak {num(peak)} of {num(r.capacity)}{r.unit ? ` ${r.unit}` : ""}</Text>
                {n > 0 && <Badge tone="danger">over on {n} {n === 1 ? "day" : "days"}</Badge>}
              </a>
              <div className="plan-track plan-load-track" role="img"
                aria-label={`${r.name}: ${n ? `over capacity on ${n} ${n === 1 ? "day" : "days"}` : `peak ${num(peak)} of ${num(r.capacity)}`}`}>
                {r.days.filter((d) => d.demand > 0).map((d) => {
                  const left = pos(d.day), right = pos(new Date(new Date(d.day).getTime() + DAY).toISOString());
                  const share = d.capacity > 0 ? d.demand / d.capacity : 1;
                  const at: CSSProperties = { left: `${left}%`, width: `${Math.max(0.3, right - left)}%`,
                                              height: `${Math.min(100, Math.max(12, share * 100))}%` };
                  return <span key={d.day} className={`plan-load-day${d.demand > d.capacity + 1e-6 ? " over" : ""}`}
                    title={`${short(d.day)}: ${num(d.demand)} of ${num(d.capacity)}${r.unit ? ` ${r.unit}` : ""}`} style={at} />;
                })}
              </div>
            </li>;
          })}</ul>
        </div>
        {!!idle.length && <Text as="p" small>Not used on these dates: {idle.map((r) => r.name).join(", ")}.</Text>}
      </> : <Empty>No shared crew or resource is used on these dates.</Empty>)}
    </Card>
  );
}

const QUANTITY: Record<string, string> = { oil_rate: "Oil", gas_rate: "Gas", condensate_rate: "Condensate" };
const amount = (r: { quantity: string; value: number; unit: string | null }) =>
  `${QUANTITY[r.quantity] ?? r.quantity.replace(/_/g, " ")} about ${estimate(r.value)}${r.unit ? ` ${r.unit}` : ""}`;

/** Production at risk from planned shutdowns over the next 90 days (P5.9a): an estimate for planners, each number
 * with the durations it used. */
function AtRiskCard({ from }: { from: string }) {
  const risk = useAsync(() => api.planAtRisk(new Date(Math.max(Date.now(), new Date(from).getTime())).toISOString(), 90), [from]);
  const [open, setOpen] = useState<number | null>(null);
  const d: AtRisk | undefined = risk.data && Array.isArray(risk.data.windows) ? risk.data : undefined;
  return (
    <Card title="Production at risk from planned shutdowns" className="plan-at-risk">
      <Status of={risk} />
      {d && (d.windows.length ? <>
        <Text as="p" small>The next {d.days} days. {d.basis} An estimate for planning, not a schedule.</Text>
        <List plain>{d.windows.map((w, n) => <li key={w.start} className="plan-risk-window">
          <Button variant="link" className="plan-label" aria-expanded={open === n} onClick={() => setOpen(open === n ? null : n)}>
            <strong>{short(w.start)}–{short(w.end)}</strong>{" "}
            <span>{w.at_risk.length ? w.at_risk.map(amount).join(", ") : "no tested wells behind it"}</span>{" "}
            <Text small muted>{w.wells.length} wells · {w.items.length} {w.items.length === 1 ? "job" : "jobs"}</Text>
          </Button>
          {open === n && <Text as="div" small>
            <ul>{w.items.map((i) => <li key={i.id}>{acronyms(i.title)}: {i.hours} h down
              {i.units.length ? ` (${i.units.map((u) => u.label).join(", ")})` : " (not on the flow network)"}
              {i.assumed && <Text muted> · assumed</Text>}</li>)}</ul>
            {w.assumptions.map((a) => <p key={a}>Assumption: {a}.</p>)}
            {!!w.untested.length && <p>No test on record, so not counted: {w.untested.join(", ")}.</p>}
            <p>{w.wells.map((x) => `${x.label} ${x.hours} h`).join(" · ")}</p>
          </Text>}
        </li>)}</List>
      </> : <Empty>No shutdowns planned in the next {d.days} days.</Empty>)}
    </Card>
  );
}
