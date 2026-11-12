/** How the plant is running (P7.3a, #353): one scorecard across production, maintenance and reliability, this period
 * against the one before, reached from Metrics. Each KPI shows its window, unit and source, and opens the records
 * behind each value; one the records can't say is unknown, never zero. Internal evidence, not an approved report. */
import { api, type Kpi, type KpiPeriod } from "../api";
import { WellsAgainstForecast } from "./WellsAgainstForecast";
import { CostOfUnreliability } from "./CostOfUnreliability";
import { InterventionGains } from "./InterventionGains";
import { AgainstTheAccounts } from "./AgainstTheAccounts";
import { BacklogBurn } from "./BacklogBurn";
import { WellScreen } from "./WellScreen";
import { AllocationFactor } from "./AllocationFactor";
import { GasLiftHeading } from "./GasLiftHeading";
import { QualityTrend } from "./QualityTrend";
import { AlarmPerformance } from "./AlarmPerformance";
import { AcrossAssets } from "./AcrossAssets";
import { Badge, Disclosure, Empty, PageHeader, Table, Tabs } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";

const PERIODS = [7, 30, 90];
const TONE: Record<Kpi["verdict"], "ok" | "danger" | "muted"> = { better: "ok", worse: "danger", same: "muted", unknown: "muted" };

const show = (v: number | null, unit: string) =>
  v == null ? "unknown" : unit === "%" ? `${v}%` : `${v.toLocaleString()} ${unit}`;

function Side({ p, unit, label }: { p: KpiPeriod; unit: string; label: string }) {
  return <>
    <strong className={p.value == null ? "kpi-unknown" : undefined}>{show(p.value, unit)}</strong>
    {p.note && <div className="small muted">{p.note}</div>}
    {p.records > 0 && <Disclosure label={`${p.records} ${p.records === 1 ? "record" : "records"}`}>
      <ul className="plain small">{p.refs.map((r) => <li key={r}><a href={entityHref(r)}>{r}</a></li>)}</ul>
      {p.records > p.refs.length && <div className="small muted">The first {p.refs.length} of {p.records} ({label}).</div>}
    </Disclosure>}
  </>;
}

export function ScorecardPage({ query }: { query: URLSearchParams }) {
  const days = PERIODS.includes(Number(query.get("days"))) ? Number(query.get("days")) : 30;
  const until = query.get("until");
  const data = useAsync(() => api.plantScorecard(days, until), [days, until]);
  const d = data.data;
  return (
    <section className="plant-scorecard">
      <PageHeader subtitle="Production, maintenance and reliability, this period against the one before."
        actions={<a href={href("/metrics")}>All metrics</a>} help="metrics">How the plant is running</PageHeader>
      <Tabs label="Period" items={PERIODS.map((n) => ({
        key: String(n), label: `${n} days`, current: n === days,
        href: href("/scorecard", { days: String(n), ...(until ? { until } : {}) }),
      }))} />
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && (d.kpis.length ? <>
        <Table density="compact" className="scorecard-table phone-rows">
          <thead><tr>
            <th>KPI</th><th>{d.now.since} to {d.now.through}</th><th>{d.before.since} to {d.before.through}</th>
            <th>Change</th><th>Source</th>
          </tr></thead>
          <tbody>{d.kpis.map((k) => (
            <tr key={k.key} data-kpi={k.key}>
              <td><strong>{k.label}</strong><div className="small muted">{k.definition}</div></td>
              <td data-label="This period"><Side p={k.now} unit={k.unit} label="this period" /></td>
              <td data-label="The period before"><Side p={k.before} unit={k.unit} label="the period before" /></td>
              <td data-label="Change">
                <Badge tone={TONE[k.verdict]} className="kpi-verdict">{k.verdict}</Badge>
                {k.change != null && <div className="small">{k.change > 0 ? "+" : ""}{k.change}{k.unit === "%" ? " points" : ` ${k.unit}`}</div>}
                <div className="small muted">{k.better} is better</div>
              </td>
              <td data-label="Source" className="small">{k.source}</td>
            </tr>
          ))}</tbody>
        </Table>
        <div className="small muted scorecard-note">{d.note}</div>
      </> : <Empty>No KPIs to show.</Empty>)}
      <AcrossAssets days={days} />
      <WellsAgainstForecast />
      <WellScreen />
      <AllocationFactor />
      <GasLiftHeading />
      <QualityTrend />
      <AlarmPerformance />
      <BacklogBurn />
      <CostOfUnreliability />
      <InterventionGains />
      <AgainstTheAccounts />
    </section>
  );
}
