import { Bar, PageHeader, Input , Card as UICard} from "../ui";
import { useState } from "react";
import { api, type RankRow } from "../api";
import { Status, entityHref, useAsync } from "../lib";
import "../styles/downtime.css";

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
    <section className="downtime">
      <div className="row spread">
        <div>
          <PageHeader subtitle="Lost production ranked by cause." help="analyses">Downtime</PageHeader>
        </div>
        <label className="asof">
          Since
          <Input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        </label>
      </div>
      <Status loading={total.loading && !total.data} error={total.error} onRetry={total.reload} />
      {total.data && (
        <p>
          <b>{fmt(total.data.lost_oil_sm3)} Sm³</b> oil lost over <b>{total.data.events.toLocaleString()}</b> events (
          {fmt(total.data.hours)} h)
        </p>
      )}
      <div className="downtime-rankings">
        <Ranking title="By equipment" rows={byTag.data} error={byTag.error} reload={byTag.reload} />
        <Ranking title="By equipment class" rows={byClass.data} error={byClass.error} reload={byClass.reload} named />
        <Ranking title="By reason" rows={byReason.data} error={byReason.error} reload={byReason.reload} />
      </div>
      <Status loading={checks.loading && !checks.data} error={checks.error} onRetry={checks.reload} />
      {checks.data && checks.data.length > 0 && (
        <UICard  as="section">
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
        </UICard>
      )}
    </section>
  );
}

function Ranking({ title, rows, error, reload, named }: {
  title: string; rows?: RankRow[]; error?: string; reload: () => void; named?: boolean;
}) {
  const top = Math.max(1, ...(rows ?? []).map((r) => r.lost_oil_sm3));
  return (
    <UICard  as="section">
      <h2>{title}</h2>
      <Status loading={!rows && !error} error={error} onRetry={reload} empty={rows?.length === 0}>
        Nothing recorded.
      </Status>
      {rows && rows.length > 0 && <p className="muted small">Oil lost (Sm³){rows.some((r) => r.unattributed)
        ? "; the last row is loss whose records name no equipment" : ""}</p>}
      <ul className="coverage ranking">
        {rows?.map((r) => (
          <li key={r.label ?? "none"} className={r.unattributed ? "unattributed" : undefined}>
            <span title={(named || r.unattributed ? r.description : r.label) ?? ""}>
              {r.fuzer_id ? (
                <a href={entityHref(r.fuzer_id)}>{named ? (r.description ?? r.label) : r.label}</a>
              ) : (
                r.label
              )}
            </span>
            <Bar className="bar" value={(100 * r.lost_oil_sm3) / top} />
            <span className="muted small">{fmt(r.lost_oil_sm3)}</span>
          </li>
        ))}
      </ul>
    </UICard>
  );
}
