/** Shared resources for their owners (P5.18): each crew, crane, bed or seat's demand from every source on the plan,
 * day by day against its capacity, each overload with the jobs in it and the move that clears it at the least cost,
 * and the owner's capacity and capacity windows (leave, a certification lapsing, a vessel in dock). */
import { type CSSProperties, useState } from "react";
import { isoDay, when, whenDay } from "../format";
import { api, type ResourceLoad, type ResourceMove, type ResourceOverload } from "../api";
import { Badge, Button, ButtonLink, Card, Empty, Field, Input, PageHeader, Select, ErrorText, Line, Text, List, Heading } from "../ui";
import { Status, useAsync, useAction } from "../lib";
import { acronyms } from "../wording";

const DAY = 86_400_000;
const num = (n: number | null | undefined) => (n == null ? "" : Number.isInteger(n) ? String(n) : n.toFixed(1));

export function ResourcesPage({ query, person }: { query: URLSearchParams; person: string | null }) {
  const pick = query.get("resource") || "";
  const [from, setFrom] = useState(() => isoDay());
  const [days, setDays] = useState(42);
  const load = useAsync(() => api.resourceLoad({ resource: pick || undefined, start: from, days }), [pick, from, days]);
  const all = useAsync(() => api.planResources(), []);
  const go = (code: string) => { location.hash = `#/plan?resources=1${code ? `&resource=${encodeURIComponent(code)}` : ""}`; };
  return (
    <section>
      <PageHeader>Resources</PageHeader>
      <Line>
        <Field inline label="Resource"><Select aria-label="Resource" value={pick} onChange={(e) => go(e.target.value)}>
          <option value="">All</option>
          {(all.data ?? []).map((r) => <option key={r.code} value={r.code}>{r.name} ({r.code})</option>)}
        </Select></Field>
        <Field inline label="From"><Input type="date" value={from} onChange={(e) => e.target.value && setFrom(e.target.value)} /></Field>
        <Field inline label="Showing"><Select aria-label="Days shown" value={String(days)} onChange={(e) => setDays(Number(e.target.value))}>
          {[14, 28, 42, 90].map((d) => <option key={d} value={d}>{d} days</option>)}
        </Select></Field>
      </Line>
      {!load.data ? <Status of={load} /> : <>
        {!load.data.resources.length && <Empty>No shared resources yet.</Empty>}
        {load.data.resources.map((r) => <ResourceCard key={r.code} r={r} person={person} onChange={load.reload} />)}
      </>}
      {person && !pick && <AddResource person={person} known={(all.data ?? []).filter((r) => !r.counts?.length).map((r) => r.code)}
        onChange={() => { all.reload(); load.reload(); }} />}
    </section>
  );
}

