import { useState } from "react";
import {
  api,
  type BoardCard,
  type Stats,
  type Upset,
  type WorkRow,
} from "../api";
import { Status, entityHref, href, useAsync } from "../lib";
import { usePerson } from "../work";
import { Button, ButtonLink, PageHeader, Input , Card as UICard} from "../ui";
import { usePermission } from "../Auth";

type HeadlineFact = { text: string; cites: string[] };

export function HomePage({ query, stats, statsLoading, statsError }: {
  query: URLSearchParams;
  stats?: Stats;
  statsLoading: boolean;
  statsError?: string;
}) {
  const configure = usePermission("configure"), ai = usePermission("ai");
  return (
    <section className="home">
      <PageHeader subtitle="Explore connected facility data, understand what needs attention, and follow work through to completion.">
        Home
      </PageHeader>
      {stats?.pack === "okan" && stats.records > 0 && (
        <UICard title="New to Fuzer? Investigate an Okan issue">
          <p>Follow train B's March 2024 issue from its asset to source evidence and a finding.
            Fictional, historical data. Read-only, with no setup or AI key needed.</p>
          <ButtonLink variant="primary" href={href("/investigate/okan")}>Start guided investigation</ButtonLink>
        </UICard>
      )}
      <HomeSearch query={query} />
      {stats && stats.records === 0 && (
        <UICard title="Connect data before exploring">
          <p>No source records have been loaded. Add a source, map its fields and load it to build the facility register.</p>
          {configure ? <a href={href("/sources", { view: "add" })}>Open source setup</a>
            : <p>Ask an administrator to load the demonstration records.</p>}
        </UICard>
      )}
      <UICard className="home-guide" aria-labelledby="home-guide-title">
        <h2 id="home-guide-title">Where would you like to start?</h2>
        <div className="home-guide-links">
          {configure && <a href={href("/sources")}><strong>Source readiness</strong><span>Connect and test sources, monitor freshness and recover failed loads.</span></a>}
          {configure && <a href={href("/settings")}><strong>Application settings</strong><span>Review shared defaults, AI limits and restart requirements.</span></a>}
          <a href={href("/browse")}><strong>Explore the facility</strong><span>Find equipment, wells, records and their connections.</span></a>
          <a href={href("/board")}><strong>See priorities</strong><span>Review operational issues and the work that matters most.</span></a>
          {ai && <a href={href("/ask")}><strong>Ask a question</strong><span>Investigate with the assistant when a provider is configured by the owner.</span></a>}
          <a href={href("/review")}><strong>Review the data</strong><span>Check uncertain links and improve the register.</span></a>
        </div>
      </UICard>
      <div className="home-grid">
        <TodayCard />
        <NeedsYouCard stats={stats} statsLoading={statsLoading} statsError={statsError} />
        <PrioritiesCard />
        <IncidentsCard />
        <HealthCard />
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
        placeholder="Search tags, wells, PI points, serials, descriptions…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <Button variant="primary" type="submit">Search</Button>
    </form>
  );
}

function TodayCard() {
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
        <h2>Today</h2>
        {latest.data && (
          <a className="badge" href={href("/reports", { day: latest.data.day.day })}>
            {latest.data.day.day} · {latest.data.report.checks.ok ? "checks passed" : "needs review"}
          </a>
        )}
      </div>
      <Status loading={latest.loading && !latest.data} error={latest.error} empty={latest.data === null}>
        No daily report yet.
      </Status>
      {latest.data && (
        <>
          {headlines.length > 0 ? (
            <ul className="home-list home-headline">
              {headlines.map((fact, i) => <li key={i}>{fact.text}</li>)}
            </ul>
          ) : <p className="muted small">This report has no headline facts.</p>}
          {production.length > 0 && (
            <p className="muted small">
              {production.filter((fact) => fact.text.includes("7-day average")).map((fact) => fact.text).join(" · ")}
            </p>
          )}
          <a href={href("/reports", { day: latest.data.day.day })}>Read the daily report</a>
        </>
      )}
    </UICard>
  );
}

function NeedsYouCard({ stats, statsLoading, statsError }: {
  stats?: Stats;
  statsLoading: boolean;
  statsError?: string;
}) {
  const [person] = usePerson();
  const inbox = useAsync(() => person ? api.inbox(person) : Promise.resolve(null), [person]);
  const rows = inbox.data ? [...inbox.data.mine, ...inbox.data.queue].slice(0, 4) : [];
  const reviews = stats ? Object.entries(stats.open_reviews).filter(([, count]) => count > 0) : [];
  const reviewTotal = reviews.reduce((sum, [, count]) => sum + count, 0);
  return (
    <UICard className="home-card" as="section">
      <div className="row spread">
        <h2>Needs you</h2>
        <a href={href("/inbox")}>Open inbox</a>
      </div>
      <Status loading={inbox.loading && !inbox.data} error={inbox.error} />
      {!person && !inbox.loading && <p className="muted small">Set your name in the sidebar to see your work.</p>}
      {inbox.data && (
        <>
          <p className="muted small">
            {inbox.data.counts.mine.total + inbox.data.counts.queue.total} items · {inbox.data.counts.mine.total} assigned · {inbox.data.counts.queue.total} in your queues
          </p>
          {rows.length ? (
            <ul className="home-list">
              {rows.map((row) => <WorkItem key={`${row.task_id ?? "item"}-${row.item_id}`} row={row} />)}
            </ul>
          ) : <p className="muted small">Nothing waiting for you.</p>}
        </>
      )}
      <div className="home-subsection">
        <div className="row spread">
          <strong>Open reviews</strong>
          <a href={href("/review")}>{reviewTotal} to review</a>
        </div>
        <Status loading={statsLoading && !stats} error={statsError} />
        {stats && (reviews.length ? (
          <div className="row small">
            {reviews.map(([kind, count]) => (
              <a key={kind} className="badge" href={href("/review", { kind })}>{kind.replace("_", " ")} · {count}</a>
            ))}
          </div>
        ) : <p className="muted small">No open reviews.</p>)}
      </div>
    </UICard>
  );
}

