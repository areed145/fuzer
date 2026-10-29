import { StrictMode, Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { api } from "./api";
import { href, notifyChanged, useAsync, useRoute } from "./lib";
import { Entity } from "./pages/Entity";
import { Review } from "./pages/Review";
import { DowntimePage } from "./pages/Downtime";
import { IncidentsPage } from "./pages/Incidents";
import { LessonsPage } from "./pages/Lessons";
import { Search } from "./pages/Search";
import "./styles.css";

// Graph views pull in Cytoscape; load them only when opened.
const Ontology = lazy(() => import("./pages/Ontology").then((m) => ({ default: m.Ontology })));
const Network = lazy(() => import("./pages/Network").then((m) => ({ default: m.Network })));

function App() {
  const { path, query } = useRoute();
  const stats = useAsync(() => api.stats(), []);
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

  return (
    <>
      <nav className="top">
        <a className="brand" href={href("/")}>
          Fuzer
        </a>
        <a className={section === "" || section === "e" ? "on" : ""} href={href("/")}>
          Browse
        </a>
        <a className={section === "ontology" ? "on" : ""} href={href("/ontology")}>
          Ontology
        </a>
        <a className={section === "network" ? "on" : ""} href={href("/network")}>
          Network
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
          ) : section === "ontology" ? (
            <Ontology />
          ) : section === "network" ? (
            <Network />
          ) : section === "downtime" ? (
            <DowntimePage />
          ) : section === "incidents" ? (
            <IncidentsPage />
          ) : section === "lessons" ? (
            <LessonsPage query={query} />
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
