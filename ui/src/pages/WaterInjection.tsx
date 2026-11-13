/** Is the field putting back what it takes out? (#365), on How the plant is running: water injected each month over the
 * reservoir volume produced, and each injector's Hall plot slope against its own earlier one. A field with no
 * injection says so, never a ratio of zero. Screening only: the injection target stays with the engineers. */
import { api } from "../api";
import { Badge, Card, Table } from "../ui";
import { Status, useAsync } from "../lib";

const SAY = { under_replacing: "under-replacing", over_injecting: "over-injecting", balanced: "balanced",
  unknown: "unknown", no_daily_report: "no daily report" } as const;
const HALL = { rising: "rising", falling: "falling", steady: "steady", too_few_months: "too few months" } as const;

export function WaterInjection() {
  const data = useAsync(() => api.waterInjection(), []);
  const d = data.data;
  if (d && d.voidage.verdict === "no_daily_report") return null;
  const v = d?.voidage;
  const months = v?.months.filter((m) => m.status !== "no_injection") ?? [];
  return (
    <Card className="water-injection" title="Water injection">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && v && v.verdict === "no_injection" && <div className="small wi-none">{v.note}</div>}
      {d && v && v.verdict !== "no_injection" && <>
        <div className="small wi-summary">
          {v.since} to {v.until}: injection over produced reservoir volume {v.summary.latest_vrr ?? "unknown"} over
          the latest {v.thresholds.months} months, {v.summary.cumulative_vrr ?? "unknown"} since injection started.{" "}
          <Badge tone={v.verdict === "balanced" ? "ok" : "warn"} className="wi-verdict">{SAY[v.verdict]}</Badge>
        </div>
        {d.hall.injectors.length > 0 && <Table density="compact" className="wi-hall phone-rows">
          <thead><tr><th>Injector</th><th>Hall slope</th><th>Reads</th></tr></thead>
          <tbody>{d.hall.injectors.map((i) => (
            <tr key={i.well} data-well={i.well}>
              <td>{i.well}</td>
              <td data-label="Hall slope">
                <Badge tone={i.verdict === "rising" || i.verdict === "falling" ? "warn" : "ok"}>{HALL[i.verdict]}</Badge>
                {i.slope_ratio != null && <span className="muted"> {i.slope_ratio} times earlier</span>}
              </td>
              <td data-label="Reads">{i.reads}</td>
            </tr>
          ))}</tbody>
        </Table>}
        <Table density="compact" className="wi-table phone-rows">
          <thead><tr><th>Month</th><th>Ratio</th><th>Running</th><th>Injected</th><th>Produced</th></tr></thead>
          <tbody>{months.map((m) => (
            <tr key={m.month} data-month={m.month}>
              <td>{m.month}</td>
              <td data-label="Ratio">{m.vrr ?? "unknown"}</td>
              <td data-label="Running">{m.cumulative_vrr ?? "unknown"}</td>
              <td data-label="Injected">{m.injected_rm3 != null ? `${m.injected_rm3.toLocaleString()} rm³` : "unknown"}</td>
              <td data-label="Produced">{m.voidage_rm3 != null ? `${m.voidage_rm3.toLocaleString()} rm³` : "unknown"}</td>
            </tr>
          ))}</tbody>
        </Table>
        <div className="small muted">{v.note} {d.hall.note}</div>
      </>}
    </Card>
  );
}
