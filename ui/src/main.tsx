import { StrictMode, Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { href, notifyChanged, useAsync, useRoute } from "./lib";
import { AskPage } from "./pages/Ask";
import { Entity } from "./pages/Entity";
import { Review } from "./pages/Review";
import { DowntimePage } from "./pages/Downtime";
import { IncidentsPage } from "./pages/Incidents";
import { LessonsPage } from "./pages/Lessons";
import { Search } from "./pages/Search";
import { WorkPage } from "./pages/Work";
import { WorkItemPage } from "./pages/WorkItem";
import { loadPerson } from "./work";
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
    <>
      <nav className="top">
        <a className="brand" href={href("/")}>
          Fuzer
        </a>
        <a className={section === "" || section === "e" ? "on" : ""} href={href("/")}>
          Browse
        </a>
        <a className={section === "ask" ? "on" : ""} href={href("/ask")}>
          Ask
        </a>
        <a className={section === "ontology" ? "on" : ""} href={href("/ontology")}>
          Ontology
        </a>
        <a className={section === "network" ? "on" : ""} href={href("/network")}>
          Network
        </a>
        <a className={section === "map" ? "on" : ""} href={href("/map")}>
          Map
        </a>
        <a className={section === "downtime" ? "on" : ""} href={href("/downtime")}>
          Downtime
        </a>
        <a className={section === "incidents" ? "on" : ""} href={href("/incidents")}>
          Incidents
        </a>
        <a className={section === "lessons" ? "on" : ""} href={href("/lessons")}>
          Lessons
        </a>
        <a className={section === "work" ? "on" : ""} href={href("/work")}>
          Work {work > 0 && <span className="count">{work}</span>}
        </a>
        <a className={section === "review" ? "on" : ""} href={href("/review")}>
          Review {openReviews > 0 && <span className="count">{openReviews}</span>}
        </a>
        <span className="spacer" />
        {rebuilding && <span className="badge updating">Updating…</span>}
        {s?.rebuild.last?.error && <span className="error small">Last update failed</span>}
        {s && (
          <span className="muted small">
            {s.facility} · {Object.values(s.entities).reduce((a, b) => a + b, 0).toLocaleString()} entities ·{" "}
            {s.edges.toLocaleString()} links · {s.records.toLocaleString()} source records
          </span>
        )}
        {stats.error && <span className="error small">API not reachable</span>}
      </nav>
      <main>
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
          ) : section === "review" ? (
            <Review query={query} stats={s} />
          ) : (
            <Search query={query} stats={s} />
          )}
        </Suspense>
      </main>
    </>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
