/** The step a case waits on, worked on the case (#1222). A step's old address opens its case. */
import { ActionStatus, Button, ConfirmAction, PageHeader, Card as UICard, Input, Disclosure, Textarea, ErrorText, Text, List, Notice, Heading } from "../../ui";
import { when } from "../../format";
import { person as personName } from "../../wording";
import { Suspense, lazy, useEffect, useState } from "react";
import { api, type WorkTaskView } from "../../api";
import { Status, href, useAsync, useAction } from "../../lib";
import { Actor, DataView, usePerson } from "../../work";
import { CloseoutPanel } from "./Cases";
import { AssignPicker, caseHref } from "./shared";
import { UpsetPanel } from "./UpsetPanel";
import { OpportunityPanel } from "./OpportunityPanel";
import { DemoContext } from "../../DemoContext";
import { usePermission } from "../../Auth";
import { FieldPhoto, photosOf } from "../../FieldPhoto";
import { sendCompletion, useOutbox } from "../../fieldOffline";

const TaskForm = lazy(() => import("../../TaskForm"));

/** A step's old address (#/inbox/task/:id) opens its case, where the step is worked (#1222): one page per case. */
export function TaskPage({ id, demo = false }: { id: number; demo?: boolean }) {
  const view = useAsync(() => api.workTask(id), [id]);
  const itemId = view.data?.item.id;
  useEffect(() => {
    if (itemId) location.replace(demo ? href(`/cases/${itemId}`, { demo: "workflow" }) : caseHref(itemId));
  }, [itemId, demo]);
  return <section><PageHeader>Case</PageHeader>
    {demo && <DemoContext step="workflow" />}
    <Status of={view} />
    {view.error && <a href={href("/inbox")}>Return to My work</a>}</section>;
}

/** The step a case waits on, worked on the case itself (#1222): what the field reported, its form or note, take or
 * give back, and sending it on. The case around it carries the evidence, the diagram and the history. */
export function StepActions({ id, demo = false, send = true, onChanged }: {
  id: number; demo?: boolean; /** offer sending the step on; the case page sends the whole case instead */ send?: boolean;
  onChanged: () => void;
}) {
  const operate = usePermission("operate");
  const view = useAsync(() => api.workTask(id), [id]);
  const v = view.data;
  const [person] = usePerson();
  const a = useAction();
  const [done, setDone] = useState<string | null>(null);
  const [completion, setCompletion] = useState<Record<string, unknown> | null>(null);
  useEffect(() => { setCompletion(null); }, [v?.task?.assignee, v?.task?.state]);
  // Closed out without signal (P5.17e): kept on the phone until the server has it, and shown as waiting, not done.
  const kept = useOutbox().waiting.some((w) => w.kind === "task_complete" && (w.payload as { task: number }).task === id);
  if (!v?.task || view.error) return view.loading || view.error ? <Status of={view} /> : null;
  const t = v.task;
  const waiting = t.state === "waiting";
  const others = !!t.assignee && t.assignee !== person;
  const refresh = () => { view.reload(); onChanged(); };
  const complete = (data: Record<string, unknown>) => {
    if (demo) { setCompletion(data); return; }
    return a.run("complete", () => sendCompletion({ task: id, by: person, data, item: v.item.id }), (how) => {
      setDone(how === "queued" ? `Done on this phone: "${t.name ?? t.element}" sends, with this time, when the signal returns.`
        : `Done: "${t.name ?? t.element}".`);
      refresh();
    });
  };
  return (
    <div className="step-actions">
      <ErrorText>{t.error}</ErrorText>
      {done && !demo && <Notice>{done}</Notice>}
      {typeof v.data.change === "string" && <UICard className="notice" title="What to change">
        <p>{v.data.change}</p>
        <Text as="p" small muted>
          {typeof v.data.from_case === "number" && <>From <a href={caseHref(v.data.from_case)}>case {v.data.from_case}</a>{" · "}</>}
          {typeof v.data.decided_by === "string" && <>decided by {personName(v.data.decided_by)}</>}
        </Text>
        <Text as="p" small>When it is done, say what you set in the note so the case keeps it.</Text>
      </UICard>}
      <FromField v={v} />
      {typeof v.data.opportunity === "string" && <OpportunityPanel opportunityKey={v.data.opportunity} person={person}
        readOnly={demo} onDecided={refresh} />}
      {typeof v.data.upset_id === "number" && <UpsetPanel upsetId={v.data.upset_id} itemId={v.item.id} person={person}
        canMerge={waiting && operate && !demo} onMerged={refresh} />}
      {kept && !demo ? <Notice>Done on this phone, waiting to send. The case moves on when the signal
        returns and the server has it.</Notice> : demo && !operate ? <Text as="p" small>Read-only. Changes need operate access and the step's workflow role.</Text> : !waiting ? null
        : t.kind === "agent" ? <Text as="p" small>The {(t.role ?? "").replace(/_/g, " ")} agent has this step. Send it to a person to take it over.</Text>
        : <>
          <div className="actions">
            {!t.assignee && (demo ? <ConfirmAction label="take this step" busy={!!a.busy} disabled={completion !== null}
              consequence="Take this shared step for your account. It leaves the queue and changes recorded ownership; this is not a private demonstration."
              onConfirm={() => a.run("take", () => api.claim(id, person), refresh)} />
              : <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("take", () => api.claim(id, person), refresh)}>Take it</Button>)}
            {t.assignee === person && (demo ? <ConfirmAction label="give it back" busy={!!a.busy} disabled={completion !== null}
              consequence="Give this shared step back to its queue so someone else can take it."
              onConfirm={() => a.run("release", () => api.release(id, person), refresh)} />
              : <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("release", () => api.release(id, person), refresh)}>Give it back to the queue</Button>)}
            {others && <Text small muted>{personName(t.assignee)} has it.</Text>}
          </div>
          {completion && demo ? <div><Heading level={3}>Review the response</Heading><DataView data={completion} />
            <ConfirmAction label="step completion" busy={!!a.busy}
              consequence="Save this response and complete the shared step. This may move the workflow on or queue agent work with configured AI costs. After a failure, look at the case before trying again."
              onConfirm={() => a.run("complete", () => api.complete(id, person, completion), () => {
                setCompletion(null); setDone("Saved."); refresh();
              })} />{" "}
            <Button disabled={!!a.busy} onClick={() => setCompletion(null)}>Edit response</Button>
          </div> : others ? null : typeof v.data.closeout_id === "number"
            ? <CloseoutPanel id={v.data.closeout_id} person={person} readOnly={demo} reload={refresh} />
            : t.kind === "fix" ? <FixForm busy={!!a.busy} onRetry={complete} />
            : v.form?.schema ? (
              <Suspense fallback={<div className="status">Loading form…</div>}>
                <TaskForm schema={v.form.schema} data={v.data} busy={!!a.busy} onSubmit={complete} />
              </Suspense>
            ) : <NoteForm element={t.element} missing={v.form?.key} busy={!!a.busy} onSubmit={complete} />}
        </>}
      {send && waiting && (!demo || operate) && <HandOff id={id} person={person} demo={demo} action={a}
        disabled={demo && completion !== null} requiresHuman={t.requires_human} onDone={() => {
        setDone(`Sent "${t.name ?? t.element}" on.`); refresh();
      }} />}
      {demo ? <ActionStatus busy={a.busy} error={a.error}>{null}</ActionStatus> : a.error && <ErrorText>{a.error}</ErrorText>}
    </div>
  );
}

