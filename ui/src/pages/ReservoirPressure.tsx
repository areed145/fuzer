/** How is the reservoir holding up? (#364), on How the plant is running: the one-tank material balance from the
 * static pressure surveys against the reservoir volume taken out net of injection, and each surveyed well's
 * productivity index. Estimates: the assumptions each rests on are always listed. No surveys, no card. */
import { api } from "../api";
import { Badge, Card, Table } from "../ui";
import { Status, useAsync } from "../lib";

export function ReservoirPressure() {
  const data = useAsync(() => api.reservoirPressure(), []);
  const d = data.data;
  if (d && d.productivity.verdict === "no_surveys" && d.material_balance.verdict !== "fitted") return null;
  const mb = d?.material_balance;
  const pi = d?.productivity;
  return (
    <Card className="reservoir-pressure" title="Reservoir pressure">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && mb && pi && <>
        <div className="small rp-summary">
          {mb.verdict === "fitted"
            ? <>Pressure falls {mb.bar_per_million_rm3} bar per million reservoir m³ taken out net of injection
              (r² {mb.r2}, {mb.wells} wells): about {mb.decline_bar_per_year} bar a year at the current rate.</>
            : mb.note}
        </div>
        {pi.wells.length > 0 && <Table density="compact" className="rp-wells phone-rows">
          <thead><tr><th>Well</th><th>Productivity index</th><th>Since the first survey</th></tr></thead>
          <tbody>{pi.wells.map((w) => (
            <tr key={w.well} data-well={w.well}>
              <td>{w.well}</td>
              <td data-label="Productivity index">{w.latest_pi ?? "unknown"} {w.latest_pi != null && <span className="muted">{pi.unit}</span>}</td>
              <td data-label="Since the first survey">
                <Badge tone={w.verdict === "fallen" ? "warn" : w.verdict === "holding" ? "ok" : "muted"}>
                  {w.verdict === "too_few_surveys" ? "too few surveys" : w.verdict}</Badge>
                {w.ratio != null && <span className="muted"> {w.ratio} times the first</span>}
              </td>
            </tr>
          ))}</tbody>
        </Table>}
        <div className="rp-assumptions small muted">
          Estimates, resting on: {[...mb.assumptions, ...pi.assumptions].join(" ")}
        </div>
      </>}
    </Card>
  );
}
