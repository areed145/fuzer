/** Alarm system performance (P3.10b, #840), on How the plant is running: the alarm rate against what an operator can
 * handle, the floods with the points that drove them, and the standing and repeating alarms, each with the case that
 * works it. Measures and work for the alarm owner against ISA-18.2 and EEMUA 191: there is nothing here to
 * acknowledge, and no live alarm list. */
import { api } from "../api";
import { Badge, Card, Empty, Table } from "../ui";
import { Status, href, useAsync } from "../lib";

const day = (s: string) => s.replace("T", " ");

export function AlarmPerformance() {
  const data = useAsync(() => api.alarmPerformance(), []);
  const d = data.data;
  if (d && !d.window) return null;  // no alarm limits to measure against
  return (
    <Card className="alarm-performance" title="Alarm system performance">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && <>
        <div className="small ap-summary">
          Last {d.days} days: {d.alarms} alarms, {d.per_hour} an hour against a target of {d.target_per_hour}
          {d.per_hour > d.target_per_hour && <> <Badge tone="warn" className="ap-rate">over target</Badge></>}.
          {" "}{d.floods.length} flood{d.floods.length === 1 ? "" : "s"}, {d.bad_actors.length} bad
          actor{d.bad_actors.length === 1 ? "" : "s"}{d.synthetic && <>, from the fake plant's synthetic values</>}.
        </div>
        {d.floods.length > 0 && (
          <Table density="compact" className="ap-floods phone-rows">
            <thead><tr><th>Flood</th><th>Alarms</th><th>Top contributors</th><th>Case</th></tr></thead>
            <tbody>{d.floods.map((f) => (
              <tr key={f.start} data-flood={f.start}>
                <td>{day(f.start)} to {day(f.end).slice(11)}</td>
                <td data-label="Alarms">{f.alarms}, {f.peak_10_min} in 10 minutes</td>
                <td data-label="Top contributors" className="small">{f.top.map((t) => `${t.point} (${t.alarms})`).join(", ")}</td>
                <td data-label="Case">{f.case ? <a href={href(`/cases/${f.case}`)}>#{f.case}</a> : <span className="muted">none yet</span>}</td>
              </tr>
            ))}</tbody>
          </Table>
        )}
        {d.bad_actors.length > 0 ? (
          <Table density="compact" className="ap-actors phone-rows">
            <thead><tr><th>Point</th><th>Found</th><th>Detail</th><th>Case</th></tr></thead>
            <tbody>{d.bad_actors.map((b) => (
              <tr key={b.point_id} data-point={b.point}>
                <td>{b.point}</td>
                <td data-label="Found">{b.kinds.map((k) => <Badge key={k} tone={k === "standing" ? "warn" : "danger"} className="ap-kind">{k}</Badge>)}</td>
                <td data-label="Detail" className="small">
                  {b.kinds.includes("standing") && <>{b.limits.join(", ")} on since {day(b.since)}</>}
                  {b.kinds.length > 1 && "; "}
                  {b.kinds.includes("repeating") && <>{b.alarms} alarms in the window</>}
                </td>
                <td data-label="Case">{b.case ? <a href={href(`/cases/${b.case}`)}>#{b.case}</a> : <span className="muted">none yet</span>}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No standing or repeating alarms in the window.</Empty>}
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
