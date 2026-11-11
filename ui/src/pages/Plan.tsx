/** The integrated plan: every planned activity from every source on one timeline, by area. SAP work orders,
 * turnaround work, inspections and the exceptions the IOC chose to plan sit side by side, so two jobs on one asset,
 * a person booked twice, and work that could ride along with a shutdown all show before they happen. */
import { useMemo, useState } from "react";
import { api, type PlanItem } from "../api";
import { Badge, Button, Card, Input, PageHeader, Select } from "../ui";
import { Status, entityHref, useAsync } from "../lib";
import { usePerson } from "../work";
import { useAct } from "./workflows/shared";

const DAY = 86_400_000;
const STATUSES = ["proposed", "scheduled", "in_progress", "done", "cancelled"];
const day = (d: Date) => d.toISOString().slice(0, 10);
const short = (at: string) => new Date(at).toLocaleDateString(undefined, { day: "numeric", month: "short" });

export function PlanPage() {
  const [person] = usePerson();
  const [from, setFrom] = useState(() => day(new Date(Date.now() - 7 * DAY)));
  const [weeks, setWeeks] = useState(8);
  const to = day(new Date(new Date(from).getTime() + weeks * 7 * DAY));
  const plan = useAsync(() => api.plan({ start: from, end: to }), [from, to]);
  const { busy, error, run } = useAct();
  const [open, setOpen] = useState<number | null>(null);
  const groups = useMemo(() => {
    const out = new Map<string, PlanItem[]>();
    for (const i of plan.data ?? []) {
      const k = i.area_label ?? (i.area_id ? i.area_id : "No area");
      out.set(k, [...(out.get(k) ?? []), i]);
    }
    return [...out.entries()].sort(([a], [b]) => (a === "No area" ? 1 : b === "No area" ? -1 : a.localeCompare(b)));
  }, [plan.data]);
  if (!plan.data) return <Status loading={plan.loading} error={plan.error} />;
  const t0 = new Date(from).getTime(), t1 = new Date(to).getTime();
  const pos = (at: string) => Math.min(100, Math.max(0, ((new Date(at).getTime() - t0) / (t1 - t0)) * 100));
  const clashes = plan.data.filter((i) => i.clashes.some((c) => c.kind !== "bundle")).length;
  const bundles = plan.data.filter((i) => i.clashes.some((c) => c.kind === "bundle")).length;
  const waiting = plan.data.filter((i) => i.parts_short?.length).length;
  const today = pos(new Date().toISOString());
  return (
    <section>
      <PageHeader subtitle="Planned activity from every source on one timeline."
        actions={<Button disabled={!!busy} onClick={() => run("Refreshing from SAP", () => api.planSync(), plan.reload)}>Refresh from SAP</Button>}>
        Integrated plan</PageHeader>
      <div className="row">
        <label className="small">From <Input type="date" value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} /></label>
        <Select aria-label="Weeks shown" value={String(weeks)} onChange={(e) => setWeeks(Number(e.target.value))}>
          {[2, 4, 8, 12, 26].map((w) => <option key={w} value={w}>{w} weeks</option>)}
        </Select>
        <Badge>{plan.data.length} items</Badge>
        {clashes > 0 && <Badge tone="danger">{clashes} with a clash</Badge>}
        {bundles > 0 && <Badge tone="ok">{bundles} could ride along with a shutdown</Badge>}
        {waiting > 0 && <Badge tone="warn">{waiting} waiting on parts</Badge>}
      </div>
      {busy && <p role="status">{busy}…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {!plan.data.length && <p className="muted">Nothing planned in these weeks.</p>}
      {groups.map(([area, list]) => (
        <Card key={area} title={area} className="plan-area">
          <ul className="plan-rows">{list.map((i) => <li key={i.id} className={`plan-row status-${i.status}`}>
            <button type="button" className="plan-label" aria-expanded={open === i.id} onClick={() => setOpen(open === i.id ? null : i.id)}>
              <strong>{i.title}</strong>
              <span className="muted small"> {i.kind.replace(/_/g, " ")} · {short(i.start_at)}–{short(i.end_at)}{i.crew ? ` · ${i.crew}` : ""}</span>
              {i.clashes.filter((c) => c.kind !== "bundle").length > 0 && <Badge tone="danger">clash</Badge>}
              {i.clashes.some((c) => c.kind === "bundle") && <Badge tone="ok">bundle</Badge>}
              {i.needs_shutdown && <Badge tone="warn">shutdown</Badge>}
              {!!i.parts_short?.length && <Badge tone="warn">parts short</Badge>}
            </button>
            <div className="plan-track" aria-hidden="true">
              {today > 0 && today < 100 && <span className="plan-today" style={{ left: `${today}%` }} />}
              <span className={`plan-bar kind-${i.kind}${i.needs_shutdown ? " shutdown" : ""}`}
                style={{ left: `${pos(i.start_at)}%`, width: `${Math.max(0.8, pos(i.end_at) - pos(i.start_at))}%` }} />
            </div>
            {open === i.id && <Detail item={i} person={person} onChange={plan.reload} />}
          </li>)}</ul>
        </Card>
      ))}
    </section>
  );
}

function Detail({ item, person, onChange }: { item: PlanItem; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const [start, setStart] = useState(item.start_at.slice(0, 10));
  const [end, setEnd] = useState(item.end_at.slice(0, 10));
  const [crew, setCrew] = useState(item.crew ?? "");
  const change = (c: Parameters<typeof api.planChange>[2]) => person && run("Saving", () => api.planChange(item.id, person, c), onChange);
  return (
    <div className="plan-detail">
      {item.clashes.map((c, n) => <p key={n} className={`small ${c.kind === "bundle" ? "" : "error"}`}>
        {c.kind === "bundle" ? "Bundle: " : "Clash: "}{c.say}</p>)}
      {!!item.parts_short?.length && <p className="small">
        Parts with no stock on record (check with stores before it starts): {item.parts_short.map((p) =>
          `${p.label ?? p.id}${p.description ? ` (${p.description})` : ""}`).join(", ")}</p>}
      <p className="small">{item.assets.map((a) => <a key={a.fuzer_id} href={entityHref(a.fuzer_id)}>{a.label ?? a.fuzer_id} </a>)}</p>
      <p className="small muted">{item.source}{item.source_key ? ` ${item.source_key}` : ""}{item.notes ? ` · ${item.notes}` : ""}
        {item.deferment_usd != null ? ` · $${Math.round(item.deferment_usd).toLocaleString()} at risk` : ""}</p>
      {person ? <div className="row">
        <label className="small">Start <Input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="small">End <Input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
        <label className="small">Crew <Input value={crew} placeholder="person or role:…" onChange={(e) => setCrew(e.target.value)} /></label>
        <Button size="sm" disabled={!!busy} onClick={() => change({ start, end: end < start ? start : end, crew })}>Reschedule</Button>
        <Select aria-label="Status" value={item.status} disabled={!!busy} onChange={(e) => change({ status: e.target.value })}>
          {STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
        </Select>
      </div> : <p className="muted small">Choose who you are to change the plan.</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}
