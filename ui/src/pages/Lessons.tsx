import { Button, PageHeader, Card as UICard } from "../ui";
import { useState } from "react";
import { api, type Lesson } from "../api";
import { Status, entityHref, href, notifyChanged, useAsync } from "../lib";

export function LessonsPage({ query }: { query: URLSearchParams }) {
  const status = query.get("status") ?? "suggested";
  const list = useAsync(() => api.lessons(status), [status]);
  return (
    <section>
      <div className="row spread">
        <div>
          <PageHeader>Lessons learned</PageHeader>
          <p className="muted">
            Drafted from incidents and the corrective work that closed them, per equipment model and failure mode.
            Approved lessons show up in document search for every piece of equipment of that model.
          </p>
        </div>
        <div className="tabs">
          {["suggested", "approved", "rejected"].map((s) => (
            <a key={s} className={status === s ? "on" : ""} href={href("/lessons", { status: s })}>{s}</a>
          ))}
        </div>
      </div>
      <Status loading={list.loading && !list.data} error={list.error} empty={list.data?.length === 0}>
        Nothing {status}.
      </Status>
      {list.data?.map((l) => <LessonCard key={l.id} lesson={l} />)}
    </section>
  );
}

function LessonCard({ lesson }: { lesson: Lesson }) {
  const [text, setText] = useState(lesson.text);
  const [busy, setBusy] = useState(false);
  const by = (() => { try { return localStorage.getItem("fuzer.reviewer") || "anonymous"; } catch { return "anonymous"; } })();
  const decide = async (d: "approve" | "reject" | "edit") => {
    setBusy(true);
    await api.decideLesson(lesson.id, d, by, text);
    notifyChanged();
  };
  return (
    <UICard className="review">
      <div className="row spread">
        <h3>{lesson.title}</h3>
        <span className="muted small">
          {lesson.stats.incidents} incidents · held {Math.round(lesson.stats.fix_rate * 100)}% · {lesson.failure_mode}
        </span>
      </div>
      <textarea className="lesson-text" value={text} onChange={(e) => setText(e.target.value)} rows={6} />
      <details className="small">
        <summary>Evidence</summary>
        <ul className="plain">
          {lesson.evidence.map((e) => (
            <li key={e.id}>
              <span className="muted mono">{e.start}</span> <a href={entityHref(e.id)}>{e.label}</a>
              {e.recurred && <span className="error"> recurred</span>}
            </li>
          ))}
        </ul>
      </details>
      <div className="actions">
        {lesson.status !== "approved" && <Button variant="primary" disabled={busy} onClick={() => decide("approve")}>Approve</Button>}
        {text !== lesson.text && <Button disabled={busy} onClick={() => decide("edit")}>Save edits</Button>}
        {lesson.status !== "rejected" && <Button disabled={busy} onClick={() => decide("reject")}>Reject</Button>}
        {lesson.decided_by && <span className="muted small">last decided by {lesson.decided_by}</span>}
      </div>
    </UICard>
  );
}
