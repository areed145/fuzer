import { api } from "./api";
import { entityHref, useAsync } from "./lib";
import { Badge } from "./ui";

const RATES = ["oil_rate", "gas_rate", "condensate_rate", "water_cut", "gor"];
const words = (name: string) => name.replace(/_/g, " ").replace(/^gor$/, "GOR");

/** One line at the top of an operated asset's page that answers "is it OK right now": whether a downtime is open,
 * how much work is open on it, its newest incident and, for a well, what its latest test measured. It says only
 * what the records say, so no open downtime is not a claim that the unit is running. */
export function EntityStatus({ id }: { id: string }) {
  const s = useAsync(() => api.entityStatus(id), [id]).data;
  if (!s || typeof s.open_work !== "number") return null;
  const day = (t: string | null) => t?.slice(0, 10) ?? "an unknown date";
  const rates = (s.well_test?.values ?? []).filter((v) => RATES.includes(v.quantity))
    .sort((a, b) => RATES.indexOf(a.quantity) - RATES.indexOf(b.quantity));
  return <p className="entity-status small" role="status" aria-label="Status now">
    {s.downtime?.open
      ? <span><Badge tone="danger">Down</Badge> since <a href={entityHref(s.downtime.id)}>{day(s.downtime.start)}</a>
        {s.downtime.reason && <>: {s.downtime.reason}</>}</span>
      : <span><Badge tone="ok">No downtime open</Badge>
        {s.downtime && <> last <a href={entityHref(s.downtime.id)}>{day(s.downtime.start)}</a></>}</span>}
    <span>{s.open_work > 0 ? <Badge tone="warn">{s.open_work} open {s.open_work === 1 ? "exception" : "exceptions"}</Badge>
      : <Badge tone="ok">No open exceptions</Badge>}</span>
    {s.incident && <span>Last incident <a href={entityHref(s.incident.id)}>{day(s.incident.start)}</a>
      {s.incident.total > 1 && <span className="muted"> of {s.incident.total}</span>}</span>}
    {s.well_test && <span>Last test {s.well_test.at}
      {rates.length > 0 && <>: {rates.map((v) => `${words(v.quantity)} ${v.value}${v.unit ? ` ${v.unit}` : ""}`).join(" · ")}</>}</span>}
  </p>;
}
