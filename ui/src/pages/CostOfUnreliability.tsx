/** Cost of unreliability by unit (P7.3c, #352), on How the plant is running: the oil each unit's downtime lost, at the
 * pack's price, beside what its corrective orders cost, each with its currency. A total only when both share one; an
 * unknown currency is shown as unknown and never added. An operational estimate, not a financial figure. */
import { api } from "../api";
import { money } from "../format";
import { Empty, Table, Text } from "../ui";
import { entityHref, ReadCard, useAsync } from "../lib";


export function CostOfUnreliability() {
  const data = useAsync(() => api.costOfUnreliability(), []);
  const d = data.data;
  return (
    <ReadCard of={data} className="cost-unreliability" title="Cost of unreliability by unit" note={d?.note}>
      {d && <>
        <Text as="div" small>{d.since} to {d.until}. Lost oil at {d.price.oil_bbl} {d.price.currency} per bbl ({d.price.source}).</Text>
        {d.units.length ? (
          <Table density="compact">
            <thead><tr><th>Unit</th><th>Lost oil</th><th>Corrective orders</th><th>Total</th></tr></thead>
            <tbody>{d.units.map((u) => (
              <tr key={u.id} data-unit={u.unit}>
                <td><a href={entityHref(u.id)}>{u.unit}</a>{u.description && <Text as="div" small muted>{u.description}</Text>}</td>
                <td>{money(u.lost_oil_value, d.price.currency)}<Text as="div" small muted>{u.lost_oil_bbl} bbl</Text></td>
                <td>{Object.keys(u.order_cost).length
                  ? Object.entries(u.order_cost).map(([cur, v]) => <div key={cur}>{money(v, cur)}</div>)
                  : <Text muted>none costed</Text>}
                  <Text as="div" small muted>{u.orders} orders{u.orders_without_cost ? `, ${u.orders_without_cost} without a cost` : ""}</Text></td>
                <td>{u.total != null && u.total_currency
                  ? <strong>{money(u.total, u.total_currency)}</strong>
                  : <Text small muted>{u.why_no_total}</Text>}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>No unit caused downtime or had corrective orders in the window.</Empty>}
      </>}
    </ReadCard>
  );
}
