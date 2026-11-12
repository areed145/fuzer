/** Against the accounts (P7.5c, #372), on How the plant is running: each month's daily allocated oil and gas against
 * what production accounting closed, citing both sources and both periods, and operating cost per accounted Sm3 of
 * oil in each currency. The accounted volume is the authority: a difference is for production accounting to resolve,
 * and nothing is restated. */
import { api, type AccountingMonth } from "../api";
import { Badge, Card, Disclosure, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const TONE: Record<AccountingMonth["verdict"], "ok" | "danger" | "warn" | "muted"> = {
  agrees: "ok", differs: "danger", preliminary: "warn", incomplete: "muted", no_accounting: "muted", no_operational: "muted",
};
const SAY: Record<AccountingMonth["verdict"], string> = {
  agrees: "agrees", differs: "differs", preliminary: "preliminary", incomplete: "incomplete",
  no_accounting: "no accounted volume", no_operational: "no daily reports",
};

const n = (v: number | null | undefined, unit: string) => (v == null ? "unknown" : `${v.toLocaleString()} ${unit}`);

export function AgainstTheAccounts() {
  const data = useAsync(() => api.plantAccounting(), []);
  const d = data.data;
  const r = d?.reconciliation;
  const u = d?.unit_cost;
  if (d && !r?.months.length && !u?.months.length) return null;  // a site without an accounting export
  return (
    <Card className="against-accounts" title="Against the accounts">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {r && <>
        <div className="small">
          {r.since} to {r.until}: {r.summary.agrees} months agree within {r.tolerance_pct}%, {r.summary.differs} differ,
          {" "}{r.summary.preliminary} preliminary, {r.summary.incomplete + r.summary.no_accounting + r.summary.no_operational} can't be compared.
        </div>
        {r.months.length ? (
          <Table density="compact" className="accounts-table phone-rows">
            <thead><tr><th>Month</th><th>Daily reports</th><th>Accounting</th><th>Difference</th></tr></thead>
            <tbody>{r.months.map((m) => (
              <tr key={`${m.period}-${m.product}`} data-month={`${m.period} ${m.product}`}>
                <td><strong>{m.period}</strong> {m.product}</td>
                <td data-label="Daily reports">{n(m.operational.value, m.unit)}
                  <div className="small muted">
                    {m.operational.since ? `${m.operational.since} to ${m.operational.through}, ${m.operational.days} days` : "none"}
                  </div>
                  {m.operational.refs.length > 0 && <Disclosure label={`${m.operational.days} daily reports`}>
                    <ul className="plain small">{m.operational.refs.map((x) => <li key={x}><a href={entityHref(x)}>{x}</a></li>)}</ul>
                  </Disclosure>}</td>
                <td data-label="Accounting">{m.accounting ? <>
                  <a href={entityHref(m.accounting.record)}>{n(m.accounting.value, m.unit)}</a>
                  <div className="small muted">{m.accounting.since} to {m.accounting.through}, {m.accounting.status}</div>
                  {m.accounting.comment && <div className="small">{m.accounting.comment}</div>}
                </> : "none"}</td>
                <td data-label="Difference">
                  <Badge tone={TONE[m.verdict]} className="accounts-verdict">{SAY[m.verdict]}</Badge>
                  {m.difference_pct != null && <div className="small">
                    {m.difference_pct > 0 ? "+" : ""}{m.difference_pct}% ({m.difference! > 0 ? "+" : ""}{m.difference!.toLocaleString()} {m.unit})
                  </div>}
                  <div className="small muted">{m.why}</div>
                </td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No accounted volumes in the window.</Empty>}
        <div className="small muted">{r.sources.operational} against {r.sources.accounting}. {r.note}</div>
      </>}
      {u && u.months.length > 0 && <>
        <h3>Operating cost per accounted Sm3 of oil</h3>
        <div className="small accounts-unit-cost">
          {Object.entries(u.window).map(([cur, w]) => (
            <div key={cur} data-currency={cur}><strong>{w.per_sm3.toLocaleString()} {cur}/Sm3</strong>{" "}
              <span className="muted">({w.cost.toLocaleString()} {cur} over {w.oil_sm3.toLocaleString()} Sm3)</span></div>
          ))}
        </div>
        <Table density="compact" className="unit-cost-table phone-rows">
          <thead><tr><th>Month</th><th>Booked cost</th><th>Accounted oil</th><th>Per Sm3</th></tr></thead>
          <tbody>{u.months.map((m) => (
            <tr key={`${m.period}-${m.currency}`}>
              <td>{m.period}</td>
              <td data-label="Booked cost">{m.cost.toLocaleString()} {m.currency}
                <div className="small muted">{Object.entries(m.by_element).map(([k, v]) => `${k} ${v.toLocaleString()}`).join(", ")}</div></td>
              <td data-label="Accounted oil">{n(m.oil_sm3, "Sm3")}{m.oil_status && <span className="small muted"> {m.oil_status}</span>}</td>
              <td data-label="Per Sm3">{m.per_sm3 != null ? `${m.per_sm3.toLocaleString()} ${m.currency}` : "unknown"}
                {m.why_unknown && <div className="small muted">{m.why_unknown}</div>}</td>
            </tr>
          ))}</tbody>
        </Table>
        <div className="small muted">{u.sources.cost} over {u.sources.volume}. {u.note}</div>
      </>}
    </Card>
  );
}
