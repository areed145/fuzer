import { useState } from "react";
import { money, when } from "../format";
import { api, type AiUsage as Usage, type UsageGroup } from "../api";
import { href, Status, useAsync } from "../lib";
import { Bar, Badge, Button, Card, Empty, Notice, Select, Stat, Stats, Table, Line, Text, Heading } from "../ui";

const tokens = (v: number) => (v >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `${Math.round(v / 1e3)}k` : String(v));
const BUDGET = href("/settings", { view: "configuration", filter: "llm.budget" });

/** Every model call the application made (Claude or another provider), what it cost and which feature asked, against
 * the month's budget (llm.budget_usd_month). Administrators only; reading it calls no model. */
export function AiUsage() {
  const [days, setDays] = useState(30);
  const u = useAsync(() => api.aiUsage(days), [days]);
  const d = u.data;
  return <div>
    <Heading>AI usage</Heading>
    <Line small>
      <Select aria-label="Window" value={days} onChange={(e) => setDays(Number(e.target.value))}>
        {[1, 7, 30, 90].map((n) => <option key={n} value={n}>Last {n === 1 ? "day" : `${n} days`}</option>)}
      </Select>
      <Button disabled={u.loading} onClick={u.reload}>Refresh</Button>
    </Line>
    <Status of={u} />
    {d && <Summary d={d} />}
  </div>;
}

function Summary({ d }: { d: Usage }) {
  const b = d.budget;
  const share = b.cap_usd ? (100 * b.spent_usd) / b.cap_usd : 0;
  return <>
    {b.reached && <Notice tone="danger">This month&apos;s model spend has reached its budget: AI features are off until
      the month ends or <a href={BUDGET}>the budget</a> is raised.</Notice>}
    {!b.reached && b.cap_usd > 0 && b.alert_pct > 0 && share >= b.alert_pct &&
      <Notice tone="warn">This month&apos;s model spend is {Math.round(share)}% of its budget.</Notice>}
    <Stats>
      <Stat label={`Spent in ${b.month} (UTC)`} value={money(b.spent_usd)}
        detail={b.cap_usd ? <>of a {money(b.cap_usd)} budget · <a href={BUDGET}>change</a></> :
          <>no budget · <a href={BUDGET}>set one</a></>} />
      <Stat label={`Last ${d.days === 1 ? "day" : `${d.days} days`}`} value={money(d.total.cost_usd)}
        detail={`${d.total.calls} calls · ${tokens(d.total.input_tokens + d.total.cache_read_tokens + d.total.cache_write_tokens)} in, ${tokens(d.total.output_tokens)} out`} />
      <Stat label="Last call" value={when(d.total.last_at, "never")} detail={d.recent[0]?.feature ?? "none yet"} />
    </Stats>
    {b.cap_usd > 0 && <Bar value={share} label="Share of the month's budget spent" />}
    <Text as="p" small>Costs are estimates from token counts and list prices{d.total.unpriced ? "; some models have no listed price" : ""}.
      The provider&apos;s bill is the record.</Text>
    <Daily d={d} />
    <Groups title="By feature" rows={d.by_feature} />
    <Groups title="By model" rows={d.by_model} />
    {d.by_project.length > 1 && <Groups title="By project" rows={d.by_project} />}
    <Card title="Latest calls">
      {d.recent.length === 0 ? <Empty>No model calls yet.</Empty> : <Table density="compact">
        <thead><tr><th>When (UTC)</th><th>Feature</th><th>Project</th><th>Model</th><th>Tokens in / out</th><th>Cost</th></tr></thead>
        <tbody>{d.recent.map((r, i) => <tr key={i}>
          <td>{when(r.at, "never")}</td><td>{r.feature}</td><td>{r.project ?? "—"}</td>
          <td>{r.model} {r.batch && <Badge>batch</Badge>}</td>
          <td>{tokens(r.input_tokens)} / {tokens(r.output_tokens)}</td><td>{money(r.cost_usd)}</td>
        </tr>)}</tbody>
      </Table>}
    </Card>
  </>;
}

function Daily({ d }: { d: Usage }) {
  const days = new Map<string, { cost: number; calls: number; top: string; topCost: number }>();
  for (const r of d.daily) {
    const day = days.get(r.day) ?? { cost: 0, calls: 0, top: "", topCost: -1 };
    day.cost += r.cost_usd; day.calls += r.calls;
    if (r.cost_usd > day.topCost) { day.top = r.feature; day.topCost = r.cost_usd; }
    days.set(r.day, day);
  }
  const rows = [...days.entries()].reverse();
  const most = Math.max(0.0001, ...rows.map(([, v]) => v.cost));
  return <Card title="By day">
    {rows.length === 0 ? <Empty>No model calls in this window.</Empty> : <Table density="compact">
      <thead><tr><th>Day (UTC)</th><th>Cost</th><th /><th>Calls</th><th>Most spent on</th></tr></thead>
      <tbody>{rows.map(([day, v]) => <tr key={day}>
        <td>{day}</td><td>{money(v.cost)}</td><td><Bar value={(100 * v.cost) / most} label={`${day} cost`} /></td>
        <td>{v.calls}</td><td>{v.top}</td>
      </tr>)}</tbody>
    </Table>}
  </Card>;
}

function Groups({ title, rows }: { title: string; rows: UsageGroup[] }) {
  return <Card title={title}>
    {rows.length === 0 ? <Empty>No model calls in this window.</Empty> : <Table density="compact">
      <thead><tr><th>{title.replace("By ", "")}</th><th>Calls</th><th>Tokens in</th><th>Tokens out</th><th>Cost</th><th>Last call (UTC)</th></tr></thead>
      <tbody>{rows.map((r) => <tr key={r.key}>
        <td>{r.key}</td><td>{r.calls}</td><td>{tokens(r.input_tokens)}</td><td>{tokens(r.output_tokens)}</td>
        <td>{money(r.cost_usd)}</td><td>{when(r.last_at, "never")}</td>
      </tr>)}</tbody>
    </Table>}
  </Card>;
}
