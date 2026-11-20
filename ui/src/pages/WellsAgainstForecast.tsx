/** Wells against their own forecast (P7.3b, #336), on How the plant is running: each producing well's tests over the
 * last year against the decline it was on before, so a new loss stands apart from natural decline. Wells with too
 * few tests are listed, never fitted. A gap is a lead for review; the reservoir cause stays unknown. */
import { api } from "../api";
import { whenDay, count } from "../format";
import { Disclosure, Empty, Table, Text } from "../ui";
import { entityHref, ReadCard, useAsync } from "../lib";

const sm3 = (v: number | null | undefined) => (v == null ? "–" : `${count(v)} bbl/d`);

export function WellsAgainstForecast() {
  const data = useAsync(() => api.forecastVsActual(), []);
  const d = data.data;
  const below = d?.wells.filter((w) => w.verdict === "below_forecast") ?? [];
  return (
    <ReadCard of={data} className="wells-forecast" title="Wells against their own forecast" note={d && <>{d.source}. {d.note}</>}>
      {d && <>
        <Text as="div" small>
          {d.since} to {d.until}. Across {d.field.wells_compared} wells the latest tests make {sm3(d.field.actual_bbld)} against
          a forecast of {sm3(d.field.forecast_bbld)} ({d.field.gap_bbld > 0 ? "+" : ""}{d.field.gap_bbld} bbl/d).
          {" "}{below.length} under their forecast, {d.field.above_forecast.length} over it.
        </Text>
        {below.length ? (
          <Table density="compact">
            <thead><tr><th>Well</th><th>Latest test</th><th>Forecast</th><th>Gap</th><th>Under since</th><th>Downtime</th></tr></thead>
            <tbody>{below.map((w) => (
              <tr key={w.id} data-well={w.well}>
                <td><a href={entityHref(w.id)}>{w.well}</a></td>
                <td>{sm3(w.latest?.actual_bbld)}<Text as="div" small muted>{w.latest?.at}</Text></td>
                <td>{sm3(w.latest?.forecast_bbld)}
                  {w.latest?.low_bbld != null && <Text as="div" small muted>90% range {w.latest.low_bbld} to {w.latest.high_bbld}</Text>}</td>
                <td><strong>{w.gap_bbld} bbl/d</strong></td>
                <td>{w.below_since ?? "–"}</td>
                <td className="small">{w.downtime.length
                  ? w.downtime.map((x) => <div key={x.id}><a href={entityHref(x.id)}>{whenDay(x.start)}</a> {x.reason}</div>)
                  : <Text muted>none recorded</Text>}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No well is under its forecast.</Empty>}
        {d.not_fitted.length > 0 && <Disclosure label={`${d.not_fitted.length} wells with too few tests to forecast`}>
          <ul className="plain small">{d.not_fitted.map((w) => (
            <li key={w.id}><a href={entityHref(w.id)}>{w.well}</a>: {w.reason}</li>))}</ul>
        </Disclosure>}
      </>}
    </ReadCard>
  );
}
