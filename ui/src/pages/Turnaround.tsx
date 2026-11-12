/** Turnaround as a window on the Plan (#506, #554): one revision's scope read against the integrated plan. What is
 * in it (its SAP orders), what should be (work that could ride along, tasks that need the unit down but are planned
 * elsewhere, shutdown-only maintenance plans, the worst bad actors), and what is missing (spares out of stock).
 * Planned work is pulled into the window with one button. When a planning system's schedule names the revision, its
 * S-curve shows progress against the baseline and the slip (#360). `#/turnaround` redirects to `#/plan?revision=…`. */
import { api, type PlanItem, type TurnaroundScope } from "../api";
import { Badge, Button, Card, Empty } from "../ui";
import { entityHref, href } from "../lib";
import { useAct } from "./workflows/shared";
import { LiveFigure } from "../analytics/LiveFigure";

const day = (at: string | null) => (at ? new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "no date");

export function TurnaroundPanel({ scope: t, person, onChange }: {
  scope: TurnaroundScope; person: string | null; onChange: () => void;
}) {
  const { busy, error, run } = useAct();
  const bringIn = (i: PlanItem) => person && run("Bringing it in", () => api.turnaroundBringIn(i.id, person, t.revision), onChange);
  const items = (list: PlanItem[], why: (i: PlanItem) => string) => <ul className="plain">{list.map((i) => <li key={i.id}>
    <strong>{i.title}</strong> <span className="muted small">{i.kind.replace(/_/g, " ")} · {day(i.start_at)} · {why(i)}</span>{" "}
    {person && t.window && <Button size="sm" disabled={!!busy} onClick={() => bringIn(i)}>Bring into {t.revision}</Button>}
  </li>)}</ul>;
  const unitDown = t.shutdown_outside.length + t.shutdown_plans_not_in_scope.length;
  return (
    <aside className="plan-panel" aria-labelledby="turnaround-heading">
      <h2 id="turnaround-heading">Turnaround {t.revision ?? ""}{" "}
        <a className="small" href={href("/plan")} aria-label="Close the turnaround">Close</a></h2>
      {!t.revision ? <Empty>No work order names a revision.</Empty> : <>
        <p className="row small">
          {t.window ? <Badge>{day(t.window.start)} to {day(t.window.end)}</Badge> : <Badge tone="warn">not on the plan yet</Badge>}
          <Badge>{t.scoped_work.length} orders</Badge>
          {t.ride_along.length > 0 && <Badge tone="ok">{t.ride_along.length} could ride along</Badge>}
          {unitDown > 0 && <Badge tone="warn">{unitDown} need the unit down, not in scope</Badge>}
          {t.spares_out_of_stock.length > 0 && <Badge tone="danger">{t.spares_out_of_stock.length} spares out of stock</Badge>}
        </p>
        {busy && <p role="status">{busy}…</p>}
        {error && <p role="alert" className="error">{error}</p>}
        {t.schedule && <Card title="Progress against the baseline">
          <LiveFigure kind="turnaround-progress" params={{ revision: t.revision }} /></Card>}
        {t.scoped_work.length > 0 && <Card title="In scope">
          <ul className="plain">{t.scoped_work.map((w) => <li key={w.id}>
            <a href={entityHref(w.id)}>{w.order ?? w.id}</a> {w.text}{" "}
            <span className="muted small">{w.tag_id ? <a href={entityHref(w.tag_id)}>{w.tag ?? w.tag_id}</a> : null}
              {w.tag_id ? " · " : ""}{day(w.planned_start)}{w.status ? ` · ${w.status}` : ""}</span>
          </li>)}</ul>
        </Card>}
        {t.shutdown_outside.length > 0 && <Card title="Needs the unit down, planned outside it">
          {items(t.shutdown_outside, () => "only with the unit down")}</Card>}
        {t.ride_along.length > 0 && <Card title="Could ride along">
          {items(t.ride_along, (i) => i.area_label ? `same area (${i.area_label}), close in time` : "close in time")}</Card>}
        {t.shutdown_plans_not_in_scope.length > 0 && <Card title="Shutdown-only maintenance plans not in scope">
          <ul className="plain">{t.shutdown_plans_not_in_scope.map((p) => <li key={p.id}>
            <a href={entityHref(p.id)}>{p.task ?? p.plan ?? p.id}</a> <span className="muted small">{p.tag ?? ""} · due {day(p.next_due)}</span>
          </li>)}</ul></Card>}
        {t.bad_actors_not_in_scope.length > 0 && <Card title="Worst bad actors not in scope">
          <ul className="plain">{t.bad_actors_not_in_scope.map((a) => <li key={a.id}>
            <a href={entityHref(a.id)}>{a.tag ?? a.id}</a> <span className="muted small">{a.failures} failures
              {a.lost_oil_sm3 ? ` · ${Math.round(a.lost_oil_sm3).toLocaleString()} Sm3 lost` : ""}</span></li>)}</ul></Card>}
        {t.spares_out_of_stock.length > 0 && <Card title="Spares out of stock">
          <ul className="plain">{t.spares_out_of_stock.map((p, n) => <li key={`${p.id}-${n}`}>
            <a href={entityHref(p.id)}>{p.material ?? p.id}</a> <span className="muted small">for {p.for} · {p.description}</span></li>)}</ul></Card>}
      </>}
    </aside>
  );
}
