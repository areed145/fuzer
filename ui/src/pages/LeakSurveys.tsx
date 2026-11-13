/** Where are the leaks? (#371), on How the plant is running: the latest LDAR surveys, the components that keep leaking,
 * and the equipment no survey reached, named as unknown, never clean. A clean reading holds for its survey date only.
 * Evidence only, not an emissions inventory. */
import { api } from "../api";
import { Badge, Card, Table } from "../ui";
import { Status, useAsync } from "../lib";

export function LeakSurveys() {
  const data = useAsync(() => api.ldar(), []);
  const d = data.data;
  const c = d?.coverage;
  return (
    <Card className="leak-surveys" title="Leak surveys">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && d.verdict === "no_surveys" && <div className="small ls-none">{d.note}</div>}
      {d && c && d.verdict !== "no_surveys" && <>
        <div className="small ls-summary">
          {d.surveys.length} surveys {d.since} to {d.until}; {c.covered} of {c.in_scope} tags screened in the last
          {" "}{d.coverage_days} days.{" "}
          {d.repeat_leakers.length > 0
            ? <Badge tone="warn" className="ls-verdict">{d.repeat_leakers.length} repeat leaker{d.repeat_leakers.length > 1 ? "s" : ""}</Badge>
            : <Badge tone="ok" className="ls-verdict">no repeat leakers</Badge>}
        </div>
        {d.repeat_leakers.length > 0 && <Table density="compact" className="ls-repeat phone-rows">
          <thead><tr><th>Tag</th><th>Component</th><th>Found leaking</th></tr></thead>
          <tbody>{d.repeat_leakers.map((r) => (
            <tr key={`${r.tag}-${r.component}`} data-tag={r.tag}>
              <td>{r.tag}</td>
              <td data-label="Component">{r.component}</td>
              <td data-label="Found leaking">{r.surveys.join(", ")} <span className="muted">(up to {r.max_ppm.toLocaleString()} ppm)</span></td>
            </tr>
          ))}</tbody>
        </Table>}
        {c.unknown.length > 0 && <div className="small ls-unknown">
          Unknown, never surveyed: {c.unknown.map((u) => u.tag).join(", ")}.
        </div>}
        <Table density="compact" className="ls-table phone-rows">
          <thead><tr><th>Survey</th><th>Components</th><th>Leaks</th><th>Missed</th></tr></thead>
          <tbody>{d.surveys.slice(-4).map((s) => (
            <tr key={s.date} data-date={s.date}>
              <td>{s.date}</td>
              <td data-label="Components">{s.components} on {s.tags} tags</td>
              <td data-label="Leaks">{s.leaks} ({s.leak_rate_pct}%)</td>
              <td data-label="Missed">{s.missed.length ? `${s.missed.length} tags usually screened` : "none"}</td>
            </tr>
          ))}</tbody>
        </Table>
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
