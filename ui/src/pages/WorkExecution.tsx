/** Does work finish when planned, and do repairs hold? (#373), on How the plant is running: schedule compliance
 * (closed orders finished by their basic finish) and the repeat repair rate (a corrective order within 30 days of one
 * on the same equipment) per quarter. A quarter below the sample floor says too little data. Measures the records,
 * never people or crews. */
import { api } from "../api";
import { Card, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const pct = (v: number | null) => (v == null ? "too little data" : `${v}%`);

export function WorkExecution() {
  const data = useAsync(() => api.workExecution(), []);
  const d = data.data;
  if (d && !d.periods?.length) return null;
  const s = d?.summary;
  const repeats = d?.periods.flatMap((p) => p.repeats) ?? [];
  return (
    <Card className="work-execution" title="Does work finish when planned, and do repairs hold?">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && s && <>
        <div className="small we-summary">
          {d.since} to {d.until}: {s.on_time} of {s.finished_with_due_date} closed orders finished by their basic finish
          {" "}({pct(s.schedule_compliance_pct)}); {s.repeats} of {s.corrective} corrective orders came within
          {" "}{d.thresholds.repeat_days} days of a repair on the same equipment ({pct(s.repeat_rate_pct)}).
        </div>
        <Table density="compact" className="we-table phone-rows">
          <thead><tr><th>Period</th><th>Schedule compliance</th><th>Repeat repairs</th></tr></thead>
          <tbody>{d.periods.map((p) => (
            <tr key={p.period} data-period={p.period}>
              <td>{p.period}</td>
              <td data-label="Schedule compliance">{pct(p.schedule_compliance_pct)}
                <span className="muted"> ({p.on_time} of {p.finished - p.no_due_date})</span></td>
              <td data-label="Repeat repairs">{pct(p.repeat_rate_pct)}
                <span className="muted"> ({p.repeats.length} of {p.corrective})</span></td>
            </tr>
          ))}</tbody>
        </Table>
        {repeats.length > 0 && <>
          <h3>Repeat repairs</h3>
          <ul className="plain small we-repeats">{repeats.slice(-8).reverse().map((r) => (
            <li key={r.id}><a href={entityHref(r.id)}>{r.order}</a> started {r.start}, after{" "}
              <a href={entityHref(r.after)}>a repair</a> finished {r.previous_finish}: {r.text}</li>
          ))}</ul>
        </>}
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
