/** Exceptions: everything the IOC should work, from every source, in one list. Each is one open condition matched
 * to the assets it is about; the ones no queue picked up wait here for someone to dispatch them. */
import { useEffect, useState } from "react";
import { money } from "../format";
import { api, type ExceptionRow, type Readiness } from "../api";
import { Badge, Button, Card, Disclosure, Field, Input, Select, Table, Empty } from "../ui";
import { Status, entityHref, href, planItemHref, useAsync } from "../lib";
import { holder, reportedBy, usePerson, when } from "../work";
import { useEntityNames } from "../useEntityNames";
import { display, type EntityNames } from "../entityRefs";
import { PriorityBadge, SampleTeam, useAct } from "./workflows/shared";
import { usePermission } from "../Auth";
import { MatchRef } from "./masterdata/MatchRef";
import { person as personName, queue, say, dayStamp } from "../wording";

export type ExceptionView = "dispatch" | "field" | "back" | "open";

const EMPTY: Record<ExceptionView, string> = {
  dispatch: "Nothing waiting to be dispatched.",
  field: "Nothing is with the field right now.",
  back: "Nothing back from the field waiting on the IOC.",
  open: "Nothing here.",
};

/** The exception views of All work (#502): to dispatch, with the field, back from the field, and every exception in
 * a chosen state. */
export function ExceptionsPage({ query, view = "open", filters = {} }: {
  query?: URLSearchParams; view?: ExceptionView; filters?: { type?: string; who?: string; q?: string; at?: string };
}) {
  const [person] = usePerson();
  const [state, setState] = useState(view === "open" ? query?.get("state") ?? "open" : "open");
  const type = filters.type ?? "";
  const rows = useAsync(() => api.exceptions({ state: state || "any", type: type || undefined, at: filters.at }),
                        [state, type, filters.at]);
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const queues = (roles.data ?? []).filter((r) => r.kind === "person").map((r) => r.name);
  if (!rows.data) return <Status loading={rows.loading} error={rows.error} />;
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const words = filters.q?.toLowerCase();
  const shown = (view === "dispatch" ? rows.data.filter(waitingToDispatch)
    : view === "back" ? rows.data.filter(backFromField)
    : view === "field" ? rows.data.filter((r) => withTheField(r, operators))
    : rows.data)
    .filter((r) => !filters.who || r.assignee === filters.who || (!!r.role && `role:${r.role}` === filters.who))
    .filter((r) => !words || r.title.toLowerCase().includes(words));
  return (
    <>
      <div className="row work-state">
        {view === "open" && <Select aria-label="State" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="open">open</option><option value="cleared">cleared</option>
          <option value="closed">closed</option><option value="shelved">shelved</option><option value="">any state</option>
        </Select>}
        <span className="small muted">{shown.length} {shown.length === 1 ? "event" : "events"}</span>
      </div>
      <Card className="work-list-card">
        {!shown.length ? <Empty>{EMPTY[view]}</Empty> : <>
          <div className="work-list-head small muted" aria-hidden="true">
            <span>Work</span><span>Assets</span><span>Age</span><span>With</span><span /></div>
          <ul className="work-list" aria-label={`${shown.length} ${shown.length === 1 ? "event" : "events"}`}>
            {shown.map((r) => <Row key={r.id} r={r} person={person}
              people={people.data ?? []} queues={queues} onChange={rows.reload} />)}</ul>
        </>}
        <SampleTeam onAdded={people.reload} />
      </Card>
      {view === "open" && <Suppressed />}
    </>
  );
}

const WINDOW: Record<string, string> = {
  shutdown: "Shutdown", planned_work: "Planned work", person: "Set by a person", muted: "Provider muted",
};

/** What planned work, shutdowns and people's windows held back (P3.5b): counted, not lost. Nothing shows until
 * something was held back. */
