import { api, type Incident } from "../api";
import { KindBadge, Status, entityHref, useAsync } from "../lib";
import { TrendGrid } from "../Trend";

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
  const values = useAsync(() => api.values(id), [id]);
  return (
    <>
      <IncidentPanels id={id} />
      {values.data && values.data.series.length > 0 && (
        <section className="card">
          <h2>
            Process values <span className="muted small">48 h before to 24 h after the start; shaded: the 12 h leading
            into each incident{values.data.provider === "synthetic" && " · synthetic values until PI / ADX are connected"}</span>
          </h2>
          <TrendGrid series={values.data.series} upsets={values.data.upsets} />
        </section>
      )}
    </>
  );
}

function IncidentPanels({ id }: { id: string }) {
  const timeline = useAsync(() => api.incidentTimeline(id), [id]);
  const similar = useAsync(() => api.similarIncidents(id), [id]);
  const reading = useAsync(() => api.incidentReading(id), [id]);
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
        {reading.data && reading.data.length > 0 && (
          <>
            <h2>Suggested reading</h2>
            <p className="muted small">Manual and guide sections for this unit that cover these failure modes.</p>
            <ul className="plain">
              {reading.data.map((r) => (
                <li key={r.id} className="small">
                  <a href={entityHref(r.id)}>{r.document_label} · {r.heading}</a>
                  {r.page && <span className="muted"> p. {r.page}</span>}{" "}
                  {r.failure_modes.map((m) => <span key={m} className="badge">{m}</span>)}
                </li>
              ))}
            </ul>
          </>
        )}
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
