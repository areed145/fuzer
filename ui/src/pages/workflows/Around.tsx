import { useState } from "react";
import { when } from "../../format";
import { api } from "../../api";
import type { CaseAround as CaseAroundData } from "../../api/work";
import { entityHref, href, useAsync } from "../../lib";
import { TrendView } from "../../TrendView";
import { ButtonLink, Card, Disclosure, Table, Text, List, Tabs } from "../../ui";

const ALARM: Record<string, string> = { hihi: "High-high", hi: "High", lo: "Low", lolo: "Low-low" };

function number(v: number): string {
  return v.toLocaleString(undefined, { maximumSignificantDigits: 4 });
}

/** Around the event (P3.5a): the trend of the unit the event is on over the event's window, with its event lanes
 * (P5.13c), then the alarms that sounded on the event's assets and the operator log entries about them, beside the
 * detections and notifications that joined the case. Nothing shows when the case works no event. */
export function CaseAround({ id, data, focus }: { id: number; data?: CaseAroundData;
  /** the case's title: the trend opens on the reading it names */ focus?: string }) {
  // The case page reads it beside the case itself and passes it in, so the trend's place is known when the page shows.
  const around = useAsync(() => data ? Promise.resolve(data) : api.workItemAround(id), [id, data]);
  const [unit, setUnit] = useState<string | null>(null);
  const d = data ?? around.data;
  if (!d?.window) return null;
  const units = d.units ?? []; // an older server names none: the alarms and log still show
  const shown = unit ?? units[0]?.id;
  return (
    <>
      {units.length > 1 && (
        <Tabs label="Trend of" items={units.map((u) => ({ key: u.id, label: u.label ?? u.id, current: u.id === shown, onSelect: () => setUnit(u.id) }))} />
      )}
      {shown && <TrendView key={shown} id={shown} start={d.window.start} end={d.window.end} focus={focus} />}
      {shown && (
        // The affected path in 3D (P5.16c): what this unit feeds, lit, with the rest of its area dimmed.
        <Text as="p" small>
          <ButtonLink variant="link" href={href("/3d", { at: shown, node: shown, path: "down", as_of: d.window.end })}>
            See what {units.find((u) => u.id === shown)?.label ?? "it"} feeds in 3D
          </ButtonLink>
        </Text>
      )}
      {(d.alarms.length > 0 || d.log.length > 0) && <AroundCard d={{ ...d, window: d.window }} />}
    </>
  );
}

/** When an alarm was on, short enough for a phone (#1071): "Dec 30, 6:45 PM to 8:10 PM", the end's date only when it
 * is another day. */
function span(start: string, end: string | null): string {
  const s = new Date(start), e = end ? new Date(end) : null;
  const day = (d: Date) => d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const time = (d: Date) => d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (!e) return `${day(s)}, ${time(s)}, still on`;
  return `${day(s)}, ${time(s)} to ${day(e) === day(s) ? "" : `${day(e)}, `}${time(e)}`;
}

function AroundCard({ d }: { d: CaseAroundData & { window: { start: string; end: string } } }) {
  return (
    <Card className="case-around" title="Around the event">
      <Text as="p" small>
        <time dateTime={d.window.start}>{when(d.window.start)}</time> to <time dateTime={d.window.end}>{when(d.window.end)}</time>
      </Text>
      {d.alarms.length > 0 && <Disclosure open label={<strong>Alarms ({d.alarms.length})</strong>}>
        <Table density="compact">
          <thead><tr><th>Point</th><th>Alarm</th><th>Limit</th><th>Peak</th><th>When</th></tr></thead>
          <tbody>{d.alarms.map((a) => (
            <tr key={`${a.point_id}-${a.type}-${a.start}`}>
              <td><a href={entityHref(a.point_id)}>{a.point}</a></td><td>{ALARM[a.type] ?? a.type}</td>
              <td>{number(a.limit)}</td><td>{number(a.peak)}</td><td>{span(a.start, a.end)}</td>
            </tr>))}
          </tbody>
        </Table>
      </Disclosure>}
      {d.log.length > 0 && <Disclosure open label={<strong>Operator log ({d.log.length})</strong>}>
        <List plain>{d.log.map((x) => (
          <li key={x.id}><Text small>{when(x.at)}{x.author ? ` · ${x.author}` : ""}</Text>
            <div><a href={entityHref(x.id)}>{x.text ?? "Log entry"}</a></div></li>))}
        </List>
      </Disclosure>}
    </Card>
  );
}
