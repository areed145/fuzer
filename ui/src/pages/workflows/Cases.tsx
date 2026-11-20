import { ActionStatus, Button, ButtonLink, ConfirmAction, Field, Input, PageHeader, Table, Card as UICard, Select, Badge as UIBadge, Disclosure, Textarea, ErrorText, Line, Text, Checkbox, Notice, Tabs } from "../../ui";
import { when } from "../../format";
import { confirmDialog } from "../../ui";
import { OpportunityPanel } from "./OpportunityPanel";
/** Cases: every work item, open or closed, and one case's page: what it's about, where its process is, who has its
 * open steps, its history, and the actions on it (assign, priority, due date, close, cancel). */
import { cap, say } from "../../wording";
import { Suspense, lazy, useMemo, useState } from "react";
import { api, type CaseRow, type CaseSource, type ReviewKind, type WorkItem, type WorkProcess } from "../../api";
import { CaseQuestion } from "../Review";
import { Status, href, useAsync, useAction } from "../../lib";
import { Actor, DataView, Due, EntityLinks, describe, holder, usePerson } from "../../work";
import { AgentIdle, AssignPicker, PriorityBadge, SendToField, StepBadges, byUrgency, caseHref, priorityLevel, processHref, taskHref } from "./shared";
import { DemoContext } from "../../DemoContext";
import { CaseFindings } from "../../Finding";
import { CaseAround } from "./Around";
import { Related } from "../../Related";
import { CaseSafeguards } from "./Safeguards";
import { AgentWork } from "../../AgentWork";
import { CaseBrief } from "../../CaseBrief";
import { CaseGates } from "../Gates";
import { CaseWritebacks } from "../Writeback";
import { FieldPhoto, photosOf } from "../../FieldPhoto";
import { usePermission } from "../../Auth";
import { WatchButton } from "../../Watch";
import { CaseCloseoutCard, RecheckCard } from "../Disposition";

const BpmnView = lazy(() => import("../../BpmnView"));

/** A plain piece of work for a person's queue, with no process behind it. */
function NewWorkItem({ onCreated }: { onCreated: () => void }) {
  const operate = usePermission("operate");
  const [person] = usePerson();
  const roles = useAsync(() => api.workRoles(), []);
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const { busy, error, run } = useAction();
  const [created, setCreated] = useState<number | null>(null);
  if (!operate) return null;
  const create = () => {
    setCreated(null);
    return run("create", () => api.createWorkItem(title.trim(), role, person), (item) => { setCreated(item.id); setTitle(""); onCreated(); });
  };
  return <Disclosure className="new-work-item" label={<>New work item</>}>
    <Line wrap>
      <Input aria-label="Work item title" width="lg" placeholder="What needs doing" maxLength={160} value={title} onChange={(e) => setTitle(e.target.value)} />
      <Select aria-label="Queue" value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="">choose a queue</option>
        {(roles.data?.person ?? []).map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}
      </Select>
      <ConfirmAction label="Create work item" busy={!!busy} disabled={!title.trim() || !role}
        consequence={`This adds shared work to the ${role.replace(/_/g, " ")} queue, with you recorded as its creator.`} onConfirm={create} />
    </Line>
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
      {state !== "closed" && <NewWorkItem onCreated={list.reload} />}
      <Line wrap>
        {!fixedState && <Tabs label="Cases by state" items={["open", "closed", "all"].map((s) => ({ key: s, label: s, current: state === s, onSelect: () => setState(s) }))} />}
        {!filters && <><Field inline label="Kind"><Select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">every kind</option>
            {types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
          </Select></Field>
        <Field inline label="With"><AssignPicker value={who} onChange={setWho} placeholder="anyone" /></Field></>}
        <Checkbox checked={overdue} onChange={setOverdue}>overdue</Checkbox>
        {!filters && <Input aria-label="Search case titles" placeholder="search titles" value={q} onChange={(e) => setQ(e.target.value)} />}
        {list.data && <Text small muted>{list.data.total} cases · {list.data.overdue} overdue · {list.data.due_soon} due soon</Text>}
      </Line>
      {picked.size > 0 && <BulkReassign ids={[...picked]} person={person} onDone={() => setPicked(new Set())} />}
      <Status of={list} empty={!!list.data && rows.length === 0}>No cases match.</Status>
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
      <td><a href={caseHref(r.id)}>{say(r.title)}</a>{r.escalated && <UIBadge tone="danger"> escalated</UIBadge>}</td>
      <td className="small"><UIBadge>{r.type.replace(/_/g, " ")}</UIBadge>{/* the standard queue is how any plain case runs: only a process of its own is worth naming */}
        {r.process_key && r.process_key !== "queue_item" && r.process_key !== r.type && <> <a className="muted" href={processHref(r.process_key)}>{r.process_key.replace(/_/g, " ")}</a></>}</td>
      <td className="small">{r.closed_at ? <UIBadge tone="ok">{cap(r.state)}{r.outcome ? `: ${say(r.outcome)}` : ""}</UIBadge> : r.step ?? r.state}</td>
      <td className="small">{r.closed_at ? "" : holder(r.assignee, r.role)}</td>
      <td className="nowrap">{!r.closed_at && <Due at={r.due} />}</td>
      <td className="small"><PriorityBadge p={r.priority} type={r.type} low /></td>
      <td className="small muted">{when(r.created_at)}</td>
    </tr>
  );
}

