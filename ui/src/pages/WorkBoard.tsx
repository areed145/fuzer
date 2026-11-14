/** The work board: All work's open exceptions as columns, one per stage of the IOC's flow, so where everything sits
 * shows on one screen. Each column uses the same rule as the view of the same name, so its count matches the tab and
 * Home. Lanes split the columns by source or kind. A card's button is the next step: dispatch it, or close out what
 * came back from the field on its case. On a phone one stage shows at a time. */
import "../styles/work-board.css";
import { useState } from "react";
import { api, type ExceptionRow } from "../api";
import { Badge, Button, ButtonLink, Empty, Field, Select } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";
import { holder, usePerson } from "../work";
import { useEntityNames } from "../useEntityNames";
import { display } from "../entityRefs";
import { DispatchControls, backFromField, waitingToDispatch, withTheField, type Person } from "./Exceptions";

type StageKey = "dispatch" | "desk" | "plan" | "field" | "back" | "closed";

/** The stages in the order work moves through them. `short` names the stage on a phone's chips. */
export const STAGES: { key: StageKey; label: string; short: string; note: string }[] = [
  { key: "dispatch", label: "To dispatch", short: "Dispatch", note: "not given to anyone" },
  { key: "desk", label: "With a desk", short: "Desk", note: "held in the IOC" },
  { key: "plan", label: "On the plan", short: "Plan", note: "booked" },
  { key: "field", label: "With the field", short: "Field", note: "sent out" },
  { key: "back", label: "Back from the field", short: "Back", note: "IOC to close out" },
  { key: "closed", label: "Closed today", short: "Closed", note: "since midnight" },
];

/** Which stage an open exception is at, by the same rules as All work's views. */
export function stageOf(r: ExceptionRow, operators: Set<string>): StageKey {
  if (r.state !== "open") return "closed";
  if (waitingToDispatch(r)) return "dispatch";
  if (backFromField(r)) return "back";
  if (withTheField(r, operators)) return "field";
  if (!r.item_id && r.planned) return "plan";
  return "desk";
}

export type Lanes = "none" | "source" | "kind";
const LANES: { key: Lanes; label: string }[] = [
  { key: "none", label: "None" }, { key: "source", label: "Source" }, { key: "kind", label: "Kind" },
];

