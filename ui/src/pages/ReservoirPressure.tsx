/** How is the reservoir holding up? (#364), on How the plant is running: the one-tank material balance from the
 * static pressure surveys against the reservoir volume taken out net of injection, and each surveyed well's
 * productivity index. Estimates: the assumptions each rests on are always listed. No surveys, no card. */
import { api } from "../api";
import { Badge, Card, Table, Text } from "../ui";
import { Status, useAsync } from "../lib";

export function ReservoirPressure() {
  const data = useAsync(() => api.reservoirPressure(), []);
  const d = data.data;
  if (d && d.productivity.verdict === "no_surveys" && d.material_balance.verdict !== "fitted") return null;
  const mb = d?.material_balance;
  const pi = d?.productivity;
  return (
    <Card className="reservoir-pressure" title="Reservoir pressure">
      <Status of={data} />
      {d && mb && pi && <>
        <div className="small rp-summary">
          {mb.verdict === "fitted"
            ? <>Pressure falls {mb.psi_per_million_rb} psi per million reservoir barrels taken out net of injection
              (r² {mb.r2}, {mb.wells} wells): about {mb.decline_psi_per_year} psi a year at the current rate.</>
            : mb.note}
        </div>
        {pi.wells.length > 0 && <Table density="compact" className="rp-wells phone-rows">
          <thead><tr><th>Well</th><th>Productivity index</th><th>Since the first survey</th></tr></thead>
          <tbody>{pi.wells.map((w) => (
            <tr key={w.well} data-well={w.well}>
              <td>{w.well}</td>
              <td>{w.latest_pi ?? "unknown"} {w.latest_pi != null && <Text muted>{pi.unit}</Text>}</td>
              <td>
                <Badge tone={w.verdict === "fallen" ? "warn" : w.verdict === "holding" ? "ok" : "muted"}>
                  {w.verdict === "too_few_surveys" ? "too few surveys" : w.verdict}</Badge>
                {w.ratio != null && <Text muted> {w.ratio} times the first</Text>}
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