function BulkReassign({ ids, person, onDone }: { ids: number[]; person: string; onDone: () => void }) {
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const a = useAction();
  return (
    <UICard className="bulk-bar actions">
      <strong>{ids.length} selected</strong> give everything open on them to
      <AssignPicker value={to} onChange={setTo} />
      <Input placeholder="why" value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button variant="primary" disabled={!to || !!a.busy}
        onClick={() => a.run("go", async () => { for (const id of ids) await api.reassignCase(id, person, to, reason || undefined); }, onDone)}>
        Reassign</Button>
      <ErrorText inline>{a.error}</ErrorText>
    </UICard>
  );
}

export function CasePage({ id, demo = false, closing = false }: { id: number; demo?: boolean; closing?: boolean }) {
  const item = useAsync(() => api.workItem(id), [id]);
  // Read beside the case, not after it, and shown with the rest of the evidence once they answer: arriving one by one,
  // each pushed down what was already on screen (#1069).
  const around = useAsync(() => api.workItemAround(id), [id]);
  const safeguards = useAsync(() => api.workItemSafeguards(id), [id]);
  const evidenceReady = (around.data !== undefined || !!around.error) && (safeguards.data !== undefined || !!safeguards.error);
  const [person] = usePerson();
  const people = useAsync(() => api.workPeople(), []);
  const canAsk = usePermission("ai");  // the assistant runs a paid model: no link for an account that can't ask
  const it = item.data;
  if (!it || item.error) return <section><PageHeader>Case</PageHeader>
    {demo && <DemoContext step="workflow" />}
    <Status of={item} />
    <a href={href("/exceptions", { list: "progress" })}>Return to All work</a></section>;
  const closed = !!it.closed_at;
  const payload = (it as WorkItem & { payload?: Record<string, unknown> }).payload ?? {};
  // a process case (queue_item, #509) keeps what was raised under payload.start
  const raised = (payload.start && typeof payload.start === "object" ? payload.start : payload) as Record<string, unknown>;
  const dataset = typeof raised.dataset === "string" ? raised.dataset : null;
  const level = priorityLevel(it.priority, it.type);
  // A field operator reading the case on a phone needs the job, not the IOC's evidence (#1071): for someone who only
  // works in the field, the evidence and history fold under Full case.
  const roles = (people.data ?? []).find((p) => p.name === person)?.roles ?? [];
  const fieldOnly = !demo && roles.length > 0 && roles.every((r) => r === "operator");
  // A steward's question is answered here, on its case (#1131): its finding names the question on Master data.
  const asks = closed ? null : questionOf(it.sources);
  const evidence = <>
    <CaseBrief it={it} person={person} readOnly={demo} reload={item.reload} />
    {!evidenceReady ? <Status loading /> : <>
      <CaseSafeguards id={it.id} data={safeguards.data} />
      {!asks && <CaseFindings sources={it.sources} />}
      {/* a question about the records has no event to trend */}
      {!asks && <CaseAround id={it.id} data={around.data} />}
      <Related about={{ case: it.id }} />
      <CaseWritebacks itemId={it.id} person={person} readOnly={demo} reload={item.reload} />
      <CaseGates id={it.id} />
      <AgentWork it={it} />
      {it.processes.map((p) => p.process_key === "queue_item"
        // The standard queue is one step, already named under Waiting on: its diagram stays a tap away.
        ? <Disclosure key={p.id} className="card" label={<>How this case is routed</>}>
            <ProcessDiagram p={p} person={demo ? undefined : person} reload={item.reload} /></Disclosure>
        : <ProcessDiagram key={p.id} p={p} person={demo ? undefined : person} reload={item.reload} />)}
      {Object.keys(payload).length > 0 && (
        <Disclosure className="card" open={typeof raised.summary === "string"} label={<>Details</>}><DataView data={payload} /></Disclosure>
      )}
      <Timeline it={it} person={person} readOnly={demo} />
    </>}
  </>;
  return (
    <section>
      {demo && <DemoContext step="workflow" />}
      <div className="entity-head">
        <div>
          <PageHeader record className={it.title.length > 48 ? "case-title-long" : undefined}>{say(it.title)}</PageHeader>
          <Line small>
            <UIBadge>{it.type.replace(/_/g, " ")}</UIBadge>
            <UIBadge tone={closed ? "ok" : "muted"}>{cap(it.state)}{it.outcome ? `: ${say(it.outcome)}` : ""}</UIBadge>
            {level && <span><PriorityBadge p={it.priority} type={it.type} low /> <Text muted>priority, {level.why}</Text></span>}
            <span className="case-opened"><Text muted>opened by</Text> <Actor who={it.created_by} /> <Text muted>{when(it.created_at)}</Text></span>
            {!demo && <WatchButton target={`case:${it.id}`} label="case" />}
            {!demo && canAsk && <a href={href("/ask", { case: String(it.id) })}>Ask about this case</a>}
          </Line>
          {(it.subjects.length > 0 || dataset) && (
            <Text as="p" small>About{" "}
              <EntityLinks ids={it.subjects} />
              {dataset && <> source dataset <a href={href("/sources", { view: "dataset", name: dataset })}>{dataset}</a></>}
            </Text>
          )}
        </div>
        {!closed && <Due at={it.due} />}
      </div>
      {/* The decision first (#1071): who has it, what it waits on, how late, and the one next action, above the
          evidence it is decided on. */}
      {asks && !demo && <CaseQuestion id={asks.id} kind={asks.kind} onDone={item.reload} />}
      <NextStep it={it} person={person} demo={demo} fieldOnly={fieldOnly} answered={!!asks && !demo} reload={item.reload}
        exceptionId={typeof raised.exception_id === "number" ? raised.exception_id : null} />
      {typeof raised.opportunity === "string" && <OpportunityPanel opportunityKey={raised.opportunity} person={person}
        readOnly={demo} onDecided={item.reload} />}
      {typeof raised.handover_id === "number" && <Notice>This is a shift handover.{" "}
        <a href={href("/handover", { id: String(raised.handover_id) })}>Read it and acknowledge it</a>.</Notice>}
      {typeof raised.closeout_id === "number" && <CloseoutPanel id={raised.closeout_id} person={person} readOnly={demo}
        reload={item.reload} />}
      {typeof raised.recheck_of === "number" && <RecheckCard firstId={raised.recheck_of} person={person} closed={closed}
        readOnly={demo} reload={item.reload} />}
      {/* a case run by a standard one-step workflow (queue item, task) closes from here, whatever step it waits on:
          the IOC closes a job back from the field without opening the step (#1222) */}
      {it.type !== "recheck" && (closed ? it.state === "done"
        : it.processes.every((p) => p.state !== "running" || CLOSES_WITH_CASE.has(p.process_key))) &&
        <CaseCloseoutCard itemId={it.id} person={person} closed={closed} readOnly={demo} reload={item.reload} startOpen={closing} />}
      {fieldOnly ? <Disclosure className="case-full" label={<>Full case</>}>{evidence}</Disclosure> : evidence}
    </section>
  );
}

