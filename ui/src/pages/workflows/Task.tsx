import { ActionStatus, Button, ConfirmAction, PageHeader, Card as UICard, Input } from "../../ui";
/** One step to do: its form and the evidence behind it, claim / release / hand off, where it sits in its process, and
 * the case's history. */
import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { api, type WorkTaskView } from "../../api";
import { Status, entityHref, href, useAsync } from "../../lib";
import { Actor, DataView, Due, holder, usePerson, when } from "../../work";
import { ProcessDiagram, Timeline } from "./Cases";
import { AssignPicker, StepBadges, caseHref, useAct } from "./shared";
import { DemoContext } from "../../DemoContext";
import { usePermission } from "../../Auth";

const TaskForm = lazy(() => import("../../TaskForm"));

export function TaskPage({ id, demo = false }: { id: number; demo?: boolean }) {
  const operate = usePermission("operate");
  const view = useAsync(() => api.workTask(id), [id]);
  const v = view.data;
  const item = useAsync(() => (v ? api.workItem(v.item.id) : Promise.resolve(null)), [v?.item.id]);
  const [person] = usePerson();
  const a = useAct();
  const [done, setDone] = useState<string | null>(null);
  const [completion, setCompletion] = useState<Record<string, unknown> | null>(null);
  useEffect(() => { setCompletion(null); }, [v?.task.assignee, v?.task.state]);
  if (!v || view.error) return <section><PageHeader>Task</PageHeader>
    {demo && <DemoContext step="workflow" />}
    <Status loading={view.loading} error={view.error} onRetry={view.reload} />
    <a href={href("/inbox")}>Return to Inbox</a></section>;
  const t = v.task;
  const waiting = t.state === "waiting";
  const others = !!t.assignee && t.assignee !== person;
  const complete = (data: Record<string, unknown>) => {
    if (demo) { setCompletion(data); return; }
    return a.run("complete", () => api.complete(id, person, data), () => setDone(`Done: "${t.name ?? t.element}" on ${v.item.title}.`));
  };
  const refresh = () => { view.reload(); item.reload(); };
  const proc = item.data?.processes.find((p) => p.id === t.process_id);
  return (
    <section>
      {demo && <DemoContext step="workflow" />}
      <div className="row spread">
        <PageHeader>{t.name ?? t.element}</PageHeader>
        <Due at={t.due_at} closed={!waiting} />
      </div>
      <p className="small muted">
        on <a href={demo ? href(`/cases/${v.item.id}`, { demo: "workflow" }) : caseHref(v.item.id)}>{v.item.title}</a> · {t.process_key} v{t.version} · with {holder(t.assignee, t.role, t.kind)}
        {t.escalated_at && <> · escalated {when(t.escalated_at)}</>}
      </p>
      <div className="row small"><StepBadges row={t} /></div>
      {t.error && <p className="error small">{t.error}</p>}
      {done && !demo && <p className="notice small">{done} <a href={href("/inbox")}>Back to your inbox</a></p>}
      <div className="task-layout">
        <div>
          <FromField v={v} />
          <Sources v={v} />
          {demo && !operate ? <p>Read-only task inspection. Changes require operate access and applicable workflow roles.</p> : !waiting ? (
            <p className="muted">This step is {t.state}{t.finished_by ? ` (by ${t.finished_by})` : ""}.</p>
          ) : t.kind === "agent" ? (
            <p className="muted">The {t.role} agent has this step{t.claimed_by ? ` (worker ${t.claimed_by})` : ""}. Hand it to a person to take it over.</p>
          ) : (
            <UICard className="">
              <div className="actions">
                {!t.assignee && (demo ? <ConfirmAction label="claim task" busy={!!a.busy} disabled={completion !== null}
                  consequence="Claim this shared task for your account. It leaves the role queue and changes recorded ownership; this is not a private demonstration."
                  onConfirm={() => a.run("claim", () => api.claim(id, person), refresh)} />
                  : <Button variant="primary" disabled={!!a.busy} onClick={() => a.run("claim", () => api.claim(id, person), view.reload)}>Claim it</Button>)}
                {t.assignee === person && (demo ? <ConfirmAction label="release task" busy={!!a.busy} disabled={completion !== null}
                  consequence="Return this shared task to its role queue so another eligible person can claim it."
                  onConfirm={() => a.run("release", () => api.release(id, person), refresh)} />
                  : <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("release", () => api.release(id, person), view.reload)}>Release back to the queue</Button>)}
                {others && <span className="small muted">{t.assignee} has it.</span>}
              </div>
              {!t.assignee && !demo && t.kind !== "fix" && (
                <p className="small muted">Not claimed yet. Completing it now records it as done by you; claim it first to hold it
                  while you work on it.</p>
              )}
              {completion && demo ? <div><h3>Review task response</h3><DataView data={completion} />
                <ConfirmAction label="task completion" busy={!!a.busy}
                  consequence="Save this response and complete the shared task. This may advance the process or queue downstream agent work with configured AI costs. Completion is not proof that downstream work finished. After a failure, inspect the recorded task and case before retrying."
                  onConfirm={() => a.run("complete", () => api.complete(id, person, completion), () => {
                    setCompletion(null); setDone("Task response saved. Inspect the refreshed case for downstream progress."); refresh();
                  })} />{" "}
                <Button disabled={!!a.busy} onClick={() => setCompletion(null)}>Edit response</Button>
              </div> : others ? null : t.kind === "fix" ? <FixForm busy={!!a.busy} onRetry={complete} />
                : v.form?.schema ? (
                  <Suspense fallback={<div className="status">Loading form…</div>}>
                    <TaskForm schema={v.form.schema} data={v.data} busy={!!a.busy} onSubmit={complete} />
                  </Suspense>
                ) : <NoteForm element={t.element} missing={v.form?.key} busy={!!a.busy} onSubmit={complete} />}
            </UICard>
          )}
          {waiting && (!demo || operate) && <HandOff id={id} person={person} demo={demo} action={a}
            disabled={demo && completion !== null} requiresHuman={t.requires_human} onDone={() => {
            setDone(`Handed off "${t.name ?? t.element}".`); if (demo) refresh();
          }} />}
          {demo ? <ActionStatus busy={a.busy} error={a.error}>
            {done && <p>{done} <a href={href(`/cases/${v.item.id}`, { demo: "workflow" })}>Inspect recorded case</a></p>}
          </ActionStatus> : a.error && <p className="error small">{a.error}</p>}
          <Evidence v={v} />
        </div>
        <div>
          {proc && <ProcessDiagram p={proc} focus={t.element} />}
          <Status loading={item.loading && !item.data} error={item.error} onRetry={item.reload} />
          {item.data && <Timeline it={item.data} person={person} readOnly={demo} />}
        </div>
      </div>
    </section>
  );
}

