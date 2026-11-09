/** Exceptions: everything the IOC should work, from every source, in one list. Each is one open condition matched
 * to the assets it is about; the ones no queue picked up wait here for someone to dispatch them. */
import { useState } from "react";
import { api, type ExceptionRow } from "../api";
import { Badge, Button, Card, Input, PageHeader, Select, Table } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";
import { holder, usePerson, when } from "../work";
import { useEntityNames } from "../useEntityNames";
import { useAct } from "./workflows/shared";
import { usePermission } from "../Auth";

export function ExceptionsPage() {
  const [person] = usePerson();
  const [state, setState] = useState("open");
  const [type, setType] = useState("");
  const rows = useAsync(() => api.exceptions({ state: state || undefined, type: type || undefined }), [state, type]);
  const people = useAsync(() => api.workPeople(), []);
  if (!rows.data) return <Status loading={rows.loading} error={rows.error} />;
  const types = [...new Set(rows.data.map((r) => r.type))].sort();
  const waiting = rows.data.filter((r) => !r.item_id && r.state === "open");
  return (
    <section>
      <PageHeader subtitle={<>Every exception raised by Fuzer's detectors, other systems and the field, once per open
        condition and matched to its assets. Routed ones are worked as <a href={href("/cases")}>cases</a>; the
        rest wait for you to dispatch. Ranked work across everything is on the <a href={href("/board")}>Work board</a>.</>}>
        Exceptions</PageHeader>
      <div className="row">
        <Select aria-label="State" value={state} onChange={(e) => setState(e.target.value)}>
          <option value="open">open</option><option value="cleared">cleared</option>
          <option value="closed">closed</option><option value="">any state</option>
        </Select>
        <Select aria-label="Type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">every type</option>{types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
        </Select>
        {waiting.length > 0 && <Badge tone="warn">{waiting.length} waiting to be dispatched</Badge>}
      </div>
      <Card>
        {!rows.data.length ? <p className="muted">Nothing here.</p> : <Table variant="results">
          <thead><tr><th>Exception</th><th>Assets</th><th>Seen</th><th>With</th><th /></tr></thead>
          <tbody>{rows.data.map((r) => <Row key={r.id} r={r} person={person}
            people={(people.data ?? []).map((p) => p.name)} onChange={rows.reload} />)}</tbody>
        </Table>}
      </Card>
      <Kinds person={person} />
    </section>
  );
}

/** The exception types: what modules raise, and the ones defined here so other systems (SAP notifications, SCADA
 * alarms, a reporting app) can raise them through POST /exceptions without code. */
function Kinds({ person }: { person: string | null }) {
  const kinds = useAsync(() => api.exceptionKinds(), []);
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
        <Input aria-label="Queue" placeholder="role:maintenance (optional)" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })} />
        <label className="small"><input type="checkbox" checked={f.field} onChange={(e) => setF({ ...f, field: e.target.checked })} /> field sees it</label>
        <Button disabled={!!busy || !f.name.trim() || !f.label.trim()} onClick={save}>Define type</Button>
      </div>}
      {error && <p role="alert" className="error">{error}</p>}
    </Card>
  );
}

function Row({ r, person, people, onChange }: { r: ExceptionRow; person: string | null; people: string[]; onChange: () => void }) {
  const names = useEntityNames(r.subjects);
  const { busy, error, run } = useAct();
  const [to, setTo] = useState("");
  const [planOn, setPlanOn] = useState("");
  return (
    <tr>
      <td><strong>{r.title}</strong><br /><span className="muted small">{r.type.replace(/_/g, " ")} · {r.source ?? ""}
        {r.value_usd != null ? ` · $${Math.round(r.value_usd).toLocaleString()}` : ""}</span></td>
      <td className="small">{r.subjects.map((s) => <div key={s}><a href={entityHref(s)}>{names?.[s]?.label ?? s}</a></div>)}
        {r.unmatched.length > 0 && <div className="muted">unmatched: {r.unmatched.join(", ")}</div>}</td>
      <td className="small">{r.raised_count}× · last {when(r.last_seen)}</td>
      <td className="small">{r.item_id ? <a href={href(`/cases/${r.item_id}`)}>{holder(r.assignee, r.role)}</a>
        : <span className="muted">not dispatched</span>}</td>
      <td className="small">{r.state === "open" && person && <div className="row">
        <Input list={`people-${r.id}`} aria-label="Dispatch to" placeholder="person" value={to}
          onChange={(e) => setTo(e.target.value)} style={{ width: "8rem" }} />
        <datalist id={`people-${r.id}`}>{people.map((p) => <option key={p} value={p} />)}</datalist>
        <Button size="sm" disabled={!to.trim() || !!busy}
          onClick={() => run("Dispatching", () => api.dispatch(r.id, person, to.trim()), () => { setTo(""); onChange(); })}>
          {r.item_id ? "Hand to" : "Dispatch"}</Button>
      </div>}
      {r.state === "open" && person && <div className="row">
        <Input type="date" aria-label="Plan for" value={planOn} onChange={(e) => setPlanOn(e.target.value)} style={{ width: "9rem" }} />
        <Button size="sm" disabled={!planOn || !!busy} title="Put it on the integrated plan instead of working it now"
          onClick={() => run("Planning", () => api.planException(r.id, person, planOn, `${planOn}T23:59:00Z`),
            () => { setPlanOn(""); onChange(); })}>Plan</Button>
      </div>}{error && <p role="alert" className="error">{error}</p>}</td>
    </tr>
  );
}
