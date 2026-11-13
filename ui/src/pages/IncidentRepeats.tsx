/** Do problems come back, and does the work raised for them close? (#350), on Incidents: incidents per unit with the
 * days between them, any that came back after linked work was recorded closed, and the age of open linked orders.
 * Linked work is a proxy, not an action register, and order status says what was recorded, not that it worked. */
import { api, type LinkedOrder } from "../api";
import { Badge, Card, Disclosure, Empty, Table } from "../ui";
import { Status, entityHref, useAsync } from "../lib";

const LINK: Record<LinkedOrder["link"], string> = {
  via_notification: "raised for its notification",
  on_unit_after: "corrective order on the unit soon after",
};

export function IncidentRepeats() {
  const data = useAsync(() => api.incidentRepeats(), []);
  const d = data.data;
  if (d && !d.units?.length) return null;  // none in the window (or a list that isn't this shape)
  const once = (d?.units ?? []).filter((u) => !u.repeat);
  return (
    <Card className="incident-repeats" title="Do problems come back?">
      <Status loading={data.loading && !d} error={data.error} onRetry={data.reload} />
      {d && <>
        <div className="small">
          {d.since} to {d.until}: {d.summary.incidents} incidents on {d.summary.units} units, {d.summary.repeat_units} with
          {" "}more than one; {d.summary.came_back_after_closed_work} came back after linked work was closed;
          {" "}{d.summary.open_orders} linked orders still open.
        </div>
        <Table density="compact" className="repeats-table phone-rows">
          <thead><tr><th>Unit</th><th>Incidents</th><th>Days between</th><th>Linked work</th></tr></thead>
          <tbody>{d.units.filter((u) => u.repeat).map((u) => (
            <tr key={u.unit_id ?? u.unit} data-unit={u.unit}>
              <td>{u.unit_id ? <a href={entityHref(u.unit_id)}>{u.unit}</a> : u.unit}
                {u.repeat && <> <Badge tone="danger" className="repeat-badge">repeat</Badge></>}</td>
              <td data-label="Incidents">{u.incidents}
                <Disclosure label="Which">
                  <ul className="plain small">{u.items.map((i) => (
                    <li key={i.id}><a href={entityHref(i.id)}>{i.start}</a> {i.description}</li>
                  ))}</ul>
                </Disclosure></td>
              <td data-label="Days between">{u.days_between.length ? u.days_between.join(", ") : "once"}</td>
              <td data-label="Linked work">
                {u.items.reduce((n, i) => n + i.linked_orders, 0)} orders, {u.items.reduce((n, i) => n + i.open_orders, 0)} open
                {u.came_back_after_closed_work.map((c) => (
                  <div key={c.incident} className="small came-back">
                    Came back on <a href={entityHref(c.incident)}>{c.start}</a> after{" "}
                    {c.closed_orders.map((o, k) => <span key={o}>{k > 0 && ", "}<a href={entityHref(o)}>closed work</a></span>)}
                  </div>
                ))}
              </td>
            </tr>
          ))}</tbody>
        </Table>
        {once.length > 0 && <Disclosure className="once-units" label={`${once.length} ${once.length === 1 ? "unit" : "units"} with one incident`}>
          <ul className="plain small">{once.map((u) => (
            <li key={u.unit_id ?? u.unit}>{u.unit_id ? <a href={entityHref(u.unit_id)}>{u.unit}</a> : u.unit}
              {u.items.map((i) => <span key={i.id}> · <a href={entityHref(i.id)}>{i.start}</a> {i.description}</span>)}</li>
          ))}</ul>
        </Disclosure>}
        <h3>Open linked work, by age at {d.as_of}</h3>
        <div className="small repeats-ageing">
          {Object.entries(d.summary.open_by_age).map(([band, n]) => <span key={band} data-band={band}>{band}: {n}. </span>)}
        </div>
        {d.open_work.length ? (
          <ul className="plain small">{d.open_work.map((w) => (
            <li key={w.id}><a href={entityHref(w.id)}>{w.order}</a> {w.status}, {w.age_days} days old,
              {" "}{LINK[w.link]}: {w.description}</li>
          ))}</ul>
        ) : <Empty>No open order is linked to these incidents.</Empty>}
        <div className="small muted">{d.summary.incidents_without_linked_work} incidents have no linked work. {d.note}</div>
      </>}
    </Card>
  );
}