function Suppressed() {
  const s = useAsync(() => api.exceptionsSuppressed(), []);
  const d = s.data;
  if (!d?.findings) return null;
  return (
    <Card title={`Suppressed: ${d.findings} ${d.findings === 1 ? "finding" : "findings"}, raised ${d.raised} times`}>
      {d.windows.length > 0 && <ul className="plain small">{d.windows.map((w) => (
        <li key={`${w.kind}-${w.id}`}><strong>{WINDOW[w.kind] ?? w.kind}</strong>: {w.reason} · until {when(w.end_at)}
          {" · "}{w.findings} held back</li>))}
      </ul>}
      <Disclosure label="What was held back">
        <Table density="compact">
          <thead><tr><th>Finding</th><th>Window</th><th>Raised</th><th>Last seen</th></tr></thead>
          <tbody>{d.items.map((x) => (
            <tr key={x.id}><td>{x.title}</td><td>{WINDOW[x.window_kind] ?? x.window_kind}: {x.reason}</td>
              <td>{x.raised}</td><td>{when(x.last_seen)}</td></tr>))}
          </tbody>
        </Table>
      </Disclosure>
    </Card>
  );
}

/** The exception types: what modules raise, and the ones defined here so other systems (SAP notifications, SCADA
 * alarms, a reporting app) can raise them through POST /exceptions without code. */
export function ExceptionTypes({ person, queues }: { person: string | null; queues: string[] }) {
  const kinds = useAsync(() => api.exceptionKinds(), []);
  const refs = useAsync(() => api.exceptionRefs(), []);
  const canConfigure = usePermission("configure");
  const { busy, error, run } = useAct();
  const [f, setF] = useState({ name: "", label: "", role: "", field: true });
  if (!kinds.data) return <Status loading={kinds.loading} error={kinds.error} />;
  const save = () => run("Saving", () => api.defineExceptionKind(f.name.trim(), {
    by: person ?? "", label: f.label.trim(), role: f.role.trim() || null, audience: f.field ? ["ioc", "field"] : ["ioc"],
  }), () => { setF({ name: "", label: "", role: "", field: true }); kinds.reload(); });
  return (
    <Card title="Exception types">
      <p className="small">Another system raises a type with <code>POST /exceptions</code> and clears it with <code>POST /exceptions/clear</code>.</p>
      {refs.data && refs.data.length > 0 && <div className="small">
        <strong>Refs Fuzer matches</strong> <span className="muted">(an exception's <code>refs</code>; one that matches nothing
          is kept as unmatched and the sender is told)</span>
        <ul className="plain">{refs.data.slice(0, 8).map((f) => <li key={f.form}>
          <code>{f.example}</code> <span className="muted">{f.form} · {f.entities.toLocaleString()} entities</span></li>)}</ul>
      </div>}
      <Table variant="results">
        <thead><tr><th>Type</th><th>Worked by</th><th>Seen by</th><th>Open</th><th>Defined</th><th /></tr></thead>
        <tbody>{kinds.data.map((k) => <tr key={k.name}>
          <td><strong>{k.label}</strong> <span className="muted small">{k.name}</span></td>
          <td className="small">{k.process ? `process ${k.process}` : k.role ?? <span className="muted">waits to be dispatched</span>}</td>
          <td className="small">{k.audience.join(", ")}</td>
          <td>{k.open}</td>
          <td className="small">{k.defined_in}</td>
          <td>{k.defined_in === "data" && canConfigure && <Button size="sm" disabled={!!busy || k.open > 0}
            title={k.open ? "Clear its open exceptions first" : undefined}
            onClick={() => run("Removing", () => api.removeExceptionKind(k.name), kinds.reload)}>Remove</Button>}</td>
        </tr>)}</tbody>
      </Table>
      {canConfigure && person && <div className="row">
        <Input aria-label="Type name" placeholder="scada_alarm" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <Input aria-label="Label" placeholder="SCADA alarm" value={f.label} onChange={(e) => setF({ ...f, label: e.target.value })} />
        <Select aria-label="Queue" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
          <option value="">waits for the IOC to dispatch</option>
          {queues.map((q) => <option key={q} value={`role:${q}`}>{queue(q)}</option>)}
        </Select>
        <label className="small"><Input type="checkbox" checked={f.field} onChange={(e) => setF({ ...f, field: e.target.checked })} /> field sees it</label>
        <Button disabled={!!busy || !f.name.trim() || !f.label.trim()} onClick={save}>Define type</Button>
      </div>}
      {error && <p role="alert" className="error">{error}</p>}
    </Card>
  );
}

