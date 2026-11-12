/** Incidents, studies and lessons about a case's subjects or an asset (P4.5c), so whoever works the case or looks up
 * the asset reaches what is already known about it. Nothing shows when there is none. */
import { api, type RelatedWork } from "./api";
import { Card } from "./ui";
import { Status, href, useAsync } from "./lib";
import { IncidentList } from "./pages/Incidents";
import { when } from "./work";

export function Related({ about, incidents = true }: { about: { case?: number; subject?: string }; incidents?: boolean }) {
  const r = useAsync(() => api.related(about), [about.case, about.subject]);
  if (r.error) return <Status error={r.error} onRetry={r.reload} />;
  const d: RelatedWork = { subjects: [], incidents: [], studies: [], lessons: [], ...r.data };
  if ((incidents ? d.incidents.length : 0) + d.studies.length + d.lessons.length === 0) return null;
  const title = incidents ? "Incidents, studies and lessons" : "Studies and lessons";
  return (
    <Card as="section" className="related-work" title={title}>
      {incidents && d.incidents.length > 0 && <>
        <h3 className="small">Incidents <a className="muted" href={href("/incidents")}>all incidents</a></h3>
        <IncidentList items={d.incidents.slice(0, 5)} />
      </>}
      {d.studies.length > 0 && <>
        <h3 className="small">Studies</h3>
        <ul className="plain small">{d.studies.map((s) => <li key={s.id}>
          <a href={href("/studies", { id: String(s.id) })}>{s.summary || s.kind.replace(/_/g, " ")}</a>{" "}
          <span className="muted">{s.kind.replace(/_/g, " ")} · {s.status} · {when(s.created_at)}</span>
        </li>)}</ul>
      </>}
      {d.lessons.length > 0 && <>
        <h3 className="small">Lessons</h3>
        <ul className="plain small">{d.lessons.map((l) => <li key={l.id}>
          <a href={href("/lessons", { status: l.status })}>{l.title}</a>{" "}
          <span className="muted">{l.status === "approved" ? "approved" : "suggested, not yet reviewed"} · {l.failure_mode}
            {" "}· for its {l.scope_kind.replace(/_/g, " ")}</span>
        </li>)}</ul>
      </>}
    </Card>
  );
}
