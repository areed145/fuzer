/** Are the well tests still good? (#338), on How the plant is running: the oil the daily report books each month over
 * what the producing wells offer at their latest tests, with each well's downtime taken out, beside the factor
 * ignoring downtime and the age of the tests used. Off one, the tests or the allocation are suspect; which stays the
 * engineer's question. */
import { api } from "../api";
import { Badge, Card, Table } from "../ui";
import { Status, useAsync } from "../lib";

const SAY = { low: "low", high: "high", drifting: "drifting" } as const;

export function AllocationFactor() {
  const data = useAsync(() => api.allocationFactor(), []);
  const d = data.data;
  if (d && !d.months?.length) return null;
  const s = d?.summary;
  return (
    <Card className="allocation-factor" title="Allocated oil against well tests">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && s && <>
        <div className="small af-summary">
          {d.since} to {d.until}: allocated over tested oil {s.factor ?? "unknown"} (band {s.band[0]} to {s.band[1]})
          {s.drift != null && <>, first {s.drift_months} months {s.first_months_factor}, latest {s.latest_months_factor}</>};
          {" "}{s.days_left_out} days left out.{" "}
          {d.flags.length
            ? d.flags.map((f) => <Badge key={f} tone="warn" className="af-flag">{SAY[f]}</Badge>)
            : <Badge tone="ok" className="af-flag">steady</Badge>}
        </div>
        <Table density="compact" className="af-table phone-rows">
          <thead><tr><th>Month</th><th>Factor</th><th>Ignoring downtime</th><th>Days used</th><th>Median test age</th></tr></thead>
          <tbody>{d.months.map((m) => (
            <tr key={m.month} data-month={m.month}>
              <td>{m.month}</td>
              <td data-label="Factor">{m.factor ?? "no test"}</td>
              <td data-label="Ignoring downtime">{m.factor_ignoring_downtime ?? "no test"}</td>
              <td data-label="Days used">{m.days}{m.days_left_out > 0 && <span className="muted"> ({m.days_left_out} left out)</span>}</td>
              <td data-label="Median test age">{m.median_test_age_days != null ? `${m.median_test_age_days} days` : "none"}</td>
            </tr>
          ))}</tbody>
        </Table>
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
