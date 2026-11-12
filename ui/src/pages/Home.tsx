import { useState } from "react";
import { api, type FieldJob, type Stats, type Upset, type WorkInbox } from "../api";
import { myWorkCounts } from "./workflows/Inbox";
import { caseHref, taskHref } from "./workflows/shared";
import { iocCounts, workHref } from "./AllWork";
import { Status, entityHref, href, timestamp, useAsync } from "../lib";
import { usePerson } from "../work";
import { Button, PageHeader, Input, Card as UICard, Empty } from "../ui";
import { usePermission } from "../Auth";
import { LiveFigure } from "../analytics/LiveFigure";

type HeadlineFact = { text: string; cites: string[] };
const HOME_FIGURES = [
  { kind: "production-efficiency", title: "Production efficiency" },
  { kind: "flow-capacity", title: "System capacity and bottleneck" },
];

export function HomePage({ query, stats }: {
  query: URLSearchParams;
  stats?: Stats;
  statsLoading: boolean;
  statsError?: string;
}) {
  const configure = usePermission("configure");
  const noRecords = stats?.records === 0;
  return (
    <section className="home">
      <PageHeader>Home</PageHeader>
      <HomeSearch query={query} />
      {noRecords && (
        <UICard title="Connect data before exploring">
          <p>No source records have been loaded. Add a source, map its fields and load it to build the facility register.</p>
          {configure ? <a href={href("/sources", { view: "add" })}>Open source setup</a>
            : <p>Ask an administrator to load the demonstration records.</p>}
        </UICard>
      )}
      {stats && stats.records > 0 && <div className="home-charts">
        {HOME_FIGURES.map(({ kind, title }) => <UICard key={kind} title={title} className="home-card"><LiveFigure kind={kind} /></UICard>)}
      </div>}
      {/* With no records loaded, reports, priorities and incidents can only be empty: keep work and health. */}
      <div className="home-grid">
        {!noRecords && <LatestReportCard />}
        <ShiftCards stats={stats} />
        {!noRecords && <IncidentsCard />}
        {configure && <HealthCard />}
      </div>
    </section>
  );
}

function HomeSearch({ query }: { query: URLSearchParams }) {
  const [value, setValue] = useState(query.get("q") ?? "");
  const search = (q: string) => window.location.replace(href("/browse", { q }));
  return (
    <form className="home-search" role="search" onSubmit={(e) => { e.preventDefault(); search(value); }}>
      <Input
        aria-label="Search the facility"
        placeholder="Search assets, tags and records"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <Button variant="primary" type="submit">Search</Button>
    </form>
  );
}

function LatestReportCard() {
  const latest = useAsync(async () => {
    const days = await api.dailyReports();
    if (!days.length) return null;
    return { day: days[0], report: await api.dailyReport(days[0].day) };
  }, []);
  const facts = latest.data?.report.facts?.sections;
  const headlines = (facts?.Headline ?? []) as HeadlineFact[];
  const production = (facts?.["Production and deferment"] ?? []) as HeadlineFact[];
  return (
    <UICard className="home-card home-today" as="section">
      <div className="row spread">
        <h2>Latest daily report</h2>
        {latest.data && (
          <a className="badge" href={href("/reports", { day: latest.data.day.day })}>
            {latest.data.day.day} · {latest.data.report.checks.ok ? "checks passed" : "needs review"}
          </a>
        )}
      </div>
      <Status loading={latest.loading && !latest.data} error={latest.error} onRetry={latest.reload} empty={latest.data === null}>
        No daily report recorded. <a href={href("/reports")}>Open daily reports</a>.
      </Status>
      {latest.data && (
        <>
          <p className="small">Report date {latest.data.day.day}</p>
          {headlines.length > 0 ? (
            <ul className="home-list home-headline">
              {headlines.map((fact, i) => <li key={i}>{fact.text}</li>)}
            </ul>
          ) : <Empty className="small">This report has no headline facts.</Empty>}
          {production.length > 0 && (
            <p className="small">
              {production.filter((fact) => fact.text.includes("7-day average")).map((fact) => fact.text).join(" · ")}
            </p>
          )}
          <a href={href("/reports", { day: latest.data.day.day })}>Read the daily report</a>
        </>
      )}
    </UICard>
  );
}

/** Home summarizes and links (#508, #556): counts for the person's roles, each linking to the view that shows the
 * same number, and the next few titles. The rows themselves live on My work, All work and Master data. */
function ShiftCards({ stats }: { stats?: Stats }) {
  const [person] = usePerson();
  const configure = usePermission("configure");
  const inbox = useAsync(() => person ? api.inbox(person) : Promise.resolve(null), [person]);
  const field = useAsync(() => person ? api.fieldJobs(person) : Promise.resolve(null), [person]);
  const roles = inbox.data?.roles ?? [];
  return <>
    <MyWorkCard person={person} inbox={inbox.data} loading={inbox.loading} error={inbox.error} retry={inbox.reload}
      jobs={field.data?.jobs ?? []} />
    {roles.includes("ops_lead") && <AllWorkCard />}
    {(roles.includes("data_steward") || configure) && <MasterDataCard stats={stats} />}
  </>;
}

