import { AttentionCard } from "./HomeAttention";
import { alarm, plural, say } from "../wording";
import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";
import { api, type Stats, type Upset } from "../api";
import { caseHref } from "./workflows/shared";
import { iocCounts, workHref } from "./AllWork";
import { Status, entityHref, href, timestamp, useAsync } from "../lib";
import { usePerson } from "../work";
import { Button, PageHeader, Input, Card as UICard, Empty } from "../ui";
import { usePermission } from "../Auth";
import { LiveFigure } from "../analytics/LiveFigure";
import { YourShift } from "./HomeShift";

type HeadlineFact = { text: string; cites: string[] };
const HOME_FIGURES = [
  { kind: "production-efficiency", title: "Production efficiency" },
  { kind: "flow-capacity", title: "System capacity and bottleneck" },
];

/** Whose Home this is (#1074): it leads with what that seat acts on first. A field operator gets their work, a
 * dispatcher (ops lead) the dispatch counts, a manager or engineer production; anyone else the map. Presentation
 * only: every card stays reachable, and none of this changes what a person may see or do. */
export type Seat = "dispatcher" | "operator" | "manager" | "default";
export function seatOf(roles: string[]): Seat {
  if (roles.includes("ops_lead")) return "dispatcher";
  if (roles.includes("operator")) return "operator";
  if (roles.some((r) => r === "production_engineer" || r === "engineer")) return "manager";
  return "default";
}
type Block = "lead" | "attention" | "charts" | "grid";
const ORDER: Record<Seat, Block[]> = {
  dispatcher: ["lead", "attention", "grid", "charts"],
  operator: ["lead", "attention", "grid", "charts"],
  manager: ["charts", "attention", "grid"],
  default: ["attention", "charts", "grid"],
};

export function HomePage({ query, stats }: {
  query: URLSearchParams;
  stats?: Stats;
  statsLoading: boolean;
  statsError?: string;
}) {
  const configure = usePermission("configure");
  const [person] = usePerson();
  const inbox = useAsync(() => person ? api.inbox(person) : Promise.resolve(null), [person]);
  const roles = inbox.data?.roles ?? [];
  const seat = seatOf(roles);
  const noRecords = stats?.records === 0;
  // Shown while the counts load too: adding the cards once /stats answered pushed everything below them down (#1069).
  const loaded = !noRecords;
  // The seat sets the order, so the blocks wait for the inbox that names it rather than reorder under the reader.
  const seated = !person || inbox.data !== undefined || !!inbox.error;
  const steward = roles.includes("data_steward") || configure;
  // The seat's own card leads; the rest of the work cards sit in the grid with reports, incidents and health.
  // The cards under it wait until it has drawn: it lands in one piece but grows from a loading line to several
  // hundred px, and shoved the attention card below the fold (#1069, CLS 0.27 live).
  const [leadReady, setLeadReady] = useState(false);
  const ready = useCallback(() => setLeadReady(true), []);
  const lead = seat === "operator" ? <YourShift key="shift" onReady={ready} />
    : seat === "dispatcher" ? <AllWorkCard key="all" onReady={ready} /> : null;
  const shown = lead && !leadReady ? ORDER[seat].slice(0, ORDER[seat].indexOf("lead") + 1) : ORDER[seat];
  const blocks: Record<Block, ReactNode> = {
    lead: lead && <div className="home-lead">{lead}</div>,
    attention: loaded && <AttentionCard />,
    charts: loaded && <div className="home-charts">
      {HOME_FIGURES.map(({ kind, title }) => <LiveFigure key={kind} kind={kind} hideEmpty
        frame={(body) => <UICard title={title} className="home-card">{body}</UICard>} />)}
    </div>,
    // With no records loaded, reports, priorities and incidents can only be empty: keep work and health.
    grid: <div className="home-grid">
      {seat !== "operator" && <YourShift />}
      {seat !== "dispatcher" && roles.includes("ops_lead") && <AllWorkCard />}
      {steward && <MasterDataCard stats={stats} />}
      {!noRecords && <LatestReportCard />}
      {!noRecords && <IncidentsCard />}
      {configure && <HealthCard />}
    </div>,
  };
  return (
    <section className="home" data-seat={seat}>
      <PageHeader>Home</PageHeader>
      <HomeSearch query={query} />
      {noRecords && (
        <UICard title="Connect data before exploring">
          <p>No source records have been loaded. Add a source, map its fields and load it to build the facility register.</p>
          {configure ? <a href={href("/sources", { view: "add" })}>Open source setup</a>
            : <p>Ask an administrator to load the demonstration records.</p>}
        </UICard>
      )}
      {seated ? shown.map((b) => <Fragment key={b}>{blocks[b]}</Fragment>) : <Status loading />}
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
              {headlines.map((fact, i) => <li key={i}>{say(fact.text)}</li>)}
            </ul>
          ) : <Empty className="small">This report has no headline facts.</Empty>}
          {production.length > 0 && (
            <p className="small">
              {production.filter((fact) => fact.text.includes("7-day average")).map((fact) => say(fact.text)).join(" · ")}
            </p>
          )}
          <a href={href("/reports", { day: latest.data.day.day })}>Read the daily report</a>
        </>
      )}
    </UICard>
  );
}