/** The standard workflows that end with their case (fuzer:closeWithCase in queue_item.bpmn and task.bpmn). */
const CLOSES_WITH_CASE = new Set(["queue_item", "task"]);

const sameTime = (a?: string | null, b?: string | null) => !!a && !!b && Math.abs(Date.parse(a) - Date.parse(b)) < 60_000;

/** The steward's question a case works, from its finding's link to Master data's Questions tab. */
function questionOf(sources?: CaseSource[]): { id: number; kind: ReviewKind } | null {
  for (const x of sources ?? []) for (const i of x.finding.items) {
    const m = i.kind === "page" && /[?&]tab=questions&kind=(\w+)&item=(\d+)/.exec(String(i.value));
    if (m) return { id: Number(m[2]), kind: m[1] as ReviewKind };
  }
  return null;
}

/** The decision card at the top of a case (#1071): each open step with who holds it and how late it is, the one next
 * action (open the step), and, for the IOC, giving it to someone else or changing its priority or due time. */
function NextStep({ it, person, demo, fieldOnly, answered = false, reload, exceptionId = null }: {
  it: WorkItem; person: string; demo: boolean; fieldOnly: boolean; answered?: boolean; reload: () => void;
  exceptionId?: number | null;
}) {
  const takeOver = useAction();
  const open = it.processes.flatMap((p) => p.open.filter((s) => ["user", "agent", "fix"].includes(s.kind)));
  const closed = !!it.closed_at;
  if (closed || (!open.length && demo)) return null;
  const next = open.find((s) => s.kind !== "agent" && (!s.assignee || s.assignee === person)) ?? open.find((s) => s.kind !== "agent");
  const stepHref = (sid: number) => demo ? href(`/inbox/task/${sid}`, { demo: "workflow" }) : taskHref(sid);
  return (
    <UICard className="case-next" aria-label="Next step" title={open.length ? "Waiting on" : "Next step"}>
      {open.length > 0 ? <ul className="plain case-next-steps">
        {open.map((s) => (
          <li key={s.id} className="row wrap">
            <strong>{s.name ?? s.element}</strong>
            <Text small muted>with {holder(s.assignee, s.role, s.kind)}</Text>
            <StepBadges row={s} />
            {/* the case's own due time is beside its title: a step says its own only when it differs */}
            {sameTime(s.due_at, it.due) ? null : <Due at={s.due_at} />}
            <AgentIdle step={s} configured={it.agents_configured}
              onTakeOver={demo || !person ? undefined : () => takeOver.run("take over", () =>
                api.handOff(s.id, person, person, "no agent worker picked it up; done by a person"), reload)} />
          </li>
        ))}
      </ul> : <Text as="p" small>No step is open. Close it out below, or give it to someone.</Text>}
      <div className="row wrap case-next-actions">
        {next && !answered && <ButtonLink variant="primary" href={stepHref(next.id)} aria-label={`Open ${next.name ?? next.element}`}>Open the step</ButtonLink>}
        {fieldOnly && <ButtonLink variant="secondary" href={href("/inbox")}>My jobs</ButtonLink>}
      </div>
      <ErrorText>{takeOver.error}</ErrorText>
      {/* the IOC sends it out through the case's workflow: not while the field already has it */}
      {!demo && !fieldOnly && exceptionId != null && !open.some((s) => s.role === "operator") && person &&
        <SendToField exceptionId={exceptionId} person={person} onDone={reload} />}
      {!demo && !fieldOnly && <CaseActions it={it} person={person} reload={reload} />}
    </UICard>
  );
}


