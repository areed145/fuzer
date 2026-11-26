import { AskOpsIq } from "../../OpsIq";
import { ActionStatus, Button, ButtonLink, ConfirmAction, Field, Input, PageHeader, Card as UICard, Select, Badge as UIBadge, Disclosure, Textarea, ErrorText, Line, Text, Notice } from "../../ui";
import { when, whenDay } from "../../format";
import { confirmDialog } from "../../ui";
import { OpportunityPanel } from "./OpportunityPanel";
/** Cases: every work item, open or closed, and one case's page: what it's about, where its process is, who has its
 * open steps, its history, and the actions on it (assign, priority, due date, close, cancel). */
import { cap, person as personName, say, workKind } from "../../wording";
import { Suspense, lazy, useMemo, useState, type ReactNode } from "react";
import { api, type CaseSource, type MessageSaid, type ReviewKind, type WorkItem, type WorkProcess } from "../../api";
import { CaseQuestion } from "../Review";
import { Status, afterMap, href, notifyChanged, useAsync, useAction } from "../../lib";
import { Actor, DataView, Due, ENGINE_FIELDS, EntityLinks, describe, holder, usePerson } from "../../work";
import { AgentIdle, AssignPicker, GENERIC_STEP, PriorityBadge, SendToField, StepBadges, caseHref, priorityLevel, processHref } from "./shared";
import { CaseChecklist } from "./Checklist";
import { Markdown } from "../../Markdown";
import { StepActions } from "./Task";
import { DemoContext } from "../../DemoContext";
import { CaseFindings } from "../../Finding";
import { CaseAround } from "./Around";
import { Related } from "../../Related";
import { CaseSafeguards } from "./Safeguards";
import { CaseBrief } from "../../CaseBrief";
import { CaseGates } from "../Gates";
import { CaseWritebacks } from "../Writeback";
import { FieldPhoto, photosOf } from "../../FieldPhoto";
import { usePermission } from "../../Auth";
import { WatchButton } from "../../Watch";
import { CaseCloseoutCard, RecheckCard } from "../Disposition";
import { CaseOptionsCard } from "./Options";
import { MatchRef } from "../masterdata/MatchRef";

const BpmnView = lazy(() => import("../../BpmnView"));

/** A plain piece of work for a person's queue, with no process behind it. */
export function NewWorkItem({ onCreated }: { onCreated: () => void }) {
  const operate = usePermission("operate");
  const [person] = usePerson();
  const roles = useAsync(() => api.workRoles(), []);
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [items, setItems] = useState("");
  const { busy, error, run } = useAction();
  const [created, setCreated] = useState<number | null>(null);
  if (!operate) return null;
  const create = () => {
    setCreated(null);
    const checklist = items.split("\n").map((t) => t.trim()).filter(Boolean);
    return run("create", () => api.createWorkItem(title.trim(), role, person, checklist),
      (item) => { setCreated(item.id); setTitle(""); setItems(""); onCreated(); });
  };
  return <Disclosure className="new-work-item" label={<>New case</>}>
    <Line wrap>
      <Input aria-label="Case title" width="lg" placeholder="What needs doing" maxLength={160} value={title} onChange={(e) => setTitle(e.target.value)} />
      <Select aria-label="Queue" value={role} onChange={(e) => setRole(e.target.value)}>
        <option value="">choose a queue</option>
        {(roles.data?.person ?? []).map((r) => <option key={r} value={r}>{r.replace(/_/g, " ")}</option>)}
      </Select>
      <ConfirmAction label="Open the case" busy={!!busy} disabled={!title.trim() || !role}
        consequence={`This adds shared work to the ${role.replace(/_/g, " ")} queue, with you recorded as its creator.`} onConfirm={create} />
    </Line>
    <Field label="Checklist, one item per line" width="lg">
      <Textarea rows={3} width="lg" aria-label="Checklist, one item per line" value={items} onChange={(e) => setItems(e.target.value)} /></Field>
    <ActionStatus busy={busy ? "Opening the case…" : null} error={error}>
      {created !== null && <>Created. <a href={caseHref(created)}>Open the case</a>.</>}
    </ActionStatus>
  </Disclosure>;
}


