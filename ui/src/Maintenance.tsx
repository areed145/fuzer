import "./styles/events.css";
import { count, whenDay } from "./format";
import type { DowntimeRow, Maintenance } from "./api";
import { entityHref } from "./lib";
import { Badge, ErrorText, Line, Text } from "./ui";

/** An asset's SAP notifications, newest first, each with its failure mode and its work orders: as its page lists them,
 * and beside an outage's windows (P5.22d). */
export function NotificationList({ items }: { items: Maintenance["recent"] }) {
  return (
    <ul className="timeline">
      {items.map((n) => (
        <li key={n.id}>
          <span className="when">{n.created}</span>
          <div className="what">
            {/* One line that wraps as text: a flex row put a long title under its code, alone (#1076). */}
            <div>
              {n.failure_mode && <><Badge tone="danger" title={`ISO 14224 ${n.failure_mode}`}>
                {n.failure_mode_name ?? n.failure_mode}</Badge>{" "}</>}
              <a href={entityHref(n.id)}>{n.text}</a>
            </div>
            {n.work_orders.map((w) => (
              <div key={w.id} className="muted small">
                WO <a href={entityHref(w.id)}>{w.label}</a> · {w.activity} · {w.start}
              </div>
            ))}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Downtime, newest first: its reason, how long and the oil lost. */
export function DowntimeList({ rows }: { rows: DowntimeRow[] }) {
  return (
    <ul className="timeline">
      {rows.map((r) => (
        <li key={r.id}>
          <span className="when">{whenDay(r.start)}</span>
          <div className="what">
            <Line>
              <Badge tone="danger">{r.reason ?? "downtime"}</Badge>
              <a href={entityHref(r.id)}>{r.comment}</a>
            </Line>
            <Text as="div" small muted>
              {r.duration_h} h · {count(Math.round(r.lost_oil_bbl ?? 0))} bbl
              {r.reason_check && <ErrorText inline> · {r.reason_check}</ErrorText>}
            </Text>
          </div>
        </li>
      ))}
    </ul>
  );
}
