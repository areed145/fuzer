/** Did the well work pay off? (P7.5a, #340), on How the plant is running: each recorded intervention's oil rate after
 * against the decline the well was on before, so a well that kept declining gained nothing. Unknown where a side has
 * no test, never zero. Attribution is not proof; the accountable engineer decides on further work. */
import { api, type InterventionGain } from "../api";
import { Badge, Card, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const TONE: Record<InterventionGain["verdict"], "ok" | "danger" | "muted"> =
  { gained: "ok", lost: "danger", no_clear_change: "muted", unknown: "muted" };
const SAY: Record<InterventionGain["verdict"], string> =
  { gained: "gained", lost: "lost", no_clear_change: "no clear change", unknown: "unknown" };

export function InterventionGains() {
  const data = useAsync(() => api.interventionGains(), []);
  const d = data.data;
  return (
    <Card className="intervention-gains" title="Did the well work pay off?">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && <>
        <div className="small">
          {d.since} to {d.until}: {d.summary.interventions} interventions, {d.summary.gained} gained, {d.summary.lost} lost,
          {" "}{d.summary.no_clear_change} no clear change, {d.summary.unknown} unknown.
        </div>
        {d.interventions.length ? (
          <Table density="compact" className="interventions-table phone-rows">
            <thead><tr><th>Well and work</th><th>Result</th><th>Oil after</th><th>Its decline said</th><th>Water cut</th></tr></thead>
            <tbody>{d.interventions.map((i) => (
              <tr key={i.id} data-intervention={`${i.well} ${i.start.slice(0, 10)}`}>
                <td><a href={entityHref(i.well_id)}>{i.well}</a> <span className="small">{i.start.slice(0, 10)}</span>
                  <div className="small muted">{i.reason}</div></td>
                <td data-label="Result"><Badge tone={TONE[i.verdict]} className="intervention-verdict">{SAY[i.verdict]}</Badge>
                  {i.gain_sm3d != null && <div className="small">{i.gain_sm3d > 0 ? "+" : ""}{i.gain_sm3d} Sm3/d net of decline</div>}
                  {i.why && <div className="small muted">{i.why}</div>}</td>
                <td data-label="Oil after">{i.oil_after_sm3d != null ? `${i.oil_after_sm3d} Sm3/d` : "unknown"}
                  <div className="small muted">{i.tests_after} tests after, {i.tests_before} before</div></td>
                <td data-label="Its decline said">{i.oil_expected_sm3d != null ? `${i.oil_expected_sm3d} Sm3/d` : "–"}
                  {i.oil_range_sm3d && <div className="small muted">90% range {i.oil_range_sm3d[0]} to {i.oil_range_sm3d[1]}</div>}</td>
                <td data-label="Water cut">{i.water_cut_change_pct != null ? `${i.water_cut_change_pct > 0 ? "+" : ""}${i.water_cut_change_pct} points` : "unknown"}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No well intervention is recorded in the window.</Empty>}
        <div className="small muted">{d.source}. {d.note}</div>
      </>}
    </Card>
  );
}
