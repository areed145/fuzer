/** Exceptions: everything the IOC should work, from every source, in one list. Each is one open condition matched
 * to the assets it is about; the ones no queue picked up wait here for someone to dispatch them. */
import { useState } from "react";
import { api, type ExceptionRow } from "../api";
import { Badge, Button, Card, Input, PageHeader, Select, Table } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";
import { holder, usePerson, when } from "../work";
import { useEntityNames } from "../useEntityNames";
import { SampleTeam, useAct } from "./workflows/shared";
import { usePermission } from "../Auth";
import { MatchRef } from "./masterdata/MatchRef";

export function ExceptionsPage({ query }: { query?: URLSearchParams }) {
  const [person] = usePerson();
  const [state, setState] = useState("open");
  const [type, setType] = useState(query?.get("type") ?? "");
  const back = state === "back";  // open, and the field's last word was done or could not do: the IOC decides next
  const rows = useAsync(() => api.exceptions({ state: back ? "open" : state || "any", type: type || undefined }), [state, type]);
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const queues = (roles.data ?? []).filter((r) => r.kind === "person").map((r) => r.name);
  if (!rows.data) return <Status loading={rows.loading} error={rows.error} />;
  const types = [...new Set(rows.data.map((r) => r.type))].sort();
  const waiting = rows.data.filter((r) => !r.item_id && !r.planned && r.state === "open");
  const backRows = rows.data.filter(backFromField);
  const shown = back ? backRows : rows.data;
  return (
    <section>
      <PageHeader subtitle={<>Every exception raised by Fuzer's detectors, other systems and the field, once per open
        condition and matched to its assets. Routed ones are worked as <a href={href("/cases")}>cases</a>; the
        rest wait for you to dispatch. Ranked work across everything is on the <a href={href("/board")}>Work board</a>.</>}>
        Exceptions</PageHeader>
      <div className="row">
        <Select aria-label="State" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="open">open</option><option value="cleared">cleared</option>
          <option value="closed">closed</option><option value="back">back from the field</option>
          <option value="">any state</option>
        </Select>
        <Select aria-label="Type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">every type</option>{types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
        </Select>
        {waiting.length > 0 && <Badge tone="warn">{waiting.length} waiting to be dispatched</Badge>}
        {!back && backRows.length > 0 && <Button size="sm" onClick={() => setState("back")}>
          {backRows.length} back from the field</Button>}
      </div>
      <Card>
        {!shown.length ? <p className="muted">{back ? "Nothing back from the field waiting on the IOC." : "Nothing here."}</p> : <Table variant="results">
          <thead><tr><th>Exception</th><th>Assets</th><th>Seen</th><th>With</th><th /></tr></thead>
          <tbody>{shown.map((r) => <Row key={r.id} r={r} person={person}
            people={people.data ?? []} queues={queues} onChange={rows.reload} />)}</tbody>
        </Table>}
        {people.data && !people.data.length && <p className="small muted">Nobody is on the roster yet, so dispatch can
          only go to a role's queue. Add the IOC team and field operators on <a href={href("/teams")}>Teams &amp; routing</a>.</p>}
        <SampleTeam onAdded={people.reload} />
      </Card>
      <Kinds person={person} queues={queues} />
    </section>
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
const backFromField = (r: ExceptionRow) => {
  const last = r.evidence?.field?.[r.evidence.field.length - 1];
  return r.state === "open" && !!last && (last.status === "done" || last.status === "could_not_do") && r.assignee !== last.by;
};

const FIELD_SAID: Record<string, string> = { on_site: "Field: on site", done: "Field: done", could_not_do: "Field: could not do", note: "Field note" };

type Person = { name: string; roles: string[]; away_until?: string | null };

/** Equipment by the tag it is installed at and what it is ("20-K-2100B 1st stage compressor"), not its serial. */
const assetName = (id: string, n?: { label: string | null; description: string | null; at?: string }) =>
  n?.at ? `${n.at}${n.description ? ` ${n.description}` : ""}` : n?.label ?? id;

/** The same event as other systems reported it, worked on this one case (#464): each source, and a way to split one out
 * when it turns out to be a different event. */
function Joined({ r, person, onChange }: { r: ExceptionRow; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const operate = usePermission("operate");
  const all = r.joined ?? [];
  return (
    <div className="small">
      <Badge tone="ok">{all.length + 1} sources</Badge>{" "}
      <span className="muted">{[r.type.replace(/_/g, " "), ...all.map((j) => j.label)].join(", ")}</span>
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
        title={names?.[s]?.at ? names[s].label ?? undefined : undefined}>{assetName(s, names?.[s])}</a></div>)}
        {r.unmatched.map((u) => <div key={u} className="muted">unmatched: {u}{" "}
          {r.state === "open" && person && <MatchRef value={u} busy={!!busy} onPick={(fid, remember) =>
            run("Matching", () => api.resolveRef(r.id, person, u, fid, remember), onChange)} />}</div>)}</td>
      <td className="small">{r.raised_count}× · last {when(r.last_seen)}</td>
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
