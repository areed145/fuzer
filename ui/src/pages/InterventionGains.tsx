/** Did the well work pay off? (P7.5a, #340), on How the plant is running: each recorded intervention's oil rate after
 * against the decline the well was on before, so a well that kept declining gained nothing. Unknown where a side has
 * no test, never zero. Attribution is not proof; the accountable engineer decides on further work. */
import { api, type InterventionGain } from "../api";
import { whenDay } from "../format";
import { Badge, Empty, Table, Text } from "../ui";
import { entityHref, ReadCard, useAsync } from "../lib";

const TONE: Record<InterventionGain["verdict"], "ok" | "danger" | "muted"> =
  { gained: "ok", lost: "danger", no_clear_change: "muted", unknown: "muted" };
const SAY: Record<InterventionGain["verdict"], string> =
  { gained: "gained", lost: "lost", no_clear_change: "no clear change", unknown: "unknown" };

export function InterventionGains() {
  const data = useAsync(() => api.interventionGains(), []);
  const d = data.data;
  return (
    <ReadCard of={data} className="intervention-gains" title="Did the well work pay off?" note={d && <>{d.source}. {d.note}</>}>
      {d && <>
        <Text as="div" small>
          {d.since} to {d.until}: {d.summary.interventions} interventions, {d.summary.gained} gained, {d.summary.lost} lost,
          {" "}{d.summary.no_clear_change} no clear change, {d.summary.unknown} unknown.
        </Text>
        {d.interventions.length ? (
          <Table density="compact">
            <thead><tr><th>Well and work</th><th>Result</th><th>Oil after</th><th>Its decline said</th><th>Water cut</th></tr></thead>
            <tbody>{d.interventions.map((i) => (
              <tr key={i.id} data-intervention={`${i.well} ${i.start.slice(0, 10)}`}>
                <td><a href={entityHref(i.well_id)}>{i.well}</a> <Text small>{whenDay(i.start)}</Text>
                  <Text as="div" small muted>{i.reason}</Text></td>
                <td><Badge tone={TONE[i.verdict]}>{SAY[i.verdict]}</Badge>
                  {i.gain_bbld != null && <Text as="div" small>{i.gain_bbld > 0 ? "+" : ""}{i.gain_bbld} bbl/d net of decline</Text>}
                  {i.why && <Text as="div" small muted>{i.why}</Text>}</td>
                <td>{i.oil_after_bbld != null ? `${i.oil_after_bbld} bbl/d` : "unknown"}
                  <Text as="div" small muted>{i.tests_after} tests after, {i.tests_before} before</Text></td>
                <td>{i.oil_expected_bbld != null ? `${i.oil_expected_bbld} bbl/d` : "–"}
                  {i.oil_range_bbld && <Text as="div" small muted>90% range {i.oil_range_bbld[0]} to {i.oil_range_bbld[1]}</Text>}</td>
                <td>{i.water_cut_change_pct != null ? `${i.water_cut_change_pct > 0 ? "+" : ""}${i.water_cut_change_pct} points` : "unknown"}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No well intervention is recorded in the window.</Empty>}
      </>}
    </ReadCard>
  );
}
