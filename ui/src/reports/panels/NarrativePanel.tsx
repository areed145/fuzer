// The daily report on Daily operations (#98, #242): the `narrative` panel draws the production day's narrative, its
// versions and its checks; `DailyReview` sits in the report's header with where the review stands and the review
// itself (publish, or send back with what to change). The `daily_report` case behind it is linked for its history; the
// day's production figures are the `production_day` record (EC DAY001).
import { useState } from "react";
import { api, type DailyReport, type DailyStatus } from "../../api";
import { readableCitations } from "../../entityRefs";
import { money, when, whenDay } from "../../format";
import { Markdown } from "../../Markdown";
import { Status, entityHref, href, useAsync, useAction } from "../../lib";
import { AgentIdle } from "../../pages/workflows/shared";
import { Badge, Button, ErrorText, Heading, List, Text, Textarea, type Tone } from "../../ui";
import { usePerson } from "../../work";
import { units } from "../../wording";
import { PanelFrame, usePanel } from "../panels";
import type { NarrativeResult, PanelProps } from "../spec";

const STATUS_TONE: Record<string, Tone> = { published: "ok", "sent back": "warn", drafting: "warn", "needs a fix": "warn" };
const STATUS_HELP: Record<string, string> = {
  drafting: "the reporter agent is drafting it",
  "awaiting review": "drafted; waiting for the ops lead to publish it or send it back",
  "sent back": "the reviewer asked for changes; the reporter agent is redrafting it",
  published: "reviewed and published",
  "needs a fix": "a step failed and needs someone to fix it",
  "no review": "no review case (drafted before reviews, or closed by hand)",
};

export function StatusBadge({ status }: { status: DailyStatus }) {
  return <Badge tone={STATUS_TONE[status]} title={STATUS_HELP[status]}>{status}</Badge>;
}

/** The production day's narrative, as the reporter agent drafted it, with its versions and its checks. */
export function NarrativePanel(props: PanelProps) {
  const r = usePanel<NarrativeResult>(props);
  const day = r.data?.status ? r.data.day : null;
  return <PanelFrame title={props.panel.title ?? "Daily report"} detail={day ? whenDay(day) : undefined} result={r.data}
    loading={r} className="report-narrative">
    {day && <Narrative key={day} day={day} />}
  </PanelFrame>;
}

function Narrative({ day }: { day: string }) {
  const [version, setVersion] = useState<number | undefined>();
  const r = useAsync(() => api.dailyReport(day, version), [day, version]);
  const d = r.data;
  return <>
    <Status of={r} />
    {d && <>
      <Markdown text={units(readableCitations(d.markdown))} />
      {!d.checks.ok && <List small>
        {d.checks.uncited_lines.map((l) => <li key={l}>No citation: {l}</li>)}
        {d.checks.unknown_citations.map((c) => <li key={c}>Cites a record not in the day's facts: {c}</li>)}
        {d.checks.unknown_numbers.map((n) => <li key={n}>A number not in the records: {n}</li>)}
      </List>}
      {d.changes && <>
        <Heading level={3}>Changed since version {d.version - 1}</Heading>
        {Object.entries(d.changes).map(([section, c]) => (
          <div key={section} className="small">
            <strong>{section}</strong>
            {c.added.map((a) => <div key={a} className="ok">+ {a}</div>)}
            {c.removed.map((a) => <div key={a} className="muted">− {a}</div>)}
          </div>
        ))}
      </>}
      <div className="row small muted report-versions">
        {d.versions.length > 1 && <>
          <span>Version</span>
          {d.versions.map((v) => (
            <Button key={v} className={v === d.version ? "chip on" : "chip"} onClick={() => setVersion(v)}>{v}</Button>
          ))}
        </>}
        <span>{d.checks.ok ? "Every line cites its records" : "Has lines to check"} · drafted by {d.drafted_by}</span>
        {d.usage?.cost_usd !== undefined && <span>{money(d.usage.cost_usd)}</span>}
        {d.production_day && <a href={entityHref(d.production_day)}>Production day record</a>}
      </div>
    </>}
  </>;
}

/** In the report header: where the day's review stands, and Publish or Send back while it waits for one. Other days
 * waiting for review are linked so none is missed. */
export function DailyReview({ day, onDay }: { day: string; onDay: (day: string) => void }) {
  const r = useAsync(() => api.dailyReport(day), [day]);
  const days = useAsync(() => api.dailyReports(), []);
  const waiting = (days.data ?? []).filter((x) => x.status === "awaiting review" && x.day !== day);
  if (!r.data && !waiting.length) return null;
  return <div className="report-review" role="group" aria-label="Daily report review">
    {r.data && <Review d={r.data} onDone={() => { r.reload(); days.reload(); }} />}
    {waiting.length > 0 && <Text small muted>Also waiting for review: {waiting.map((x, i) => <span key={x.day}>
      {i > 0 && ", "}<Button variant="link" onClick={() => onDay(x.day)}>{whenDay(x.day)}</Button></span>)}</Text>}
  </div>;
}

function Review({ d, onDone }: { d: DailyReport; onDone: () => void }) {
  const [person] = usePerson();
  const [revising, setRevising] = useState(false);
  const [comments, setComments] = useState("");
  const a = useAction();
  const rv = d.review;
  if (!rv) return <Text small muted><StatusBadge status="no review" /> {STATUS_HELP["no review"]}</Text>;
  const step = rv.step;
  const caseLink = <a href={href(`/cases/${rv.item_id}`)}>Case history</a>;
  if (rv.status !== "awaiting review" || !step) {
    return <p className="small row">
      <StatusBadge status={rv.status} /> <Text muted>{STATUS_HELP[rv.status] ?? rv.status}
        {rv.closed_at && ` ${when(rv.closed_at)}`}</Text>
      {rv.comments && rv.status === "sent back" && <span>Asked for: “{rv.comments}”</span>}
      {step?.error && <ErrorText inline>{step.error}</ErrorText>}
      <AgentIdle step={step} />
      {caseLink}
    </p>;
  }
  const held = !!step.assignee && step.assignee !== person;
  const finish = (data: Record<string, unknown>) => a.run("review", async () => {
    if (!step.assignee) await api.claim(step.id, person);
    return api.complete(step.id, person, data);
  }, () => { setRevising(false); setComments(""); onDone(); });
  return <div className="report-review-step">
    <p className="small row"><StatusBadge status={rv.status} />
      {step.due_at && <Text muted>due {when(step.due_at)}</Text>} {caseLink}</p>
    {!person ? (
      <Text as="p" small>Say who you are in the sidebar (bottom left) to review it.</Text>
    ) : held ? (
      <Text as="p" small>{step.assignee} has this review. <a href={href(`/inbox/task/${step.id}`)}>Open the task</a> to hand it on.</Text>
    ) : revising ? (
      <div className="task-form">
        <Textarea rows={3} placeholder="What to change, e.g. lead with the train C trip" value={comments}
          onChange={(e) => setComments(e.target.value)} aria-label="What to change" />
        <div className="actions">
          <Button variant="primary" disabled={!!a.busy || !comments.trim()}
            onClick={() => finish({ review_decision: "revise", review_comments: comments.trim() })}>Send back</Button>
          <Button onClick={() => setRevising(false)}>Cancel</Button>
        </div>
      </div>
    ) : (
      <div className="actions">
        <Button variant="primary" disabled={!!a.busy} onClick={() => finish({ review_decision: "publish" })}>Publish</Button>
        <Button disabled={!!a.busy} onClick={() => setRevising(true)}>Send back…</Button>
      </div>
    )}
    <ErrorText>{a.error}</ErrorText>
  </div>;
}
