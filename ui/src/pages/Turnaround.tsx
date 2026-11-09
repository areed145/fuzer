/** Turnaround: one revision's scope read against the integrated plan. What is in it (its SAP orders and their
 * window on the plan), what should be (work that could ride along, tasks that need the unit down but are planned
 * elsewhere, shutdown-only maintenance plans, the worst bad actors), and what is missing (spares out of stock).
 * Planned work is pulled into the window with one button. */
import { useState } from "react";
import { api, type PlanItem } from "../api";
import { Badge, Button, Card, PageHeader, Select, Table } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";
import { usePerson } from "../work";
import { useAct } from "./workflows/shared";

const day = (at: string | null) => (at ? new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : "no date");

export function TurnaroundPage() {
  const [person] = usePerson();
  const [revision, setRevision] = useState("");
  const s = useAsync(() => api.turnaround(revision || undefined), [revision]);
  const { busy, error, run } = useAct();
  if (!s.data) return <Status loading={s.loading} error={s.error} />;
  const t = s.data;
  const bringIn = (i: PlanItem) => person && run("Bringing it in", () => api.turnaroundBringIn(i.id, person, t.revision), s.reload);
  const items = (list: PlanItem[], why: (i: PlanItem) => string) => <ul className="plain">{list.map((i) => <li key={i.id}>
    <strong>{i.title}</strong> <span className="muted small">{i.kind.replace(/_/g, " ")} · {day(i.start_at)} · {why(i)}</span>{" "}
    {person && t.window && <Button size="sm" disabled={!!busy} onClick={() => bringIn(i)}>Bring into {t.revision}</Button>}
  </li>)}</ul>;
  return (
    <section>
      <PageHeader subtitle={<>A turnaround's scope read against the <a href={href("/plan")}>integrated plan</a>: what is in,
        what should be, and what is missing.</>}>Turnaround {t.revision ?? ""}</PageHeader>
      <div className="row">
        {t.revisions.length > 1 && <Select aria-label="Revision" value={t.revision ?? ""} onChange={(e) => setRevision(e.target.value)}>
          {t.revisions.map((r) => <option key={r} value={r}>{r}</option>)}</Select>}
        {t.window ? <Badge>{day(t.window.start)} to {day(t.window.end)}</Badge> : <Badge tone="warn">not on the plan yet</Badge>}
        <Badge>{t.scoped_work.length} orders</Badge>
        {t.ride_along.length > 0 && <Badge tone="ok">{t.ride_along.length} could ride along</Badge>}
        {t.shutdown_outside.length + t.shutdown_plans_not_in_scope.length > 0 &&
          <Badge tone="warn">{t.shutdown_outside.length + t.shutdown_plans_not_in_scope.length} need the unit down, not in scope</Badge>}
        {t.spares_out_of_stock.length > 0 && <Badge tone="danger">{t.spares_out_of_stock.length} spares out of stock</Badge>}
      </div>
      {busy && <p role="status">{busy}…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {!t.revision && <p className="muted">No SAP work order names a revision, so there is no turnaround to show.</p>}
      {t.scoped_work.length > 0 && <Card title="In scope">
        <Table variant="results"><thead><tr><th>Order</th><th>Asset</th><th>Work</th><th>Start</th><th>Status</th></tr></thead>
          <tbody>{t.scoped_work.map((w) => <tr key={w.id}>
            <td><a href={entityHref(w.id)}>{w.order ?? w.id}</a></td>
            <td>{w.tag_id ? <a href={entityHref(w.tag_id)}>{w.tag ?? w.tag_id}</a> : ""}</td>
            <td>{w.text}</td><td className="small">{day(w.planned_start)}</td><td className="small">{w.status}</td>
          </tr>)}</tbody></Table>
      </Card>}
      {t.shutdown_outside.length > 0 && <Card title="Needs the unit down, planned outside it">
        {items(t.shutdown_outside, () => "can only be done with the unit down")}</Card>}
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
      {t.spares_out_of_stock.length > 0 && <Card title="Spares out of stock (order now)">
        <ul className="plain">{t.spares_out_of_stock.map((p, n) => <li key={`${p.id}-${n}`}>
          <a href={entityHref(p.id)}>{p.material ?? p.id}</a> <span className="muted small">for {p.for} · {p.description}</span></li>)}</ul></Card>}
    </section>
  );
}
