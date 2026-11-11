/** Exceptions: everything the IOC should work, from every source, in one list. Each is one open condition matched
 * to the assets it is about; the ones no queue picked up wait here for someone to dispatch them. */
import { useState } from "react";
import { api, type ExceptionRow } from "../api";
import { Badge, Button, Card, Input, Select, Table } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";
import { holder, reportedBy, usePerson, when } from "../work";
import { useEntityNames } from "../useEntityNames";
import { display } from "../entityRefs";
import { SampleTeam, useAct } from "./workflows/shared";
import { usePermission } from "../Auth";
import { MatchRef } from "./masterdata/MatchRef";

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
  query?: URLSearchParams; view?: ExceptionView; filters?: { type?: string; who?: string; q?: string };
}) {
  const [person] = usePerson();
  const [state, setState] = useState(view === "open" ? query?.get("state") ?? "open" : "open");
  const type = filters.type ?? "";
  const rows = useAsync(() => api.exceptions({ state: state || "any", type: type || undefined }), [state, type]);
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
      <div className="row">
        {view === "open" && <Select aria-label="State" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="open">open</option><option value="cleared">cleared</option>
          <option value="closed">closed</option><option value="">any state</option>
        </Select>}
        <span className="small muted">{shown.length} {shown.length === 1 ? "event" : "events"}</span>
      </div>
      <Card>
        {!shown.length ? <p className="muted">{EMPTY[view]}</p> : <Table variant="results">
          <thead><tr><th>Exception</th><th>Assets</th><th>Seen</th><th>With</th><th /></tr></thead>
          <tbody>{shown.map((r) => <Row key={r.id} r={r} person={person}
            people={people.data ?? []} queues={queues} onChange={rows.reload} />)}</tbody>
        </Table>}
        <SampleTeam onAdded={people.reload} />
      </Card>
      <details className="exception-types">
        <summary>Exception types: what raises work and where it goes</summary>
        <Kinds person={person} queues={queues} />
      </details>
    </>
  );
}

/** The exception types: what modules raise, and the ones defined here so other systems (SAP notifications, SCADA
 * alarms, a reporting app) can raise them through POST /exceptions without code. */
function Kinds({ person, queues }: { person: string | null; queues: string[] }) {
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
      <p className="muted small">Modules declare the types they raise. Define one here for another system to raise
        with <code>POST /exceptions</code> (type, key, title, refs); a repeat with the same key updates the open one,
        and <code>POST /exceptions/clear</code> clears it.</p>
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
          {queues.map((q) => <option key={q} value={`role:${q}`}>{q.replace(/_/g, " ")} queue</option>)}
        </Select>
        <label className="small"><input type="checkbox" checked={f.field} onChange={(e) => setF({ ...f, field: e.target.checked })} /> field sees it</label>
        <Button disabled={!!busy || !f.name.trim() || !f.label.trim()} onClick={save}>Define type</Button>
      </div>}
      {error && <p role="alert" className="error">{error}</p>}
    </Card>
  );
}

/** Open, and the field's latest report says done or could not do: their part is over and the case is back with the
 * IOC to decide what happens next. */
export const backFromField = (r: ExceptionRow) => {
  const last = r.evidence?.field?.[r.evidence.field.length - 1];
  return r.state === "open" && !!last && (last.status === "done" || last.status === "could_not_do") && r.assignee !== last.by;
};

/** Open, raised and not yet given to anyone or put on the plan: the IOC's to dispatch. */
export const waitingToDispatch = (r: ExceptionRow) => r.state === "open" && !r.item_id && !r.planned;

/** Open, its case held by a field operator (a person with the operator role, or the operator queue) or the field said
 * they are on site, and not yet back from the field. */
export const withTheField = (r: ExceptionRow, operators: Set<string>) => {
  if (r.state !== "open" || !r.item_id || backFromField(r)) return false;
  const last = r.evidence?.field?.[r.evidence.field.length - 1];
  return r.role === "operator" || (!!r.assignee && operators.has(r.assignee)) || last?.status === "on_site";
};

const FIELD_SAID: Record<string, string> = { on_site: "Field: on site", done: "Field: done", could_not_do: "Field: could not do", note: "Field note" };

type Person = { name: string; roles: string[]; away_until?: string | null };

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
        {j.label}: {j.title} <span className="muted">· {j.source ?? ""} · {when(j.first_seen)}{j.state !== "open" ? ` · ${j.state}` : ""}</span>
        {j.state === "open" && person && operate && <> <Button size="sm" disabled={!!busy}
          title="A different event: give it a case of its own"
          onClick={() => run("Splitting", () => api.splitException(j.id, person), onChange)}>Split out</Button></>}
      </li>)}</ul>
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}

