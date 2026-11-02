// A work item (#97): what it is about, its open steps, each process drawn with the path taken and the
// step it waits on, and the full history of who (person or agent) did what.

import { Suspense, lazy, useMemo, useState } from "react";
import { api, type WorkProcess } from "../api";
import { Status, entityHref, href, notifyChanged, useAsync } from "../lib";
import { Actor, DataView, Due, describe, holder, usePerson, when } from "../work";

const BpmnView = lazy(() => import("../BpmnView"));

export function WorkItemPage({ id }: { id: number }) {
  const item = useAsync(() => api.workItem(id), [id]);
  const [person] = usePerson();
  const it = item.data;
  if (!it) return <Status loading={item.loading} error={item.error} />;
  const open = it.processes.flatMap((p) => p.open.filter((s) => ["user", "agent", "fix"].includes(s.kind)));
  const closed = !!it.closed_at;

  return (
    <section>
      <p className="small"><a href={href("/work")}>← Work</a></p>
      <div className="entity-head">
        <div>
          <h1>{it.title}</h1>
          <div className="row small">
            <span className="badge">{it.type.replace(/_/g, " ")}</span>
            <span className={`badge ${closed ? "ok" : ""}`}>{it.state}{it.outcome ? `: ${it.outcome}` : ""}</span>
            {it.priority != null && <span className="muted">score {Math.round(it.priority).toLocaleString()}</span>}
            <span className="muted">created by</span> <Actor who={it.created_by} /> <span className="muted">{when(it.created_at)}</span>
          </div>
          {it.subjects.length > 0 && (
            <p className="small">About {it.subjects.map((s, i) => <span key={s}>{i > 0 && ", "}<a className="mono" href={entityHref(s)}>{s}</a></span>)}</p>
          )}
        </div>
        {!closed && <Due at={it.due} />}
      </div>

      {open.length > 0 && (
        <div className="card">
          <h3>Waiting on</h3>
          <ul className="plain">
            {open.map((s) => (
              <li key={s.id} className="row">
                {s.kind === "agent" ? <strong>{s.name ?? s.element}</strong>
                  : <a href={href("/work", { tab: s.assignee ? "mine" : "queue", task: String(s.id) })}><strong>{s.name ?? s.element}</strong></a>}
                <span className="small muted">with {holder(s.assignee, s.role, s.kind)}</span>
                {s.requires_human && <span className="badge human">needs a person</span>}
                {s.escalated_at && <span className="badge late">escalated</span>}
                <Due at={s.due_at} />
              </li>
            ))}
          </ul>
        </div>
      )}

      {it.processes.map((p) => <ProcessCard key={p.id} p={p} />)}

      <div className="card">
        <h3>History</h3>
        <ol className="timeline">
          {it.events.map((e) => {
            const { text, body } = describe(e);
            return (
              <li key={e.id} className={`ev-${e.kind}`}>
                <div className="row small">
                  <span className="muted mono">{when(e.at)}</span> {e.kind !== "step_started" && <Actor who={e.actor} />} {text}
                </div>
                {body}
              </li>
            );
          })}
        </ol>
        <CommentBox itemId={it.id} person={person} />
      </div>
    </section>
  );
}

function ProcessCard({ p }: { p: WorkProcess }) {
  const xml = useAsync(() => api.workDefinitionXml(p.definition_id), [p.definition_id]);
  const marks = useMemo(() => ({
    done: p.path.map((s) => s.element),
    open: p.open.filter((s) => s.state === "waiting" && s.kind !== "join").map((s) => s.element),
    flows: [...p.path, ...p.open].map((s) => s.flow).filter((f): f is string => !!f),
  }), [p]);
  return (
    <div className="card">
      <div className="row spread">
        <h3>
          <a href={href(`/work/processes/${p.process_key}`, { tab: "monitor" })}>{p.process_key}</a> <span className="muted small">v{p.version} · {p.state}{p.outcome ? `: ${p.outcome}` : ""}</span>
        </h3>
        <span className="small muted">
          {p.parent_token ? "called by another process · " : ""}
          {p.correlation_key && <>key <span className="mono">{p.correlation_key}</span> · </>}
          started {when(p.started_at)}
        </span>
      </div>
      <Status loading={xml.loading} error={xml.error} />
      {xml.data && (
        <Suspense fallback={<div className="status">Loading diagram…</div>}>
          <BpmnView xml={xml.data} marks={marks} />
        </Suspense>
      )}
      <details className="small">
        <summary>Process data</summary>
        <DataView data={p.data} />
      </details>
    </div>
  );
}

function CommentBox({ itemId, person }: { itemId: number; person: string }) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string>();
  const send = async () => {
    try {
      await api.comment(itemId, person || "anonymous", text);
      setText("");
      notifyChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="actions">
      <input placeholder={person ? "Add a comment" : "Add a comment (set your name on the Work page)"} value={text}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && text && send()} />
      <button disabled={!text} onClick={send}>Comment</button>
      {error && <span className="error small">{error}</span>}
    </div>
  );
}