/* Home summarizes and links (#508, #556): counts for the person's roles, each linking to the view that shows the
 * same number, and the next few titles. The rows themselves live on My work, All work and Master data. */

/** One count linking to its view. `children` is "one|many" when the noun changes with the number. */
function Count({ n, to, children, late }: { n: number; to: string; children: string; late?: boolean }) {
  const [one, many = one] = children.split("|");
  return <li><a href={to} className={late && n > 0 ? "late" : undefined}><strong>{n}</strong> {n === 1 ? one : many}</a></li>;
}

function AllWorkCard({ onReady }: { onReady?: () => void } = {}) {
  const open = useAsync(() => api.exceptions({ state: "open" }), []);
  const people = useAsync(() => api.workPeople(), []);
  const board = useAsync(() => api.board({}, 1), []);
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const c = open.data ? iocCounts(open.data, operators) : null;
  const top = board.data?.cards.slice(0, 3) ?? [];
  // The counts and the highest value together: drawn as each answered, the list growing under the counts pushed the
  // rest of Home down (#1069). It leads a dispatcher's Home.
  const ready = [open, people, board].every((r) => r.data !== undefined || !!r.error);
  useEffect(() => { if (ready) onReady?.(); }, [ready, onReady]);
  return (
    <UICard className="home-card" as="section">
      <div className="row spread"><h2>All work</h2><a href={workHref("dispatch")}>Open All work</a></div>
      <Status loading={!ready} error={open.error} onRetry={open.reload} />
      {ready && c && <ul className="home-counts">
        <Count n={c.dispatch} to={workHref("dispatch")} late>to dispatch</Count>
        <Count n={c.field} to={workHref("field")}>with the field</Count>
        <Count n={c.back} to={workHref("back")}>back from the field</Count>
        <Count n={c.open} to={workHref("open")}>open</Count>
      </ul>}
      {ready && top.length > 0 && <>
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
        {reviews !== null && <Count n={reviews} to={href("/masterdata", { tab: "questions" })}>record to review|records to review</Count>}
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
              <a href={entityHref(incident.id)}>{say(incident.description)}</a>
              <span className="muted small">Started {incident.start}{incident.lost_oil_sm3 > 0
                && ` · ${Math.round(incident.lost_oil_sm3).toLocaleString()} Sm³ lost oil`}</span>
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
        {upset.first_key ? `${alarm(upset.first_key)}${upset.first_unit ? ` on ${upset.first_unit}` : ""}`
          : upset.first_point || `Upset ${upset.id}`}</a>
      <span className="muted small">From {timestamp(upset.start)} · {upset.first_key ? upset.first_point ?? "Unknown point"
        : upset.first_unit ?? "Unknown unit"} · {plural(upset.detections, "detection")}</span>
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

/** The scheduler's health in a line. The summary the sidebar already reads, not the full Jobs overview: that
 * builds every job's run history, took half a minute on a cold server and held the page's other requests. */
function JobsHealthPanel() {
  const jobs = useAsync(() => api.jobsHealth(), []);
  return (
    <div className="home-subsection">
      <div className="row spread">
        <strong>Jobs</strong>
        <a href={href("/jobs")}>Job health</a>
      </div>
      <Status loading={jobs.loading && !jobs.data} error={jobs.error} onRetry={jobs.reload} />
      {jobs.data && (!jobs.data.available ? <Empty className="small">Scheduler history unavailable.</Empty>
        : jobs.data.failed_runs_24h > 0 || jobs.data.failing_checks > 0
          ? <p className="small error">{jobs.data.summary}</p>
          : <Empty className="small">No failures in recent runs or checks.</Empty>)}
    </div>
  );
}