function ResourceCard({ r, person, onChange }: { r: ResourceLoad; person: string | null; onChange: () => void }) {
  const [day, setDay] = useState<string | null>(null);
  const over = r.days.filter((d) => d.demand > d.capacity + 1e-6).length;
  // headroom above the tallest of demand and capacity, so the capacity line shows over a full column
  const top = 1.15 * Math.max(1, ...r.days.map((d) => Math.max(d.demand, d.capacity)));
  const along = (n: number) => `${(n * 100) / top}%`;
  const h = (n: number): CSSProperties => ({ height: along(n) });
  const cap = (n: number): CSSProperties => ({ bottom: along(n) });
  const color = (s: string) => `load-src-${Math.max(0, r.sources.indexOf(s)) % 6}`;
  const picked = r.days.find((d) => d.day === day);
  // Nothing on the plan uses it in this window: say so, not "within capacity" over an empty chart (red team #36).
  const idle = !r.routine_load && r.days.every((d) => d.demand <= 1e-6);
  return (
    <Card title={`${r.name} (${r.code})`} className="load-card"
      actions={<Text small>capacity {num(r.capacity)} {r.unit ?? ""}{r.work_center ? ` · work center ${r.work_center}` : ""}</Text>}>
      <Line>
        {over > 0 ? <Badge tone="danger">over capacity on {over} {over === 1 ? "day" : "days"}</Badge>
          : idle ? <Badge tone="muted">not used in this window</Badge> : <Badge tone="ok">within capacity</Badge>}
        {r.sources.map((s) => <span key={s} className="load-legend small"><span className={`load-swatch ${color(s)}`} />{s}</span>)}
      </Line>
      {r.counts.length > 0 && <Text as="p" small>{r.kind === "seats" ? `Counts ${r.counts.join(", ")} flying out for their first job on a schedule and back after their last.`
        : `Counts everyone working on ${r.counts.join(", ")}.`}</Text>}
      {r.kind === "capacity" && <Text as="p" small>Counts what is online against what the system can handle: every well at its latest test rate, less the wells behind a unit that a job has down, plus what a job brings on from the day it finishes.</Text>}
      {r.routine_load && <Text as="p" small>{r.routine_load.source} hold {num(r.routine_load.quantity)} the whole time: {r.routine_load.basis}.</Text>}
      {idle ? <Text as="p" small>No job on the plan needs it from {whenDay(r.days[0]?.day ?? "")} to {whenDay(r.days[r.days.length - 1]?.day ?? "")}.</Text> : <>
      <div className="load-cols" role="img" aria-label={`${r.name}: ${over ? `over capacity on ${over} days` : "within capacity"}`}>
        {r.days.map((d) => <span role="button" tabIndex={0} key={d.day} aria-pressed={day === d.day} aria-label={`${whenDay(d.day)}: ${num(d.demand)} of ${num(d.capacity)}`}
          className={`load-col${d.demand > d.capacity + 1e-6 ? " over" : ""}${day === d.day ? " on" : ""}`} onClick={() => setDay(day === d.day ? null : d.day)}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setDay(day === d.day ? null : d.day); } }}>
          {r.sources.filter((s) => d.by_source[s]).map((s) => <span key={s} className={`load-part ${color(s)}`} style={h(d.by_source[s])} />)}
          <span className="load-cap" style={cap(d.capacity)} />
        </span>)}
      </div>
      <div className="load-axis small"><span>{whenDay(r.days[0]?.day ?? "")}</span><span>{whenDay(r.days[r.days.length - 1]?.day ?? "")}</span></div>
      </>}
      {picked && <Text as="p" small>{whenDay(picked.day)}: {num(picked.demand)} needed at the busiest hour against {num(picked.capacity)}
        {Object.keys(picked.by_source).length ? ` (${Object.entries(picked.by_source).map(([s, q]) => `${s} ${num(q)}`).join(", ")})` : ""}.</Text>}
      {r.overloads.length > 0 && <>
        <Heading level={3}>Overloads</Heading>
        <ul className="plain load-overloads">{r.overloads.map((o) => <Overload key={o.from} o={o} unit={r.unit} person={person} onChange={onChange} />)}</ul>
      </>}
      <Capacity r={r} person={person} onChange={onChange} />
    </Card>
  );
}

