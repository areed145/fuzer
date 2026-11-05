import { Button, Input, PageHeader, Table, Card as UICard } from "../../ui";
/** Cases: every work item, open or closed, and one case's page: what it's about, where its process is, who has its
 * open steps, its history, and the actions on it (assign, priority, due date, close, cancel). */
import { Suspense, lazy, useMemo, useState } from "react";
import { api, type CaseRow, type WorkItem, type WorkProcess } from "../../api";
import { Status, entityHref, href, useAsync } from "../../lib";
import { Actor, DataView, Due, describe, holder, usePerson, when } from "../../work";
import { AssignPicker, StepBadges, byUrgency, caseHref, processHref, taskHref, useAct } from "./shared";

const BpmnView = lazy(() => import("../../BpmnView"));

export function CasesPage({ query }: { query: URLSearchParams }) {
  const [state, setState] = useState(query.get("state") ?? "open");
  const [type, setType] = useState(query.get("type") ?? "");
  const [who, setWho] = useState(query.get("who") ?? "");
  const [overdue, setOverdue] = useState(query.get("overdue") === "1");
  const [q, setQ] = useState("");
  const [person] = usePerson();
  const filters = { state, type: type || undefined, q: q || undefined, overdue: overdue ? true : undefined,
                    assignee: who && !who.startsWith("role:") ? who : undefined, role: who.startsWith("role:") ? who.slice(5) : undefined };
  const list = useAsync(() => api.cases(filters), [JSON.stringify(filters)]);
  const all = useAsync(() => api.cases({ state: "all", limit: 1000 }), []);
  const types = [...new Set((all.data?.items ?? []).map((r) => r.type))].sort();
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const rows = [...(list.data?.items ?? [])].sort(state === "open" ? byUrgency : () => 0);
  return (
    <section>
      <PageHeader>Cases</PageHeader>
      <p className="muted">Every piece of work: an upset, a daily report, a source issue, an IOW breach. Each moves through its
        process; open one for where it is, who has it and its history. The steps waiting on you are in
        your <a href={href("/inbox")}>Inbox</a>; all outstanding work ranked by value, workflow or not, is
        the <a href={href("/board")}>Work board</a>.</p>
      <div className="row wrap">
        <div className="tabs">
          {["open", "closed", "all"].map((s) => <a key={s} className={state === s ? "on" : ""} onClick={() => setState(s)}>{s}</a>)}
        </div>
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">every kind</option>
          {types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
        </select>
        <AssignPicker value={who} onChange={setWho} />
        <label className="small"><input type="checkbox" checked={overdue} onChange={(e) => setOverdue(e.target.checked)} /> overdue</label>
        <input placeholder="search titles" value={q} onChange={(e) => setQ(e.target.value)} />
        {list.data && <span className="small muted">{list.data.total} cases · {list.data.overdue} overdue · {list.data.due_soon} due soon</span>}
      </div>
      {picked.size > 0 && <BulkReassign ids={[...picked]} person={person} onDone={() => setPicked(new Set())} />}
      <Status loading={list.loading && !list.data} error={list.error} empty={!!list.data && rows.length === 0}>No cases match.</Status>
      {rows.length > 0 && (
        <Table variant="results">
          <thead><tr><th /><th>Case</th><th>Kind</th><th>State / step</th><th>With</th><th>Due</th><th>Priority</th><th>Opened</th></tr></thead>
          <tbody>
            {rows.map((r) => <CaseLine key={r.id} r={r} picked={picked.has(r.id)}
              toggle={() => setPicked((s) => { const n = new Set(s); n.has(r.id) ? n.delete(r.id) : n.add(r.id); return n; })} />)}
          </tbody>
        </Table>
      )}
    </section>
  );
}

function CaseLine({ r, picked, toggle }: { r: CaseRow; picked: boolean; toggle: () => void }) {
  return (
    <tr>
      <td>{!r.closed_at && <input type="checkbox" checked={picked} onChange={toggle} />}</td>
      <td><a href={caseHref(r.id)}>{r.title}</a>{r.escalated && <span className="badge late"> escalated</span>}</td>
      <td className="small"><span className="badge">{r.type.replace(/_/g, " ")}</span>{r.process_key && <> <a className="muted" href={processHref(r.process_key)}>{r.process_key}</a></>}</td>
      <td className="small">{r.closed_at ? <span className="badge ok">{r.state}{r.outcome ? `: ${r.outcome}` : ""}</span> : r.step ?? r.state}</td>
      <td className="small">{r.closed_at ? "" : holder(r.assignee, r.role)}</td>
      <td className="nowrap">{!r.closed_at && <Due at={r.due} />}</td>
      <td className="small">{r.priority != null ? (r.priority < 2 ? r.priority.toFixed(2) : Math.round(r.priority).toLocaleString()) : ""}</td>
      <td className="small muted">{when(r.created_at)}</td>
    </tr>
  );
}

function BulkReassign({ ids, person, onDone }: { ids: number[]; person: string; onDone: () => void }) {
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const a = useAct();
  return (
    <UICard className="bulk-bar actions">
      <strong>{ids.length} selected</strong> give everything open on them to
      <AssignPicker value={to} onChange={setTo} />
      <input placeholder="why" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button variant="primary" disabled={!to || !!a.busy}
        onClick={() => a.run("go", async () => { for (const id of ids) await api.reassignCase(id, person, to, reason || undefined); }, onDone)}>
        Reassign</Button>
      {a.error && <span className="error small">{a.error}</span>}
    </UICard>
  );
}

export function CasePage({ id }: { id: number }) {
  const item = useAsync(() => api.workItem(id), [id]);
  const [person] = usePerson();
  const it = item.data;
  if (!it) return <Status loading={item.loading} error={item.error} />;
  const open = it.processes.flatMap((p) => p.open.filter((s) => ["user", "agent", "fix"].includes(s.kind)));
  const closed = !!it.closed_at;
  const payload = (it as WorkItem & { payload?: Record<string, unknown> }).payload ?? {};
  const dataset = typeof payload.dataset === "string" ? payload.dataset : null;
  return (
    <section>
      <div className="entity-head">
        <div>
          <PageHeader>{it.title}</PageHeader>
          <div className="row small">
            <span className="badge">{it.type.replace(/_/g, " ")}</span>
            <span className={`badge ${closed ? "ok" : ""}`}>{it.state}{it.outcome ? `: ${it.outcome}` : ""}</span>
            {it.priority != null && <span className="muted">priority {it.priority < 2 ? it.priority.toFixed(2) : Math.round(it.priority).toLocaleString()}</span>}
            <span className="muted">opened by</span> <Actor who={it.created_by} /> <span className="muted">{when(it.created_at)}</span>
          </div>
          {(it.subjects.length > 0 || dataset) && (
            <p className="small">About{" "}
              {it.subjects.map((s, i) => <span key={s}>{i > 0 && ", "}<a className="mono" href={entityHref(s)}>{s}</a></span>)}
              {dataset && <> source dataset <a href={href("/sources", { view: "dataset", name: dataset })}>{dataset}</a></>}
            </p>
          )}
        </div>
        {!closed && <Due at={it.due} />}
      </div>
      {!closed && <CaseActions it={it} person={person} reload={item.reload} />}
      {open.length > 0 && (
        <UICard className="">
          <h3>Waiting on</h3>
          <ul className="plain">
            {open.map((s) => (
              <li key={s.id} className="row">
                {s.kind === "agent" ? <strong>{s.name ?? s.element}</strong> : <a href={taskHref(s.id)}><strong>{s.name ?? s.element}</strong></a>}
                <span className="small muted">with {holder(s.assignee, s.role, s.kind)}</span>
                <StepBadges row={s} />
                <Due at={s.due_at} />
              </li>
            ))}
          </ul>
        </UICard>
      )}
      {it.processes.map((p) => <ProcessDiagram key={p.id} p={p} person={person} reload={item.reload} />)}
      {Object.keys(payload).length > 0 && (
        <details className="card"><summary>Details</summary><DataView data={payload} /></details>
      )}
      <Timeline it={it} person={person} />
    </section>
  );
}

function CaseActions({ it, person, reload }: { it: WorkItem; person: string; reload: () => void }) {
  const [to, setTo] = useState("");
  const [priority, setPriority] = useState(it.priority?.toString() ?? "");
  const [due, setDue] = useState("");
  const [reason, setReason] = useState("");
  const a = useAct();
  const hasProcess = it.processes.some((p) => p.state === "running");
  return (
    <UICard className="inset">
      <div className="row wrap">
        <span className="small">Give it to</span> <AssignPicker value={to} onChange={setTo} />
        <Button variant="secondary" disabled={!to || !!a.busy}
          onClick={() => a.run("to", () => api.reassignCase(it.id, person, to, reason || undefined), () => { setTo(""); reload(); })}>Reassign</Button>
        <span className="small">Priority</span>
        <Input width="xs" value={priority} onChange={(e) => setPriority(e.target.value)} />
        <span className="small">Due</span>
        <input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
        <Button variant="secondary" disabled={!!a.busy || (!due && priority === (it.priority?.toString() ?? ""))}
          onClick={() => a.run("set", () => api.setCase(it.id, person, {
            ...(priority !== (it.priority?.toString() ?? "") && priority ? { priority: Number(priority) } : {}),
            ...(due ? { due: new Date(due).toISOString() } : {}), reason: reason || undefined }), () => { setDue(""); reload(); })}>Save</Button>
        <input placeholder="why (kept in its history)" value={reason} onChange={(e) => setReason(e.target.value)} />
        {!hasProcess && <Button variant="secondary" disabled={!!a.busy}
          onClick={() => a.run("close", () => api.transitionCase(it.id, person, "done", reason || undefined), reload)}>Close as done</Button>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </UICard>
  );
}

export function ProcessDiagram({ p, person, reload, focus }: { p: WorkProcess; person?: string; reload?: () => void; focus?: string }) {
  const xml = useAsync(() => api.workDefinitionXml(p.definition_id), [p.definition_id]);
  const a = useAct();
  const marks = useMemo(() => ({
    done: p.path.map((s) => s.element),
    open: focus ? [focus] : p.open.filter((s) => s.state === "waiting" && s.kind !== "join").map((s) => s.element),
    flows: [...p.path, ...p.open].map((s) => s.flow).filter((f): f is string => !!f),
  }), [p, focus]);
  return (
    <UICard className="">
      <div className="row spread">
        <h3>
          <a href={processHref(p.process_key)}>{p.process_key}</a> <span className="muted small">v{p.version} · {p.state}{p.outcome ? `: ${p.outcome}` : ""}</span>
        </h3>
        <span className="small muted">
          {p.correlation_key && <>key <span className="mono">{p.correlation_key}</span> · </>}started {when(p.started_at)} by {p.started_by}
          {p.state === "running" && person && reload && (
            <> · <Button variant="link" disabled={!!a.busy}
              onClick={() => confirm("Cancel this process? Its open steps are cancelled.") &&
                a.run("cancel", () => api.cancelProcess(p.id, person, "cancelled from the case page"), reload)}>cancel process</Button></>
          )}
        </span>
      </div>
      <Status loading={xml.loading} error={xml.error} />
      {xml.data && <Suspense fallback={<div className="status">Loading diagram…</div>}><BpmnView xml={xml.data} marks={marks} /></Suspense>}
      <details className="small"><summary>Process data</summary><DataView data={p.data} /></details>
      {a.error && <p className="error small">{a.error}</p>}
    </UICard>
  );
}

export function Timeline({ it, person }: { it: WorkItem; person: string }) {
  const [text, setText] = useState("");
  const a = useAct();
  return (
    <UICard className="">
      <h3>History</h3>
      <ol className="timeline">
        {it.events.map((e) => {
          const { text: t, body } = describe(e);
          return (
            <li key={e.id} className={`ev-${e.kind}`}>
              <div className="row small"><span className="muted mono">{when(e.at)}</span> {e.kind !== "step_started" && <Actor who={e.actor} />} {t}</div>
              {body}
            </li>
          );
        })}
      </ol>
      <div className="actions">
        <input placeholder="Add a comment" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && text && a.run("c", () => api.comment(it.id, person || "anonymous", text), () => setText(""))} />
        <Button disabled={!text || !!a.busy} onClick={() => a.run("c", () => api.comment(it.id, person || "anonymous", text), () => setText(""))}>Comment</Button>
        {a.error && <span className="error small">{a.error}</span>}
      </div>
    </UICard>
  );
}