function WorkItem({ row }: { row: WorkRow }) {
  const to = row.task_id ? `/inbox/task/${row.task_id}` : `/cases/${row.item_id}`;
  return (
    <li>
      <a href={href(to)}>{row.title}</a>
      <span className="muted small">{row.type.replace(/_/g, " ")} · {row.assignee ?? row.role ?? "open"}</span>
    </li>
  );
}

function PrioritiesCard() {
  const board = useAsync(() => api.board({}, 1), []);
  const cards = board.data?.cards.slice(0, 5) ?? [];
  return (
    <UICard className="home-card" as="section">
      <div className="row spread">
        <h2>Top priorities</h2>
        <a href={href("/board")}>Work board</a>
      </div>
      <Status loading={board.loading && !board.data} error={board.error} empty={board.data?.total === 0}>
        Nothing on the work board.
      </Status>
      {cards.length > 0 && (
        <ol className="home-list home-priorities">
          {cards.map((card) => <Priority key={card.key} card={card} />)}
        </ol>
      )}
    </UICard>
  );
}

function Priority({ card }: { card: BoardCard }) {
  const value = card.effective_usd == null ? "not valued" : `$${Math.round(card.effective_usd).toLocaleString()}`;
  return (
    <li>
      <span className="mono muted">{card.rank}.</span>
      <div>
        <a href={card.item_id ? href(`/cases/${card.item_id}`) : href("/board")}>{card.title}</a>
        <span className="muted small">{card.kind.replace(/_/g, " ")} · {value}</span>
      </div>
    </li>
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
      <Status loading={incidents.loading && !incidents.data} error={incidents.error} empty={incidents.data?.items.length === 0}>
        No incidents recorded.
      </Status>
      {recentIncidents.length > 0 && (
        <ul className="home-list">
          {recentIncidents.map((incident) => (
            <li key={incident.id}>
              <a href={entityHref(incident.id)}>{incident.description}</a>
              <span className="muted small">{incident.start} · {Math.round(incident.lost_oil_sm3).toLocaleString()} Sm³ lost</span>
            </li>
          ))}
        </ul>
      )}
      <div className="home-subsection">
        <div className="row spread">
          <h3>Open upsets</h3>
          <a href={href("/board")}>Work board</a>
        </div>
        <Status loading={upsets.loading && !upsets.data} error={upsets.error}
          empty={upsets.data !== undefined && openUpsets.length === 0}>
          No open upsets.
        </Status>
        {openUpsets.length > 0 && (
          <ul className="home-list">
            {openUpsets.map((upset) => <UpsetItem key={upset.id} upset={upset} />)}
          </ul>
        )}
      </div>
    </UICard>
  );
}

function UpsetItem({ upset }: { upset: Upset }) {
  return (
    <li>
      <a href={href("/board")}>{upset.first_key || upset.first_point || `Upset ${upset.id}`}</a>
      <span className="muted small">{upset.start} · {upset.first_unit ?? "Unknown unit"} · {upset.detections} detections</span>
    </li>
  );
}

function HealthCard() {
  const configure = usePermission("configure");
  return (
    <UICard className="home-card" as="section">
      <h2>Health</h2>
      {configure ? <><SourceHealthPanel /><JobsHealthPanel /></>
        : <p className="muted small">Administrators monitor source loads and scheduled jobs. Historic demo timestamps are not live plant outages.</p>}
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
      <Status loading={sources.loading && !sources.data} error={sources.error} empty={sources.data?.length === 0}>
        No source health data.
      </Status>
      {sources.data && rows.length > 0 && (
        <>
          <p className="muted small">{rows.length} datasets · {problems.length} with freshness issues · {checks.length} failed checks</p>
          {rows.length > 0 && (
            <ul className="home-list">
              {rows.filter((source) => source.problems.length || source.failing_checks.length).slice(0, 3).map((source) => (
                <li key={source.dataset}>
                  <a href={href("/sources")}>{source.dataset}</a>
                  <span className="muted small">{source.status} · {source.age_hours == null ? "never loaded" : `${Math.round(source.age_hours)} h old`}</span>
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
      <Status loading={jobs.loading && !jobs.data} error={jobs.error} />
      {jobs.data && (
        <>
          <p className="muted small">{jobs.data.health.summary}</p>
          {failedRuns.length > 0 || failedChecks.length > 0 ? (
            <ul className="home-list">
              {failedRuns.map((run) => (
                <li key={run.run_id}>
                  <a href={href(`/jobs/run/${run.run_id}`)}>{run.job}</a>
                  <span className="muted small">failed · {run.start ?? "time unavailable"}</span>
                </li>
              ))}
              {failedChecks.slice(0, 3).map((check) => (
                <li key={`${check.asset}-${check.name}`} className="error small">{check.asset}: {check.name}</li>
              ))}
            </ul>
          ) : <p className="muted small">No failed runs or checks.</p>}
        </>
      )}
    </div>
  );
}
