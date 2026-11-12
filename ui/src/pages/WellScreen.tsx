/** Water, gas and test age (#339), on How the plant is running: the wells whose water cut stepped up (a breakthrough
 * suggestion) or whose GOR climbed, and whether each well's latest test is recent enough to trust. Patterns are
 * suggestions for the engineer; cumulative oil is estimated from tests. */
import { api } from "../api";
import { Badge, Card, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

export function WellScreen() {
  const data = useAsync(() => api.wellScreen(), []);
  const d = data.data;
  if (d && !d.wells_screened) return null;
  const s = d?.staleness;
  return (
    <Card className="well-screen" title="Water, gas and test age">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && s && <>
        <div className="small well-staleness">
          {s.summary.current} of {s.summary.wells} wells tested within {s.max_days} days at {s.as_of}
          {s.summary.median_age_days != null && <> (median {s.summary.median_age_days} days, oldest {s.summary.oldest_days})</>};
          {" "}{s.summary.stale} stale, {s.summary.no_test_on_file} with no test on file.
        </div>
        {s.wells.filter((w) => w.status !== "current").length > 0 && (
          <ul className="plain small">{s.wells.filter((w) => w.status !== "current").map((w) => (
            <li key={w.id}><a href={entityHref(w.id)}>{w.well}</a>: {w.status === "stale"
              ? `latest test ${w.latest_test}, ${w.age_days} days old` : "no test on file"}</li>
          ))}</ul>
        )}
        {d.patterns.length ? (
          <Table density="compact" className="well-patterns phone-rows">
            <thead><tr><th>Well</th><th>Pattern</th><th>What the tests show</th></tr></thead>
            <tbody>{d.patterns.map((p) => (
              <tr key={p.well_id} data-well={p.well}>
                <td><a href={entityHref(p.well_id)}>{p.well}</a></td>
                <td data-label="Pattern"><Badge tone={p.verdict === "water_cut_step" ? "danger" : "warn"} className="well-pattern">
                  {p.verdict === "water_cut_step" ? "water cut step" : "GOR climb"}</Badge></td>
                <td data-label="What the tests show">
                  {p.water_cut_step && <>Water cut {p.water_cut_step.water_cut_before_pct}% to {p.water_cut_step.water_cut_after_pct}% on
                    {" "}{p.water_cut_step.at}, oil {p.water_cut_step.oil_before_sm3d} to {p.water_cut_step.oil_after_sm3d} Sm3/d,
                    {" "}at about {p.water_cut_step.cum_oil_sm3.toLocaleString()} Sm3 cumulative (estimated from tests)</>}
                  {p.gor_climb && <>GOR {p.gor_climb.first_gor} to {p.gor_climb.latest_gor} Sm3/Sm3 (x{p.gor_climb.ratio})</>}
                </td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No water cut step or GOR climb on any well.</Empty>}
        <div className="small muted">
          A step is {d.thresholds.water_cut_step_points} water cut points over the tests before; a climb is GOR up
          {" "}{Math.round((d.thresholds.gor_rise - 1) * 100)}% end to end. {d.note} {s.note}
        </div>
      </>}
    </Card>
  );
}
