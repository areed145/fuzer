import "./styles.css";
import "./styles/shell.css";
import "./styles/shared.css";
import "./styles/workflows.css";
import { Badge as UIBadge } from "./ui";
import { StrictMode, Suspense, lazy, useEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import { href, useRoute } from "./lib";
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
import { FieldPage } from "./pages/Field";
import { ExceptionsPage } from "./pages/Exceptions";
import { TaskPage } from "./pages/workflows/Task";
import { CasePage, CasesPage } from "./pages/workflows/Cases";
import { TeamsPage } from "./pages/workflows/Teams";
import { Identity } from "./Identity";
import { AreaNavigation, Breadcrumbs, Sidebar } from "./nav";
import { JobsPage, JobRunPage } from "./pages/Jobs";
import { HomePage } from "./pages/Home";
import { InvestigationPage } from "./pages/Investigation";
import { DemoPage } from "./pages/Demo";
import type { JobsHealth } from "./api";
import { useAppData } from "./useAppData";
import { AppErrorBoundary } from "./AppErrorBoundary";
import { Button, EmptyState, PageHeader } from "./ui";
import { Status } from "./lib";
import "./theme";
import { AuthGate } from "./Auth";
import { UsersPage } from "./pages/Users";

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
  const routeKey = path.join("/");
  const previousRoute = useRef(routeKey);
  const { stats, demo, inbox, jobsHealth, declared, rebuilding } = useAppData();
  void declared.data;

  const s = stats.data;
  const openReviews = s ? Object.values(s.open_reviews).reduce((a, b) => a + b, 0) : 0;
  const section = path[0] ?? "";
  const navSection = !section && query.has("q") ? "browse" : section;
  const work = inbox.data ? inbox.data.counts.mine.total + inbox.data.counts.queue.total : 0;
  // The old Work pages' links (#/work, #/work/item/3, #/work/processes/upset, #/work/forms/x) still land right.
  useEffect(() => {
    if (section === "access") {
      location.replace(href("/users", Object.fromEntries(query.entries())));
      return;
    }
    // Its charts moved to Home; old links land there.
    if (section === "analytics") {
      location.replace(href("/"));
      return;
    }
    if (section === "settings" && query.get("view") === "people") {
      location.replace(href("/users", { view: "workflow" }));
      return;
    }
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
      <a className="skip-link" href="#main-content" onClick={(e) => { e.preventDefault(); document.getElementById("main-content")?.focus(); }}>Skip to content</a>
      <Sidebar
        demoEnabled={demo.data?.enabled === true}
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
            {rebuilding && <UIBadge className="badge updating">Updating…</UIBadge>}
            <HealthIndicator health={jobsHealth.data} rebuildError={s?.rebuild.last?.error} />
            <Identity />
            {stats.error && <span className="error small">API not reachable</span>}
            {demo.error && section !== "demo" && section !== "investigate" &&
              <Status error={`Demo availability: ${demo.error}`} onRetry={demo.reload} />}
          </>
        }
      />
      <main id="main-content" tabIndex={-1}>
        <Breadcrumbs path={path} />
        <AreaNavigation section={navSection} demoEnabled={demo.data?.enabled === true} />
        <Suspense fallback={<div className="status" role="status" aria-live="polite">Loading…</div>}>
          {section === "" && query.has("q") ? (
            <Search query={query} stats={s} />
          ) : section === "" ? (
            <HomePage query={query} stats={s} statsLoading={stats.loading} statsError={stats.error} />
          ) : section === "demo" || (section === "investigate" && path[1] === "okan") ? (
            !demo.data || demo.error ? <section><PageHeader>Demo</PageHeader>
              <Status loading={demo.loading && !demo.data} error={demo.error} onRetry={demo.reload} />
              <a href={href("/")}>Return home</a></section>
              : !demo.data.enabled ? <section><PageHeader>Demo</PageHeader>
                <div role="status"><EmptyState title="Demo is disabled">An administrator has hidden onboarding for this application.
                  Your existing tools and permissions are unchanged.</EmptyState></div>
                <Button onClick={demo.reload}>Check again</Button>{" "}<a href={href("/")}>Return home</a></section>
                : section === "investigate" ? <InvestigationPage query={query} />
                  : <DemoPage query={query} stats={s} />
          ) : section === "browse" ? (
            <Search query={query} stats={s} />
          ) : section === "e" && path[1] ? (
            <Entity id={path[1]} tab={query.get("tab") === "graph" ? "graph" : "details"} />
          ) : section === "ask" ? (
            <AskPage query={query} />
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
            <TaskPage key={`${path[2]}:${query.get("demo")}`} id={Number(path[2])} demo={query.get("demo") === "workflow"} />
          ) : section === "inbox" || section === "work" ? (
            <InboxPage />
          ) : section === "field" ? (
            <FieldPage />
          ) : section === "exceptions" ? (
            <ExceptionsPage />
          ) : section === "cases" && path[1] ? (
            <CasePage key={path[1]} id={Number(path[1])} demo={query.get("demo") === "workflow"} />
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
          ) : section === "users" || section === "access" ? (
            <UsersPage query={query} />
          ) : section === "board" ? (
            <BoardPage query={query} />
          ) : section === "review" ? (
            <Review query={query} stats={s} />
          ) : (
            <EmptyState title="Page not found"><a href={href("/")}>Return home</a> or use the navigation to choose a page.</EmptyState>
          )}
          <RouteFocus routeKey={routeKey} previousRoute={previousRoute} />
        </Suspense>
      </main>
    </div>
  );
}

function RouteFocus({ routeKey, previousRoute }: {
  routeKey: string; previousRoute: { current: string };
}) {
  useEffect(() => {
    if (previousRoute.current === routeKey) return;
    previousRoute.current = routeKey;
    const sidebar = document.getElementById("sidebar");
    const menu = document.querySelector(".menu-button");
    if (sidebar?.classList.contains("open") || document.activeElement === menu) return;
    const heading = document.querySelector<HTMLElement>("#main-content h1");
    (heading ?? document.getElementById("main-content"))?.focus({ preventScroll: true });
  }, [routeKey, previousRoute]);
  return null;
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
    <AppErrorBoundary><AuthGate><App /></AuthGate></AppErrorBoundary>
  </StrictMode>,
);
