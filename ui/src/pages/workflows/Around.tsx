import { useState } from "react";
import { api } from "../../api";
import type { CaseAround as CaseAroundData } from "../../api/work";
import { entityHref, useAsync } from "../../lib";
import { TrendView } from "../../TrendView";
import { Button, Card, Disclosure, Table } from "../../ui";
import { when } from "../../work";

const ALARM: Record<string, string> = { hihi: "High-high", hi: "High", lo: "Low", lolo: "Low-low" };

function number(v: number): string {
  return v.toLocaleString(undefined, { maximumSignificantDigits: 4 });
}

/** Around the event (P3.5a): the trend of the unit the event is on over the event's window, with its event lanes
 * (P5.13c), then the alarms that sounded on the event's assets and the operator log entries about them, beside the
 * detections and notifications that joined the case. Nothing shows when the case works no event. */
export function CaseAround({ id }: { id: number }) {
  const around = useAsync(() => api.workItemAround(id), [id]);
  const [unit, setUnit] = useState<string | null>(null);
  const d = around.data;
  if (!d?.window) return null;
  const shown = unit ?? d.units[0]?.id;
  return (
    <>
      {d.units.length > 1 && (
        <div className="chips" role="group" aria-label="Trend of">
          {d.units.map((u) => (
            <Button key={u.id} className="chip" aria-pressed={u.id === shown} onClick={() => setUnit(u.id)}>{u.label ?? u.id}</Button>
          ))}
        </div>
      )}
      {shown && <TrendView key={shown} id={shown} start={d.window.start} end={d.window.end} />}
      {(d.alarms.length > 0 || d.log.length > 0) && <AroundCard d={{ ...d, window: d.window }} />}
    </>
  );
}

function AroundCard({ d }: { d: CaseAroundData & { window: { start: string; end: string } } }) {
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
