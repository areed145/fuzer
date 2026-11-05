import { StrictMode, Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { applyOntology } from "./kinds";
import { href, notifyChanged, useAsync, useRoute } from "./lib";
import { AskPage } from "./pages/Ask";
import { BoardPage } from "./pages/Board";
import { ReportsPage } from "./pages/Reports";
import { SettingsPage } from "./pages/Settings";
import { SourcesPage } from "./pages/sources";
import { StudiesPage } from "./pages/Studies";
import { Entity } from "./pages/Entity";
import { Review } from "./pages/Review";
import { DowntimePage } from "./pages/Downtime";
import { IncidentsPage } from "./pages/Incidents";
import { LessonsPage } from "./pages/Lessons";
import { Search } from "./pages/Search";
import { InboxPage } from "./pages/workflows/Inbox";
import { TaskPage } from "./pages/workflows/Task";
import { CasePage, CasesPage } from "./pages/workflows/Cases";
import { TeamsPage } from "./pages/workflows/Teams";
import { Identity } from "./Identity";
import { loadPerson } from "./work";
import { Breadcrumbs, Sidebar } from "./nav";
import { JobsPage, JobRunPage } from "./pages/Jobs";
import { HomePage } from "./pages/Home";
import type { JobsHealth } from "./api";
import "./styles.css";

// Graph views pull in Cytoscape; load them only when opened.
const Ontology = lazy(() => import("./pages/Ontology").then((m) => ({ default: m.Ontology })));
const Network = lazy(() => import("./pages/Network").then((m) => ({ default: m.Network })));
// The map pulls in Leaflet; load it only when opened.
const MapPage = lazy(() => import("./pages/MapPage").then((m) => ({ default: m.MapPage })));
// The modeler pages pull in bpmn-js's modeler and the form-js editor; load them only when opened.
const ProcessesPage = lazy(() => import("./pages/Processes").then((m) => ({ default: m.ProcessesPage })));
const ProcessPage = lazy(() => import("./pages/Processes").then((m) => ({ default: m.ProcessPage })));
const FormPage = lazy(() => import("./pages/Processes").then((m) => ({ default: m.FormPage })));

function App() {
  const { path, query } = useRoute();
  const stats = useAsync(() => api.stats(), []);
  const inbox = useAsync(() => (loadPerson() ? api.inbox(loadPerson()) : Promise.resolve(undefined)), []);
  const reload = stats.reload;
  const jobsHealth = useAsync(() => api.jobsHealth(), []);
  // Kind colours and link views come from the ontology (#241); re-render once they're in.
  const declared = useAsync(() => api.ontologyDeclared().then((o) => { applyOntology(o); return o; }), []);
  void declared.data;
  const reloadHealth = jobsHealth.reload;
  useEffect(() => {
    const t = setInterval(reloadHealth, 60_000);
    return () => clearInterval(t);
  }, [reloadHealth]);

  const s = stats.data;
  const rebuilding = !!s?.rebuild.running;

  // While a background rebuild runs, poll; when it finishes, tell every view to refresh.
  const [wasRebuilding, setWasRebuilding] = useState(false);
  useEffect(() => {
    if (rebuilding) {
      const t = setTimeout(reload, 1500);
      return () => clearTimeout(t);
    }
  }, [rebuilding, s, reload]);
  useEffect(() => {
    if (wasRebuilding && !rebuilding) notifyChanged();
    setWasRebuilding(rebuilding);
  }, [rebuilding, wasRebuilding]);
  const openReviews = s ? Object.values(s.open_reviews).reduce((a, b) => a + b, 0) : 0;
  const section = path[0] ?? "";
  const navSection = !section && query.has("q") ? "browse" : section;
  const work = inbox.data ? inbox.data.counts.mine.total + inbox.data.counts.queue.total : 0;
  // The old Work pages' links (#/work, #/work/item/3, #/work/processes/upset, #/work/forms/x) still land right.
  useEffect(() => {
    if (section !== "work") return;
    const task = query.get("task");
    const to = path[1] === "item" && path[2] ? `/cases/${path[2]}`
      : path[1] === "processes" ? `/processes${path[2] ? `/${path[2]}` : ""}`
      : path[1] === "forms" && path[2] ? `/processes/forms/${path[2]}`
      : task ? `/inbox/task/${task}` : "/inbox";
    location.replace(href(to, Object.fromEntries([...query.entries()].filter(([k]) => k !== "task" && k !== "tab"))));
  }, [section, path, query]);

  return (
    <div className="app">
      <Sidebar
        section={navSection}
        badges={{ work, review: openReviews }}
        status={
          <>
            {s && (
              <div className="facts">
                <strong>{s.facility}</strong>
                <span>{Object.values(s.entities).reduce((a, b) => a + b, 0).toLocaleString()} entities</span>
                <span>{s.edges.toLocaleString()} links</span>
                <span>{s.records.toLocaleString()} source records</span>
              </div>
            )}
            {rebuilding && <span className="badge updating">Updating…</span>}
            <HealthIndicator health={jobsHealth.data} rebuildError={s?.rebuild.last?.error} />
            <Identity />
            {stats.error && <span className="error small">API not reachable</span>}
          </>
        }
      />
      <main>
        <Breadcrumbs path={path} />
        <Suspense fallback={<div className="status">Loading…</div>}>
          {section === "" && query.has("q") ? (
            <Search query={query} stats={s} />
          ) : section === "" ? (
            <HomePage query={query} stats={s} statsLoading={stats.loading} statsError={stats.error} />
          ) : section === "browse" ? (
            <Search query={query} stats={s} />
          ) : section === "e" && path[1] ? (
            <Entity id={path[1]} tab={query.get("tab") === "graph" ? "graph" : "details"} />
          ) : section === "ask" ? (
            <AskPage />
          ) : section === "ontology" ? (
            <Ontology query={query} />
          ) : section === "network" ? (
            <Network />
          ) : section === "map" ? (
            <MapPage />
          ) : section === "downtime" ? (
            <DowntimePage />
          ) : section === "incidents" ? (
            <IncidentsPage />
          ) : section === "lessons" ? (
            <LessonsPage query={query} />
          ) : section === "inbox" && path[1] === "task" && path[2] ? (
            <TaskPage key={path[2]} id={Number(path[2])} />
          ) : section === "inbox" || section === "work" ? (
            <InboxPage />
          ) : section === "cases" && path[1] ? (
            <CasePage key={path[1]} id={Number(path[1])} />
          ) : section === "cases" ? (
            <CasesPage query={query} />
          ) : section === "processes" && path[1] === "forms" && path[2] ? (
            <FormPage key={path[2]} formKey={path[2]} query={query} />
          ) : section === "processes" && path[1] ? (
            <ProcessPage key={path[1]} processKey={path[1]} query={query} />
          ) : section === "processes" ? (
            <ProcessesPage />
          ) : section === "teams" ? (
            <TeamsPage query={query} />
          ) : section === "reports" ? (
            <ReportsPage query={query} />
          ) : section === "studies" ? (
            <StudiesPage query={query} />
          ) : section === "jobs" && path[1] === "run" && path[2] ? (
            <JobRunPage key={path[2]} id={path[2]} />
          ) : section === "jobs" ? (
            <JobsPage />
          ) : section === "sources" ? (
            <SourcesPage query={query} />
          ) : section === "settings" ? (
            <SettingsPage query={query} />
          ) : section === "board" ? (
            <BoardPage query={query} />
          ) : section === "review" ? (
            <Review query={query} stats={s} />
          ) : (
            <Search query={query} stats={s} />
          )}
        </Suspense>
      </main>
    </div>
  );
}

/** Green / amber / red from the Dagster jobs and the last register update, linking to the Jobs page. */
function HealthIndicator({ health, rebuildError }: { health?: JobsHealth; rebuildError?: string }) {
  if (!health && !rebuildError) return null;
  const level = rebuildError ? "red" : health?.level ?? "unknown";
  const parts = [rebuildError && "last update failed", health?.available && health.summary].filter(Boolean);
  const text = parts.join("; ") || "Jobs: no history yet";
  return (
    <a className={`health health-${level}`} href={href("/jobs")} title={rebuildError ?? undefined}>
      <i className="dot" aria-hidden="true" />
      <span>{text.charAt(0).toUpperCase() + text.slice(1)}</span>
    </a>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
