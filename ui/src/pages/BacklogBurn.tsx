/** Is the backlog under control? (#348), on How the plant is running: work orders created against closed each month,
 * the open backlog at each month's end, and the days from a notification to the order raised for it. Orders with no
 * notification are counted and left out of the lag, never read as zero. */
import { api } from "../api";
import { Badge, Table, Text, List, Heading } from "../ui";
import { entityHref, ReadCard, useAsync } from "../lib";

const TONE = { growing: "danger", steady: "muted", shrinking: "ok" } as const;

export function BacklogBurn() {
  const data = useAsync(() => api.backlogBurn(), []);
  const d = data.data;
  if (d && !d.months?.length) return null;
  return (
    <ReadCard of={data} className="backlog-burn" title="Is the backlog under control?" note={d && <>{d.source}. {d.note}</>}>
      {d && <>
        <Text as="div" small>
          {d.since} to {d.until}: {d.summary.created} orders created, {d.summary.closed} closed; open went from
          {" "}{d.summary.open_at_start} to {d.summary.open_at_end}{" "}
          <Badge tone={TONE[d.summary.trend]}>{d.summary.trend}</Badge>
        </Text>
        <Table density="compact">
          <thead><tr><th>Month</th><th>Created</th><th>Closed</th><th>Open at month end</th></tr></thead>
          {/* the history starts where the sentence above does (#1386): "went from 1" over a first row reading 0 */}
          <tbody><tr data-month="start"><td>{d.since}</td><td /><td /><td>{d.summary.open_at_start}</td></tr>
            {d.months.map((m) => (
            <tr key={m.month} data-month={m.month}>
              <td>{m.month}</td>
              <td>{m.created}</td>
              <td>{m.closed}{m.closed_without_finish > 0 &&
                <Text as="div" small muted>{m.closed_without_finish} closed with no finish date</Text>}</td>
              <td>{m.open_at_end}</td>
            </tr>
          ))}</tbody>
        </Table>
        <Heading level={3}>Notification to order</Heading>
        <Text as="div" small>
          {d.lag.median_days != null
            ? <>Median {d.lag.median_days} days, 90% within {d.lag.p90_days}, longest {d.lag.max_days}, over {d.lag.with_notification} orders
              raised for a notification.</>
            : <>No order in the window was raised for a notification.</>}
          {" "}{d.lag.without_notification} of {d.lag.orders} orders had no notification and are left out.
          {d.lag.ordered_before_notified > 0 && <> {d.lag.ordered_before_notified} were ordered before the notification was created.</>}
        </Text>
        {d.lag.longest.length > 0 && <List plain small>{d.lag.longest.slice(0, 5).map((l) => (
          <li key={l.order}><a href={entityHref(l.order)}>{l.label}</a>: {l.lag_days} days, notified {l.notified}, ordered {l.ordered}
            {l.priority && `, priority ${l.priority}`}</li>
        ))}</List>}
      </>}
    </ReadCard>
  );
}
