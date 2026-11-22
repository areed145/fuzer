/** Logistics from the plan (P5.18): day by day, each crew's jobs, who travels, the seats and beds they
 * take against what there is, and the cranes, vessels and deck space booked. Read from the plan; changed on Plan. */
import { useState } from "react";
import { isoDay, when, whenDay } from "../format";
import { api, type LogisticsDay, type LogisticsJob } from "../api";
import { Badge, ButtonLink, Card, Empty, Field, Heading, Input, Line, List, PageHeader, Select, Table, Text } from "../ui";
import { Status, useAsync } from "../lib";
import { acronyms } from "../wording";

const num = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

export function LogisticsPage() {
  const [from, setFrom] = useState(() => isoDay());
  const [days, setDays] = useState(14);
  const plan = useAsync(() => api.planLogistics({ start: from, days }), [from, days]);
  return (
    <section>
      <PageHeader>Logistics</PageHeader>
      <Line>
        <Field inline label="From"><Input type="date" value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} /></Field>
        <Field inline label="Showing"><Select aria-label="Days shown" value={String(days)} onChange={(e) => setDays(Number(e.target.value))}>
          {[7, 14, 28, 42].map((d) => <option key={d} value={d}>{d} days</option>)}
        </Select></Field>
      </Line>
      {!plan.data ? <Status of={plan} /> : <>
        {!plan.data.by_day.length && <Empty>Nothing to send in these days.</Empty>}
        {plan.data.by_day.map((d) => <Day key={d.day} d={d} />)}
      </>}
    </section>
  );
}

function Day({ d }: { d: LogisticsDay }) {
  return (
    <Card title={whenDay(d.day)} className="logistics-day">
      {(d.travel.length > 0 || d.limits.length > 0) && <Line>
        {d.travel.map((t) => <Badge key={`${t.crew}-${t.way}-${t.schedule}`} tone="info">
          {num(t.people)} {t.name} {t.way === "out" ? "travel out" : "travel back"}{t.by ? ` on ${t.by}` : ""}</Badge>)}
        {d.limits.map((l) => <Badge key={l.resource} tone={l.over ? "danger" : "ok"}>
          {l.name}: {num(l.need)} of {num(l.capacity)}</Badge>)}
      </Line>}
      {d.crews.length > 0 && <Table density="compact">
        <thead><tr><th>Crew</th><th>People</th><th>Job</th><th>Where</th><th>When</th></tr></thead>
        <tbody>{d.crews.flatMap((c) => c.jobs.map((j) => <tr key={`${c.resource}-${j.item_id}`}>
          <td>{c.name}</td><td>{num(j.quantity)}</td><td><Job j={j} /></td>
          <td>{[...j.assets.slice(0, 2), j.area && !j.assets.includes(j.area) ? j.area : null].filter(Boolean).join(", ")}</td>
          <td>{when(j.start)} to {when(j.end)}</td>
        </tr>))}</tbody>
      </Table>}
      {d.equipment.length > 0 && <>
        <Heading level={3}>Booked</Heading>
        <List plain>{d.equipment.map((e) => <li key={e.resource} className="small">
          <strong>{e.name}</strong>: {e.jobs.map((j) => `${j.code ? `${j.code} ` : ""}${acronyms(j.title)}`).join("; ")}
        </li>)}</List>
      </>}
    </Card>
  );
}

function Job({ j }: { j: LogisticsJob }) {
  const label = `${j.code ? `${j.code} ` : ""}${acronyms(j.title)}`;
  return <>
    {j.schedule ? <ButtonLink size="sm" variant="link" href={`#/plan?schedule=${encodeURIComponent(j.schedule)}`}>{label}</ButtonLink>
      : <ButtonLink size="sm" variant="link" href={`#/plan?item=${j.item_id}&from=${isoDay(j.start)}`}>{label}</ButtonLink>}
    {j.order && <Text small> order {j.order}</Text>}
  </>;
}
