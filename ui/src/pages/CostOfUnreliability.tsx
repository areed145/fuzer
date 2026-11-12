/** Cost of unreliability by unit (P7.3c, #352), on How the plant is running: the oil each unit's downtime lost, at the
 * pack's price, beside what its corrective orders cost, each with its currency. A total only when both share one; an
 * unknown currency is shown as unknown and never added. An operational estimate, not a financial figure. */
import { api } from "../api";
import { Card, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const money = (v: number, cur: string) => (cur === "unknown" ? `${v.toLocaleString()} (currency unknown)` : `${v.toLocaleString()} ${cur}`);

export function CostOfUnreliability() {
  const data = useAsync(() => api.costOfUnreliability(), []);
  const d = data.data;
  return (
    <Card className="cost-unreliability" title="Cost of unreliability by unit">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && <>
        <div className="small">{d.since} to {d.until}. Lost oil at {d.price.oil_sm3} {d.price.currency} per Sm3 ({d.price.source}).</div>
        {d.units.length ? (
          <Table density="compact" className="unreliability-table phone-rows">
            <thead><tr><th>Unit</th><th>Lost oil</th><th>Corrective orders</th><th>Total</th></tr></thead>
            <tbody>{d.units.map((u) => (
              <tr key={u.id} data-unit={u.unit}>
                <td><a href={entityHref(u.id)}>{u.unit}</a>{u.description && <div className="small muted">{u.description}</div>}</td>
                <td data-label="Lost oil">{money(u.lost_oil_value, d.price.currency)}<div className="small muted">{u.lost_oil_sm3} Sm3</div></td>
                <td data-label="Corrective orders">{Object.keys(u.order_cost).length
                  ? Object.entries(u.order_cost).map(([cur, v]) => <div key={cur}>{money(v, cur)}</div>)
                  : <span className="muted">none costed</span>}
                  <div className="small muted">{u.orders} orders{u.orders_without_cost ? `, ${u.orders_without_cost} without a cost` : ""}</div></td>
                <td data-label="Total">{u.total != null && u.total_currency
                  ? <strong>{money(u.total, u.total_currency)}</strong>
                  : <span className="small muted unreliability-no-total">{u.why_no_total}</span>}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No unit caused downtime or had corrective orders in the window.</Empty>}
        <div className="small muted">{d.note}</div>
      </>}
    </Card>
  );
}
