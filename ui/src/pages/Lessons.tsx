import { Badge, Button, PageHeader, Card as UICard, Disclosure } from "../ui";
import { useState } from "react";
import { api, type Lesson } from "../api";
import { Status, entityHref, href, notifyChanged, useAsync } from "../lib";
import { Actor, usePerson, when } from "../work";

export function LessonsPage({ query }: { query: URLSearchParams }) {
  const status = query.get("status") ?? "suggested";
  const list = useAsync(() => api.lessons(status), [status]);
  return (
    <section>
      <div className="row spread">
        <div>
          <PageHeader subtitle="Lessons drafted from incidents and the work that closed them.">Lessons learned</PageHeader>
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
  const [person] = usePerson();
  const by = person || "anonymous";
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
          {" "}· version {lesson.version}
        </span>
      </div>
      {lesson.status === "approved" && (
        <p className="small">
          {lesson.owner ? <>Owned by <Actor who={lesson.owner} /></> : <>No owner yet</>}
          {lesson.review_by ? <>, review due {lesson.review_by}</> : <>, no review date</>}
          {lesson.review_overdue && <> <Badge tone="warn">review overdue</Badge></>}
        </p>
      )}
      <textarea className="lesson-text" value={text} onChange={(e) => setText(e.target.value)} rows={6} />
      <Disclosure className="small" label={<>Evidence</>}>
        <ul className="plain">
          {lesson.evidence.map((e) => (
            <li key={e.id}>
              <span className="muted mono">{e.start}</span> <a href={entityHref(e.id)}>{e.label}</a>
              {e.recurred && <span className="error"> recurred</span>}
            </li>
          ))}
        </ul>
      </Disclosure>
      <Disclosure className="small" label={<>History</>}>
        <LessonHistory id={lesson.id} />
      </Disclosure>
      <div className="actions">
        {lesson.status !== "approved" && <Button variant="primary" disabled={busy} onClick={() => decide("approve")}>Approve</Button>}
        {lesson.status === "approved" && text === lesson.text && (
          <Button variant={lesson.review_overdue ? "primary" : "secondary"} disabled={busy} onClick={() => decide("approve")}>
            Reviewed, still holds</Button>
        )}
        {text !== lesson.text && <Button disabled={busy} onClick={() => decide("edit")}>Save edits</Button>}
        {lesson.status !== "rejected" && <Button disabled={busy} onClick={() => decide("reject")}>Reject</Button>}
        {lesson.decided_by && <span className="muted small">last decided by {lesson.decided_by}</span>}
      </div>
    </UICard>
  );
}

const ACTION = { approve: "Approved", reject: "Rejected", edit: "Edited" } as const;

/** Every version of a lesson, newest first: who decided or edited it, when, and the wording it had. */
function LessonHistory({ id }: { id: number }) {
  const h = useAsync(() => api.lessonHistory(id), [id]);
  return (
    <>
      <Status loading={h.loading && !h.data} error={h.error} empty={h.data?.length === 0}>
        No decisions or edits yet.
      </Status>
      <ul className="plain">
        {h.data?.map((v) => (
          <li key={v.version}>
            <span className="muted mono">v{v.version}</span> {ACTION[v.action]} by <Actor who={v.by} /> {when(v.at)}
            <Disclosure label={<>Wording</>}><p className="small">{v.text}</p></Disclosure>
          </li>
        ))}
      </ul>
    </>
  );
}
