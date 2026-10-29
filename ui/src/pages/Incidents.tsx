import { api, type Incident } from "../api";
import { KindBadge, Status, entityHref, useAsync } from "../lib";

export function IncidentsPage() {
  const list = useAsync(() => api.incidents(), []);
  return (
    <section>
      <h1>Incidents</h1>
      <p className="muted">
        Logged events, downtime records and SAP notifications on the same piece of plant within a few days of each
        other, grouped into one upset. Newest first.
      </p>
      <Status loading={list.loading && !list.data} error={list.error} empty={list.data?.total === 0}>
        No incidents yet.
      </Status>
      {list.data && list.data.total > 0 && (
        <>
          <p className="muted small">
            {list.data.total.toLocaleString()} incidents{list.data.total > list.data.items.length && `, showing the latest ${list.data.items.length}`}
          </p>
          <IncidentList items={list.data.items} />
        </>
      )}
    </section>
  );
}

export function IncidentList({ items }: { items: Incident[] }) {
  return (
    <ul className="timeline card">
      {items.map((i) => (
        <li key={i.id}>
          <span className="when">{i.start}</span>
          <div className="what">
            <a href={entityHref(i.id)}>{i.description}</a>
            <div className="row small muted">
              {i.downtime_records > 0 && <span className="badge event-type">downtime</span>}
              {i.events > 0 && <span>{i.events} events</span>}
              {i.notifications > 0 && <span>{i.notifications} notifications</span>}
              {i.lost_oil_sm3 > 0 && <span>{Math.round(i.lost_oil_sm3).toLocaleString()} Sm³ lost</span>}
              {i.failure_modes?.map((m) => <span key={m} className="badge">{m}</span>)}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}

export function IncidentDetail({ id }: { id: string }) {
  const timeline = useAsync(() => api.incidentTimeline(id), [id]);
  const similar = useAsync(() => api.similarIncidents(id), [id]);
  return (
    <div className="grid">
      <section className="card">
        <h2>Timeline</h2>
        <Status loading={timeline.loading && !timeline.data} error={timeline.error} />
        <ul className="timeline">
          {timeline.data?.map((t) => (
            <li key={t.id}>
              <span className="when">{t.at?.replace("T", " ").slice(0, 16)}</span>
              <div className="what">
                <div className="row">
                  <KindBadge kind={t.kind} />
                  {t.detail && <span className="badge event-type">{t.detail.replace("_", " ")}</span>}
                </div>
                <a href={entityHref(t.id)}>{t.text}</a>
              </div>
            </li>
          ))}
        </ul>
      </section>
      <section className="card">
        <h2>Similar past incidents</h2>
        <p className="muted small">Same unit or equipment class, ranked by shared failure modes and event types.</p>
        <Status loading={similar.loading && !similar.data} empty={similar.data?.length === 0}>
          None found.
        </Status>
        <ul className="plain">
          {similar.data?.map((s) => (
            <li key={s.id} className="small">
              <span className="muted mono">{s.start}</span> <a href={entityHref(s.id)}>{s.description}</a>
              {s.same_unit && <span className="badge">same unit</span>}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