function HandOff({ id, person, requiresHuman, onDone, demo = false, action: a, disabled }: {
  id: number; person: string; requiresHuman: boolean; onDone: () => void; demo?: boolean;
  action: ReturnType<typeof useAct>; disabled: boolean;
}) {
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  return (
    <details className="card small">
      <summary>Hand off to someone else</summary>
      <div className="actions">
        <AssignPicker value={to} onChange={setTo} agents={!requiresHuman} />
        <Input placeholder="why" value={reason} onChange={(e) => setReason(e.target.value)} />
        {demo ? <ConfirmAction label="task handoff" disabled={!to || disabled} busy={!!a.busy}
          consequence={`Transfer this shared task to ${to}. This changes assignment and may queue agent execution with configured AI costs. Human-only steps cannot be assigned to agents. Inspect recorded state before a retry.`}
          onConfirm={() => a.run("h", () => api.handOff(id, person, to, reason || undefined), onDone)} />
          : <Button disabled={!to || !!a.busy} onClick={() => a.run("h", () => api.handOff(id, person, to, reason || undefined), onDone)}>Hand off</Button>}
      </div>
      {requiresHuman && <p className="muted">This step needs a person: it can't go to an agent.</p>}
    </details>
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
      <p className="small">Correct the process data the step tripped on, then retry it.</p>
      <textarea className="mono" rows={4} value={text} onChange={(e) => setText(e.target.value)} />
      <div className="actions">
        <Button variant="primary" disabled={busy || !data} onClick={() => data && onRetry(data)}>Retry step</Button>
        {!data && <span className="error small">Not valid JSON.</span>}
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
      {missing && <p className="small muted">Form “{missing}” isn't loaded; completing with a note.</p>}
      <textarea rows={3} placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} />
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
      {asked && <p className="small muted">You asked: {asked}</p>}
      <ul className="plain">{notes.map((c) => <li key={c.id}>
        <strong>{String(c.data.text).slice(7)}</strong> <span className="muted small">· <Actor who={c.actor} /> {when(c.at)}</span>
      </li>)}</ul>
    </UICard>
  );
}

/** Every system that reported the event this case works, each with what it saw, so the decision weighs them together
 * (#464). Shown only when more than one source reported it. */
function Sources({ v }: { v: WorkTaskView }) {
  const all = v.sources ?? [];
  if (all.length < 2) return null;
  return (
    <UICard className="sources" title={`Reported by ${all.length} sources`}>
      {all.map((x) => <details key={x.id} open className="evidence-item">
        <summary><strong>{x.label}</strong> from {x.source ?? "unknown"}: {x.title}{" "}
          <span className="muted small">· {when(x.first_seen)}{x.state !== "open" ? ` · ${x.state}` : ""}</span></summary>
        {x.seen.length > 0 && <dl className="kv small">{x.seen.map(([k, val]) => <div key={k}><dt>{k}</dt><dd>{val}</dd></div>)}</dl>}
      </details>)}
    </UICard>
  );
}

function Evidence({ v }: { v: WorkTaskView }) {
  const e = v.evidence;
  const latest = useMemo(() => {
    const seen = new Set<string>();  // newest result per step first; older tries fold away
    return e.agents.map((a) => ({ a, old: seen.has(a.element ?? "") || !seen.add(a.element ?? "") }));
  }, [e.agents]);
  return (
    <UICard className="evidence">
      <h3>Evidence</h3>
      {e.subjects.length > 0 && (
        <p className="small">About {e.subjects.map((s, i) => (
          <span key={s.fuzer_id}>{i > 0 && ", "}<a href={entityHref(s.fuzer_id)}>{s.label ?? s.fuzer_id}</a></span>))}</p>
      )}
      {latest.map(({ a, old }) => (
        <details key={a.id} open={!old} className="evidence-item">
          <summary><Actor who={a.actor} /> on {a.data.step ?? a.element} · <span className="muted">{when(a.at)}</span>{old && " (earlier try)"}</summary>
          <DataView data={a.data.data} />
        </details>
      ))}
      {e.messages.map((m) => (
        <details key={m.id} open className="evidence-item">
          <summary><Actor who={m.actor} /> sent {m.data.name} · <span className="muted">{when(m.at)}</span></summary>
          <DataView data={m.data.payload} />
        </details>
      ))}
      {!e.agents.length && !e.messages.length && <p className="small muted">What started the process:</p>}
      {!e.agents.length && <DataView data={v.data} />}
    </UICard>
  );
}