/** One count linking to its view. `children` is "one|many" when the noun changes with the number. */
function Count({ n, to, children, late }: { n: number; to: string; children: string; late?: boolean }) {
  const [one, many = one] = children.split("|");
  return <li><a href={to} className={late && n > 0 ? "late" : undefined}><strong>{n}</strong> {n === 1 ? one : many}</a></li>;
}

function MyWorkCard({ person, inbox, loading, error, retry, jobs }: {
  person: string | null; inbox?: WorkInbox | null; loading: boolean; error?: string; retry: () => void; jobs: FieldJob[];
}) {
  const c = inbox ? myWorkCounts(inbox, jobs) : null;
  const my = href("/inbox");
  return (
    <UICard className="home-card" as="section">
      <div className="row spread"><h2>My work</h2><a href={my}>Open My work</a></div>
      {!person && <Empty className="small">Choose your name under “You are” in the menu.</Empty>}
      <Status loading={loading && !inbox} error={error} onRetry={retry} />
      {c && <>
        <ul className="home-counts">
          {c.escalated > 0 && <Count n={c.escalated} to={my} late>escalated to you</Count>}
          {c.sent > 0 && <Count n={c.sent} to={my}>sent to you</Count>}
          <Count n={c.mine} to={my}>assigned to you</Count>
          <Count n={c.queue} to={my}>unclaimed in your roles</Count>
          {c.overdue > 0 && <Count n={c.overdue} to={my} late>overdue</Count>}
        </ul>
        {c.top.length > 0 ? <ol className="home-list">{c.top.map((r) => <li key={`${r.task_id ?? "i"}-${r.item_id}`}>
          <a href={r.task_id ? taskHref(r.task_id) : caseHref(r.item_id)}>{r.title}</a></li>)}</ol>
          : <Empty className="small">Nothing waiting for you.</Empty>}
      </>}
    </UICard>
  );
}

function AllWorkCard() {
  const open = useAsync(() => api.exceptions({ state: "open" }), []);
  const people = useAsync(() => api.workPeople(), []);
  const board = useAsync(() => api.board({}, 1), []);
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const c = open.data ? iocCounts(open.data, operators) : null;
  const top = board.data?.cards.slice(0, 3) ?? [];
  return (
    <UICard className="home-card" as="section">
      <div className="row spread"><h2>All work</h2><a href={workHref("dispatch")}>Open All work</a></div>
      <Status loading={open.loading && !open.data} error={open.error} onRetry={open.reload} />
      {c && <ul className="home-counts">
        <Count n={c.dispatch} to={workHref("dispatch")} late>to dispatch</Count>
        <Count n={c.field} to={workHref("field")}>with the field</Count>
        <Count n={c.back} to={workHref("back")}>back from the field</Count>
        <Count n={c.open} to={workHref("open")}>open</Count>
      </ul>}
      {top.length > 0 && <>
        <h3>Highest value</h3>
        <ol className="home-list">{top.map((card) => <li key={card.key}>
          <a href={card.item_id ? caseHref(card.item_id) : workHref("ranked")}>{card.title}</a>
          <span className="muted small">{card.effective_usd == null ? "not valued" : `$${Math.round(card.effective_usd).toLocaleString()}`}</span>
        </li>)}</ol>
      </>}
    </UICard>
  );
}

function MasterDataCard({ stats }: { stats?: Stats }) {
  const unmatched = useAsync(() => api.unmatchedRefs(), []);
  const reviews = stats ? Object.values(stats.open_reviews).reduce((a, b) => a + b, 0) : null;
  return (
    <UICard className="home-card" as="section">
      <div className="row spread"><h2>Master data</h2><a href={href("/masterdata")}>Open Master data</a></div>
      <Status loading={unmatched.loading && !unmatched.data} error={unmatched.error} onRetry={unmatched.reload} />
      <ul className="home-counts">
        {unmatched.data && <Count n={unmatched.data.length} to={href("/masterdata")} late>ref on open work matched nothing|refs on open work matched nothing</Count>}
        {reviews !== null && <Count n={reviews} to={href("/review")}>record to review|records to review</Count>}
      </ul>
    </UICard>
  );
}

