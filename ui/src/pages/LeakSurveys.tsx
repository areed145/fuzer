/** Where are the leaks? (#371), on How the plant is running: the latest LDAR surveys, the components that keep leaking,
 * and the equipment no survey reached, named as unknown, never clean. A clean reading holds for its survey date only.
 * Evidence only, not an emissions inventory. */
import { api } from "../api";
import { count } from "../format";
import { Badge, Table, Text } from "../ui";
import { ReadCard, useAsync } from "../lib";

export function LeakSurveys() {
  const data = useAsync(() => api.ldar(), []);
  const d = data.data;
  const c = d?.coverage;
  return (
    <ReadCard of={data} className="leak-surveys" title="Leak surveys" note={d && d.verdict !== "no_surveys" && d.note}>
      {d && d.verdict === "no_surveys" && <Text as="div" small>{d.note}</Text>}
      {d && c && d.verdict !== "no_surveys" && <>
        <Text as="div" small>
          {d.surveys.length} surveys {d.since} to {d.until}; {c.covered} of {c.in_scope} tags screened in the last
          {" "}{d.coverage_days} days.{" "}
          {d.repeat_leakers.length > 0
            ? <Badge tone="warn">{d.repeat_leakers.length} repeat leaker{d.repeat_leakers.length > 1 ? "s" : ""}</Badge>
            : <Badge tone="ok">no repeat leakers</Badge>}
        </Text>
        {d.repeat_leakers.length > 0 && <Table density="compact">
          <thead><tr><th>Tag</th><th>Component</th><th>Found leaking</th></tr></thead>
          <tbody>{d.repeat_leakers.map((r) => (
            <tr key={`${r.tag}-${r.component}`} data-tag={r.tag}>
              <td>{r.tag}</td>
              <td>{r.component}</td>
              <td>{r.surveys.join(", ")} <Text muted>(up to {count(r.max_ppm)} ppm)</Text></td>
            </tr>
          ))}</tbody>
        </Table>}
        {c.unknown.length > 0 && <Text as="div" small>
          Unknown, never surveyed: {c.unknown.map((u) => u.tag).join(", ")}.
        </Text>}
        <Table density="compact">
          <thead><tr><th>Survey</th><th>Components</th><th>Leaks</th><th>Missed</th></tr></thead>
          <tbody>{d.surveys.slice(-4).map((s) => (
            <tr key={s.date} data-date={s.date}>
              <td>{s.date}</td>
              <td>{s.components} on {s.tags} tags</td>
              <td>{s.leaks} ({s.leak_rate_pct}%)</td>
              <td>{s.missed.length ? `${s.missed.length} tags usually screened` : "none"}</td>
            </tr>
          ))}</tbody>
        </Table>
      </>}
    </ReadCard>
  );
}