export function CasePage({ id, demo = false, closing = false }: { id: number; demo?: boolean; closing?: boolean }) {
  const item = useAsync(() => api.workItem(id), [id]);
  const options = useAsync(() => api.caseOptions(id), [id]);
  const people = useAsync(() => api.workPeople(), []);
  // The options decide where Waiting on sits and the roster whether the evidence folds (a field operator): the page
  // waits for both, failures included, so case 1136 no longer moved under the reader as they answered (P5.26h).
  const settled = (a: { data?: unknown; error?: unknown }) => a.data !== undefined || !!a.error;
  const drawn = settled(item) && settled(options) && settled(people);
  // The evidence is read once the page has drawn, and shown together once it answers: arriving one by one, each pushed
  // down what was already on screen (#1069). Asked beside the case, the trend's slow read took one of the browser's
  // four places and the server's one CPU, and the case sat on Loading for seconds (P5.28d).
  const around = useAsync(() => afterMap(drawn, () => api.workItemAround(id)), [id, drawn], [id]);
  const safeguards = useAsync(() => afterMap(drawn, () => api.workItemSafeguards(id)), [id, drawn], [id]);
  const evidenceReady = settled(around) && settled(safeguards);
  const [person] = usePerson();
  const canAsk = usePermission("ai");  // the assistant runs a paid model: no link for an account that can't ask
  // A question answered here closes its case: its confirmation, with the case and entities it links, stays through
  // the reload that follows (#1382).
  const [answered, setAnswered] = useState<ReactNode>(null);
  const it = item.data;
  if (!it || item.error || !drawn) return <section><PageHeader>Case</PageHeader>
    {demo && <DemoContext step="workflow" />}
    {it && !item.error ? <Status loading /> : <Status of={item} />}
    <a href={href("/list")}>Return to All work</a></section>;
  const closed = !!it.closed_at;
  const shelved = closed ? null : it.sources?.find((x) => x.joined_to == null && x.shelved_until)?.shelved_until;
  const payload = (it as WorkItem & { payload?: Record<string, unknown> }).payload ?? {};
  // a process case (queue_item, #509) keeps what was raised under payload.start
  const raised = (payload.start && typeof payload.start === "object" ? payload.start : payload) as Record<string, unknown>;
  const dataset = typeof raised.dataset === "string" ? raised.dataset : null;
  // what was asked and its evidence are shown under Waiting on, so Details leaves them out
  // a message's case opens with the message itself (P5.26a): who sent it, how and when, then all it said
  const message = messageOf(raised.message);
  const hidden: readonly string[] = [...(typeof raised.summary === "string" ? HIDDEN_DETAILS : ENGINE_FIELDS),
    ...(message ? ["message"] : [])];
  const level = priorityLevel(it.priority, it.type);
  // A field operator reading the case on a phone needs the job, not the IOC's evidence (#1071): for someone who only
  // works in the field, the evidence and history fold under Full case.
  const roles = (people.data ?? []).find((p) => p.name === person)?.roles ?? [];
  const fieldOnly = !demo && roles.length > 0 && roles.every((r) => r === "operator");
  // A steward's question is answered here, on its case (#1131): its finding names the question on Master data. Once
  // answered it is still a question about records, with no event to trend.
  const question = questionOf(it.sources);
  const asks = closed ? null : question;
  // A case run by a standard one-step workflow (queue item, task) closes from here, whatever step it waits on: the IOC
  // closes a job back from the field without opening the step (#1222).
  const closesHere = it.type !== "recheck" && (closed ? it.state === "done"
    : it.processes.every((p) => p.state !== "running" || CLOSES_WITH_CASE.has(p.process_key)));
  const closeout = closesHere ? <CaseCloseoutCard itemId={it.id} person={person} closed={closed} readOnly={demo} reload={item.reload}
    startOpen={closing} openItems={(it.checklist ?? []).filter((c) => c.state === "open").length} /> : null;
  // Close out sits with the step it ends, in Waiting on, rather than loose between the cards below it.
  const steps = it.processes.flatMap((p) => p.open.filter((s) => ["user", "agent", "fix"].includes(s.kind)));
  const inStep = !closed && !(steps.length === 0 && demo);
  const optionsCard = !closed && !fieldOnly ? <CaseOptionsCard o={options} person={person} readOnly={demo}
    onDone={() => { item.reload(); options.reload(); }} /> : null;
  // Back from the field, the IOC's next act is reading the report and closing out, so Waiting on (the report, Close
  // out) comes first and the options to send it again follow it, rather than "Dispatch now · Recommended" on top.
  const backFromField = options.data?.stage === "back";
  const evidence = <>
    <CaseBrief it={it} person={person} readOnly={demo} reload={item.reload} />
    {!evidenceReady ? <Status loading /> : <>
      <CaseSafeguards id={it.id} data={safeguards.data} />
      {/* the message card above is what was found when the case is only messages */}
      {!asks && !(message && it.sources?.every((x) => x.type === "message_report")) && <CaseFindings sources={it.sources} openedBy={it.created_by} />}
      {/* a question about the records has no event to trend, answered or not */}
      {!question && <CaseAround id={it.id} data={around.data} focus={it.title} />}
      <Related about={{ case: it.id }} />
      <CaseWritebacks itemId={it.id} person={person} readOnly={demo} reload={item.reload} />
      <CaseGates id={it.id} />
      {it.processes.map((p) => CLOSES_WITH_CASE.has(p.process_key)
        // A standard one-step workflow (queue item, task) is already named under Waiting on: its diagram stays a tap away.
        ? <Disclosure key={p.id} className="card" label={<>How this case is routed</>}>
            <ProcessDiagram p={p} person={demo ? undefined : person} reload={item.reload} /></Disclosure>
        : <ProcessDiagram key={p.id} p={p} person={demo ? undefined : person} reload={item.reload} />)}
      {/* what was asked is at the top, under Waiting on; the engine's routing keys are not for reading */}
      {Object.keys(raised).some((k) => !hidden.includes(k)) && (
        <Disclosure className="card" label={<>Details</>}><DataView data={raised} omit={hidden} /></Disclosure>
      )}
      <Timeline it={it} person={person} readOnly={demo} />
    </>}
  </>;
  return (
    <section>
      {demo && <DemoContext step="workflow" />}
      <div className="entity-head case-head">
        <div>
          <PageHeader record className={it.title.length > 48 ? "case-title-long" : undefined}>{say(it.title)}</PageHeader>
          {/* One line under the title: what it is, how late, what it is about, who opened it. The due time and the
              entity it is about each took a line of their own, and Watch sat between them. */}
          <Line small wrap>
            <UIBadge>{workKind(it.type)}</UIBadge>
            {/* a shelved case is off every queue until its date: that, not "Open, overdue" (#1400) */}
            <UIBadge tone={closed ? "ok" : "muted"}>{shelved ? `Shelved until ${whenDay(shelved)}`
              : `${cap(it.state)}${it.outcome ? `: ${say(it.outcome)}` : ""}`}</UIBadge>
            {!closed && !shelved && <Due at={it.due} />}
            {level && <span><PriorityBadge p={it.priority} type={it.type} low /> <Text muted>priority{level.why && `, ${level.why}`}</Text></span>}
            {(it.subjects.length > 0 || dataset) && <span><Text muted>about</Text>{" "}
              <EntityLinks ids={it.subjects} />
              {dataset && <> source dataset <a href={href("/sources", { view: "dataset", name: dataset })}>{dataset}</a></>}
            </span>}
            {!message && <span className="case-opened"><Text muted>opened by</Text> <Actor who={it.created_by} /> <Text muted>{when(it.created_at)}</Text></span>}
            {!demo && <WatchButton target={`case:${it.id}`} label="case" />}
            {!demo && canAsk && <AskOpsIq about={{ case: String(it.id) }} what="case" />}
          </Line>
          {it.parent && <Text as="p" small>Part of <a href={caseHref(it.parent.id)}>{say(it.parent.title)}</a></Text>}
        </div>
      </div>
      {message && <CaseMessage m={message} matched={matchedBy(it.sources)} person={person}
        exceptionId={!closed && !demo && typeof raised.exception_id === "number" ? raised.exception_id : null}
        onChanged={item.reload} />}
      {/* The decision first (#1071): who has it, what it waits on, how late, and the one next action, above the
          evidence it is decided on. */}
      {answered ? <Notice role="status">{answered}</Notice>
        : asks && !demo && <CaseQuestion id={asks.id} kind={asks.kind} onDone={(said) => { setAnswered(said); item.reload(); }} />}
      {/* the choices side by side, with what each costs (P5.20): the IOC's decision on field work comes first */}
      {!backFromField && optionsCard}
      <NextStep it={it} person={person} demo={demo} fieldOnly={fieldOnly} answered={!!asks && !demo} reload={item.reload}
        optionsShown={!!options.data?.applies || backFromField} closeout={inStep ? closeout : null}
        exceptionId={typeof raised.exception_id === "number" ? raised.exception_id : null}
        proposal={typeof raised.opportunity === "string"}
        asked={typeof raised.summary === "string" ? raised.summary : null}
        evidence={Array.isArray(raised.evidence) && typeof raised.summary === "string" ? raised.evidence.filter((x): x is string => typeof x === "string") : []} />
      {backFromField && optionsCard}
      {closed && (it.checklist ?? []).length > 0 && <UICard title="Checklist">
        <CaseChecklist it={it} person={person} readOnly reload={item.reload} /></UICard>}
      {typeof raised.opportunity === "string" && <OpportunityPanel opportunityKey={raised.opportunity} person={person}
        readOnly={demo} onDecided={item.reload} />}
      {typeof raised.handover_id === "number" && <Notice>This is a shift handover.{" "}
        <a href={href("/handover", { id: String(raised.handover_id) })}>Read it and acknowledge it</a>.</Notice>}
      {typeof raised.closeout_id === "number" && <CloseoutPanel id={raised.closeout_id} person={person} readOnly={demo}
        reload={item.reload} />}
      {typeof raised.recheck_of === "number" && <RecheckCard firstId={raised.recheck_of} person={person} closed={closed}
        readOnly={demo} reload={item.reload} />}
      {!inStep && closeout}
      {fieldOnly ? <Disclosure className="case-full" label={<>Full case</>}>{evidence}</Disclosure> : evidence}
    </section>
  );
}

