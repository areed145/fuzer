/** Is the field putting back what it takes out? (#365), on How the plant is running: water injected each month over the
 * reservoir volume produced, and each injector's Hall plot slope against its own earlier one. A field with no
 * injection says so, never a ratio of zero. Screening only: the injection target stays with the engineers. */
import { api } from "../api";
import { count } from "../format";
import { Badge, Table, Text } from "../ui";
import { ReadCard, useAsync } from "../lib";

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
    <ReadCard of={data} className="water-injection" title="Water injection" note={d && v && v.verdict !== "no_injection" && <>{v.note} {d.hall.note}</>}>
      {d && v && v.verdict === "no_injection" && <Text as="div" small>{v.note}</Text>}
      {d && v && v.verdict !== "no_injection" && <>
        <Text as="div" small>
          {v.since} to {v.until}: injection over produced reservoir volume {v.summary.latest_vrr ?? "unknown"} over
          the latest {v.thresholds.months} months, {v.summary.cumulative_vrr ?? "unknown"} since injection started.{" "}
          <Badge tone={v.verdict === "balanced" ? "ok" : "warn"}>{SAY[v.verdict]}</Badge>
        </Text>
        {d.hall.injectors.length > 0 && <Table density="compact">
          <thead><tr><th>Injector</th><th>Hall slope</th><th>Reads</th></tr></thead>
          <tbody>{d.hall.injectors.map((i) => (
            <tr key={i.well} data-well={i.well}>
              <td>{i.well}</td>
              <td>
                <Badge tone={i.verdict === "rising" || i.verdict === "falling" ? "warn" : "ok"}>{HALL[i.verdict]}</Badge>
                {i.slope_ratio != null && <Text muted> {i.slope_ratio} times earlier</Text>}
              </td>
              <td>{i.reads}</td>
            </tr>
          ))}</tbody>
        </Table>}
        <Table density="compact">
          <thead><tr><th>Month</th><th>Ratio</th><th>Running</th><th>Injected</th><th>Produced</th></tr></thead>
          <tbody>{months.map((m) => (
            <tr key={m.month} data-month={m.month}>
              <td>{m.month}</td>
              <td>{m.vrr ?? "unknown"}</td>
              <td>{m.cumulative_vrr ?? "unknown"}</td>
              <td>{m.injected_rb != null ? `${count(m.injected_rb)} rb` : "unknown"}</td>
              <td>{m.voidage_rb != null ? `${count(m.voidage_rb)} rb` : "unknown"}</td>
            </tr>
          ))}</tbody>
        </Table>
      </>}
    </ReadCard>
  );
}