function Row({ r, person, people, queues, onChange }: {
  r: ExceptionRow; person: string | null; people: Person[]; queues: string[]; onChange: () => void;
}) {
  const names = useEntityNames(r.subjects);
  const { busy, error, run } = useAct();
  const [to, setTo] = useState("");
  const [ask, setAsk] = useState("");
  const [planOn, setPlanOn] = useState("");
  const fieldSaid = r.evidence?.field?.[r.evidence.field.length - 1];
  return (
    <tr>
      <td><strong>{r.title}</strong><br /><span className="muted small">{r.type.replace(/_/g, " ")} · {r.source ?? ""}
        {r.value_usd != null ? ` · $${Math.round(r.value_usd).toLocaleString()}` : ""}</span>
        {!!r.joined?.length && <Joined r={r} person={person} onChange={onChange} />}</td>
      <td className="small">{r.subjects.map((s) => <div key={s}><a href={entityHref(s)}
        title={names?.[s]?.at ? names[s].label ?? undefined : undefined}>{display(s, names)}</a></div>)}
        {r.unmatched.map((u) => <div key={u} className="muted">unmatched: {u}{" "}
          {r.state === "open" && person && <MatchRef value={u} busy={!!busy} onPick={(fid, remember) =>
            run("Matching", () => api.resolveRef(r.id, person, u, fid, remember), onChange)} />}</div>)}</td>
      <td className="small">{r.reports ?? r.raised_count}× · last {when(r.last_seen)}</td>
      <td className="small">{r.item_id ? <a href={href(`/cases/${r.item_id}`)}>{holder(r.assignee, r.role)}</a>
        : r.planned ? <a href={href("/plan")}>planned {new Date(r.planned.start_at).toLocaleDateString(undefined,
          { day: "numeric", month: "short" })}{r.planned.crew ? ` · ${r.planned.crew}` : ""}</a>
        : <span className="muted">not dispatched</span>}
        {fieldSaid && <div title={fieldSaid.note ?? undefined}>
          <Badge tone={fieldSaid.status === "could_not_do" ? "warn" : "ok"}>
            {FIELD_SAID[fieldSaid.status] ?? fieldSaid.status}</Badge> <span className="muted">{fieldSaid.by} · {when(fieldSaid.at)}</span>
          {fieldSaid.note && <div className="muted">{fieldSaid.note}</div>}</div>}</td>
      <td className="small">{r.state === "open" && person && <div className="row">
        <Select aria-label="Dispatch to" value={to} onChange={(e) => setTo(e.target.value)} style={{ maxWidth: "12rem" }}>
          <option value="">send to…</option>
          {people.length > 0 && <optgroup label="People">{people.map((p) => <option key={p.name} value={p.name}>
            {p.name}{p.roles.length ? ` (${p.roles.join(", ").replace(/_/g, " ")})` : ""}
            {p.away_until && new Date(p.away_until) > new Date() ? " · away" : ""}</option>)}</optgroup>}
          <optgroup label="Role queues">{queues.map((q) => <option key={q} value={`role:${q}`}>{q.replace(/_/g, " ")} queue</option>)}</optgroup>
        </Select>
        <Button size="sm" disabled={!to.trim() || !!busy || (!r.item_id && !ask.trim())}
          title={!r.item_id && to.trim() && !ask.trim() ? "Say what to check or do first: they see it on their job" : undefined}
          onClick={() => run("Dispatching", () => api.dispatch(r.id, person, to.trim(), ask.trim() || undefined),
            () => { setTo(""); setAsk(""); onChange(); })}>
          {r.item_id ? "Hand to" : "Dispatch"}</Button>
      </div>}
      {r.state === "open" && person && to.trim() && <Input aria-label="What to do" placeholder="what to check or do (they see this)"
        value={ask} onChange={(e) => setAsk(e.target.value)} style={{ width: "100%" }} />}
      {r.state === "open" && person && <div className="row">
        <Input type="date" aria-label="Plan for" value={planOn} onChange={(e) => setPlanOn(e.target.value)} style={{ width: "9rem" }} />
        <Button size="sm" disabled={!planOn || !!busy} title="Put it on the integrated plan instead of working it now"
          onClick={() => run("Planning", () => api.planException(r.id, person, planOn, `${planOn}T23:59:00Z`),
            () => { setPlanOn(""); onChange(); })}>Plan</Button>
      </div>}{error && <p role="alert" className="error">{error}</p>}</td>
    </tr>
  );
}
