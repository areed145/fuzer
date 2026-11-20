/** Against the accounts (P7.5c, #372), on How the plant is running: each month's daily allocated oil and gas against
 * what production accounting closed, citing both sources and both periods, and operating cost per accounted bbl of
 * oil in each currency. The accounted volume is the authority: a difference is for production accounting to resolve,
 * and nothing is restated. */
import { api, type AccountingMonth } from "../api";
import { quantity, count } from "../format";
import { Badge, Card, Disclosure, Empty, Table, Text } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const TONE: Record<AccountingMonth["verdict"], "ok" | "danger" | "warn" | "muted"> = {
  agrees: "ok", differs: "danger", preliminary: "warn", incomplete: "muted", no_accounting: "muted", no_operational: "muted",
};
const SAY: Record<AccountingMonth["verdict"], string> = {
  agrees: "agrees", differs: "differs", preliminary: "preliminary", incomplete: "incomplete",
  no_accounting: "no accounted volume", no_operational: "no daily reports",
};

const n = (v: number | null | undefined, unit: string) => (v == null ? "unknown" : quantity(v, unit));

export function AgainstTheAccounts() {
  const data = useAsync(() => api.plantAccounting(), []);
  const d = data.data;
  const r = d?.reconciliation;
  const u = d?.unit_cost;
  if (d && !r?.months.length && !u?.months.length) return null;  // a site without an accounting export
  return (
    <Card className="against-accounts" title="Against the accounts">
      <Status of={data} />
      {r && <>
        <Text as="div" small>
          {r.since} to {r.until}: {r.summary.agrees} months agree within {r.tolerance_pct}%, {r.summary.differs} differ,
          {" "}{r.summary.preliminary} preliminary, {r.summary.incomplete + r.summary.no_accounting + r.summary.no_operational} can't be compared.
        </Text>
        {r.months.length ? (
          <Table density="compact" className="accounts-table phone-rows">
            <thead><tr><th>Month</th><th>Daily reports</th><th>Accounting</th><th>Difference</th></tr></thead>
            <tbody>{r.months.map((m) => (
              <tr key={`${m.period}-${m.product}`} data-month={`${m.period} ${m.product}`}>
                <td><strong>{m.period}</strong> {m.product}</td>
                <td>{n(m.operational.value, m.unit)}
                  <Text as="div" small muted>
                    {m.operational.since ? `${m.operational.since} to ${m.operational.through}, ${m.operational.days} days` : "none"}
                  </Text>
                  {m.operational.refs.length > 0 && <Disclosure label={`${m.operational.days} daily reports`}>
                    <ul className="plain small">{m.operational.refs.map((x) => <li key={x}><a href={entityHref(x)}>{x}</a></li>)}</ul>
                  </Disclosure>}</td>
                <td>{m.accounting ? <>
                  <a href={entityHref(m.accounting.record)}>{n(m.accounting.value, m.unit)}</a>
                  <Text as="div" small muted>{m.accounting.since} to {m.accounting.through}, {m.accounting.status}</Text>
                  {m.accounting.comment && <Text as="div" small>{m.accounting.comment}</Text>}
                </> : "none"}</td>
                <td>
                  <Badge tone={TONE[m.verdict]}>{SAY[m.verdict]}</Badge>
                  {m.difference_pct != null && <Text as="div" small>
                    {m.difference_pct > 0 ? "+" : ""}{m.difference_pct}% ({m.difference! > 0 ? "+" : ""}{count(m.difference!)} {m.unit})
                  </Text>}
                  <Text as="div" small muted>{m.why}</Text>
                </td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No accounted volumes in the window.</Empty>}
        <Text as="div" small muted>{r.sources.operational} against {r.sources.accounting}. {r.note}</Text>
      </>}
      {u && u.months.length > 0 && <>
        <h3>Operating cost per accounted bbl of oil</h3>
        <div className="small accounts-unit-cost">
          {Object.entries(u.window).map(([cur, w]) => (
            <div key={cur} data-currency={cur}><strong>{count(w.per_bbl)} {cur}/bbl</strong>{" "}
              <Text muted>({count(w.cost)} {cur} over {count(w.oil_bbl)} bbl)</Text></div>
          ))}
        </div>
        <Table density="compact" className="unit-cost-table phone-rows">
          <thead><tr><th>Month</th><th>Booked cost</th><th>Accounted oil</th><th>Per bbl</th></tr></thead>
          <tbody>{u.months.map((m) => (
            <tr key={`${m.period}-${m.currency}`}>
              <td>{m.period}</td>
              <td>{count(m.cost)} {m.currency}
                <Text as="div" small muted>{Object.entries(m.by_element).map(([k, v]) => `${k} ${count(v)}`).join(", ")}</Text></td>
              <td>{n(m.oil_bbl, "bbl")}{m.oil_status && <Text small muted> {m.oil_status}</Text>}</td>
              <td>{m.per_bbl != null ? `${count(m.per_bbl)} ${m.currency}` : "unknown"}
                {m.why_unknown && <Text as="div" small muted>{m.why_unknown}</Text>}</td>
            </tr>
          ))}</tbody>
        </Table>
        <Text as="div" small muted>{u.sources.cost} over {u.sources.volume}. {u.note}</Text>
      </>}
    </Card>
  );
}
