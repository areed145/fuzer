/** One step to do: its form and the evidence behind it, claim / release / hand off, where it sits in its process, and
 * the case's history. */
import { Suspense, lazy, useMemo, useState } from "react";
import { api, type WorkTaskView } from "../../api";
import { Status, entityHref, href, useAsync } from "../../lib";
import { Actor, DataView, Due, holder, usePerson, when } from "../../work";
import { ProcessDiagram, Timeline } from "./Cases";
import { AssignPicker, StepBadges, caseHref, useAct } from "./shared";

const TaskForm = lazy(() => import("../../TaskForm"));

export function TaskPage({ id }: { id: number }) {
  const view = useAsync(() => api.workTask(id), [id]);
  const v = view.data;
  const item = useAsync(() => (v ? api.workItem(v.item.id) : Promise.resolve(null)), [v?.item.id]);
  const [person] = usePerson();
  const a = useAct();
  const [done, setDone] = useState<string | null>(null);
  if (!v) return <Status loading={view.loading} error={view.error} />;
  const t = v.task;
  const waiting = t.state === "waiting";
  const others = !!t.assignee && t.assignee !== person;
  const complete = (data: Record<string, unknown>) =>
    a.run("complete", () => api.complete(id, person, data), () => setDone(`Done: "${t.name ?? t.element}" on ${v.item.title}.`));
  const proc = item.data?.processes.find((p) => p.id === t.process_id);
  return (
    <section>
      <div className="row spread">
        <h1>{t.name ?? t.element}</h1>
        <Due at={t.due_at} closed={!waiting} />
      </div>
      <p className="small muted">
        on <a href={caseHref(v.item.id)}>{v.item.title}</a> · {t.process_key} v{t.version} · with {holder(t.assignee, t.role, t.kind)}
        {t.escalated_at && <> · escalated {when(t.escalated_at)}</>}
      </p>
      <div className="row small"><StepBadges row={t} /></div>
      {t.error && <p className="error small">{t.error}</p>}
      {done && <p className="notice small">{done} <a href={href("/inbox")}>Back to your inbox</a></p>}
      <div className="task-layout">
        <div>
          {!waiting ? (
            <p className="muted">This step is {t.state}{t.finished_by ? ` (by ${t.finished_by})` : ""}.</p>
          ) : t.kind === "agent" ? (
            <p className="muted">The {t.role} agent has this step{t.claimed_by ? ` (worker ${t.claimed_by})` : ""}. Hand it to a person to take it over.</p>
          ) : (
            <div className="card">
              <div className="actions">
                {!t.assignee && <button className="primary" disabled={!!a.busy} onClick={() => a.run("claim", () => api.claim(id, person), view.reload)}>Claim it</button>}
                {t.assignee === person && <button className="secondary" disabled={!!a.busy} onClick={() => a.run("release", () => api.release(id, person), view.reload)}>Release back to the queue</button>}
                {others && <span className="small muted">{t.assignee} has it.</span>}
              </div>
              {others ? null : t.kind === "fix" ? <FixForm busy={!!a.busy} onRetry={complete} />
                : v.form?.schema ? (
                  <Suspense fallback={<div className="status">Loading form…</div>}>
                    <TaskForm schema={v.form.schema} data={v.data} busy={!!a.busy} onSubmit={complete} />
                  </Suspense>
                ) : <NoteForm element={t.element} missing={v.form?.key} busy={!!a.busy} onSubmit={complete} />}
            </div>
          )}
          {waiting && <HandOff id={id} person={person} requiresHuman={t.requires_human} onDone={() => setDone(`Handed off "${t.name ?? t.element}".`)} />}
          {a.error && <p className="error small">{a.error}</p>}
          <Evidence v={v} />
        </div>
        <div>
          {proc && <ProcessDiagram p={proc} focus={t.element} />}
          {item.data && <Timeline it={item.data} person={person} />}
        </div>
      </div>
    </section>
  );
}

function HandOff({ id, person, requiresHuman, onDone }: { id: number; person: string; requiresHuman: boolean; onDone: () => void }) {
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const a = useAct();
  return (
    <details className="card small">
      <summary>Hand off to someone else</summary>
      <div className="actions">
        <AssignPicker value={to} onChange={setTo} agents={!requiresHuman} />
        <input placeholder="why" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button disabled={!to || !!a.busy} onClick={() => a.run("h", () => api.handOff(id, person, to, reason || undefined), onDone)}>Hand off</button>
      </div>
      {requiresHuman && <p className="muted">This step needs a person: it can't go to an agent.</p>}
      {a.error && <p className="error">{a.error}</p>}
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
        <button className="primary" disabled={busy || !data} onClick={() => data && onRetry(data)}>Retry step</button>
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
        <button className="primary" disabled={busy} onClick={() => onSubmit(note ? { [`${element}_note`]: note } : {})}>Complete</button>
      </div>
    </div>
  );
}

function Evidence({ v }: { v: WorkTaskView }) {
  const e = v.evidence;
  const latest = useMemo(() => {
    const seen = new Set<string>();  // newest result per step first; older tries fold away
    return e.agents.map((a) => ({ a, old: seen.has(a.element ?? "") || !seen.add(a.element ?? "") }));
  }, [e.agents]);
  return (
    <div className="card evidence">
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
    </div>
  );
}