const messageOf = (m: unknown): MessageSaid | null =>
  m && typeof m === "object" && typeof (m as MessageSaid).text === "string" ? m as MessageSaid : null;

/** How a message's case was placed: the reader's "Matched by", or the steward's "Chosen by", whose value is an actor
 * and reads as their name (#1383). */
const matchedBy = (sources?: CaseSource[]): string | null => {
  const items = sources?.find((x) => x.type === "message_report")?.finding.items ?? [];
  const chosen = items.find((i) => i.label === "Chosen by");
  if (chosen) return `Chosen by ${personName(String(chosen.value))}`;
  const item = items.find((i) => i.label === "Matched by");
  return item ? `Matched by ${String(item.value)}` : null;
};

/** The message a case was opened from, first on the case (P5.26a): "Radio call · GGCP CRO · Nov 23, 6:52 AM", then
 * everything it said. The title above it is a short summary. */
function CaseMessage({ m, matched, person, exceptionId, onChanged }: {
  m: MessageSaid; matched: string | null; person: string;
  /** The open message case's event, where its entities can be changed (a wrong match, #1382); null when they can't. */
  exceptionId: number | null; onChanged: () => void;
}) {
  // a field note's sender is the operator's actor, named like anyone else (#1383)
  const operate = usePermission("operate");
  const a = useAction();
  const [changes, setChanges] = useState(0);  // a change made closes the picker: it opens fresh for the next
  return <UICard className="case-message" title={`${m.channel_label} · ${personName(m.sender)} · ${when(m.received_at)}`}>
    {m.subject && <Text as="p" small>{m.subject}</Text>}
    <p>{say(m.text)}</p>
    {matched && <Text as="p" small muted>{matched}</Text>}
    {exceptionId != null && operate && person && <MatchRef key={changes} value="" label="Change the entity"
      canRemember={false} kinds={["work"]} multiple busy={!!a.busy} onPick={(_, __, all) => a.run("Changing the entity…",
        () => api.changeMessageEntities(exceptionId, person, all),
        () => { setChanges((n) => n + 1); notifyChanged(); onChanged(); })} />}
    <ActionStatus busy={a.busy} error={a.error} />
  </UICard>;
}

