/** Are the well tests still good? (#338), on How the plant is running: the oil the daily report books each month over
 * what the producing wells offer at their latest tests, with each well's downtime taken out, beside the factor
 * ignoring downtime and the age of the tests used. Off one, the tests or the allocation are suspect; which stays the
 * engineer's question. */
import { api } from "../api";
import { Badge, Table, Text } from "../ui";
import { ReadCard, useAsync } from "../lib";

const SAY = { low: "low", high: "high", drifting: "drifting" } as const;

export function AllocationFactor() {
  const data = useAsync(() => api.allocationFactor(), []);
  const d = data.data;
  if (d && !d.months?.length) return null;
  const s = d?.summary;
  return (
    <ReadCard of={data} className="allocation-factor" title="Allocated oil against well tests" note={d?.note}>
      {d && s && <>
        <Text as="div" small>
          {d.since} to {d.until}: allocated over tested oil {s.factor ?? "unknown"} (band {s.band[0]} to {s.band[1]})
          {s.drift != null && <>, first {s.drift_months} months {s.first_months_factor}, latest {s.latest_months_factor}</>};
          {" "}{s.days_left_out} days left out.{" "}
          {d.flags.length
            ? d.flags.map((f) => <Badge key={f} tone="warn">{SAY[f]}</Badge>)
            : <Badge tone="ok">steady</Badge>}
        </Text>
        <Table density="compact">
          <thead><tr><th>Month</th><th>Factor</th><th>Ignoring downtime</th><th>Days used</th><th>Median test age</th></tr></thead>
          <tbody>{d.months.map((m) => (
            <tr key={m.month} data-month={m.month}>
              <td>{m.month}</td>
              <td>{m.factor ?? "no test"}</td>
              <td>{m.factor_ignoring_downtime ?? "no test"}</td>
              <td>{m.days}{m.days_left_out > 0 && <Text muted> ({m.days_left_out} left out)</Text>}</td>
              <td>{m.median_test_age_days != null ? `${m.median_test_age_days} days` : "none"}</td>
            </tr>
          ))}</tbody>
        </Table>
      </>}
    </ReadCard>
  );
}
