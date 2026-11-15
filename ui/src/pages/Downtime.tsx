import { Bar, Button, PageHeader, Input, Table, Card as UICard } from "../ui";
import { useState } from "react";
import { api, type DowntimeMonth, type RankRow } from "../api";
import { Status, entityHref, useAsync } from "../lib";
import "../styles/downtime.css";

const fmt = (n: number) => Math.round(n).toLocaleString();
const SCOPES = [["unplanned", "Unplanned"], ["planned", "Planned"], ["all", "All"]] as const;
type Scope = typeof SCOPES[number][0];
const monthName = (m: string) => new Date(`${m}-01T00:00Z`).toLocaleDateString(undefined, { month: "short", year: "numeric", timeZone: "UTC" });

/** Lost production, planned apart from unplanned (#1078): a planned shutdown is most of the loss, and ranked with it the
 * failures that need action were slivers. The rankings open on the unplanned loss, on its own scale; the month bars and
 * the event list say when and what. */
export function DowntimePage() {
  const [since, setSince] = useState("");
  const [scope, setScope] = useState<Scope>("unplanned");
  const s = since || undefined;
  const planned = scope === "all" ? undefined : scope === "planned";
  const total = useAsync(() => api.downtime(undefined, s, 100), [since]);
  const months = useAsync(() => api.downtimeMonthly(s), [since]);
  const byTag = useAsync(() => api.downtimeRanking("tag", s, planned), [since, scope]);
  const byClass = useAsync(() => api.downtimeRanking("class", s, planned), [since, scope]);
  const byReason = useAsync(() => api.downtimeRanking("reason", s, planned), [since, scope]);
  const checks = useAsync(() => api.reasonChecks(), []);
  const t = total.data;
  const plannedSm3 = t?.planned?.lost_oil_sm3 ?? 0;
  const plannedEvents = t?.planned?.events ?? 0;
  const events = (t?.recent ?? []).filter((e) => scope === "all" || (e.reason_category === "planned") === (scope === "planned"));

  return (
    <section className="downtime">
      {/* The date sits with Help at the header's right, as on other pages (#1078). */}
      <PageHeader subtitle="Lost production ranked by cause." help="analyses" actions={
        <label className="asof">
          Since
          <Input type="date" value={since} onChange={(e) => setSince(e.target.value)} />
        </label>}>Downtime</PageHeader>
      <Status loading={total.loading && !t} error={total.error} onRetry={total.reload} />
      {t && (
        <div className="downtime-totals">
          <p><b>{fmt(t.lost_oil_sm3)} Sm³</b> oil lost over <b>{t.events.toLocaleString()}</b> events ({fmt(t.hours)} h)</p>
          {t.planned && (
            <p className="small">
              <span className="dot planned" /> Planned <b>{fmt(plannedSm3)} Sm³</b> in {plannedEvents} {plannedEvents === 1 ? "event" : "events"}
              {" · "}<span className="dot unplanned" /> Unplanned <b>{fmt(t.lost_oil_sm3 - plannedSm3)} Sm³</b> in {t.events - plannedEvents}{" "}
              {t.events - plannedEvents === 1 ? "event" : "events"}
            </p>
          )}
        </div>
      )}
      <Months rows={months.data} error={months.error} reload={months.reload} />
      <div className="chips segmented downtime-scope" role="group" aria-label="Which downtime the rankings count">
        {SCOPES.map(([key, label]) => (
          <Button key={key} className="chip" aria-pressed={scope === key} onClick={() => setScope(key)}>{label}</Button>
        ))}
      </div>
      <div className="downtime-rankings">
        <Ranking title="By equipment" rows={byTag.data} error={byTag.error} reload={byTag.reload} />
        <Ranking title="By equipment class" rows={byClass.data} error={byClass.error} reload={byClass.reload} named />
        <Ranking title="By reason" rows={byReason.data} error={byReason.error} reload={byReason.reload} />
      </div>
      {t && (
        <UICard as="section" className="downtime-events">
          <h2>Events <span className="muted small">{events.length}{(t.recent?.length ?? 0) < t.events && scope === "all" ? ` newest of ${t.events}` : ""}</span></h2>
          <Table variant="list" density="compact">
            <thead><tr><th>Start</th><th>Hours</th><th>Reason</th><th>Equipment or well</th><th>Oil lost (Sm³)</th></tr></thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td><a href={entityHref(e.id)}>{e.start?.slice(0, 10) ?? "unknown"}</a></td>
                  <td>{e.duration_h ?? "–"}</td>
                  <td>{e.reason ?? "–"}<div className="muted small">{e.comment}</div></td>
                  <td>{[...e.caused_by, ...e.affects].slice(0, 2).map((x, i) => <span key={x.id}>{i > 0 && ", "}
                    <a href={entityHref(x.id)}>{x.label ?? x.id}</a></span>)}</td>
                  <td>{e.lost_oil_sm3 == null ? "–" : fmt(e.lost_oil_sm3)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </UICard>
      )}
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

/** "Jan '24": short enough that two years of months fit across the card. */
const shortMonth = (m: string) => `${new Date(`${m}-01T00:00Z`).toLocaleDateString(undefined, { month: "short", timeZone: "UTC" })} '${m.slice(2, 4)}`;

/** Every month from the first to the last, the empty ones too, so the bars read as a time axis. */
function allMonths(rows: DowntimeMonth[]): DowntimeMonth[] {
  if (!rows.length) return [];
  const by = new Map(rows.map((r) => [r.month, r]));
  const out: DowntimeMonth[] = [];
  let [y, m] = rows[0].month.split("-").map(Number);
  const last = rows[rows.length - 1].month;
  for (let guard = 0; guard < 600; guard++) {
    const key = `${y}-${String(m).padStart(2, "0")}`;
    out.push(by.get(key) ?? { month: key, planned_sm3: 0, unplanned_sm3: 0, planned_events: 0, unplanned_events: 0 });
    if (key >= last) break;
    [y, m] = m === 12 ? [y + 1, 1] : [y, m + 1];
  }
  return out;
}

/** Unplanned loss by month on its own scale; a month with planned loss says how much beside its bar, since drawn
 * to scale a shutdown flattened every other month (#1078). */
function Months({ rows, error, reload }: { rows?: DowntimeMonth[]; error?: string; reload: () => void }) {
  if (!rows && !error) return null;
  const months = allMonths(rows ?? []);
  const top = Math.max(1, ...months.map((r) => r.unplanned_sm3));
  return (
    <UICard as="section" className="downtime-months">
      <h2>Unplanned loss by month <span className="muted small">oil lost (Sm³), by the month each downtime started</span></h2>
      <Status error={error} onRetry={reload} empty={rows?.length === 0}>Nothing recorded.</Status>
      {months.length > 0 && (
        <ol className="month-bars" aria-label="Unplanned oil lost by month">
          {months.map((r) => (
            <li key={r.month} title={`${monthName(r.month)}: ${fmt(r.unplanned_sm3)} Sm³ unplanned in ${r.unplanned_events}, ${fmt(r.planned_sm3)} Sm³ planned in ${r.planned_events}`}>
              <span className="month-value">{r.unplanned_sm3 ? fmt(r.unplanned_sm3) : ""}</span>
              <span className="month-stack">
                <span className="unplanned" style={{ height: `${(100 * r.unplanned_sm3) / top}%` }} />
              </span>
              <span className="month-name">{shortMonth(r.month)}</span>
              <span className="month-planned">{r.planned_sm3 ? <>+{fmt(r.planned_sm3)}<br />planned</> : ""}</span>
            </li>
          ))}
        </ol>
      )}
    </UICard>
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
      {rows && rows.length > 0 && <p className="small">Oil lost (Sm³)</p>}
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
