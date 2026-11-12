/** Gas lift heading (#341), on How the plant is running: the gas lifted wells whose casing pressure cycles (the
 * annulus filling and dumping through the valve), or whose lift gas or wellhead pressure cycles without it, over the
 * last days. Candidates for the engineer: the cause is theirs to find, and no gas or choke change is proposed. */
import { api } from "../api";
import { Badge, Card, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const SIGNAL: Record<string, string> = { casing_pressure: "casing", gas_lift_rate: "lift gas", wellhead_pressure: "wellhead" };

export function GasLiftHeading() {
  const data = useAsync(() => api.gasLiftStability(), []);
  const d = data.data;
  if (d && !d.summary?.wells) return null;  // no gas lifted well with a casing pressure point
  const found = d?.wells.filter((w) => w.verdict === "heading" || w.verdict === "unstable") ?? [];
  return (
    <Card className="gas-lift-heading" title="Gas lift heading">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && <>
        <div className="small gl-summary">
          Last {d.days} days: {d.summary.heading} of {d.summary.wells} gas lifted wells heading, {d.summary.unstable} unstable,
          {" "}{d.summary.steady} steady{d.summary.too_little_data > 0 && <>, {d.summary.too_little_data} with too few readings</>}.
        </div>
        {found.length ? (
          <Table density="compact" className="gl-table phone-rows">
            <thead><tr><th>Well</th><th>Pattern</th><th>Cycle</th><th>Cycling together</th></tr></thead>
            <tbody>{found.map((w) => (
              <tr key={w.well_id} data-well={w.well}>
                <td><a href={entityHref(w.well_id)}>{w.well}</a></td>
                <td data-label="Pattern"><Badge tone={w.verdict === "heading" ? "danger" : "warn"} className="gl-verdict">{w.verdict}</Badge></td>
                <td data-label="Cycle">every {w.period_min} min, casing swings {w.casing_swing_bar} ({w.casing_swing_pct}% of level)</td>
                <td data-label="Cycling together">{(w.cycling_together ?? []).map((s) => SIGNAL[s] ?? s).join(", ") || "none"}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No gas lifted well heads or cycles in the window.</Empty>}
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
