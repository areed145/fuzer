/** Wells against their own forecast (P7.3b, #336), on How the plant is running: each producing well's tests over the
 * last year against the decline it was on before, so a new loss stands apart from natural decline. Wells with too
 * few tests are listed, never fitted. A gap is a lead for review; the reservoir cause stays unknown. */
import { api } from "../api";
import { Card, Disclosure, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const sm3 = (v: number | null | undefined) => (v == null ? "–" : `${v.toLocaleString()} Sm3/d`);

export function WellsAgainstForecast() {
  const data = useAsync(() => api.forecastVsActual(), []);
  const d = data.data;
  const below = d?.wells.filter((w) => w.verdict === "below_forecast") ?? [];
  return (
    <Card className="wells-forecast" title="Wells against their own forecast">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && <>
        <div className="small">
          {d.since} to {d.until}. Across {d.field.wells_compared} wells the latest tests make {sm3(d.field.actual_sm3d)} against
          a forecast of {sm3(d.field.forecast_sm3d)} ({d.field.gap_sm3d > 0 ? "+" : ""}{d.field.gap_sm3d} Sm3/d).
          {" "}{below.length} under their forecast, {d.field.above_forecast.length} over it.
        </div>
        {below.length ? (
          <Table density="compact" className="forecast-table phone-rows">
            <thead><tr><th>Well</th><th>Latest test</th><th>Forecast</th><th>Gap</th><th>Under since</th><th>Downtime</th></tr></thead>
            <tbody>{below.map((w) => (
              <tr key={w.id} data-well={w.well}>
                <td><a href={entityHref(w.id)}>{w.well}</a></td>
                <td data-label="Latest test">{sm3(w.latest?.actual_sm3d)}<div className="small muted">{w.latest?.at}</div></td>
                <td data-label="Forecast">{sm3(w.latest?.forecast_sm3d)}
                  {w.latest?.low_sm3d != null && <div className="small muted">90% range {w.latest.low_sm3d} to {w.latest.high_sm3d}</div>}</td>
                <td data-label="Gap"><strong>{w.gap_sm3d} Sm3/d</strong></td>
                <td data-label="Under since">{w.below_since ?? "–"}</td>
                <td data-label="Downtime" className="small">{w.downtime.length
                  ? w.downtime.map((x) => <div key={x.id}><a href={entityHref(x.id)}>{x.start.slice(0, 10)}</a> {x.reason}</div>)
                  : <span className="muted">none recorded</span>}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No well is under its forecast.</Empty>}
        {d.not_fitted.length > 0 && <Disclosure label={`${d.not_fitted.length} wells with too few tests to forecast`}>
          <ul className="plain small">{d.not_fitted.map((w) => (
            <li key={w.id}><a href={entityHref(w.id)}>{w.well}</a>: {w.reason}</li>))}</ul>
        </Disclosure>}
        <div className="small muted">{d.source}. {d.note}</div>
      </>}
    </Card>
  );
}