function Overload({ o, unit, person, onChange }: { o: ResourceOverload; unit: string | null; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const move = (m: ResourceMove) => person && run("Moving", () => api.planChange(m.item_id, person, { start: m.new_start, end: m.new_end }), onChange);
  return (
    <li>
      <strong>{when(o.from)} to {when(o.to)}</strong>{" "}
      <span>needs {num(o.peak)} against {num(o.capacity)}{unit ? ` ${unit}` : ""}</span> <Badge tone="danger">{num(o.over_by)} over</Badge>
      <Text as="p" small>In use by {o.jobs.map((j) => `${j.code ?? ""}${j.code ? " " : ""}${acronyms(j.title)} (${num(j.quantity)})`).join("; ")}.</Text>
      {o.moves.length ? <List plain>{o.moves.map((m) => <li key={m.item_id} className="small load-move">
        Slip {m.code ? `${m.code} ` : ""}{acronyms(m.title)} {num(m.slip_hours)} h to {when(m.new_start)}
        {m.finish_later_days == null ? ", no schedule logic behind it" : m.finish_later_days > 0
          ? `, ${m.source} finishes ${num(m.finish_later_days)} days later` : `, within its float (${num(m.float_hours)} h)`}{" "}
        {m.source && m.finish_later_days != null
          ? <ButtonLink size="sm" variant="link" href={`#/plan?schedule=${encodeURIComponent(m.source)}`}>Open schedule</ButtonLink>
          : <Button size="sm" variant="link" disabled={!person || !!busy} onClick={() => move(m)}>Move</Button>}
      </li>)}</List> : <Text as="p" small>No single job clears it: raise capacity or split the work.</Text>}
      <ErrorText>{error}</ErrorText>
    </li>
  );
}

function Capacity({ r, person, onChange }: { r: ResourceLoad; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const [base, setBase] = useState(num(r.capacity));
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [editing, setEditing] = useState(false);
  const add = () => person && run("Saving", () => api.capacityWindow(r.code, { by: person, start: new Date(start).toISOString(),
    end: new Date(end).toISOString(), capacity: Number(qty), reason: reason || undefined }), () => { setStart(""); setEnd(""); setQty(""); setReason(""); onChange(); });
  return (
    <>
      <Heading level={3}>Capacity</Heading>
      {r.capacity_windows.length ? <List plain>{r.capacity_windows.map((w) => <li key={w.id} className="small">
        {when(w.start)} to {when(w.end)}: {num(w.capacity)}{w.reason ? ` (${w.reason})` : ""}{" "}
        {person && <Button size="sm" variant="link" disabled={!!busy} onClick={() => run("Removing", () => api.capacityWindowRemove(w.id, person), onChange)}>Remove</Button>}
      </li>)}</List> : <Empty>{`${num(r.capacity)} every day.`}</Empty>}
      {!person ? <Text as="p" small>Choose who you are to change capacity.</Text>
        : !editing ? <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>Change capacity</Button> : <>
        <Line>
          <Field label="Usual capacity" width="xs"><Input type="number" min="0" step="any" value={base} onChange={(e) => setBase(e.target.value)} /></Field>
          <Button disabled={!!busy || base === "" || Number(base) === r.capacity}
            onClick={() => run("Saving", () => api.planResourceChange(r.code, person, { capacity: Number(base) }), onChange)}>Save</Button>
        </Line>
        <Line>
          <Field label="From" width="sm"><Input type="datetime-local" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="To" width="sm"><Input type="datetime-local" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          <Field label="Capacity" width="xs"><Input type="number" min="0" step="any" value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
          <Field label="Reason" width="md"><Input value={reason} placeholder="leave, certification, in dock" onChange={(e) => setReason(e.target.value)} /></Field>
          <Button disabled={!!busy || !start || !end || qty === "" || new Date(end).getTime() - new Date(start).getTime() < DAY / 24}
            onClick={add}>Set for a while</Button>
        </Line>
        {r.kind === "crew" && <Line>
          <Field label="Routine duties" width="md"><Select value={r.routine ?? ""} disabled={!!busy}
            onChange={(e) => run("Saving", () => api.planResourceChange(r.code, person, { routine: e.target.value }), onChange)}>
            <option value="">None</option><option value="rounds">Operator rounds and checks</option>
          </Select></Field>
        </Line>}
        <Button variant="ghost" size="sm" onClick={() => setEditing(false)}>Done</Button>
      </>}
      <ErrorText>{error}</ErrorText>
    </>
  );
}

const KINDS: [string, string, string][] = [["crew", "Crew", "people"], ["beds", "Bed space", "beds"], ["seats", "Helicopter or boat seats", "seats"],
  ["vessel", "Vessel", "vessels"], ["crane", "Crane", "cranes"], ["deck", "Deck space", "sq ft"], ["capacity", "Production capacity", "BOE/d"], ["other", "Other", "units"]];

/** A new shared resource to plan against: a crew, bed space, seats, a vessel, a crane, deck space or production capacity. */
function AddResource({ person, known, onChange }: { person: string; known: string[]; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState("crew");
  const [cap, setCap] = useState("");
  const [counts, setCounts] = useState("");
  const unit = KINDS.find((k) => k[0] === kind)?.[2] ?? "units";
  const list = counts.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean);
  const unknown = list.filter((c) => !known.includes(c));
  const add = () => run("Adding", () => api.planResourceChange(code.trim().toUpperCase(), person, { name: name.trim(), kind, unit, capacity: Number(cap), counts: list }),
    () => { setCode(""); setName(""); setCap(""); setCounts(""); setOpen(false); onChange(); });
  if (!open) return <Button variant="ghost" onClick={() => setOpen(true)}>Add a resource</Button>;
  return (
    <Card title="Add a resource" actions={<Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Close</Button>}>
      <Line>
        <Field label="Kind" width="md"><Select value={kind} onChange={(e) => setKind(e.target.value)}>
          {KINDS.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </Select></Field>
        <Field label="Code" width="xs"><Input value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        <Field label="Name" width="lg"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={`Capacity (${unit})`} width="sm"><Input type="number" min="0" step="any" value={cap} onChange={(e) => setCap(e.target.value)} /></Field>
        {(kind === "beds" || kind === "seats") && <Field label="Counts the people on" width="md">
          <Input value={counts} placeholder={known.slice(0, 3).join(", ")} onChange={(e) => setCounts(e.target.value)} /></Field>}
        <Button disabled={!!busy || !code.trim() || !name.trim() || cap === "" || unknown.length > 0} onClick={add}>Add</Button>
      </Line>
      {unknown.length > 0 && <ErrorText>No resource {unknown.join(", ")}.</ErrorText>}
      <ErrorText>{error}</ErrorText>
    </Card>
  );
}
