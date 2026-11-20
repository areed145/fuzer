/** Needs you (Home): one live list of what is waiting on the signed-in person, across every field they hold roles in,
 * and what others did on their work since they last looked. Each item opens the record it is about, and the map rings
 * the place that record sits at: while an item is pointed at or focused, and for a few seconds when one arrives.
 * Read only; it reads again every 30 s while the page is in view. */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { when } from "../format";
import { api, type ExceptionRow, type LocatedPlace, type WorkHome, type WorkInbox, type WorkRow } from "../api";
import { Badge, Button, Empty } from "../ui";
import { Status, href, useAsync, useLive } from "../lib";
import { Actor, Due, holder, usePerson } from "../work";
import { useAuth } from "../Auth";
import { switchProject } from "../authState";
import { GENERIC_STEP, StepBadges, byUrgency, caseHref, taskHref } from "./workflows/shared";
import { waitingToDispatch } from "./Exceptions";
import { workHref } from "./AllWork";

const TOP = 12;
const CHANGES = 10;
const NEW_FOR = 120_000;   // an arrival reads as new for two minutes
const RING_FOR = 8_000;    // and the map rings its place for eight seconds
const SAID: Record<string, string> = {
  created: "opened", handed_off: "handed on", escalated: "escalated", commented: "commented", state_changed: "changed state",
  completed_process: "finished", cancelled: "cancelled", gated: "stopped for a person", failed: "a step failed",
  agent_failed: "an agent step failed", delegated: "delegated", item_changed: "changed",
};

export interface FeedItem {
  key: string; title: string; to: string; project?: string; meta: ReactNode; due?: string | null; late: boolean;
  subjects: string[]; at: string; priority: number | null;
}
type Change = WorkHome["changes"][number];
export type Ring = { at: [number, number]; label: string } | null;

const rank = (a: FeedItem, b: FeedItem) =>
  Number(b.late) - Number(a.late) || byUrgency({ due_at: a.due, priority: a.priority }, { due_at: b.due, priority: b.priority })
  || b.at.localeCompare(a.at);

/** The keys that appeared after the first read, each with when it did. */
function useArrivals(keys: string[] | null): Map<string, number> {
  const seen = useRef<Set<string> | null>(null);
  const [arrived, setArrived] = useState(new Map<string, number>());
  useEffect(() => {
    if (!keys) return;
    if (!seen.current) { seen.current = new Set(keys); return; }
    const fresh = keys.filter((k) => !seen.current!.has(k));
    if (!fresh.length) return;
    fresh.forEach((k) => seen.current!.add(k));
    setArrived((m) => new Map([...m, ...fresh.map((k) => [k, Date.now()] as const)]));
  }, [keys?.join("|")]);  // eslint-disable-line react-hooks/exhaustive-deps
  return arrived;
}

