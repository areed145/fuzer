import { api } from "../../api";
import { entityHref, useAsync } from "../../lib";
import { Card, Disclosure, Table } from "../../ui";
import { when } from "../../work";

const ALARM: Record<string, string> = { hihi: "High-high", hi: "High", lo: "Low", lolo: "Low-low" };

function number(v: number): string {
  return v.toLocaleString(undefined, { maximumSignificantDigits: 4 });
}

/** Around the event (P3.5a): the alarms that sounded on the event's assets and the operator log entries about them,
 * in its window, beside the detections and notifications that joined the case. Nothing shows when there are none. */
export function CaseAround({ id }: { id: number }) {
  const around = useAsync(() => api.workItemAround(id), [id]);
  const d = around.data;
  if (!d?.window || (!d.alarms.length && !d.log.length)) return null;
  return (
    <Card className="case-around" title="Around the event">
      <p className="small">
        <time dateTime={d.window.start}>{when(d.window.start)}</time> to <time dateTime={d.window.end}>{when(d.window.end)}</time>
      </p>
      {d.alarms.length > 0 && <Disclosure open label={<strong>Alarms ({d.alarms.length})</strong>}>
        <Table density="compact">
          <thead><tr><th>Point</th><th>Alarm</th><th>Limit</th><th>Peak</th><th>From</th><th>To</th></tr></thead>
          <tbody>{d.alarms.map((a) => (
            <tr key={`${a.point_id}-${a.type}-${a.start}`}>
              <td><a href={entityHref(a.point_id)}>{a.point}</a></td><td>{ALARM[a.type] ?? a.type}</td>
              <td>{number(a.limit)}</td><td>{number(a.peak)}</td><td>{when(a.start)}</td><td>{a.end ? when(a.end) : "still on"}</td>
            </tr>))}
          </tbody>
        </Table>
      </Disclosure>}
      {d.log.length > 0 && <Disclosure open label={<strong>Operator log ({d.log.length})</strong>}>
        <ul className="plain">{d.log.map((x) => (
          <li key={x.id}><span className="small">{when(x.at)}{x.author ? ` · ${x.author}` : ""}</span>
            <div><a href={entityHref(x.id)}>{x.text ?? "Log entry"}</a></div></li>))}
        </ul>
      </Disclosure>}
    </Card>
  );
}