/** Open, and the field's latest report says done or could not do: their part is over and the case is back with the
 * IOC to decide what happens next. */
export const backFromField = (r: ExceptionRow) => {
  if (r.state !== "open" || r.case_state === "done" || r.case_state === "cancelled") return false;
  // the case's last hand-on returned it from the field, even to the person who went (one account doing both)
  if (r.back_from_field) return true;
  const last = r.evidence?.field?.[r.evidence.field.length - 1];
  return !!last && (last.status === "done" || last.status === "could_not_do") && r.assignee !== last.by;
};

/** Planned for a later day: it waits on Plan until its day comes. */
export const plannedLater = (r: ExceptionRow) => {
  if (!r.planned) return false;
  const tonight = new Date(); tonight.setHours(23, 59, 59, 999);
  return new Date(r.planned.start_at) > tonight;
};

/** Open, raised and not yet given to anyone, nor planned for a later day: the IOC's to dispatch. Planned work comes
 * back here on the day it was planned for. */
export const waitingToDispatch = (r: ExceptionRow) => r.state === "open" && !r.item_id && !plannedLater(r);

/** Open, its case held by a field operator (a person with the operator role, or the operator queue) or the field said
 * they are on site, and not yet back from the field. */
export const withTheField = (r: ExceptionRow, operators: Set<string>) => {
  if (r.state !== "open" || !r.item_id || backFromField(r)) return false;
  const last = r.evidence?.field?.[r.evidence.field.length - 1];
  return r.role === "operator" || (!!r.assignee && operators.has(r.assignee)) || last?.status === "on_site";
};

const FIELD_SAID: Record<string, string> = { on_site: "Field: on site", done: "Field: done", could_not_do: "Field: could not do", note: "Field note" };

export type Person = { name: string; roles: string[]; away_until?: string | null; display_name?: string | null };

/** The same event as other systems reported it, worked on this one case (#464): each source, and a way to split one out
 * when it turns out to be a different event. */