/** The update for the system that raised the work (#466): what the field did, as a completion or a follow-up, to
 * edit and send, or not send. */
/** The update a field report drafted for the system that raised the job (#466): the approver approves it, with any
 * edit to its text, or sends it back with why (#1145). Shown on the case and on its task. */
export function CloseoutPanel({ id, person, readOnly, reload }: { id: number; person: string; readOnly: boolean; reload: () => void }) {
  const c = useAsync(() => api.closeout(id), [id]);
  const [text, setText] = useState<string | null>(null);
  const [short, setShort] = useState<string | null>(null);
  const [back, setBack] = useState<string | null>(null);
  const a = useAction();
  if (!c.data) return <Status of={c} />;
  const d = c.data;
  const system = d.system.toUpperCase();
  const done = () => { c.reload(); reload(); };
  const sentBack = d.state === "discarded";
  return (
    <UICard title={d.action === "complete" ? `Complete ${d.record} in ${system}` : `Raise a follow-up to ${d.record} in ${system}`}>
      <p>{d.change ?? (d.action === "complete" ? `Close ${d.record} in ${system}${d.draft.status ? ` (status ${d.draft.status})` : ""}.`
        : `Raise a new ${system} record for the work still needed.`)}</p>
      <Text as="p" small muted>{d.action === "complete"
        ? `The field reported it done. Approved, it goes in ${system}'s next batch, so its record agrees and the IOC stops holding it.`
        : `The field could not do it. Approved, the work stays on ${system}'s books.`} Nothing is written until you approve it.</Text>
      {d.state !== "draft" ? (
        <Text as="p" small><UIBadge tone={d.state === "sent" ? "ok" : "muted"}>{d.state === "approved" ? "approved, goes in the next batch"
          : sentBack ? "sent back, not written" : d.state}</UIBadge>{" "}
          by <Actor who={d.decided_by ?? ""} /> {d.decided_at && when(d.decided_at)}
          {d.applied_at && <Text muted> · applied to the demo's {system} data</Text>}</Text>
      ) : null}
      {d.action === "follow_up" && (
        <p><Text small>Short text</Text>{" "}
          <Input width="lg" disabled={d.state !== "draft" || readOnly} value={short ?? d.draft.short_text ?? ""}
            onChange={(e) => setShort(e.target.value)} /></p>
      )}
      <Textarea rows={4} width="full" disabled={d.state !== "draft" || readOnly} aria-label="Update text"
        value={text ?? d.draft.text} onChange={(e) => setText(e.target.value)} />
      {d.state === "draft" && !readOnly && (back === null ? (
        <Line>
          <Button variant="primary" disabled={!!a.busy} onClick={() => a.run("send", () =>
            api.approveCloseout(id, person, text ?? undefined, short ?? undefined), done)}>Approve for {system}</Button>
          <Button variant="secondary" disabled={!!a.busy} onClick={() => setBack("")}>Send back</Button>
        </Line>
      ) : (
        <Line wrap>
          <Field label="Why it goes back (whoever holds the job reads this)" width="lg">
            <Input value={back} onChange={(e) => setBack(e.target.value)} /></Field>
          <Button variant="primary" disabled={!back.trim() || !!a.busy} onClick={() => a.run("back", () =>
            api.sendBackCloseout(id, person, back.trim()), () => { setBack(null); done(); })}>Send back</Button>
          <Button variant="secondary" onClick={() => setBack(null)}>Cancel</Button>
        </Line>
      ))}
      <ErrorText>{a.error}</ErrorText>
    </UICard>
  );
}

function CaseActions({ it, person, reload }: { it: WorkItem; person: string; reload: () => void }) {
  const [to, setTo] = useState("");
  const [priority, setPriority] = useState(it.priority?.toString() ?? "");
  const [due, setDue] = useState("");
  const [reason, setReason] = useState("");
  const a = useAction();
  return (
    <div className="case-actions">
      <Disclosure className="small" label={<>Give it to someone else</>}>
      <Line wrap>
        <Field label="Give it to" width="md"><AssignPicker value={to} onChange={setTo} /></Field>
        <Field label="Why (kept in its history)" width="md">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <Button variant="secondary" disabled={!to || !!a.busy}
          onClick={() => a.run("to", () => api.reassignCase(it.id, person, to, reason || undefined), () => { setTo(""); reload(); })}>Reassign</Button>
      </Line>
      </Disclosure>
      <Disclosure className="small" label={<>Change priority or due time</>}>
        <Line wrap>
          <Field label="Priority" width="xs"><Input value={priority} onChange={(e) => setPriority(e.target.value)} /></Field>
          <Field label="Due" width="md"><Input type="datetime-local" value={due} onChange={(e) => setDue(e.target.value)} /></Field>
          <Button variant="secondary" disabled={!!a.busy || (!due && priority === (it.priority?.toString() ?? ""))}
            onClick={() => a.run("set", () => api.setCase(it.id, person, {
              ...(priority !== (it.priority?.toString() ?? "") && priority ? { priority: Number(priority) } : {}),
              ...(due ? { due: new Date(due).toISOString() } : {}), reason: reason || undefined }), () => { setDue(""); reload(); })}>Save</Button>
        </Line>
      </Disclosure>
      <ErrorText>{a.error}</ErrorText>
    </div>
  );
}


export function ProcessDiagram({ p, person, reload, focus }: { p: WorkProcess; person?: string; reload?: () => void; focus?: string }) {
  const xml = useAsync(() => api.workDefinitionXml(p.definition_id), [p.definition_id]);
  const a = useAction();
  const marks = useMemo(() => ({
    done: p.path.map((s) => s.element),
    open: focus ? [focus] : p.open.filter((s) => s.state === "waiting" && s.kind !== "join").map((s) => s.element),
    flows: [...p.path, ...p.open].map((s) => s.flow).filter((f): f is string => !!f),
  }), [p, focus]);
  return (
    <UICard title={<a href={processHref(p.process_key)} title={`version ${p.version}`}>{p.process_key.replace(/_/g, " ")} workflow</a>}
      detail={<>{cap(p.state)}{p.outcome ? `: ${say(p.outcome)}` : ""}</>}
      actions={<span className="small muted" title={p.correlation_key ? `key ${p.correlation_key}` : undefined}>
          started {when(p.started_at)} by <Actor who={p.started_by} />
          {p.state === "running" && person && reload && (
            <> · <Button variant="link" disabled={!!a.busy}
              onClick={async () => (await confirmDialog("Cancel this workflow? Its open steps are cancelled.")) &&
                a.run("cancel", () => api.cancelProcess(p.id, person, "cancelled from the case page"), reload)}>cancel workflow</Button></>
          )}
        </span>}>
      <Status of={xml} />
      {xml.data && <Suspense fallback={<div className="status">Loading diagram…</div>}><BpmnView xml={xml.data} marks={marks} /></Suspense>}
      <Disclosure className="small" label={<>Workflow data</>}><DataView data={p.data} /></Disclosure>
      <ErrorText>{a.error}</ErrorText>
    </UICard>
  );
}

export function Timeline({ it, person, readOnly = false }: { it: WorkItem; person: string; readOnly?: boolean }) {
  const [text, setText] = useState("");
  const a = useAction();
  return (
    <UICard title="History">
      <ol className="timeline">
        {it.events.map((e) => {
          const described = describe(e);
          // The brief has its own card above; History says it was written and points there, rather than printing it
          // a second time (#1071).
          const brief = !!it.brief?.entry && (e.kind === "agent_result" || e.kind === "completed")
            && JSON.stringify(e.data ?? {}).includes("what_happened");
          const t = brief ? <>wrote the specialist brief{" "}<Button variant="link" onClick={() =>
            document.querySelector(".case-brief")?.scrollIntoView({ behavior: "smooth", block: "start" })}>Read it</Button></> : described.text;
          const body = brief ? null : described.body;
          return (
            <li key={e.id} className={`ev-${e.kind}`}>
              <Line small><Text muted mono>{when(e.at)}</Text> {e.kind !== "step_started" && <Actor who={e.actor} />} {t}</Line>
              {body}
              {photosOf(e.data).length > 0 && <div className="field-photos">{photosOf(e.data).map((p) =>
                <FieldPhoto key={p.photo_id} exceptionId={p.exception_id} photoId={p.photo_id} />)}</div>}
            </li>
          );
        })}
      </ol>
      {!readOnly && <div className="actions">
        <Input placeholder="Add a comment" value={text} onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && text && a.run("c", () => api.comment(it.id, person || "anonymous", text), () => setText(""))} />
        <Button disabled={!text || !!a.busy} onClick={() => a.run("c", () => api.comment(it.id, person || "anonymous", text), () => setText(""))}>Comment</Button>
        <ErrorText inline>{a.error}</ErrorText>
      </div>}
    </UICard>
  );
}
