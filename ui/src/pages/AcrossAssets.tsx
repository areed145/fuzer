/** Across assets (P7.5b), on How the plant is running: the same KPIs for every field the person may open, each read
 * for the days to its own latest report. Only granted projects are read; the ones left out are named (D6). Shown
 * only when there is more than one field to compare or one left out. */
import { api, type Kpi } from "../api";
import { quantity } from "../format";
import { Badge, Card, Table, Text } from "../ui";
import { Status, useAsync } from "../lib";

const TONE: Record<Kpi["verdict"], "ok" | "danger" | "muted"> = { better: "ok", worse: "danger", same: "muted", unknown: "muted" };
const show = (v: number | null, unit: string) => (v == null ? "unknown" : quantity(v, unit));

export function AcrossAssets({ days }: { days: number }) {
  const data = useAsync(() => api.fleetScorecards(days), [days]);
  const d = data.data;
  if (d && d.projects.length < 2 && !d.left_out.length) return null;
  return (
    <Card className="across-assets" title="Across assets">
      <Status of={data} />
      {d && <>
        <Table density="compact" className="fleet-table phone-rows">
          <thead><tr><th>KPI</th>{d.projects.map((p) => <th key={p.project}>{p.project}
            {p.scorecard && <Text as="div" small muted>{p.scorecard.now.since} to {p.scorecard.now.through}</Text>}</th>)}</tr></thead>
          <tbody>{d.kpis.map((k) => (
            <tr key={k.key} data-kpi={k.key}>
              <td><strong>{k.label}</strong><Text as="div" small muted>{k.better} is better</Text></td>
              {d.projects.map((p) => {
                const v = p.scorecard?.kpis.find((x) => x.key === k.key);
                return <td key={p.project} data-label={p.project} data-project={p.project}>
                  {p.error ? <Text small muted>could not read: {p.error}</Text> : v ? <>
                    <span className={v.now.value == null ? "kpi-unknown" : undefined}>{show(v.now.value, v.unit)}</span>{" "}
                    <Badge tone={TONE[v.verdict]}>{v.verdict}</Badge>
                  </> : "–"}
                </td>;
              })}
            </tr>
          ))}</tbody>
        </Table>
        {d.left_out.length > 0 && <div className="small fleet-left-out">
          Not shown, because you aren't granted them: {d.left_out.join(", ")}.</div>}
        <Text as="div" small muted>{d.note}</Text>
      </>}
    </Card>
  );
}
