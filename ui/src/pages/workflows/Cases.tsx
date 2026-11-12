import { ActionStatus, Button, ConfirmAction, Input, PageHeader, Table, Card as UICard, Select , Badge as UIBadge, Disclosure } from "../../ui";
/** Cases: every work item, open or closed, and one case's page: what it's about, where its process is, who has its
 * open steps, its history, and the actions on it (assign, priority, due date, close, cancel). */
import { Suspense, lazy, useMemo, useState } from "react";
import { api, type CaseRow, type WorkItem, type WorkProcess } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { Actor, DataView, Due, EntityLinks, ScenarioClock, describe, holder, usePerson, when } from "../../work";
import { AgentIdle, AssignPicker, StepBadges, byUrgency, caseHref, priorityText, processHref, taskHref, useAct } from "./shared";
import { DemoContext } from "../../DemoContext";
import { CaseFindings } from "../../Finding";
import { AgentWork } from "../../AgentWork";
import { usePermission } from "../../Auth";

const BpmnView = lazy(() => import("../../BpmnView"));

/** A plain piece of work for a person's queue, with no process behind it. */
function NewWorkItem({ onCreated }: { onCreated: () => void }) {
  const operate = usePermission("operate");
  const [person] = usePerson();
  const roles = useAsync(() => api.workRoles(), []);
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<number | null>(null);
  if (!operate) return null;
  const create = async () => {
    setBusy(true); setError(null); setCreated(null);
    try {
      const item = await api.createWorkItem(title.trim(), role, person);
      setCreated(item.id); setTitle(""); onCreated();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  return <Disclosure className="new-work-item" label={<>New work item</>}>
    <div className="row wrap">
      <Input aria-label="Work item title" width="lg" placeholder="What needs doing" maxLength={160} value={title} onChange={(e) => setTitle(e.target.value)} />
      <Select aria-label="Queue" value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="">choose a queue</option>
        {(roles.data?.person ?? []).map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}
      </Select>
      <ConfirmAction label="Create work item" busy={busy} disabled={!title.trim() || !role}
        consequence={`This adds shared work to the ${role.replace(/_/g, " ")} queue, with you recorded as its creator.`} onConfirm={create} />
    </div>
    <ActionStatus busy={busy ? "Creating the work item…" : null} error={error}>
      {created !== null && <>Created. <a href={caseHref(created)}>Open the case</a>.</>}
    </ActionStatus>
  </Disclosure>;
}

/** The In progress and Closed views of All work (#502), formerly the Cases list. With `fixedState` the view sets the
 * state and the page's own header and state tabs are left out. */
export function CasesPage({ query, fixedState, filters }: {
  query: URLSearchParams; fixedState?: "open" | "closed"; filters?: { type?: string; who?: string; q?: string };
}) {
  const [chosen, setState] = useState(query.get("state") ?? "open");
  const state = fixedState ?? chosen;
  const [ownType, setType] = useState(query.get("type") ?? "");
  const [ownWho, setWho] = useState(query.get("who") ?? "");
  const [overdue, setOverdue] = useState(query.get("overdue") === "1");
  const [ownQ, setQ] = useState("");
  // Under All work the shared filter bar decides kind, holder and words; on its own the page keeps its controls.
  const type = filters ? filters.type ?? "" : ownType;
  const who = filters ? filters.who ?? "" : ownWho;
  const q = filters ? filters.q ?? "" : ownQ;
  const [person] = usePerson();
  const asked = { state, type: type || undefined, q: q || undefined, overdue: overdue ? true : undefined,
                    assignee: who && !who.startsWith("role:") ? who : undefined, role: who.startsWith("role:") ? who.slice(5) : undefined };
  const list = useAsync(() => api.cases(asked), [JSON.stringify(asked)]);
  const all = useAsync(() => filters ? Promise.resolve(null) : api.cases({ state: "all", limit: 1000 }), [!!filters]);
  const types = [...new Set((all.data?.items ?? []).map((r) => r.type))].sort();
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const rows = [...(list.data?.items ?? [])].sort(state === "open" ? byUrgency : () => 0);
  return (
    <section>
      {!fixedState && <PageHeader>Cases</PageHeader>}
      <ScenarioClock />
      {state !== "closed" && <NewWorkItem onCreated={list.reload} />}
      <div className="row wrap">
        {!fixedState && <div className="tabs">
          {["open", "closed", "all"].map((s) => <a key={s} className={state === s ? "on" : ""} onClick={() => setState(s)}>{s}</a>)}
        </div>}
        {!filters && <><label className="small row">Kind
          <Select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">every kind</option>
            {types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
          </Select>
        </label>
        <label className="small row">With {/* #409: who holds the case's open steps, a person or a role's queue */}
          <AssignPicker value={who} onChange={setWho} placeholder="anyone" />
        </label></>}
        <label className="small"><Input type="checkbox" checked={overdue} onChange={(e) => setOverdue(e.target.checked)} /> overdue</label>
        {!filters && <Input aria-label="Search case titles" placeholder="search titles" value={q} onChange={(e) => setQ(e.target.value)} />}
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
      <td>{!r.closed_at && <Input type="checkbox" checked={picked} onChange={toggle} />}</td>
      <td><a href={caseHref(r.id)}>{r.title}</a>{r.escalated && <UIBadge className="badge late"> escalated</UIBadge>}</td>
      <td className="small"><UIBadge className="badge">{r.type.replace(/_/g, " ")}</UIBadge>{r.process_key && <> <a className="muted" href={processHref(r.process_key)}>{r.process_key}</a></>}</td>
      <td className="small">{r.closed_at ? <UIBadge className="badge ok">{r.state}{r.outcome ? `: ${r.outcome}` : ""}</UIBadge> : r.step ?? r.state}</td>
      <td className="small">{r.closed_at ? "" : holder(r.assignee, r.role)}</td>
      <td className="nowrap">{!r.closed_at && <Due at={r.due} />}</td>
      <td className="small">{priorityText(r.priority) ?? ""}</td>
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
      <Input placeholder="why" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button variant="primary" disabled={!to || !!a.busy}
        onClick={() => a.run("go", async () => { for (const id of ids) await api.reassignCase(id, person, to, reason || undefined); }, onDone)}>
        Reassign</Button>
      {a.error && <span className="error small">{a.error}</span>}
    </UICard>
  );
}

export function CasePage({ id, demo = false }: { id: number; demo?: boolean }) {
  const item = useAsync(() => api.workItem(id), [id]);
  const [person] = usePerson();
  const takeOver = useAct();
  const it = item.data;
  if (!it || item.error) return <section><PageHeader>Case</PageHeader>
    {demo && <DemoContext step="workflow" />}
    <Status loading={item.loading} error={item.error} onRetry={item.reload} />
    <a href={href("/exceptions", { list: "progress" })}>Return to All work</a></section>;
  const open = it.processes.flatMap((p) => p.open.filter((s) => ["user", "agent", "fix"].includes(s.kind)));
  const closed = !!it.closed_at;
  const payload = (it as WorkItem & { payload?: Record<string, unknown> }).payload ?? {};
  // a process case (queue_item, #509) keeps what was raised under payload.start
  const raised = (payload.start && typeof payload.start === "object" ? payload.start : payload) as Record<string, unknown>;
  const dataset = typeof raised.dataset === "string" ? raised.dataset : null;
  return (
    <section>
      {demo && <DemoContext step="workflow" />}
      <div className="entity-head">
        <div>
          <PageHeader>{it.title}</PageHeader>
          <div className="row small">
            <UIBadge className="badge">{it.type.replace(/_/g, " ")}</UIBadge>
            <UIBadge className={`badge ${closed ? "ok" : ""}`}>{it.state}{it.outcome ? `: ${it.outcome}` : ""}</UIBadge>
            {priorityText(it.priority) && <span className="muted">priority {priorityText(it.priority)}</span>}
            <span className="muted">opened by</span> <Actor who={it.created_by} /> <span className="muted">{when(it.created_at)}</span>
          </div>
          {(it.subjects.length > 0 || dataset) && (
            <p className="small">About{" "}
              <EntityLinks ids={it.subjects} />
              {dataset && <> source dataset <a href={href("/sources", { view: "dataset", name: dataset })}>{dataset}</a></>}
            </p>
          )}
        </div>
        {!closed && <Due at={it.due} />}
      </div>
      <ScenarioClock />
      {typeof raised.handover_id === "number" && <p className="notice small">This is a shift handover.{" "}
        <a href={href("/handover", { id: String(raised.handover_id) })}>Read it and acknowledge it</a>.</p>}
      {typeof raised.closeout_id === "number" && <CloseoutPanel id={raised.closeout_id} person={person} readOnly={demo}
        reload={item.reload} />}
      <CaseFindings sources={it.sources} />
      {!closed && !demo && <CaseActions it={it} person={person} reload={item.reload} />}
      {open.length > 0 && (
        <UICard className="">
          <h3>Waiting on</h3>
          <ul className="plain">
            {open.map((s) => (
              <li key={s.id} className="row">
                {s.kind === "agent" ? <strong>{s.name ?? s.element}</strong> : <a href={demo ? href(`/inbox/task/${s.id}`, { demo: "workflow" }) : taskHref(s.id)}><strong>{s.name ?? s.element}</strong></a>}
                <span className="small muted">with {holder(s.assignee, s.role, s.kind)}</span>
                <StepBadges row={s} />
                <Due at={s.due_at} />
                <AgentIdle step={s} configured={it.agents_configured}
                  onTakeOver={demo || !person ? undefined : () => takeOver.run("take over", () =>
                    api.handOff(s.id, person, person, "no agent worker picked it up; done by a person"), item.reload)} />
              </li>
            ))}
          </ul>
          {takeOver.error && <p role="alert" className="error small">{takeOver.error}</p>}
        </UICard>
      )}
      <AgentWork it={it} />
      {it.processes.map((p) => <ProcessDiagram key={p.id} p={p} person={demo ? undefined : person} reload={item.reload} />)}
      {Object.keys(payload).length > 0 && (
        <Disclosure className="card" open={typeof raised.summary === "string"} label={<>Details</>}><DataView data={payload} /></Disclosure>
      )}
      <Timeline it={it} person={person} readOnly={demo} />
    </section>
  );
}

/** The update for the system that raised the work (#466): what the field did, as a completion or a follow-up, to
 * edit and send, or not send. */
function CloseoutPanel({ id, person, readOnly, reload }: { id: number; person: string; readOnly: boolean; reload: () => void }) {
  const c = useAsync(() => api.closeout(id), [id]);
  const [text, setText] = useState<string | null>(null);
  const [short, setShort] = useState<string | null>(null);
  const a = useAct();
  if (!c.data) return <Status loading={c.loading} error={c.error} />;
  const d = c.data;
  const system = d.system.toUpperCase();
  const done = () => { c.reload(); reload(); };
  return (
    <UICard className="">
      <h3>{d.action === "complete" ? `Complete ${d.record} in ${system}` : `Raise a follow-up to ${d.record} in ${system}`}</h3>
      <p className="small muted">{d.action === "complete"
        ? `The field reported it done. Sending this closes it in ${system}${d.draft.status ? ` (status ${d.draft.status})` : ""}, so its record agrees and the IOC stops holding it.`
        : `The field could not do it. Sending this raises a new ${system} record for the work that is still needed.`}</p>
      {d.state !== "draft" ? (
        <p className="small"><UIBadge className={`badge ${d.state === "sent" ? "ok" : ""}`}>{d.state}</UIBadge>{" "}
          by <Actor who={d.decided_by ?? ""} /> {d.decided_at && when(d.decided_at)}
          {d.applied_at && <span className="muted"> · applied to the demo's {system} data</span>}</p>
      ) : null}
      {d.action === "follow_up" && (
        <p><span className="small">Short text</span>{" "}
          <Input width="lg" disabled={d.state !== "draft" || readOnly} value={short ?? d.draft.short_text ?? ""}
            onChange={(e) => setShort(e.target.value)} /></p>
      )}
      <textarea rows={4} style={{ width: "100%" }} disabled={d.state !== "draft" || readOnly} aria-label="Update text"
        value={text ?? d.draft.text} onChange={(e) => setText(e.target.value)} />
      {d.state === "draft" && !readOnly && (
        <div className="row">
          <Button variant="primary" disabled={!!a.busy} onClick={() => a.run("send", () =>
            api.approveCloseout(id, person, text ?? undefined, short ?? undefined), done)}>Approve and send to {system}</Button>
          <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("discard", () =>
            api.discardCloseout(id, person, "not sent: updated by hand"), done)}>Don't send</Button>
        </div>
      )}
      {a.error && <p className="error small">{a.error}</p>}
    </UICard>
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
        <Input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} />
        <Button variant="secondary" disabled={!!a.busy || (!due && priority === (it.priority?.toString() ?? ""))}
          onClick={() => a.run("set", () => api.setCase(it.id, person, {
            ...(priority !== (it.priority?.toString() ?? "") && priority ? { priority: Number(priority) } : {}),
            ...(due ? { due: new Date(due).toISOString() } : {}), reason: reason || undefined }), () => { setDue(""); reload(); })}>Save</Button>
        <Input placeholder="why (kept in its history)" value={reason} onChange={(e) => setReason(e.target.value)} />
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
      <Disclosure className="small" label={<>Process data</>}><DataView data={p.data} /></Disclosure>
      {a.error && <p className="error small">{a.error}</p>}
    </UICard>
  );
}

export function Timeline({ it, person, readOnly = false }: { it: WorkItem; person: string; readOnly?: boolean }) {
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
      {!readOnly && <div className="actions">
        <Input placeholder="Add a comment" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && text && a.run("c", () => api.comment(it.id, person || "anonymous", text), () => setText(""))} />
        <Button disabled={!text || !!a.busy} onClick={() => a.run("c", () => api.comment(it.id, person || "anonymous", text), () => setText(""))}>Comment</Button>
        {a.error && <span className="error small">{a.error}</span>}
      </div>}
    </UICard>
  );
}
