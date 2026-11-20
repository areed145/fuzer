// The daily report (#98, #242), and the regulatory reports (P7.6, RegulatoryReports.tsx): one page for each production day's narrative, where its review stands, and the
// review itself (publish, or send back with what to change). The `daily_report` case behind it is linked for
// its history; the day's production figures are the `production_day` record (EC DAY001).
import { units } from "../wording";
import { money, when, whenDay } from "../format";
import { useState } from "react";
import { api, type DailyReport, type DailyStatus } from "../api";
import { Markdown } from "../Markdown";
import { readableCitations } from "../entityRefs";
import { Status, entityHref, href, useAsync, useAction } from "../lib";
import { usePerson } from "../work";
import { AgentIdle } from "./workflows/shared";
import { Button, PageHeader, Card as UICard, Badge as UIBadge, Textarea, ErrorText, Text, type Tone, List, Heading } from "../ui";
import { ObligationsDue, RegulatoryList, RegulatoryReportView } from "./RegulatoryReports";

const STATUS_TONE: Record<string, Tone> = { published: "ok", "sent back": "warn", drafting: "warn", "needs a fix": "warn" };
const STATUS_HELP: Record<string, string> = {
  drafting: "the reporter agent is drafting it",
  "awaiting review": "drafted; waiting for the ops lead to publish it or send it back",
  "sent back": "the reviewer asked for changes; the reporter agent is redrafting it",
  published: "reviewed and published",
  "needs a fix": "a step failed and needs someone to fix it",
  "no review": "no review case (drafted before reviews, or closed by hand)",
};

const weekday = (day: string) => new Date(`${day}T00:00Z`).toLocaleDateString(undefined, { weekday: "short", timeZone: "UTC" });
const dayMonth = (day: string) => whenDay(day);

export function StatusBadge({ status }: { status: DailyStatus }) {
  return <UIBadge tone={STATUS_TONE[status]} title={STATUS_HELP[status]}>{status}</UIBadge>;
}

export function ReportsPage({ query }: { query: URLSearchParams }) {
  const days = useAsync(() => api.dailyReports(), []);
  const regulatory = useAsync(() => api.regulatoryReports(), []);
  const report = query.get("report") ?? undefined;
  const [template, period] = report ? report.split(":") : [];
  const day = report ? undefined : query.get("day") ?? days.data?.[0]?.day;
  const waiting = days.data?.filter((d) => d.status === "awaiting review").length ?? 0;
  return (
    <section>
      <PageHeader>Daily reports</PageHeader>
      <Status of={days} empty={days.data?.length === 0}>
        No reports yet: `fuzer report` closes a day.
      </Status>
      {waiting > 0 && <Text as="p" small>{waiting} report{waiting > 1 ? "s" : ""} awaiting review.</Text>}
      {/* A strip of days, newest first, each with its status: the wrapping line of versions read as noise (#1078). */}
      {days.data && days.data.length > 0 && (
        <nav className="report-days" aria-label="Report days">
          {days.data.map((d) => (
            <a key={d.day} className={`report-day${d.day === day ? " on" : ""}`} aria-current={d.day === day ? "page" : undefined}
              href={href("/reports", { day: d.day })} title={`${d.status}${d.ok ? "" : "; has lines to check"}`}>
              <span className="report-day-week">{weekday(d.day)}</span>
              <strong>{dayMonth(d.day)}</strong>
              <StatusBadge status={d.status} />
              {!d.ok && <span className="small warn-text">lines to check</span>}
            </a>
          ))}
        </nav>
      )}
      {day && <Report key={day} day={day} onChange={days.reload} />}
      <Heading>Regulatory reports</Heading>
      <Status of={regulatory} />
      {regulatory.data && <RegulatoryList data={regulatory.data} selected={report} onChange={regulatory.reload} />}
      <ObligationsDue />
      {template && period && (
        <RegulatoryReportView key={report} template={template} period={period} onChange={regulatory.reload} />
      )}
    </section>
  );
}