function HandOff({ id, person, requiresHuman, onDone, demo = false, action: a, disabled }: {
  id: number; person: string; requiresHuman: boolean; onDone: () => void; demo?: boolean;
  action: ReturnType<typeof useAction>; disabled: boolean;
}) {
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  return (
    <Disclosure className="card small" label={<>Send to someone else</>}>
      <div className="actions">
        <AssignPicker value={to} onChange={setTo} agents={!requiresHuman} />
        <Input placeholder="why" value={reason} onChange={(e) => setReason(e.target.value)} />
        {demo ? <ConfirmAction label="send the step" disabled={!to || disabled} busy={!!a.busy}
          consequence={`Transfer this shared task to ${to}. This changes assignment and may queue agent execution with configured AI costs. Human-only steps cannot be assigned to agents. Inspect recorded state before a retry.`}
          onConfirm={() => a.run("h", () => api.handOff(id, person, to, reason || undefined), onDone)} />
          : <Button disabled={!to || !!a.busy} onClick={() => a.run("h", () => api.handOff(id, person, to, reason || undefined), onDone)}>Send</Button>}
      </div>
      {requiresHuman && <Text as="p" small>This step needs a person: it can't go to an agent.</Text>}
    </Disclosure>
  );
}

function FixForm({ busy, onRetry }: { busy: boolean; onRetry: (data: Record<string, unknown>) => void }) {
  const [text, setText] = useState("{}");
  let data: Record<string, unknown> | undefined;
  try {
    data = JSON.parse(text);
  } catch {
    data = undefined;
  }
  return (
    <div className="task-form">
      <Text as="p" small>Correct the workflow data the step tripped on, then retry it.</Text>
      <Textarea className="mono" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="actions">
        <Button variant="primary" disabled={busy || !data} onClick={() => data && onRetry(data)}>Retry step</Button>
        {!data && <ErrorText inline>Not valid JSON.</ErrorText>}
      </div>
    </div>
  );
}

function NoteForm({ element, missing, busy, onSubmit }: {
  element: string; missing?: string; busy: boolean; onSubmit: (data: Record<string, unknown>) => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="task-form">
      {missing && <Text as="p" small muted>Form “{missing}” isn't loaded; completing with a note.</Text>}
      <Textarea rows={3} placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="actions">
        <Button variant="primary" disabled={busy} onClick={() => onSubmit(note ? { [`${element}_note`]: note } : {})}>Complete</Button>
      </div>
    </div>
  );
}

/** What the field reported on this case, above the decision it informs: their notes (newest first) and, when the
 * process has a Field check, its result. Empty when nobody has been sent. */
function FromField({ v }: { v: WorkTaskView }) {
  const notes = v.evidence.comments.filter((c) => String(c.data.text ?? "").startsWith("Field: ")).slice(0, 5);
  const asked = typeof v.data.field_ask === "string" && v.data.field_ask ? v.data.field_ask : null;
  if (!notes.length) return null;
  return (
    <UICard className="from-field" title="From the field">
      {asked && <Text as="p" small muted>You asked: {asked}</Text>}
      <List plain>{notes.map((c) => <li key={c.id}>
        <strong>{String(c.data.text).slice(7)}</strong> <Text small muted>· <Actor who={c.actor} /> {when(c.at)}</Text>
        {photosOf(c.data).length > 0 && <div className="field-photos">{photosOf(c.data).map((p) =>
          <FieldPhoto key={p.photo_id} exceptionId={p.exception_id} photoId={p.photo_id} />)}</div>}
      </li>)}</List>
    </UICard>
  );
}


