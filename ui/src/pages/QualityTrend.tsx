/** Separator and export quality (#342), on How the plant is running: each lab analyte at each sample point over the
 * last year, its earlier level against its latest samples, gaps where sampling stopped, and the drifts the
 * quality_drift detector found. No on or off specification claim: no specification or discharge limit is loaded. */
import { api } from "../api";
import { Badge, Card, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

export function QualityTrend() {
  const data = useAsync(() => api.plantQuality(), []);
  const d = data.data;
  if (d && !d.series?.length) return null;  // no lab results landed
  return (
    <Card className="quality-trend" title="Separator and export quality">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && <>
        <div className="small quality-summary">
          {d.since} to {d.until}: {d.summary.series} analytes at {d.summary.sample_points} sample points,
          {" "}{d.summary.drifting} drifting off their own level, {d.summary.with_gaps} with gaps in sampling.
        </div>
        <Table density="compact" className="quality-table phone-rows">
          <thead><tr><th>Sample point</th><th>Analyte</th><th>Earlier, then latest</th><th>Sampling</th></tr></thead>
          <tbody>{d.series.map((s) => (
            <tr key={`${s.subject}|${s.sample_point}|${s.analyte}`} data-series={`${s.sample_point}|${s.analyte}`}>
              <td><a href={entityHref(s.subject)}>{s.label}</a> {s.sample_point}</td>
              <td data-label="Analyte">{s.analyte}{s.drift && <> <Badge tone="warn" className="quality-drift">
                drifting {s.drift.direction}</Badge></>}</td>
              <td data-label="Earlier, then latest">{s.first_half_median} to {s.latest_3_median} {s.unit}
                {s.change_pct != null && <span className="muted"> ({s.change_pct > 0 ? "+" : ""}{s.change_pct}%)</span>}</td>
              <td data-label="Sampling">{s.samples} samples, every {s.usual_interval_days} days
                {s.gaps.map((g) => <div key={g.from} className="small quality-gap">no sample {g.from} to {g.to} ({g.days} days)</div>)}
                {s.below_detection > 0 && <div className="small muted">{s.below_detection} below detection</div>}</td>
            </tr>
          ))}</tbody>
        </Table>
        <div className="small muted quality-limits">{d.note}</div>
      </>}
    </Card>
  );
}