/** The standard workflows that end with their case (fuzer:closeWithCase in queue_item.bpmn and task.bpmn). */
const CLOSES_WITH_CASE = new Set(["queue_item", "task"]);
/** Whether a step is the one a standard workflow's case is worked in: a task's one step, or a queue item's "Work the
 * case". Close out ends it, with what was found, so the step offers no Complete with a note of its own, which closed
 * the case with no close-out at all. A queue item's Field check is the field's step and keeps its own. */
function standsForCase(it: WorkItem, step: { process_id: number; element: string }): boolean {
  const p = it.processes.find((x) => x.id === step.process_id);
  return !!p && (p.process_key === "task" || (p.process_key === "queue_item" && step.element === "work"));
}

/** What a case's Details leave out: the engine's routing keys, and the summary, which is shown as what was asked. */
const HIDDEN_DETAILS = [...ENGINE_FIELDS, "summary", "evidence"];

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
function NextStep({ it, person, demo, fieldOnly, answered = false, reload, exceptionId = null, asked = null, evidence = [], proposal = false,
  optionsShown = false, closeout = null }: {
  it: WorkItem; person: string; demo: boolean; fieldOnly: boolean; answered?: boolean; reload: () => void;
  /** Close out or Finish, shown under the step it ends. */
  closeout?: ReactNode;
  /** The Options card dispatches it (P5.20), or the field has reported, so the step leaves out its own Send to the field. */
  optionsShown?: boolean;
  /** The case shows its proposal with the decision below, so the step leaves it out. */
  proposal?: boolean;
  exceptionId?: number | null; /** what was asked, in the asker's words (Markdown) */ asked?: string | null;
  /** the records the ask cites */ evidence?: string[];
}) {
  const takeOver = useAction();
  const open = it.processes.flatMap((p) => p.open.filter((s) => ["user", "agent", "fix"].includes(s.kind)));
  const closed = !!it.closed_at;
  if (closed || (!open.length && demo)) return null;
  const next = open.find((s) => s.kind !== "agent" && (!s.assignee || s.assignee === person)) ?? open.find((s) => s.kind !== "agent");
  return (
    <UICard className="case-next" aria-label="Next step" title={open.length ? "Waiting on" : "Next step"}>
      {open.length > 0 ? <ul className="plain case-next-steps">
        {open.map((s) => (
          <li key={s.id} className="row wrap">
            {/* a standard one-step workflow's step ("Do the task") says nothing: who holds it does */}
            {GENERIC_STEP.test(s.name ?? "") ? <strong>With {holder(s.assignee, s.role, s.kind)}</strong> : <>
              <strong>{s.name ?? s.element}</strong>
              <Text small muted>with {holder(s.assignee, s.role, s.kind)}</Text></>}
            <StepBadges row={s} />
            {/* the case's own due time is beside its title: a step says its own only when it differs */}
            {sameTime(s.due_at, it.due) ? null : <Due at={s.due_at} />}
            <AgentIdle step={s} configured={it.agents_configured}
              onTakeOver={demo || !person ? undefined : () => takeOver.run("take over", () =>
                api.handOff(s.id, person, person, "no agent worker picked it up; done by a person"), reload)} />
          </li>
        ))}
      </ul> : <Text as="p" small>No step is open. Close it out below, or send it to someone.</Text>}
      {asked && <div className="md case-asked"><Markdown text={asked} /></div>}
      {evidence.length > 0 && <Text as="p" small muted>Evidence <EntityLinks ids={evidence} /></Text>}
      <CaseChecklist it={it} person={person} readOnly={demo} reload={reload} />
      {/* the step is worked here (#1222): no separate step page. Where Close out is offered it ends the step with the
          case, so the step offers no Complete of its own: one way to finish (#1348) */}
      {/* Take it and Close out side by side: the step's form or the close-out form, once open, takes the full width */}
      <div className="case-next-do">
        {next && !answered && <StepActions key={next.id} id={next.id} demo={demo} send={false} onChanged={reload} caseShowsProposal={proposal}
          complete={!closeout && !standsForCase(it, next)} />}
        {closeout && <div className="case-next-close">{closeout}</div>}
      </div>
      {fieldOnly && <div className="row wrap case-next-actions"><ButtonLink variant="secondary" href={href("/inbox")}>My jobs</ButtonLink></div>}
      <ErrorText>{takeOver.error}</ErrorText>
      {/* the IOC sends it out through the case's workflow: not while the field already has it */}
      {!demo && !fieldOnly && !optionsShown && exceptionId != null && !open.some((s) => s.role === "operator") && person &&
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
  // Sent: the form closes (a new key folds the Disclosure) and says where it went (#1385)
  const [sent, setSent] = useState<string | null>(null);
  const [sends, setSends] = useState(0);
  const a = useAction();
  const send = () => a.run("to", () => api.reassignCase(it.id, person, to, reason || undefined), () => {
    setSent(to); setSends((n) => n + 1); setTo(""); setReason(""); reload();
  });
  const role = sent?.startsWith("role:");
  return (
    <div className="case-actions">
      <Disclosure key={sends} className="small" label={<>Send to someone else</>}>
      <Line wrap>
        <Field label="Send to" width="md"><AssignPicker value={to} onChange={(v) => { setTo(v); setSent(null); }} /></Field>
        <Field label="Why (kept in its history)" width="md">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <Button variant="secondary" disabled={!to || !!a.busy} onClick={send}>Send</Button>
      </Line>
      </Disclosure>
      {sent && !a.busy && !a.error && <Text as="p" small role="status">Sent to {holder(role ? null : sent, role ? sent : null)}.</Text>}
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
              <Line small><Text muted mono>{when(e.at)}</Text> {e.kind !== "step_started" && <Actor who={e.actor} />} {t}
                {e.count && e.count > 1 ? <Text muted>{" "}({e.count} times)</Text> : null}</Line>
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
