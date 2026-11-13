/** Does the gas add up? (#369), on How the plant is running: the gas handled each day against export, lift gas, flare,
 * fuel and vent, and the closure gap, which is always shown. The stretches the gap stayed open are listed with any term
 * that read zero meanwhile. A term the report doesn't carry is named, never taken as zero. Evidence only, not an
 * emissions inventory. */
import { api } from "../api";
import { Badge, Card, Table } from "../ui";
import { Status, useAsync } from "../lib";

const SAY = { closes: "closes", closes_with_episodes: "closes, with open stretches", open: "doesn't close",
  unknown: "unknown", no_daily_report: "no daily report" } as const;

export function GasBalance() {
  const data = useAsync(() => api.gasBalance(), []);
  const d = data.data;
  if (d && d.verdict === "no_daily_report") return null;
  const s = d?.summary;
  const latest = d?.days.filter((r) => r.gap_pct != null).slice(-7) ?? [];
  return (
    <Card className="gas-balance" title="Gas balance">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && s && <>
        <div className="small gb-summary">
          {d.since} to {d.until}: median closure gap {s.median_gap_pct ?? "unknown"}% of the gas handled,
          {" "}{s.days_open ?? 0} of {s.days_said ?? 0} days open past ±{d.threshold_pct}%.{" "}
          <Badge tone={d.verdict === "closes" ? "ok" : "warn"} className="gb-verdict">{SAY[d.verdict]}</Badge>
        </div>
        {d.missing_terms.length > 0 && <div className="small gb-missing">
          The report carries no {d.missing_terms.join(" or ")}, so the gap includes it.
        </div>}
        {d.episodes.length > 0 && <Table density="compact" className="gb-episodes phone-rows">
          <thead><tr><th>Open stretch</th><th>Gap</th><th>Reading zero</th></tr></thead>
          <tbody>{d.episodes.map((e) => (
            <tr key={e.start} data-start={e.start}>
              <td>{e.start} to {e.end} <span className="muted">({e.days} days)</span></td>
              <td data-label="Gap">{e.median_gap_pct}% ({e.direction === "unaccounted" ? "gas unaccounted for" : "more out than in"})</td>
              <td data-label="Reading zero">{e.terms_reading_zero.length ? e.terms_reading_zero.join(", ") : "none"}</td>
            </tr>
          ))}</tbody>
        </Table>}
        <Table density="compact" className="gb-table phone-rows">
          <thead><tr><th>Day</th><th>Gas handled</th><th>Fuel</th><th>Vent</th><th>Gap</th></tr></thead>
          <tbody>{latest.map((r) => (
            <tr key={r.date} data-date={r.date}>
              <td>{r.date}</td>
              <td data-label="Gas handled">{r.gas_in_ksm3} kSm³</td>
              <td data-label="Fuel">{r.fuel ?? "not reported"}</td>
              <td data-label="Vent">{r.vent ?? "not reported"}</td>
              <td data-label="Gap">{r.gap_ksm3} kSm³ ({r.gap_pct}%)</td>
            </tr>
          ))}</tbody>
        </Table>
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
