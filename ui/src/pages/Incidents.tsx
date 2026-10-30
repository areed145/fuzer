import { useState } from "react";
import { api, type Brief, type Incident } from "../api";
import { KindBadge, Status, entityHref, href, useAsync } from "../lib";
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
      <RatingProgress />
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

// Who is rating: the name the Review page remembers.
const rater = () => {
  try {
    return localStorage.getItem("fuzer.reviewer") || "anonymous";
  } catch {
    return "anonymous";
  }
};

/** How far engineers are through rating briefs on past incidents, and the next ones to rate. */
function RatingProgress() {
  const r = useAsync(() => api.briefRatings(), []);
  if (!r.data || r.data.incidents === 0) return null;
  const next = r.data.items.filter((i) => i.ratings === 0).slice(0, 5);
  return (
    <section className="card">
      <h2>
        Rate the troubleshooting briefs{" "}
        <span className="muted small">
          {r.data.rated} of {r.data.incidents} incidents rated
          {r.data.useful_share !== null && ` · ${Math.round(r.data.useful_share * 100)}% of ratings useful`}
        </span>
      </h2>
      <p className="muted small">
        Open a past incident, read its brief as if you were on shift, and say whether it would have helped.
      </p>
      {next.length > 0 && (
        <ul className="plain">
          {next.map((i) => (
            <li key={i.id} className="small">
              <span className="muted mono">{i.start}</span> <a href={entityHref(i.id)}>{i.description}</a>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function IncidentDetail({ id }: { id: string }) {
  const values = useAsync(() => api.values(id), [id]);
  return (
    <>
      <BriefCard id={id} />
      <IncidentPanels id={id} />
      {values.data && values.data.series.length > 0 && (
        <section className="card">
          <h2>
            Process values <span className="muted small">48 h before to 24 h after {values.data.centre.replace("T", " ")};
            shaded: the 12 h leading into each incident · {values.data.provider === "synthetic"
              ? "synthetic values: no PI / ADX connection configured" : `from ${values.data.provider.toUpperCase()}`}</span>
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

/** The troubleshooting brief's own suggestions (checks, what moved, lessons, spares) and a rating. */
function BriefCard({ id }: { id: string }) {
  const brief = useAsync(() => api.brief(id), [id]);
  const b = brief.data;
  return (
    <section className="card">
      <h2>
        Troubleshooting brief <span className="muted small">what Fuzer would tell an engineer or an agent</span>
      </h2>
      <Status loading={brief.loading && !b} error={brief.error} />
      {b && (
        <div className="grid">
          <div>
            <h3>Suggested checks</h3>
            {b.suggested_checks.length === 0 ? (
              <p className="muted small">No approved lessons or reading for this unit yet.</p>
            ) : (
              <ol className="small">
                {b.suggested_checks.map((c, n) => (
                  <li key={n}>
                    {c.step}{" "}
                    <span className="muted">
                      ({c.from.startsWith("fz-") ? <a href={entityHref(c.from)}>source</a> : c.from})
                    </span>
                  </li>
                ))}
              </ol>
            )}
            <Moved pv={b.process_values} />
          </div>
          <div>
            {b.lessons.approved.length > 0 && (
              <>
                <h3>Lessons</h3>
                <ul className="plain small">
                  {b.lessons.approved.map((l) => <li key={l.id}><a href={href("/lessons")}>{l.title}</a></li>)}
                </ul>
              </>
            )}
            {b.spares.length > 0 && (
              <>
                <h3>Spares</h3>
                <ul className="plain small">
                  {b.spares.slice(0, 6).map((p) => (
                    <li key={p.fuzer_id}>
                      <a href={entityHref(p.fuzer_id)}>{p.description || p.label}</a>{" "}
                      <span className={p.stock > 0 ? "muted" : "error"}>{p.stock > 0 ? `${p.stock} in ${p.bin}` : "none in stock"}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
            <Rate target={`brief:${id}`} />
          </div>
        </div>
      )}
    </section>
  );
}

/** Points that moved most in the lead-in, against the hours before it. */
function Moved({ pv }: { pv: Brief["process_values"] }) {
  const moved = pv.series
    .filter((s) => s.lead_in?.change_pct != null && Math.abs(s.lead_in.change_pct) >= 10)
    .sort((a, b) => Math.abs(b.lead_in!.change_pct!) - Math.abs(a.lead_in!.change_pct!));
  return (
    <>
      <h3>
        What moved <span className="muted small">12 h before {pv.centre.replace("T", " ")} vs. before that</span>
      </h3>
      {moved.length === 0 ? (
        <p className="muted small">No point moved more than 10%.{pv.errors && ` ${pv.errors.length} points couldn't be read.`}</p>
      ) : (
        <ul className="plain small">
          {moved.map((s) => (
            <li key={s.id}>
              <a href={entityHref(s.id)}>{s.description || s.point}</a>{" "}
              <span className="mono">
                {s.lead_in!.change_pct! > 0 ? "+" : ""}{s.lead_in!.change_pct}%
              </span>{" "}
              <span className="muted">
                ({s.lead_in!.baseline_mean} → {s.lead_in!.extreme} {s.uom})
              </span>
            </li>
          ))}
        </ul>
      )}
      {pv.note && <p className="muted small">{pv.note}</p>}
    </>
  );
}

function Rate({ target }: { target: string }) {
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const send = (useful: boolean) =>
    api.feedback(target, useful, rater(), comment || undefined).then(() => setSent(useful), (e) => setError(String(e)));
  return (
    <div className="rate">
      <h3>Would this brief have helped?</h3>
      {sent !== null ? (
        <p className="small">Thanks: rated {sent ? "useful" : "not useful"}.</p>
      ) : (
        <>
          <textarea className="lesson-text" rows={2} placeholder="What was missing or wrong? (optional)"
                    value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="row">
            <button className="primary" onClick={() => send(true)}>Useful</button>
            <button onClick={() => send(false)}>Not useful</button>
          </div>
        </>
      )}
      {error && <p className="error small">{error}</p>}
    </div>
  );
}