export function NeedsYou({ inbox, exceptions, steward, configure, onRing, top, open, onToggle }: {
  /** The person's own work here, read (and kept live) by Home, which takes their roles from it. */
  inbox: { data?: WorkInbox | null; error?: string; reload: () => void };
  /** The open exceptions for a dispatcher (those waiting to dispatch join the list), undefined while they load, null
   * for anyone else. Home reads them for its counts too. */
  exceptions: ExceptionRow[] | null | undefined; steward: boolean; configure: boolean; onRing: (ring: Ring) => void;
  /** Shown above the list: the place chosen on the map. */
  top?: ReactNode;
  /** On a phone the feed is a sheet over the map: open, or only its header. */
  open: boolean; onToggle: () => void;
}) {
  const [person] = usePerson();
  const session = useAuth();
  const here = session?.project ?? "";
  const fields = useAsync(() => (person ? api.inboxFields(person) : Promise.resolve(null)), [person]);
  const since = useAsync(() => (person ? api.workHome(person, true) : Promise.resolve(null)), [person]);
  const later = useAsync(() => (person ? api.workHome(person, false) : Promise.resolve(null)), [person]);
  const loads = useAsync(() => (steward ? api.sourceHealth() : Promise.resolve([])), [steward]);
  const jobs = useAsync(() => (configure ? api.jobsHealth() : Promise.resolve(null)), [configure]);
  useLive([fields.reload, later.reload, loads.reload, jobs.reload]);

  const settled = !person || [inbox, fields, since].every((r) => r.data !== undefined || !!r.error);
  // Everything that can add an item has answered, so what comes after is an arrival, not the first load.
  const complete = settled && exceptions !== undefined && [loads, jobs].every((r) => r.data !== undefined || !!r.error);
  const items = useMemo(() => {
    const out: FeedItem[] = [];
    const row = (r: WorkRow, project: string): FeedItem => ({
      key: `t-${project}-${r.task_id ?? "i"}-${r.item_id}`, title: r.title, to: r.task_id ? taskHref(r.task_id) : caseHref(r.item_id),
      project: project === here ? undefined : project, due: r.due_at, late: !!r.overdue || !!r.escalated_at,
      subjects: r.subjects ?? [], at: r.created_at, priority: r.priority,
      meta: <>{!GENERIC_STEP.test(r.name ?? "") && <><strong>{r.name ?? "open"}</strong> · </>}{r.type.replace(/_/g, " ")} · with {holder(r.assignee, r.role, r.kind)}
        {project !== here && <> · <Badge>{project || "this field"}</Badge></>} <StepBadges row={r} /></>,
    });
    if (inbox.data) out.push(...[...inbox.data.mine, ...inbox.data.queue].map((r) => row(r, here)));
    for (const f of fields.data ?? []) if (f.project !== here) out.push(...[...f.mine, ...f.queue].map((r) => row(r, f.project)));
    for (const x of (exceptions ?? []).filter(waitingToDispatch)) {
      out.push({ key: `x-${x.id}`, title: x.title, to: workHref("dispatch", { q: x.title }), late: false, subjects: x.subjects ?? [],
                 at: x.first_seen, priority: x.priority, meta: <><strong>To dispatch</strong> · {x.source ?? "unknown source"} · since {when(x.first_seen)}</> });
    }
    for (const d of (loads.data ?? []).filter((d) => d.problems.includes("held") || d.problems.includes("failed"))) {
      const held = d.problems.includes("held");
      out.push({ key: `l-${d.dataset}`, title: `${d.dataset}: load ${held ? "held" : "failed"}`, late: !held, subjects: [], priority: null,
                 to: href("/sources", { view: "dataset", name: d.dataset }), at: d.last_loaded ?? "",
                 meta: <><strong>{held ? "Fix the held load" : "Look at the failed run"}</strong> · {d.system} · {d.last_loaded ? `last ingested ${when(d.last_loaded)}` : "never ingested"}</> });
    }
    if (jobs.data?.available && (jobs.data.failed_runs_24h > 0 || jobs.data.failing_checks > 0)) {
      out.push({ key: "jobs", title: jobs.data.summary, to: href("/jobs"), late: true, subjects: [], at: "", priority: null,
                 meta: <><strong>Scheduled jobs</strong> · Jobs</> });
    }
    return out.sort(rank);
  }, [inbox.data, fields.data, exceptions, loads.data, jobs.data, here]);

  // What others did since the last look: the first read, then each later read's new events added on top.
  const [changes, setChanges] = useState<Change[]>([]);
  useEffect(() => { if (since.data) setChanges(since.data.changes ?? []); }, [since.data]);
  useEffect(() => {
    if (!later.data) return;
    setChanges((c) => [...(later.data!.changes ?? []).filter((n) => !c.some((o) => o.id === n.id)), ...c]);
  }, [later.data]);

  const arrived = useArrivals(complete ? [...items.map((i) => i.key), ...changes.map((c) => `c-${c.id}`)] : null);

  // Where each item's subjects sit, so the map can ring them.
  const subjects = useMemo(() => [...new Set([...items.flatMap((i) => i.subjects), ...changes.flatMap((c) => c.subjects ?? [])])].sort().slice(0, 200),
                           [items, changes]);
  const located = useAsync(() => (subjects.length ? api.attentionPlaces(subjects) : Promise.resolve({} as Record<string, LocatedPlace>)),
                           [subjects.join(",")]);
  const placeOf = (ids: string[]) => ids.map((s) => located.data?.[s]).find(Boolean) ?? null;
  const ringOf = (ids: string[], title: string): Ring => {
    const p = placeOf(ids);
    return p ? { at: [p.lat, p.lon], label: `${p.name ?? p.label}: ${title}` } : null;
  };

  // An arrival rings its place for a few seconds, the newest one if several came at once.
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = window.setInterval(() => setNow(Date.now()), 15_000); return () => window.clearInterval(t); }, []);
  const lastArrival = useRef(0);
  useEffect(() => {
    const newest = [...arrived.entries()].sort((a, b) => b[1] - a[1])[0];
    if (!newest || newest[1] <= lastArrival.current || !located.data) return;
    lastArrival.current = newest[1];
    const item = items.find((i) => i.key === newest[0]);
    const change = changes.find((c) => `c-${c.id}` === newest[0]);
    const ring = item ? ringOf(item.subjects, item.title) : change ? ringOf(change.subjects ?? [], change.title) : null;
    if (!ring) return;
    onRing(ring);
    const t = window.setTimeout(() => onRing(null), RING_FOR);
    return () => window.clearTimeout(t);
  }, [arrived, located.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  const fresh = (key: string) => (arrived.get(key) ?? 0) > now - NEW_FOR;
  // Pointing at an item rings its place while the pointer is there; Show on the map keeps the ring until another is asked
  // for, so a phone (no hover) can close the sheet and see it.
  const pinned = useRef<Ring>(null);
  const point = (ids: string[], title: string) => ({
    onMouseEnter: () => onRing(ringOf(ids, title)), onMouseLeave: () => onRing(pinned.current),
    onFocus: () => onRing(ringOf(ids, title)), onBlur: () => onRing(pinned.current),
  });
  const showOnMap = (ids: string[], title: string) => {
    pinned.current = ringOf(ids, title);
    onRing(pinned.current);
    if (open) onToggle();
  };

  const shown = items.slice(0, TOP);
  const own = changes.filter((c) => !c.watching);
  const watched = changes.filter((c) => c.watching);
  const changeList = (rows: Change[], label: string) => (
    <ol className="feed feed-changes" aria-label={label}>{rows.slice(0, CHANGES).map((c) => (
      <li key={c.id} className={`feed-item${fresh(`c-${c.id}`) ? " fresh" : ""}`} {...point(c.subjects ?? [], c.title)}>
        <a href={caseHref(c.item_id)} className="feed-title">{c.title}</a>
        <span className="small muted">{SAID[c.kind] ?? c.kind.replace(/_/g, " ")} by <Actor who={c.actor} /> · {when(c.at)}
          {typeof c.data.text === "string" && c.data.text ? <>: {c.data.text.slice(0, 100)}</> : null}</span>
      </li>))}</ol>);
  return (
    <aside className={`home-feed${open ? " open" : ""}`} aria-label="Needs you">
      {/* On a phone the header is the sheet's handle: a tap anywhere on it opens or closes the list. */}
      <div className="home-feed-head" onClick={onToggle}>
        <span className="home-feed-grab" aria-hidden="true" />
        <h2>Needs you</h2>
        {settled && <span className="count">{items.length}</span>}
        <span className="home-feed-toggle">
          <Button variant="ghost" size="sm" aria-expanded={open} aria-label={open ? "Close Needs you" : "Open Needs you"}
                  onClick={(e) => { e.stopPropagation(); onToggle(); }}>
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d={open ? "M6 9l6 6 6-6" : "M6 15l6-6 6 6"} /></svg>
          </Button>
        </span>
      </div>
      <div className="home-feed-body">
        {top}
        {!person ? <Empty className="small">Choose your name under “You are” in the menu.</Empty>
          : !settled ? <Status loading />
          : <>
            <Status error={inbox.error} onRetry={inbox.reload} />
            {shown.length ? <ol className="feed" aria-label="Waiting on you">{shown.map((i) => (
              <li key={i.key} className={`feed-item${i.late ? " late" : ""}${fresh(i.key) ? " fresh" : ""}`} {...point(i.subjects, i.title)}>
                <span className="feed-line">
                  {i.project ? <Button variant="link" className="feed-title" onClick={() => switchProject(i.project!, i.to)}>{i.title}</Button>
                    : <a href={i.to} className="feed-title">{i.title}</a>}
                  {i.due !== undefined && <Due at={i.due ?? null} />}
                </span>
                <span className="small muted">{i.meta}</span>
                {placeOf(i.subjects) && <span className="feed-locate"><Button variant="ghost" size="sm" aria-label={`Show ${i.title} on the map`}
                  title="Show on the map" onClick={() => showOnMap(i.subjects, i.title)}><Pin /></Button></span>}
              </li>))}</ol>
              : !inbox.error && <Empty className="small">Nothing waiting for you.</Empty>}
            {items.length > TOP && <a className="small" href={href("/inbox")}>{items.length - TOP} more on My work</a>}
            <h3>Since you last looked <span className="muted small">{since.data?.seen_at ? when(since.data.seen_at) : "in the last day"}</span></h3>
            {own.length ? changeList(own, "Since you last looked") : <Empty className="small">Nothing new on your work.</Empty>}
            {(watched.length > 0 || (since.data?.watching?.length ?? 0) > 0) && <>
              <h3>Watching <span className="muted small">not assigned to you</span></h3>
              {watched.length ? changeList(watched, "Watching") : <Empty className="small">Nothing new on what you watch.</Empty>}
            </>}
          </>}
      </div>
    </aside>
  );
}

function Pin() {
  return <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
    <path d="M12 21s-6-5.6-6-11a6 6 0 0 1 12 0c0 5.4-6 11-6 11z" /><circle cx="12" cy="10" r="2.2" /></svg>;
}
