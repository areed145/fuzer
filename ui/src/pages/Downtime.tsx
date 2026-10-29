import { useState } from "react";
import { api, type RankRow } from "../api";
import { Status, entityHref, useAsync } from "../lib";

const fmt = (n: number) => Math.round(n).toLocaleString();

export function DowntimePage() {
  const [since, setSince] = useState("");
  const s = since || undefined;
  const total = useAsync(() => api.downtime(undefined, s), [since]);
  const byTag = useAsync(() => api.downtimeRanking("tag", s), [since]);
  const byClass = useAsync(() => api.downtimeRanking("class", s), [since]);
  const byReason = useAsync(() => api.downtimeRanking("reason", s), [since]);
  const checks = useAsync(() => api.reasonChecks(), []);

  return (
    <section>
      <div className="row spread">
        <div>
          <h1>Downtime</h1>
          <p className="muted">
            Lost production from Energy Components downtime records, ranked by what caused it. Causes come from the
            equipment named in each record's comment.
          </p>
        </div>
        <label className="asof">
          Since
          <input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        </label>
      </div>
      <Status loading={total.loading && !total.data} error={total.error} />
      {total.data && (
        <p>
          <b>{fmt(total.data.lost_oil_sm3)} Sm³</b> oil lost over <b>{total.data.events.toLocaleString()}</b> events (
          {fmt(total.data.hours)} h)
        </p>
      )}
      <div className="grid three">
        <Ranking title="By equipment" rows={byTag.data} />
        <Ranking title="By equipment class" rows={byClass.data} named />
        <Ranking title="By reason" rows={byReason.data} />
      </div>
      {checks.data && checks.data.length > 0 && (
        <section className="card">
          <h2>
            Reason code doesn't match the comment <span className="muted small">{checks.data.length}</span>
          </h2>
          <ul className="timeline">
            {checks.data.map((c) => (
              <li key={c.fuzer_id}>
                <span className="when">{c.start?.slice(0, 10)}</span>
                <div className="what">
                  <a href={entityHref(c.fuzer_id)}>{c.label}</a>
                  <span className="muted small">{c.issue}</span>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

function Ranking({ title, rows, named }: { title: string; rows?: RankRow[]; named?: boolean }) {
  const top = Math.max(1, ...(rows ?? []).map((r) => r.lost_oil_sm3));
  return (
    <section className="card">
      <h2>{title}</h2>
      <Status loading={!rows} empty={rows?.length === 0}>
        Nothing recorded.
      </Status>
      <ul className="coverage ranking">
        {rows?.map((r) => (
          <li key={r.label ?? "none"}>
            <span title={(named ? r.label : r.description) ?? ""}>
              {r.fuzer_id ? (
                <a href={entityHref(r.fuzer_id)}>{named ? (r.description ?? r.label) : r.label}</a>
              ) : (
                r.label
              )}
            </span>
            <span className="bar">
              <span style={{ width: `${(100 * r.lost_oil_sm3) / top}%` }} />
            </span>
            <span className="muted small">{fmt(r.lost_oil_sm3)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
