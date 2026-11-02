// The action inbox (#97): a person's tasks and their roles' queue, the selected task's form and
// evidence, claim / release / hand off, and bulk actions for low-risk steps.

import { Suspense, lazy, useMemo, useState } from "react";
import { api, type WorkRow, type WorkTaskView } from "../api";
import { Status, entityHref, href, notifyChanged, useAsync, useDebounced } from "../lib";
import { Actor, DataView, Due, holder, usePerson, when } from "../work";

const TaskForm = lazy(() => import("../TaskForm"));

type Tab = "mine" | "queue";
type DueFilter = "all" | "overdue" | "soon";

export const itemHref = (id: number) => href(`/work/item/${id}`);

export function WorkPage({ query }: { query: URLSearchParams }) {
  const tab: Tab = query.get("tab") === "queue" ? "queue" : "mine";
  const taskId = Number(query.get("task")) || undefined;
  const [person, setPerson] = usePerson();
  const people = useAsync(() => api.workPeople(), []);
  const inbox = useAsync(() => (person ? api.inbox(person) : Promise.resolve(undefined)), [person]);
  const [type, setType] = useState("");
  const [due, setDue] = useState<DueFilter>("all");
  const [text, setText] = useState("");
  const q = useDebounced(text).toLowerCase();
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [toast, setToast] = useState<string>();

  const all = inbox.data?.[tab] ?? [];
  const types = [...new Set([...(inbox.data?.mine ?? []), ...(inbox.data?.queue ?? [])].map((r) => r.type))].sort();
  const rows = all.filter((r) =>
    (!type || r.type === type) &&
    (due === "all" || (due === "overdue" ? r.overdue : !r.overdue && r.due_at && new Date(r.due_at).getTime() - Date.now() < 86400_000)) &&
    (!q || r.title.toLowerCase().includes(q) || (r.name ?? "").toLowerCase().includes(q)));
  const tickable = rows.filter((r) => r.task_id && r.bulk.length);
  const chosen = tickable.filter((r) => picked.has(r.task_id!));
  const actions = chosen.length ? chosen.map((r) => r.bulk).reduce((a, b) => a.filter((x) => b.includes(x))) : [];
  const me = people.data?.find((p) => p.name === person);

  const toggle = (id: number) => setPicked((s) => {
    const n = new Set(s);
    if (n.has(id)) n.delete(id); else n.add(id);
    return n;
  });

  return (
    <section>
      <div className="row spread">
        <div>
          <h1>Work</h1>
          <p className="muted">
            Steps waiting for you and for your roles' queues, from upsets, daily reports and other processes. Agents
            propose; approvals and anything that reaches outside Fuzer wait for a person.{" "}
            <a href={href("/work/processes")}>Processes and forms</a>
          </p>
        </div>
        <label className="reviewer">
          You are
          <input list="work-people" value={person} placeholder="your name" onChange={(e) => setPerson(e.target.value.trim())} />
          <datalist id="work-people">{people.data?.map((p) => <option key={p.name} value={p.name}>{p.roles.join(", ")}</option>)}</datalist>
          {me && <span className="small">{me.roles.map((r) => r.replace(/_/g, " ")).join(", ") || "no roles"}</span>}
        </label>
      </div>

      {!person ? (
        <div className="status">Type your name to see your work.</div>
      ) : (
        <>
          <div className="work-toolbar">
            <div className="tabs">
              {(["mine", "queue"] as Tab[]).map((t) => {
                const c = inbox.data?.counts[t];
                return (
                  <a key={t} className={tab === t ? "on" : ""} href={href("/work", { tab: t })} onClick={() => setPicked(new Set())}>
                    {t === "mine" ? "My work" : "My roles' queue"} {c && <span className="muted">{c.total}</span>}
                    {c && c.overdue > 0 && <span className="badge late">{c.overdue} overdue</span>}
                    {c && c.due_soon > 0 && <span className="badge soon">{c.due_soon} due soon</span>}
                  </a>
                );
              })}
            </div>
            <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
              <option value="">all types</option>
              {types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
            </select>
            <div className="tabs">
              {(["all", "overdue", "soon"] as DueFilter[]).map((d) => (
                <button key={d} className={due === d ? "on" : ""} onClick={() => setDue(d)}>
                  {d === "soon" ? "due soon" : d}
                </button>
              ))}
            </div>
            <input type="search" placeholder="Filter by title or step" value={text} onChange={(e) => setText(e.target.value)} />
          </div>

          {chosen.length > 0 && (
            <BulkBar person={person} rows={chosen} actions={actions}
              onDone={(msg) => { setPicked(new Set()); setToast(msg); notifyChanged(); }} />
          )}

          <div className="work-layout">
            <div className="card work-list">
              <Status loading={inbox.loading && !inbox.data} error={inbox.error} empty={inbox.data && rows.length === 0}>
                {tab === "mine" ? "Nothing assigned to you." : `Nothing waiting in ${inbox.data?.roles.length ? "your roles' queues" : "a queue: you hold no roles"}.`}
              </Status>
              {rows.length > 0 && (
                <table className="work-rows">
                  <thead>
                    <tr>
                      <th>
                        {tickable.length > 0 && (
                          <input type="checkbox" aria-label="Select all that allow bulk actions"
                            checked={chosen.length === tickable.length}
                            onChange={(e) => setPicked(e.target.checked ? new Set(tickable.map((r) => r.task_id!)) : new Set())} />
                        )}
                      </th>
                      <th>Work</th>
                      <th>With</th>
                      <th>Due</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <Row key={`${r.item_id}-${r.task_id}`} row={r} tab={tab} on={r.task_id === taskId}
                        picked={r.task_id ? picked.has(r.task_id) : false} onPick={toggle} />
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="card task-panel">
              {taskId ? (
                <TaskPanel key={taskId} id={taskId} person={person}
                  onDone={(msg) => { setToast(msg); location.hash = href("/work", { tab }); notifyChanged(); }} />
              ) : (
                <div className="status">Pick a step to see its form and evidence.</div>
              )}
            </div>
          </div>
        </>
      )}
      {toast && <div className="toast" onClick={() => setToast(undefined)}>{toast}</div>}
    </section>
  );
}

function Row({ row, tab, on, picked, onPick }: {
  row: WorkRow; tab: Tab; on: boolean; picked: boolean; onPick: (id: number) => void;
}) {
  const open = row.task_id ? href("/work", { tab, task: String(row.task_id) }) : itemHref(row.item_id);
  return (
    <tr className={on ? "on" : ""}>
      <td>
        {row.task_id && row.bulk.length > 0 && (
          <input type="checkbox" aria-label={`Select ${row.title}`} checked={picked} onChange={() => onPick(row.task_id!)} />
        )}
      </td>
      <td>
        <a href={open} className="work-title">{row.name ?? row.title}</a>
        <div className="small muted">
          <span className="badge">{row.type.replace(/_/g, " ")}</span> <a href={itemHref(row.item_id)}>{row.title}</a>
          {row.priority != null && <> · score {Math.round(row.priority).toLocaleString()}</>}
        </div>
        <div className="row small">
          {row.requires_human && <span className="badge human">needs a person</span>}
          {row.kind === "fix" && <span className="badge late">failed: fix and retry</span>}
          {row.escalated_at && <span className="badge late">escalated</span>}
        </div>
      </td>
      <td className="small">{holder(row.assignee, row.role, row.kind)}</td>
      <td><Due at={row.due_at} /></td>
    </tr>
  );
}

function BulkBar({ person, rows, actions, onDone }: {
  person: string; rows: WorkRow[]; actions: string[]; onDone: (msg: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = async (action: string) => {
    setBusy(true);
    try {
      const out = await api.bulkComplete(rows.map((r) => r.task_id!), person, action, reason || undefined);
      onDone(`${action}: ${out.done.length} done` + (out.refused.length ? `, ${out.refused.length} refused (${out.refused[0].error})` : ""));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <div className="card bulk-bar actions">
      <strong>{rows.length} selected</strong>
      <input placeholder="Reason (kept on each item)" value={reason} onChange={(e) => setReason(e.target.value)} />
      {actions.length ? actions.map((a) => (
        <button key={a} className="primary" disabled={busy} onClick={() => run(a)}>{a}</button>
      )) : <span className="muted small">These steps share no bulk action.</span>}
      {error && <span className="error small">{error}</span>}
    </div>
  );
}

function TaskPanel({ id, person, onDone }: { id: number; person: string; onDone: (msg: string) => void }) {
  const view = useAsync(() => api.workTask(id), [id]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const v = view.data;

  const act = async (fn: () => Promise<unknown>, after?: string) => {
    setBusy(true);
    setError(undefined);
    try {
      await fn();
      if (after) onDone(after); else view.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!v) return <Status loading={view.loading} error={view.error} />;
  const t = v.task;
  const waiting = t.state === "waiting";
  const othersClaim = !!t.assignee && t.assignee !== person;
  const complete = (data: Record<string, unknown>) =>
    act(() => api.complete(id, person, data), `Completed "${t.name ?? t.element}" on ${v.item.title}.`);

  return (
    <div>
      <div className="row spread">
        <h2>{t.name ?? t.element}</h2>
        <Due at={t.due_at} closed={!waiting} />
      </div>
      <p className="small muted">
        <a href={itemHref(v.item.id)}>{v.item.title}</a> · {t.process_key} v{t.version} · with {holder(t.assignee, t.role, t.kind)}
      </p>
      <div className="row small">
        {t.requires_human && <span className="badge human">needs a person</span>}
        {t.escalated_at && <span className="badge late">escalated {when(t.escalated_at)}</span>}
      </div>
      {t.error && <p className="error small">{t.error}</p>}

      {!waiting ? (
        <p className="muted">This step is {t.state}{t.finished_by ? ` (by ${t.finished_by})` : ""}.</p>
      ) : t.kind === "agent" ? (
        <p className="muted">The {t.role} agent has this step{t.claimed_by ? ` (worker ${t.claimed_by})` : ""}. Hand it to a person to take it over.</p>
      ) : (
        <>
          <div className="actions">
            {!t.assignee && <button disabled={busy} onClick={() => act(() => api.claim(id, person))}>Claim</button>}
            {t.assignee === person && <button disabled={busy} onClick={() => act(() => api.release(id, person))}>Release</button>}
            {othersClaim && <span className="small muted">Claimed by {t.assignee}.</span>}
          </div>
          {othersClaim ? null : t.kind === "fix" ? (
            <FixForm busy={busy} onRetry={(data) => complete(data)} />
          ) : v.form?.schema ? (
            <Suspense fallback={<div className="status">Loading form…</div>}>
              <TaskForm schema={v.form.schema} data={v.data} busy={busy} onSubmit={complete} />
            </Suspense>
          ) : (
            <NoteForm element={t.element} missing={v.form?.key} busy={busy} onSubmit={complete} />
          )}
        </>
      )}
      {waiting && <HandOff busy={busy} requiresHuman={t.requires_human} onHandOff={(to, reason) =>
        act(() => api.handOff(id, person, to, reason), `Handed "${t.name ?? t.element}" to ${to}.`)} />}
      {error && <p className="error small">{error}</p>}

      <Evidence v={v} />
    </div>
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

function HandOff({ busy, requiresHuman, onHandOff }: {
  busy: boolean; requiresHuman: boolean; onHandOff: (to: string, reason: string) => void;
}) {
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  return (
    <details className="small hand-off">
      <summary>Hand off</summary>
      <div className="actions">
        <input placeholder={requiresHuman ? "a person, or role:ops_lead" : "a person, role:ops_lead or agent:troubleshooter"}
          value={to} onChange={(e) => setTo(e.target.value.trim())} />
        <input placeholder="Why" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button disabled={busy || !to} onClick={() => onHandOff(to, reason)}>Hand off</button>
      </div>
    </details>
  );
}

function Evidence({ v }: { v: WorkTaskView }) {
  const e = v.evidence;
  const latest = useMemo(() => {
    // Newest result per step first; older tries (before a rejection) fold away.
    const seen = new Set<string>();
    return e.agents.map((a) => ({ a, old: seen.has(a.element ?? "") || !seen.add(a.element ?? "") }));
  }, [e.agents]);
  return (
    <div className="evidence">
      <h3>Evidence</h3>
      {e.subjects.length > 0 && (
        <p className="small">
          About{" "}
          {e.subjects.map((s, i) => (
            <span key={s.fuzer_id}>{i > 0 && ", "}<a href={entityHref(s.fuzer_id)}>{s.label ?? s.fuzer_id}</a></span>
          ))}
        </p>
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
      {e.comments.map((c) => (
        <div key={c.id} className="evidence-item small">
          <Actor who={c.actor} /> <span className="muted">{when(c.at)}</span>
          <p className="section-text">{c.data.text}</p>
        </div>
      ))}
      {!e.agents.length && !e.messages.length && !e.comments.length && (
        <p className="small muted">Nothing yet beyond what started the process:</p>
      )}
      {!e.agents.length && <DataView data={v.data} />}
    </div>
  );
}