function Report({ day, onChange }: { day: string; onChange: () => void }) {
  const [version, setVersion] = useState<number | undefined>();
  const r = useAsync(() => api.dailyReport(day, version), [day, version]);
  const d = r.data;
  return (
    <>
      <Status of={r} />
      {d && (
        <>
          <Review d={d} onDone={() => { r.reload(); onChange(); }} />
          <div className="grid">
            <UICard as="section">
              {/* The day's headline leads; how it was drafted and its versions follow it (#1078). */}
              <Markdown text={units(readableCitations(d.markdown))} />
              <div className="row small muted report-versions">
                <span>Version</span>
                {d.versions.map((v) => (
                  <Button key={v} className={v === d.version ? "chip on" : "chip"} onClick={() => setVersion(v)}>{v}</Button>
                ))}
                <span>drafted by {d.drafted_by}</span>
                {d.usage?.cost_usd !== undefined && <span>{money(d.usage.cost_usd)}</span>}
                {d.seconds !== null && <span>{d.seconds} s</span>}
              </div>
            </UICard>
            <UICard as="section" title="Checks">
              {d.checks.ok ? <Text as="p" small>Every line cites its records and every number is in them.</Text> : (
                <List small>
                  {d.checks.uncited_lines.map((l) => <li key={l}>No citation: {l}</li>)}
                  {d.checks.unknown_citations.map((c) => <li key={c}>Cites a record not in the day's facts: {c}</li>)}
                  {d.checks.unknown_numbers.map((n) => <li key={n}>A number not in the records: {n}</li>)}
                </List>
              )}
              {d.production_day && (
                <Text as="p" small>The day's production figures: <a href={entityHref(d.production_day)}>EC DAY001 record</a>.</Text>
              )}
              {d.changes && (
                <>
                  <Heading>Changed since version {d.version - 1}</Heading>
                  {Object.entries(d.changes).map(([section, c]) => (
                    <div key={section} className="small">
                      <strong>{section}</strong>
                      {c.added.map((a) => <div key={a} className="ok">+ {a}</div>)}
                      {c.removed.map((a) => <div key={a} className="muted">− {a}</div>)}
                    </div>
                  ))}
                </>
              )}
            </UICard>
          </div>
        </>
      )}
    </>
  );
}

/** Where the review stands, and the review itself when the report is waiting for it. */
function Review({ d, onDone }: { d: DailyReport; onDone: () => void }) {
  const [person] = usePerson();
  const [revising, setRevising] = useState(false);
  const [comments, setComments] = useState("");
  const a = useAction();
  const rv = d.review;
  if (!rv) return <Text as="p" small muted><StatusBadge status="no review" /> {STATUS_HELP["no review"]}</Text>;
  const step = rv.step;
  const caseLink = <a href={href(`/cases/${rv.item_id}`)}>case history</a>;
  if (rv.status !== "awaiting review" || !step) {
    return (
      <p className="small row">
        <StatusBadge status={rv.status} /> <Text muted>{STATUS_HELP[rv.status] ?? rv.status}
          {rv.closed_at && ` ${when(rv.closed_at)}`}</Text>
        {rv.comments && rv.status === "sent back" && <span>Asked for: “{rv.comments}”</span>}
        {step?.error && <ErrorText inline>{step.error}</ErrorText>}
        <AgentIdle step={step} />
        {caseLink}
      </p>
    );
  }
  const held = !!step.assignee && step.assignee !== person;
  const finish = (data: Record<string, unknown>) => a.run("review", async () => {
    if (!step.assignee) await api.claim(step.id, person);
    return api.complete(step.id, person, data);
  }, () => { setRevising(false); setComments(""); onDone(); });
  return (
    <UICard as="section" title="Review this report"
      actions={<span className="small row"><StatusBadge status={rv.status} />
          {step.due_at && <Text muted>due {when(step.due_at)}</Text>} {caseLink}</span>}>
      {!person ? (
        <Text as="p" small>Say who you are in the sidebar (bottom left) to review it.</Text>
      ) : held ? (
        <Text as="p" small>{step.assignee} has this review. <a href={href(`/inbox/task/${step.id}`)}>Open the task</a> to hand it on.</Text>
      ) : (
        <>
          {revising ? (
            <div className="task-form">
              <Textarea rows={3} placeholder="What to change, e.g. lead with the train C trip" value={comments}
                onChange={(e) => setComments(e.target.value)} />
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
        </>
      )}
      <ErrorText>{a.error}</ErrorText>
    </UICard>
  );
}
