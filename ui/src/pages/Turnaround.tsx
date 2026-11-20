/** Turnaround as a window on the Plan (#506, #554): one revision's scope read against the integrated plan. What is
 * in it (its SAP orders), what should be (work that could ride along, tasks that need the unit down but are planned
 * elsewhere, shutdown-only maintenance plans, the worst bad actors), and what is missing (spares out of stock).
 * Planned work is pulled into the window with one button. When a planning system's schedule names the revision, its
 * S-curve shows progress against the baseline and the slip (#360). `#/turnaround` redirects to `#/plan?revision=…`. */
import { api, type PlanItem, type TurnaroundScope } from "../api";
import { whenDay, count } from "../format";
import { Badge, Button, Card, Empty, ErrorText, Line, Text, List } from "../ui";
import { entityHref, href, useAction } from "../lib";
import { LiveFigure } from "../analytics/LiveFigure";

const day = (at: string | null) => (at ? whenDay(at) : "no date");

export function TurnaroundPanel({ scope: t, person, onChange }: {
  scope: TurnaroundScope; person: string | null; onChange: () => void;
}) {
  const { busy, error, run } = useAction();
  const bringIn = (i: PlanItem) => person && run("Bringing it in", () => api.turnaroundBringIn(i.id, person, t.revision), onChange);
  const items = (list: PlanItem[], why: (i: PlanItem) => string) => <List plain>{list.map((i) => <li key={i.id}>
    <strong>{i.title}</strong> <Text small muted>{i.kind.replace(/_/g, " ")} · {day(i.start_at)} · {why(i)}</Text>{" "}
    {person && t.window && <Button size="sm" disabled={!!busy} aria-label={`Bring into ${t.revision}`} onClick={() => bringIn(i)}>Bring in</Button>}
  </li>)}</List>;
  const unitDown = t.shutdown_outside.length + t.shutdown_plans_not_in_scope.length;
  return (
    <aside className="plan-panel" aria-labelledby="turnaround-heading">
      <h2 id="turnaround-heading">Turnaround {t.revision ?? ""}{" "}
        <a className="small" href={href("/plan")} aria-label="Close the turnaround">Close</a></h2>
      {!t.revision ? <Empty>No work order names a revision.</Empty> : <>
        <Line as="p" small>
          {t.window ? <Badge>{day(t.window.start)} to {day(t.window.end)}</Badge> : <Badge tone="warn">not on the plan yet</Badge>}
          <Badge>{t.scoped_work.length} orders</Badge>
          {t.ride_along.length > 0 && <Badge tone="ok">{t.ride_along.length} could ride along</Badge>}
          {unitDown > 0 && <Badge tone="warn">{unitDown} need the unit down, not in scope</Badge>}
          {t.spares_out_of_stock.length > 0 && <Badge tone="danger">{t.spares_out_of_stock.length} spares out of stock</Badge>}
        </Line>
        {busy && <p role="status">{busy}…</p>}
        <ErrorText>{error}</ErrorText>
        {t.schedule && <Card title="Progress against the baseline">
          <LiveFigure kind="turnaround-progress" params={{ revision: t.revision }} /></Card>}
        {t.scoped_work.length > 0 && <Card title="In scope">
          <List plain>{t.scoped_work.map((w) => <li key={w.id}>
            <a href={entityHref(w.id)}>{w.order ?? w.id}</a> {w.text}{" "}
            <Text small muted>{w.tag_id ? <a href={entityHref(w.tag_id)}>{w.tag ?? w.tag_id}</a> : null}
              {w.tag_id ? " · " : ""}{day(w.planned_start)}{w.status ? ` · ${w.status}` : ""}</Text>
          </li>)}</List>
        </Card>}
        {t.shutdown_outside.length > 0 && <Card title="Needs the unit down, planned outside it">
          {items(t.shutdown_outside, () => "only with the unit down")}</Card>}
        {t.ride_along.length > 0 && <Card title="Could ride along">
          {items(t.ride_along, (i) => i.area_label ? `same area (${i.area_label}), close in time` : "close in time")}</Card>}
        {t.shutdown_plans_not_in_scope.length > 0 && <Card title="Shutdown-only maintenance plans not in scope">
          <List plain>{t.shutdown_plans_not_in_scope.map((p) => <li key={p.id}>
            <a href={entityHref(p.id)}>{p.task ?? p.plan ?? p.id}</a> <Text small muted>{p.tag ?? ""} · due {day(p.next_due)}</Text>
          </li>)}</List></Card>}
        {t.bad_actors_not_in_scope.length > 0 && <Card title="Worst bad actors not in scope">
          <List plain>{t.bad_actors_not_in_scope.map((a) => <li key={a.id}>
            <a href={entityHref(a.id)}>{a.tag ?? a.id}</a> <Text small muted>{a.failures} failures
              {a.lost_oil_bbl ? ` · ${count(Math.round(a.lost_oil_bbl))} bbl lost` : ""}</Text></li>)}</List></Card>}
        {t.spares_out_of_stock.length > 0 && <Card title="Spares out of stock">
          <List plain>{t.spares_out_of_stock.map((p, n) => <li key={`${p.id}-${n}`}>
            <a href={entityHref(p.id)}>{p.material ?? p.id}</a> <Text small muted>for {p.for} · {p.description}</Text></li>)}</List></Card>}
      </>}
    </aside>
  );
}