function Joined({ r, person, onChange }: { r: ExceptionRow; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const operate = usePermission("operate");
  const all = r.joined ?? [];
  return (
    <div className="small">
      <Badge tone="ok">{reportedBy([r, ...all])}</Badge>
      <ul className="plain">{all.map((j) => <li key={j.id}>
        {j.label}: {say(j.title)} <span className="muted">· {j.source ?? ""} · {when(j.first_seen)}{j.state !== "open" ? ` · ${j.state}` : ""}</span>
        {j.state === "open" && person && operate && <> <Button size="sm" disabled={!!busy}
          title="A different event: give it a case of its own"
          onClick={() => run("Splitting", () => api.splitException(j.id, person), onChange)}>Split out</Button></>}
      </li>)}</ul>
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}

/** How long ago, as an operator says it: 15m, 2h 10m, 4d 12h. */
export function age(ms: number): string {
  const m = Math.max(0, Math.floor(ms / 60000));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return m % 60 ? `${h}h ${m % 60}m` : `${h}h`;
  return h % 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${Math.floor(h / 24)}d`;
}

/** When the event itself happened, by its source (an SAP notification's created date, an alarm's start), when the
 * source said; first_seen is when Fuzer raised it, which after a bulk load is the load time (#1070). */
export function happened(r: ExceptionRow): string | null {
  const f = r.evidence?.finding as { start?: string | null } | undefined;
  return typeof f?.start === "string" && f.start ? f.start : null;
}

const day = (at: string) => dayStamp(at.length === 10 ? `${at}T12:00:00` : at);

/** The subjects worth showing: those with a name. An equipment record with neither a name nor an installed tag would
 * show as its id, which says nothing; it is left out when another subject names the place (#1070). */
export function namedSubjects(ids: string[], names: EntityNames | null | undefined): string[] {
  if (!names) return [];  // until the names come: never a raw id in their place (#1072)
  // Equipment reads as the tag it is installed at, so that tag beside it would say the same thing twice.
  const at = new Set(ids.map((id) => names[id]?.at).filter(Boolean));
  const named = ids.filter((id) => display(id, names) !== id && (names[id]?.at || !at.has(names[id]?.label ?? "")));
  return named;
}

type Panel = null | "dispatch" | "more" | "plan" | "shelve";

/** One exception as a row on a desk and a card on a phone (#1070): what it is, where, how old, who has it, and one
 * main action. Dispatch opens who, what to do and readiness beneath the row; Plan and Shelve sit behind More. */
function Row({ r, person, people, queues, onChange }: {
  r: ExceptionRow; person: string | null; people: Person[]; queues: string[]; onChange: () => void;
}) {
  const names = useEntityNames(r.subjects);
  const { busy, error, run } = useAct();
  const [panel, setPanel] = useState<Panel>(null);
  const [planOn, setPlanOn] = useState("");
  const [shelveTo, setShelveTo] = useState("");
  const [shelveWhy, setShelveWhy] = useState("");
  const fieldSaid = r.evidence?.field?.[r.evidence.field.length - 1];
  const shelved = !!r.shelved_until && new Date(r.shelved_until) > new Date();
  const canAct = r.state === "open" && !!person;
  const subjects = namedSubjects(r.subjects, names);
  const event = happened(r);
  const reports = r.reports ?? r.raised_count;
  const done = () => { setPanel(null); onChange(); };
  return (
    <li className={fieldSaid?.status === "could_not_do" ? "work-card work-row attention" : "work-card work-row"}>
      <div className="work-row-what">
        <div className="work-card-title">{r.item_id ? <a href={href(`/cases/${r.item_id}`)}>{say(r.title)}</a> : say(r.title)}</div>
        <div className="small muted"><PriorityBadge p={r.priority} type={r.type} />{" "}
          {/* The type opens its route: where this kind of work goes, who holds it and when it is due (#1078). */}
          <a className="muted work-type-link" href={href("/routing", { type: r.type })} title="Where this type of work goes">
            {r.type.replace(/_/g, " ")}</a>
          {[r.source, r.value_usd ? `${money(r.value_usd)} at stake` : null].filter(Boolean).map((x) => ` · ${x}`).join("")}</div>
        {!!r.joined?.length && <Joined r={r} person={person} onChange={onChange} />}
      </div>
      <div className="work-row-where small">{subjects.map((s) => <div key={s}><a href={entityHref(s)}
        title={names?.[s]?.at ? names[s].label ?? s : s}>{display(s, names)}</a></div>)}
        {r.unmatched.map((u) => <div key={u} className="muted">unmatched: {u}{" "}
          {canAct && <MatchRef value={u} busy={!!busy} onPick={(fid, remember) =>
            run("Matching", () => api.resolveRef(r.id, person!, u, fid, remember), onChange)} />}</div>)}</div>
      <div className="work-row-age small" title={`Raised in Fuzer ${when(r.first_seen)} · last reported ${when(r.last_seen)}`}>
        <strong>{age(Date.now() - new Date(r.first_seen).getTime())}</strong>
        <span className="muted">{event ? ` · happened ${day(event)}` : ""}{reports > 1 ? ` · ${reports} reports` : ""}</span>
      </div>
      <div className="work-row-with small">{r.item_id ? <a href={href(`/cases/${r.item_id}`)}>{holder(r.assignee, r.role)}</a>
        : r.planned ? <a href={planItemHref(r.planned)}>planned {dayStamp(r.planned.start_at)}{r.planned.crew ? ` · ${r.planned.crew}` : ""}</a>
        : <span className="muted">not dispatched</span>}
        {fieldSaid && <div title={fieldSaid.note ?? undefined}>
          <Badge tone={fieldSaid.status === "could_not_do" ? "warn" : "ok"}>
            {FIELD_SAID[fieldSaid.status] ?? fieldSaid.status}</Badge> <span className="muted">{personName(fieldSaid.by)} · {when(fieldSaid.at)}</span>
          {fieldSaid.note && <div className="muted">{fieldSaid.note}</div>}</div>}
        {shelved && <div><Badge>Shelved</Badge> until {day(r.shelved_until!)}: {r.shelved_reason}
          {r.shelved_by ? ` · ${personName(r.shelved_by)}` : ""}</div>}
      </div>
      {canAct && <div className="row work-card-actions work-row-actions">
        <Button size="sm" variant="secondary" className="work-lead" aria-expanded={panel === "dispatch"}
          onClick={() => setPanel(panel === "dispatch" ? null : "dispatch")}>{r.item_id ? "Hand to" : "Dispatch"}</Button>
        {shelved ? <Button size="sm" variant="secondary" disabled={!!busy}
          onClick={() => run("Unshelving", () => api.unshelveException(r.id, person!), onChange)}>Back on the list</Button>
          : <Button size="sm" variant="secondary" aria-expanded={panel === "more" || panel === "plan" || panel === "shelve"}
            onClick={() => setPanel(panel && panel !== "dispatch" ? null : "more")}>More</Button>}
      </div>}
      {canAct && panel && <div className="work-row-panel">
        {panel === "dispatch" && <DispatchControls r={r} person={person!} people={people} queues={queues} onDone={done} />}
        {panel === "more" && <div className="row wrap" role="group" aria-label="More actions">
          <Button size="sm" variant="secondary" onClick={() => setPanel("plan")}>Plan for a day</Button>
          <Button size="sm" variant="secondary" onClick={() => setPanel("shelve")}>Shelve until a date</Button>
        </div>}
        {panel === "plan" && <div className="row wrap work-row-form">
          <Field label="Plan for" width="sm"><Input type="date" value={planOn} onChange={(e) => setPlanOn(e.target.value)} /></Field>
          <Button size="sm" variant="primary" disabled={!planOn || !!busy} title="Put it on the integrated plan instead of working it now"
            onClick={() => run("Planning", () => api.planException(r.id, person!, planOn, `${planOn}T23:59:00Z`),
              () => { setPlanOn(""); done(); })}>Plan</Button>
          <Button size="sm" variant="ghost" onClick={() => setPanel(null)}>Cancel</Button>
        </div>}
        {panel === "shelve" && <div className="row wrap work-row-form">
          <Field label="Shelve until" width="sm"><Input type="date" value={shelveTo} onChange={(e) => setShelveTo(e.target.value)} /></Field>
          <Field label="Why it can wait" width="md"><Input value={shelveWhy} onChange={(e) => setShelveWhy(e.target.value)} /></Field>
          <Button size="sm" variant="primary" disabled={!shelveTo || !shelveWhy.trim() || !!busy}
            title="Off the list until then; it comes back on its own"
            onClick={() => run("Shelving", () => api.shelveException(r.id, person!, shelveWhy.trim(), `${shelveTo}T23:59:00Z`),
              () => { setShelveTo(""); setShelveWhy(""); done(); })}>Shelve</Button>
          <Button size="sm" variant="ghost" onClick={() => setPanel(null)}>Cancel</Button>
        </div>}
      </div>}
      {error && <p role="alert" className="error small work-row-panel">{error}</p>}
    </li>
  );
}

/** Who to send it to, what they should do, and whether it is ready to go (P5.5): the one dispatch control, used by
 * the All work lists and the board's cards. */
export function DispatchControls({ r, person, people, queues, onDone }: {
  r: ExceptionRow; person: string; people: Person[]; queues: string[]; onDone: () => void;
}) {
  const { busy, error, run } = useAct();
  const [to, setTo] = useState("");
  const [ask, setAsk] = useState("");
  const [why, setWhy] = useState("");
  const [ready, setReady] = useState<Readiness | null>(null);
  const blocked = !!ready && !ready.ready;
  return <div className="dispatch-controls">
    <div className="row wrap">
      <Field label="Dispatch to" width="md"><Select value={to} onChange={(e) => setTo(e.target.value)}>
        <option value="">choose a person or queue</option>
        {people.length > 0 && <optgroup label="People">{people.map((p) => <option key={p.name} value={p.name}>
          {p.display_name || p.name}{p.roles.length ? ` (${p.roles.join(", ").replace(/_/g, " ")})` : ""}
          {p.away_until && new Date(p.away_until) > new Date() ? " · away" : ""}</option>)}</optgroup>}
        <optgroup label="Role queues">{queues.map((q) => <option key={q} value={`role:${q}`}>{queue(q)}</option>)}</optgroup>
      </Select></Field>
      {to.trim() && <Field label="What to do" width="lg"><Input placeholder="what to check or do (they see this)"
        value={ask} onChange={(e) => setAsk(e.target.value)} /></Field>}
    </div>
    {to.trim() && <ReadyToDispatch id={r.id} onResult={setReady} />}
    {to.trim() && blocked && <Input width="full" aria-label="Why send it anyway"
      placeholder="why it should go anyway (kept on its case)" value={why} onChange={(e) => setWhy(e.target.value)} />}
    <div className="row">
      <Button size="sm" variant="primary" disabled={!to.trim() || !!busy || (!r.item_id && !ask.trim()) || (blocked && !why.trim())}
        title={!r.item_id && to.trim() && !ask.trim() ? "Say what to check or do first: they see it on their job"
          : blocked && !why.trim() ? "Not ready: say why it should go anyway" : undefined}
        onClick={() => run("Dispatching", () => api.dispatch(r.id, person, to.trim(), ask.trim() || undefined,
          blocked ? why.trim() : undefined), () => { setTo(""); setAsk(""); setWhy(""); onDone(); })}>
        {r.item_id ? "Hand over" : "Send"}</Button>
    </div>
    {error && <p role="alert" className="error">{error}</p>}
  </div>;
}

/** Ready to dispatch (P5.5, #684): ready, or each blocker by name, read from the job's equipment records. */
function ReadyToDispatch({ id, onResult }: { id: number; onResult: (r: Readiness | null) => void }) {
  const check = useAsync(() => api.exceptionReadiness(id), [id]);
  const r = check.data && Array.isArray(check.data.blockers) ? check.data : null;
  useEffect(() => { onResult(r); }, [r]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (!r) return check.error ? <p className="small error">Readiness not checked: {check.error}</p> : null;
  const warned = r.warnings?.length ? <ul className="plain small ready-warnings" aria-label="Before you send it">
    {r.warnings.map((w) => <li key={w}><Badge tone="warn">check</Badge> {w}</li>)}</ul> : null;
  const confirm = r.checks.some((c) => c.confirm);  // needs a permit, none to check it against (#742)
  return <>{warned}{r.ready
    ? <p className="small ready-note">{confirm ? <Badge tone="warn">check permit</Badge> : <Badge tone="ok">ready</Badge>}{" "}
      {[...r.checks.flatMap((c) => (c.centers ?? []).filter((w) => w.crew != null)
        .map((w) => `${w.description ?? w.label}: ${w.busy} of ${w.crew} busy.`)),
        ...r.checks.flatMap((c) => (c.permits ?? []).filter((p) => p.valid).slice(0, 1)
          .map((p) => `Permit ${p.label} (${p.type ?? "permit"}) valid to ${p.valid_to ?? "?"}.`)),
        ...r.checks.filter((c) => !c.checked && !c.confirm).map((c) => c.note)].filter(Boolean).join(" ")}</p>
    : <div className="small ready-note" role="status"><Badge tone="warn">not ready</Badge>
      <ul className="plain">{r.blockers.map((b) => <li key={b}>{b}</li>)}</ul></div>}</>;
}
