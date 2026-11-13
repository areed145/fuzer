// The daily report (#98, #242), and the regulatory reports (P7.6, RegulatoryReports.tsx): one page for each production day's narrative, where its review stands, and the
// review itself (publish, or send back with what to change). The `daily_report` case behind it is linked for
// its history; the day's production figures are the `production_day` record (EC DAY001).
import { useState } from "react";
import { api, type DailyReport, type DailyStatus } from "../api";
import { Markdown } from "../Markdown";
import { readableCitations } from "../entityRefs";
import { Status, entityHref, href, useAsync } from "../lib";
import { usePerson, when } from "../work";
import { AgentIdle, useAct } from "./workflows/shared";
import { Button, PageHeader , Card as UICard, Badge as UIBadge} from "../ui";
import { RegulatoryList, RegulatoryReportView } from "./RegulatoryReports";

const STATUS_CLASS: Record<string, string> = {
  published: "ok", "awaiting review": "key", "sent back": "warn", drafting: "warn", "needs a fix": "warn",
};
const STATUS_HELP: Record<string, string> = {
  drafting: "the reporter agent is drafting it",
  "awaiting review": "drafted; waiting for the ops lead to publish it or send it back",
  "sent back": "the reviewer asked for changes; the reporter agent is redrafting it",
  published: "reviewed and published",
  "needs a fix": "a step failed and needs someone to fix it",
  "no review": "no review case (drafted before reviews, or closed by hand)",
};

export function StatusBadge({ status }: { status: DailyStatus }) {
  return <UIBadge className={`badge ${STATUS_CLASS[status] ?? ""}`} title={STATUS_HELP[status]}>{status}</UIBadge>;
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
      <PageHeader subtitle="What happened each production day.">Daily reports</PageHeader>
      <Status loading={days.loading && !days.data} error={days.error} empty={days.data?.length === 0}>
        No reports yet: `fuzer report` closes a day.
      </Status>
      {waiting > 0 && <p className="small">{waiting} report{waiting > 1 ? "s" : ""} awaiting review.</p>}
      {days.data && days.data.length > 0 && (
        <div className="row wrap">
          {days.data.map((d) => (
            <a key={d.day} className={`badge${d.day === day ? " on" : ""} ${STATUS_CLASS[d.status] ?? ""}`}
              href={href("/reports", { day: d.day })}
              title={`${d.status}${d.ok ? "" : "; has lines to check"}`}>
              {d.day}{d.version > 1 ? ` v${d.version}` : ""}{d.ok ? "" : " ⚠"}
              {d.status !== "published" && <span className="muted"> · {d.status}</span>}
            </a>
          ))}
        </div>
      )}
      {day && <Report key={day} day={day} onChange={days.reload} />}
      <h2>Regulatory reports</h2>
      <p className="small muted">Drafted from their records for a period, each figure drilling to them; a figure on
        estimated or missing data is marked for the reviewer.</p>
      <Status loading={regulatory.loading && !regulatory.data} error={regulatory.error} />
      {regulatory.data && <RegulatoryList data={regulatory.data} selected={report} onChange={regulatory.reload} />}
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
      <Status loading={r.loading && !d} error={r.error} />
      {d && (
        <>
          <Review d={d} onDone={() => { r.reload(); onChange(); }} />
          <div className="grid">
            <UICard  as="section">
              <div className="row small muted">
                <span>Version</span>
                {d.versions.map((v) => (
                  <Button key={v} className={v === d.version ? "chip on" : "chip"} onClick={() => setVersion(v)}>{v}</Button>
                ))}
                <span>drafted by {d.drafted_by}</span>
                {d.usage?.cost_usd !== undefined && <span>${d.usage.cost_usd.toFixed(2)}</span>}
                {d.seconds !== null && <span>{d.seconds} s</span>}
              </div>
              <Markdown text={readableCitations(d.markdown)} />
            </UICard>
            <UICard  as="section">
              <h2>Checks</h2>
              {d.checks.ok ? <p className="small">Every line cites its records and every number is in them.</p> : (
                <ul className="small">
                  {d.checks.uncited_lines.map((l) => <li key={l}>No citation: {l}</li>)}
                  {d.checks.unknown_citations.map((c) => <li key={c}>Cites a record not in the day's facts: {c}</li>)}
                  {d.checks.unknown_numbers.map((n) => <li key={n}>A number not in the records: {n}</li>)}
                </ul>
              )}
              {d.production_day && (
                <p className="small">The day's production figures: <a href={entityHref(d.production_day)}>EC DAY001 record</a>.</p>
              )}
              {d.changes && (
                <>
                  <h2>Changed since version {d.version - 1}</h2>
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
  const a = useAct();
  const rv = d.review;
  if (!rv) return <p className="small muted"><StatusBadge status="no review" /> {STATUS_HELP["no review"]}</p>;
  const step = rv.step;
  const caseLink = <a href={href(`/cases/${rv.item_id}`)}>case history</a>;
  if (rv.status !== "awaiting review" || !step) {
    return (
      <p className="small row">
        <StatusBadge status={rv.status} /> <span className="muted">{STATUS_HELP[rv.status] ?? rv.status}
          {rv.closed_at && ` ${when(rv.closed_at)}`}</span>
        {rv.comments && rv.status === "sent back" && <span>Asked for: “{rv.comments}”</span>}
        {step?.error && <span className="error">{step.error}</span>}
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
    <UICard  as="section">
      <div className="row spread">
        <h2>Review this report</h2>
        <span className="small row"><StatusBadge status={rv.status} />
          {step.due_at && <span className="muted">due {when(step.due_at)}</span>} {caseLink}</span>
      </div>
      {!person ? (
        <p className="small">Say who you are in the sidebar (bottom left) to review it.</p>
      ) : held ? (
        <p className="small">{step.assignee} has this review. <a href={href(`/inbox/task/${step.id}`)}>Open the task</a> to hand it on.</p>
      ) : (
        <>
          <p className="small muted">Read it below, check the checks, then publish it or send it back to the reporter agent
            with what to change{!step.assignee && step.role ? ` (it is in the ${step.role.replace("role:", "")} queue; reviewing claims it)` : ""}.</p>
          {revising ? (
            <div className="task-form">
              <textarea rows={3} placeholder="What to change, e.g. lead with the train C trip" value={comments}
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
      {a.error && <p className="error small">{a.error}</p>}
    </UICard>
  );
}