/** A span of time as an operator says it: 15m, 2h 10m, 1d 4h. */
export function age(ms: number): string {
  const m = Math.max(0, Math.floor(ms / 60000));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  return h % 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${Math.floor(h / 24)}d`;
}

const money = (usd: number) => usd >= 1000 ? `$${Math.round(usd / 1000).toLocaleString()}k` : `$${Math.round(usd)}`;
const lastField = (r: ExceptionRow) => r.evidence?.field?.[r.evidence.field.length - 1];
const FIELD_SAID: Record<string, string> = { on_site: "on site", done: "field: done", could_not_do: "could not do", note: "field note" };

function midnight(): string {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}

export function WorkBoardPage({ query, filters = {} }: {
  query: URLSearchParams; filters?: { type?: string; who?: string; q?: string };
}) {
  const type = filters.type || undefined;
  const open = useAsync(() => api.exceptions({ state: "open", type }), [type]);
  const [since] = useState(midnight);
  const closed = useAsync(() => api.exceptions({ state: "any", type, closed_since: since }), [type, since]);
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const kinds = useAsync(() => api.exceptionKinds(), []);
  const [person] = usePerson();
  if (!open.data) return <Status loading={open.loading} error={open.error} />;
  const lanes: Lanes = LANES.some((l) => l.key === query.get("lanes")) ? query.get("lanes") as Lanes : "none";
  const stage = (STAGES.some((s) => s.key === query.get("stage")) ? query.get("stage") : "dispatch") as StageKey;
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const queues = (roles.data ?? []).filter((r) => r.kind === "person").map((r) => r.name);
  const kindLabel = new Map((kinds.data ?? []).map((k) => [k.name, k.label]));
  const words = filters.q?.toLowerCase();
  const rows = [...open.data, ...(closed.data ?? []).filter((r) => r.state !== "open")]
    .filter((r) => !filters.who || r.assignee === filters.who || (!!r.role && `role:${r.role}` === filters.who))
    .filter((r) => !words || r.title.toLowerCase().includes(words));
  const by = new Map<StageKey, ExceptionRow[]>(STAGES.map((s) => [s.key, []]));
  for (const r of rows) by.get(stageOf(r, operators))!.push(r);
  const laneOf = (r: ExceptionRow) => lanes === "source" ? r.source ?? "Other"
    : lanes === "kind" ? kindLabel.get(r.type) ?? r.type.replace(/_/g, " ") : "";
  const refresh = () => { open.reload(); closed.reload(); };
  const keep = Object.fromEntries([...query.entries()].filter(([k]) => k !== "stage" && k !== "list"));
  const setLanes = (next: Lanes) => location.replace(href("/exceptions", { ...keep, list: "board", lanes: next === "none" ? undefined : next,
    stage: query.get("stage") ?? undefined }));
  return (
    <>
      <div className="row wrap work-board-controls">
        <Field label="Lanes" width="sm">
          <Select value={lanes} onChange={(e) => setLanes(e.target.value as Lanes)}>
            {LANES.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
          </Select>
        </Field>
      </div>
      <nav className="chips work-board-stages" aria-label="Stages">
        {STAGES.map((s) => <a key={s.key} href={href("/exceptions", { ...keep, list: "board", stage: s.key })}
          className={s.key === stage ? "chip on" : "chip"} aria-current={s.key === stage ? "true" : undefined}>
          {s.short} <span className="muted">{by.get(s.key)!.length}</span></a>)}
      </nav>
      <div className="work-board">
        {STAGES.map((s) => <Column key={s.key} stage={s} rows={by.get(s.key)!} lane={lanes} laneOf={lanes === "none" ? null : laneOf}
          shown={s.key === stage} person={person} people={people.data ?? []} queues={queues} kindLabel={kindLabel}
          closedHref={href("/exceptions", { ...keep, list: "closed" })} onChange={refresh} />)}
      </div>
    </>
  );
}

const CLOSED_SHOWN = 8;

function Column({ stage, rows, lane, laneOf, shown, person, people, queues, kindLabel, closedHref, onChange }: {
  stage: (typeof STAGES)[number]; rows: ExceptionRow[]; lane: Lanes; laneOf: ((r: ExceptionRow) => string) | null; shown: boolean;
  person: string | null; people: Person[]; queues: string[]; kindLabel: Map<string, string>; closedHref: string;
  onChange: () => void;
}) {
  const now = Date.now();
  const closed = stage.key === "closed";
  const value = rows.reduce((sum, r) => sum + (r.value_usd ?? 0), 0);
  const oldest = rows.length && !closed ? Math.max(...rows.map((r) => now - new Date(r.first_seen).getTime())) : 0;
  const list = closed ? rows.slice(0, CLOSED_SHOWN) : rows;
  const groups = new Map<string, ExceptionRow[]>();
  for (const r of list) {
    const lane = laneOf ? laneOf(r) : "";
    groups.set(lane, [...(groups.get(lane) ?? []), r]);
  }
  const sorted = [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  return (
    <section className={shown ? "work-board-column shown" : "work-board-column"} aria-label={stage.label}>
      <h2><span>{stage.label}</span> <span className="muted">{rows.length}</span></h2>
      <p className="small muted">{[stage.note, value > 0 && !closed ? `${money(value)} at stake` : null,
        oldest ? `oldest ${age(oldest)}` : null].filter(Boolean).join(" · ")}</p>
      {!rows.length ? <Empty>Nothing here.</Empty> : sorted.map(([name, items]) => <div key={name || "all"}>
        {laneOf && <h3 className="work-board-lane">{name}</h3>}
        {items.map((r) => <WorkCard key={r.id} r={r} stage={stage.key} lane={lane} now={now} person={person} people={people}
          queues={queues} kind={kindLabel.get(r.type) ?? r.type.replace(/_/g, " ")} onChange={onChange} />)}
      </div>)}
      {closed && rows.length > CLOSED_SHOWN && <a className="small" href={closedHref}>
        {rows.length - CLOSED_SHOWN} more closed</a>}
    </section>
  );
}

function WorkCard({ r, stage, lane, now, person, people, queues, kind, onChange }: {
  r: ExceptionRow; stage: StageKey; lane: Lanes; now: number; person: string | null; people: Person[]; queues: string[];
  kind: string; onChange: () => void;
}) {
  const names = useEntityNames(r.subjects.slice(0, 1));
  const [sending, setSending] = useState(false);
  const said = lastField(r);
  const couldNot = said?.status === "could_not_do";
  const reports = r.reports ?? r.raised_count;
  const since = stage === "closed" && r.closed_at ? new Date(r.closed_at).toLocaleTimeString(undefined,
    { hour: "2-digit", minute: "2-digit" }) : said && (stage === "field" || stage === "back")
    ? `${age(now - new Date(said.at).getTime())} ago` : age(now - new Date(r.first_seen).getTime());
  const who = r.item_id ? holder(r.assignee, r.role) : r.planned?.crew ?? null;
  const canSend = !!person && r.state === "open" && (stage === "dispatch" || (stage === "back" && couldNot));
  return (
    <article className={couldNot && stage === "back" ? "work-card attention" : "work-card"}>
      <div className="work-card-title">{r.item_id ? <a href={href(`/cases/${r.item_id}`)}>{r.title}</a> : r.title}</div>
      {r.subjects[0] && <a className="small" href={entityHref(r.subjects[0])}>{display(r.subjects[0], names)}
        {r.subjects.length > 1 ? ` +${r.subjects.length - 1}` : ""}</a>}
      <div className="small muted">{[lane !== "kind" ? kind : null, lane !== "source" ? r.source : null,
        reports > 1 ? `${reports} reports` : null].filter(Boolean).join(" · ")}</div>
      {said && stage !== "dispatch" && stage !== "closed" && <div className="small">
        <Badge tone={couldNot ? "warn" : "ok"} title={said.note ?? undefined}>{FIELD_SAID[said.status] ?? said.status}</Badge></div>}
      {stage === "plan" && r.planned && <a className="small" href={href("/plan")}>planned {new Date(r.planned.start_at)
        .toLocaleDateString(undefined, { day: "numeric", month: "short" })}</a>}
      <div className="work-card-meta small">
        <span className="muted">{who ?? (stage === "closed" ? r.state : "")}</span>
        <span>{[since, r.value_usd != null && stage !== "closed" ? money(r.value_usd) : null].filter(Boolean).join(" · ")}</span>
      </div>
      {(canSend || (stage === "back" && r.item_id)) && <div className="row work-card-actions">
        {canSend && <Button size="sm" variant={stage === "dispatch" ? "primary" : "secondary"} aria-expanded={sending}
          onClick={() => setSending(!sending)}>{stage === "dispatch" ? "Dispatch" : "Re-dispatch"}</Button>}
        {stage === "back" && r.item_id && <ButtonLink size="sm" variant={couldNot ? "secondary" : "primary"}
          href={href(`/cases/${r.item_id}`)}>Close out</ButtonLink>}
      </div>}
      {sending && person && <DispatchControls r={r} person={person} people={people} queues={queues}
        onDone={() => { setSending(false); onChange(); }} />}
    </article>
  );
}
