import { ActionStatus, Badge, Button, PageHeader, Card as UICard, Disclosure, Textarea, ErrorText, Line, Text, List } from "../ui";
import { percent, when } from "../format";
import { useState } from "react";
import { api, type Lesson } from "../api";
import { Status, entityHref, href, useAction, useAsync } from "../lib";
import { Actor, usePerson } from "../work";

export function LessonsPage({ query }: { query: URLSearchParams }) {
  const status = query.get("status") ?? "suggested";
  const list = useAsync(() => api.lessons(status), [status]);
  return (
    <section>
      <Line spread>
        <div>
          <PageHeader>Lessons learned</PageHeader>
        </div>
        <div className="tabs">
          {["suggested", "approved", "rejected"].map((s) => (
            <a key={s} className={status === s ? "on" : ""} href={href("/lessons", { status: s })}>{s[0].toUpperCase() + s.slice(1)}</a>
          ))}
        </div>
      </Line>
      <Status of={list} empty={list.data?.length === 0}>
        Nothing {status}.
      </Status>
      {list.data?.map((l) => <LessonCard key={l.id} lesson={l} />)}
    </section>
  );
}

function LessonCard({ lesson }: { lesson: Lesson }) {
  const [text, setText] = useState(lesson.text);
  const { busy, error, run } = useAction();
  const [person] = usePerson();
  const by = person || "anonymous";
  const decide = (d: "approve" | "reject" | "edit") => run(d, () => api.decideLesson(lesson.id, d, by, text));
  return (
    <UICard className="review" title={lesson.title}
      actions={<Text small muted>
          {lesson.stats.incidents} incidents · held {percent(lesson.stats.fix_rate)} · {lesson.failure_mode}
          {" "}· version {lesson.version}
        </Text>}>
      {lesson.status === "approved" && (
        <Text as="p" small>
          {lesson.owner ? <>Owned by <Actor who={lesson.owner} /></> : <>No owner yet</>}
          {lesson.review_by ? <>, review due {lesson.review_by}</> : <>, no review date</>}
          {lesson.review_overdue && <> <Badge tone="warn">review overdue</Badge></>}
        </Text>
      )}
      <Textarea className="lesson-text" value={text} onChange={(e) => setText(e.target.value)} rows={6} />
      <Disclosure className="small" label={<>Evidence</>}>
        <List plain>
          {lesson.evidence.map((e) => (
            <li key={e.id}>
              <Text muted mono>{e.start}</Text> <a href={entityHref(e.id)}>{e.label}</a>
              {e.recurred && <ErrorText inline> recurred</ErrorText>}
            </li>
          ))}
        </List>
      </Disclosure>
      <Disclosure className="small" label={<>History</>}>
        <LessonHistory id={lesson.id} />
      </Disclosure>
      <ActionStatus error={error} />
      <div className="actions">
        {lesson.status !== "approved" && <Button variant="primary" disabled={!!busy} onClick={() => decide("approve")}>Approve</Button>}
        {lesson.status === "approved" && text === lesson.text && (
          <Button variant={lesson.review_overdue ? "primary" : "secondary"} disabled={!!busy} onClick={() => decide("approve")}>
            Reviewed, still holds</Button>
        )}
        {text !== lesson.text && <Button disabled={!!busy} onClick={() => decide("edit")}>Save edits</Button>}
        {lesson.status !== "rejected" && <Button disabled={!!busy} onClick={() => decide("reject")}>Reject</Button>}
        {lesson.decided_by && <Text small muted>last decided by {lesson.decided_by}</Text>}
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
      <Status of={h} empty={h.data?.length === 0}>
        No decisions or edits yet.
      </Status>
      <List plain>
        {h.data?.map((v) => (
          <li key={v.version}>
            <Text muted mono>v{v.version}</Text> {ACTION[v.action]} by <Actor who={v.by} /> {when(v.at)}
            <Disclosure label={<>Wording</>}><Text as="p" small>{v.text}</Text></Disclosure>
          </li>
        ))}
      </List>
    </>
  );
}
