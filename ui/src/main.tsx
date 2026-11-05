import { StrictMode, Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { href, notifyChanged, useAsync, useRoute } from "./lib";
import { AskPage } from "./pages/Ask";
import { BoardPage } from "./pages/Board";
import { ReportsPage } from "./pages/Reports";
import { SettingsPage } from "./pages/Settings";
import { ArchitecturePage } from "./pages/Architecture";
import { SourcesPage } from "./pages/sources";
import { StudiesPage } from "./pages/Studies";
import { Entity } from "./pages/Entity";
import { Review } from "./pages/Review";
import { DowntimePage } from "./pages/Downtime";
import { IncidentsPage } from "./pages/Incidents";
import { LessonsPage } from "./pages/Lessons";
import { Search } from "./pages/Search";
import { WorkPage } from "./pages/Work";
import { WorkItemPage } from "./pages/WorkItem";
import { loadPerson } from "./work";
import { Breadcrumbs, Sidebar } from "./nav";
import { JobsPage, JobRunPage } from "./pages/Jobs";
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
  const work = inbox.data ? inbox.data.counts.mine.total + inbox.data.counts.queue.total : 0;

  return (
    <div className="app">
      <Sidebar
        section={section}
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
            {stats.error && <span className="error small">API not reachable</span>}
          </>
        }
      />
      <main>
        <Breadcrumbs path={path} />
        <Suspense fallback={<div className="status">Loading…</div>}>
          {section === "e" && path[1] ? (
            <Entity id={path[1]} tab={query.get("tab") === "graph" ? "graph" : "details"} />
          ) : section === "ask" ? (
            <AskPage />
          ) : section === "ontology" ? (
            <Ontology />
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
          ) : section === "work" && path[1] === "processes" && path[2] ? (
            <ProcessPage key={path[2]} processKey={path[2]} query={query} />
          ) : section === "work" && path[1] === "processes" ? (
            <ProcessesPage />
          ) : section === "work" && path[1] === "forms" && path[2] ? (
            <FormPage key={path[2]} formKey={path[2]} query={query} />
          ) : section === "work" && path[1] === "item" && path[2] ? (
            <WorkItemPage id={Number(path[2])} />
          ) : section === "work" ? (
            <WorkPage query={query} />
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
          ) : section === "architecture" ? (
            <ArchitecturePage />
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