function IncidentsCard() {
  const incidents = useAsync(() => api.incidents(), []);
  const upsets = useAsync(() => api.upsets("detected", "process", 20), []);
  const openUpsets = upsets.data?.filter((upset) => !upset.end).slice(0, 3) ?? [];
  const recentIncidents = incidents.data?.items.slice(0, 3) ?? [];
  return (
    <UICard className="home-card" as="section">
      <div className="row spread">
        <h2>Incidents and upsets</h2>
        <a href={href("/incidents")}>All incidents</a>
      </div>
      <h3>Latest incidents</h3>
      <Status loading={incidents.loading && !incidents.data} error={incidents.error} onRetry={incidents.reload} empty={incidents.data?.items.length === 0}>
        No incidents recorded.
      </Status>
      {recentIncidents.length > 0 && (
        <ul className="home-list">
          {recentIncidents.map((incident) => (
            <li key={incident.id}>
              <a href={entityHref(incident.id)}>{incident.description}</a>
              <span className="muted small">Started {incident.start} · {Math.round(incident.lost_oil_sm3).toLocaleString()} Sm³ lost oil</span>
            </li>
          ))}
        </ul>
      )}
      <div className="home-subsection">
        <div className="row spread">
          <h3>Detections without a recorded end</h3>
          <a href={href("/exceptions", { list: "ranked", view: "kind" })}>All work by type</a>
        </div>
        <Status loading={upsets.loading && !upsets.data} error={upsets.error} onRetry={upsets.reload}
          empty={upsets.data !== undefined && openUpsets.length === 0}>
          No upsets without a recorded end.
        </Status>
        {openUpsets.length > 0 && (
          <><ul className="home-list">
            {openUpsets.map((upset) => <UpsetItem key={upset.id} upset={upset} />)}
          </ul></>
        )}
      </div>
    </UICard>
  );
}

function UpsetItem({ upset }: { upset: Upset }) {
  return (
    <li>
      <a href={upset.work_item_id ? href(`/cases/${upset.work_item_id}`) : href("/exceptions", { list: "ranked", view: "kind" })}>
        {upset.first_key || upset.first_point || `Upset ${upset.id}`}</a>
      <span className="muted small">Detected from {timestamp(upset.start)} · {upset.first_unit ?? "Unknown unit"} · {upset.detections} detections</span>
    </li>
  );
}

function HealthCard() {
  return (
    <UICard className="home-card" as="section">
      <h2>Data and scheduler health</h2>
      <SourceHealthPanel /><JobsHealthPanel />
    </UICard>
  );
}

function SourceHealthPanel() {
  const sources = useAsync(() => api.sourceHealth(), []);
  const rows = sources.data ?? [];
  const checks = rows.flatMap((source) => source.failing_checks.map((check) => ({ source, check })));
  const problems = rows.filter((source) => source.problems.length > 0);
  return (
    <div className="home-subsection">
      <div className="row spread">
        <strong>Sources</strong>
        <a href={href("/sources")}>Source health</a>
      </div>
      <Status loading={sources.loading && !sources.data} error={sources.error} onRetry={sources.reload} empty={sources.data?.length === 0}>
        No source health data.
      </Status>
      {sources.data && rows.length > 0 && (
        <>
          <p className="small">{rows.length} datasets · {problems.length} with reported problems · {checks.length} failed {checks.length === 1 ? "check" : "checks"}.</p>
          {rows.length > 0 && (
            <ul className="home-list">
              {rows.filter((source) => source.problems.length || source.failing_checks.length).slice(0, 3).map((source) => (
                <li key={source.dataset}>
                  <a href={href("/sources", { view: "dataset", name: source.dataset })}>{source.dataset}</a>
                  <span className="muted small">{source.state} · {source.last_loaded
                    ? `Last ingested: ${timestamp(source.last_loaded)}` : "No recorded ingestion time"}</span>
                </li>
              ))}
              {checks.slice(0, 3).map(({ source, check }, i) => (
                <li key={`${source.dataset}-${check.kind}-${i}`} className="error small">
                  {source.dataset}: {check.kind}{check.column ? ` · ${check.column}` : ""}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

function JobsHealthPanel() {
  const jobs = useAsync(() => api.jobs(), []);
  const failedRuns = jobs.data?.jobs.flatMap((job) => job.recent_runs
    .filter((run) => run.status === "FAILURE")
    .map((run) => ({ ...run, job: job.name }))).slice(0, 3) ?? [];
  const failedChecks = jobs.data?.checks.filter((check) => check.level === "fail") ?? [];
  return (
    <div className="home-subsection">
      <div className="row spread">
        <strong>Jobs</strong>
        <a href={href("/jobs")}>Job health</a>
      </div>
      <Status loading={jobs.loading && !jobs.data} error={jobs.error} onRetry={jobs.reload} />
      {jobs.data && (
        <>
          {jobs.data.available && <p className="small">{jobs.data.health.summary}</p>}
          {!jobs.data.available ? <Empty className="small">Scheduler history unavailable.</Empty>
          : failedRuns.length > 0 || failedChecks.length > 0 ? (
            <ul className="home-list">
              {failedRuns.map((run) => (
                <li key={run.run_id}>
                  <a href={href(`/jobs/run/${run.run_id}`)}>{run.job}</a>
                  <span className="muted small">Run failed · started {timestamp(run.start)}</span>
                </li>
              ))}
              {failedChecks.slice(0, 3).map((check) => (
                <li key={`${check.asset}-${check.name}`} className="error small">{check.asset}: {check.name}</li>
              ))}
            </ul>
          ) : <Empty className="small">No failures in recent runs or checks.</Empty>}
        </>
      )}
    </div>
  );
}
