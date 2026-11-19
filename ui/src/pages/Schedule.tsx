/** A schedule on the integrated plan (P5.18, #1176): a turnaround, project or campaign as an interactive Gantt board.
 * Activities grouped by work order with their logic drawn (or the same work by resource), dragged to hold a start or
 * stretched to change a duration; a click opens the activity's properties beside the board. Every change recalculates
 * the critical path and the dates levelled against every shared resource; Schedule puts those dates on the plan. */
import { useState } from "react";
import { api, type ScheduleAnalysis, type ScheduleLinkRef, type ScheduleRow } from "../api";
import { Badge, Button, Card, Empty, Field, Input, Notice, PageHeader, Select, Tabs } from "../ui";
import { Status, useAsync, useAction } from "../lib";
import { acronyms, dayStamp, stamp } from "../wording";
import { type BoardChange, type BoardRows, ScheduleBoard } from "./ScheduleBoard";

const TYPES: ScheduleLinkRef["type"][] = ["FS", "SS", "FF", "SF"];
const TYPE_WORD: Record<ScheduleLinkRef["type"], string> = { FS: "finish to start", SS: "start to start", FF: "finish to finish", SF: "start to finish" };
const hours = (n: number | null | undefined) => (n == null ? "" : Number.isInteger(n) ? String(n) : n.toFixed(1));
/** A date for a datetime-local field, in the reader's own time. */
const local = (at: string | Date) => { const d = new Date(at); return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16); };

type Undo = { say: string; back: () => Promise<unknown> };

export function SchedulePage({ code, person, view }: { code: string; person: string | null; view?: string | null }) {
  const data = useAsync(() => api.schedule(code), [code]);
  const list = useAsync(() => api.schedules(), []);
  const rows: BoardRows = view === "resources" ? "resources" : "activities";
  const [zoom, setZoom] = useState(() => (typeof innerWidth === "number" && innerWidth < 600 ? 2 : 1));  // a phone starts zoomed in
  const [selected, setSelected] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [undo, setUndo] = useState<Undo[]>([]);
  const [said, setSaid] = useState<string | null>(null);
  const { busy, error, run } = useAction();
  const s = data.data;
  if (!s) return <Status loading={data.loading} error={data.error} onRetry={data.reload} />;
  const p = s.project;
  const over = s.overruns_window_by_days > 0;
  const here = (v: BoardRows) => `#/plan?schedule=${encodeURIComponent(p.code)}${v === "resources" ? "&view=resources" : ""}`;
  const sel = selected != null ? s.activities.find((a) => a.id === selected) : undefined;
  const apply = () => person && run("Scheduling", () => api.scheduleApply(p.code, person), (r) => {
    setSaid(`These dates are on the plan. Finishes ${stamp(r.finish)}.`);
    data.reload();
  });
  // A change on the board: saved at once, the schedule recalculated, and kept so it can be undone.
  const change = (c: BoardChange) => {
    if (!person) return;
    const a = s.activities.find((x) => x.id === c.id);
    if (!a) return;
    const named = (r: string | null) => (r ? s.resources.find((x) => x.code === r)?.name ?? r : "no shared resource");
    const need = (r: string | null, q: number | null) => (r ? api.scheduleDemand(c.id, person, r, q) : Promise.resolve());
    const [say, forth, back] = c.kind === "link"
      ? [`${c.toCode} now follows ${c.code}.`, () => api.scheduleLink(person, c.id, c.to, "FS", 0), () => api.scheduleUnlink(person, c.id, c.to)] as const
      : c.kind === "reassign"
      ? [`${c.code} now needs ${named(c.to)}, not ${named(c.from)}.`, () => need(c.from, null).then(() => need(c.to, c.quantity)),
         () => need(c.to, null).then(() => need(c.from, c.quantity))] as const
      : c.kind === "stretch"
      ? [`${c.code} now takes ${hours(c.hours)} h.`, () => api.scheduleChange(c.id, person, { duration_hours: c.hours }),
         () => api.scheduleChange(c.id, person, { duration_hours: a.duration_hours ?? 0 })] as const
      : [c.earliest ? `${c.code} starts as early as its logic and crews allow.` : `${c.code} starts no earlier than ${stamp(c.start)}.`,
         () => api.scheduleChange(c.id, person, c.earliest ? { clear_not_before: true } : { not_before: c.start.toISOString() }),
         () => api.scheduleChange(c.id, person, a.not_before ? { not_before: a.not_before } : { clear_not_before: true })] as const;
    run("Rescheduling", forth, () => { setUndo((u) => [...u.slice(-19), { say, back }]); setSaid(say); data.reload(); });
  };
  const undoLast = () => {
    const last = undo[undo.length - 1];
    if (last) run("Undoing", last.back, () => { setUndo((u) => u.slice(0, -1)); setSaid(`Undone: ${last.say}`); data.reload(); });
  };
  const panel = adding || !!sel;
  return (
    <section>
      <PageHeader subtitle={`${p.kind[0].toUpperCase()}${p.kind.slice(1)} · ${dayStamp(p.start_at)}${p.finish_by ? ` to ${dayStamp(p.finish_by)}` : ""}`}
        actions={<>
          <Button variant="primary" disabled={!person || !!busy} title={person ? undefined : "Choose who you are to schedule"}
            onClick={apply}>Schedule</Button>
        </>}>{acronyms(p.title)}</PageHeader>
      <div className="row gb-bar">
        <Tabs label="Rows" items={[{ key: "activities", label: "Activities", href: here("activities"), current: rows === "activities" },
          { key: "resources", label: "Resources", href: here("resources"), current: rows === "resources" }]} />
        {(list.data?.length ?? 0) > 1 && <label className="small">Schedule <Select aria-label="Schedule" value={p.code}
          onChange={(e) => { location.hash = `#/plan?schedule=${encodeURIComponent(e.target.value)}`; }}>
          {list.data!.map((x) => <option key={x.code} value={x.code}>{x.code} {acronyms(x.title).slice(0, 40)}</option>)}
        </Select></label>}
        <label className="small">Zoom <Select aria-label="Zoom" value={String(zoom)} onChange={(e) => setZoom(Number(e.target.value))}>
          <option value="1">Fit</option><option value="2">2×</option><option value="4">4×</option>
        </Select></label>
        {person && <Button size="sm" onClick={() => { setAdding(true); setSelected(null); }}>Add activity</Button>}
        {undo.length > 0 && <Button size="sm" disabled={!!busy} onClick={undoLast}>Undo</Button>}
        <Badge tone={over ? "danger" : "ok"}>Finishes {stamp(s.levelled_finish)}</Badge>
        <Badge tone="danger">{s.critical_path.length} critical</Badge>
        {s.overloads_levelled.length > 0 && <Badge tone="danger">{s.overloads_levelled.length} over capacity</Badge>}
      </div>
      {over && <Notice tone="warn">This runs {s.overruns_window_by_days} days past the window. The finish waits
        on {[...new Set(s.levelled_chain.map((c) => c.waited_for).filter(Boolean))].join(", ") || "the logic"}.</Notice>}
      {error && <p role="alert" className="error">{error}</p>}
      <p role="status" className="small gb-said">{busy ? `${busy}…` : said ?? (person ? rows === "resources" ? "Drag an activity to move it, or onto another row to hand it to that resource." : "Drag an activity to move it, its right edge to change how long it takes. Select one and drag its dot to link it." : "Choose who you are to change the schedule.")}</p>
      <div className={panel ? "plan-layout with-panel" : "plan-layout"}>
        <ScheduleBoard s={s} rows={rows} zoom={zoom} selected={selected} canEdit={!!person && !busy}
          onSelect={(id) => { setSelected(id); if (id != null) setAdding(false); }} onChange={change} />
        {panel && <aside className="plan-panel gb-panel">
          {sel ? <Card title={`${sel.code ?? ""} ${acronyms(sel.name)}`.trim()} actions={<Button size="sm" variant="ghost" onClick={() => setSelected(null)}>Close</Button>}>
            <Editor key={`${sel.id}-${sel.duration_hours}-${sel.not_before ?? ""}`} a={sel} s={s} person={person} onChange={data.reload} />
          </Card> : person && <AddActivity project={p.code} person={person} onClose={() => setAdding(false)} onChange={data.reload} />}
        </aside>}
      </div>
    </section>
  );
}

function Editor({ a, s, person, onChange }: { a: ScheduleRow; s: ScheduleAnalysis; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const [name, setName] = useState(a.name);
  const [dur, setDur] = useState(hours(a.duration_hours));
  const [done, setDone] = useState(hours(a.percent_complete ?? 0));
  const [pred, setPred] = useState("");
  const [type, setType] = useState<ScheduleLinkRef["type"]>("FS");
  const [lag, setLag] = useState("0");
  const [res, setRes] = useState("");
  const [qty, setQty] = useState("1");
  const [removing, setRemoving] = useState(false);
  const [nb, setNb] = useState(a.not_before ? local(a.not_before) : "");
  const byCode = new Map(s.activities.map((x) => [x.code, x.id]));
  const act = (label: string, fn: () => Promise<unknown>) => person && run(label, fn, onChange);
  return (
    <div className="sched-editor">
      <p className="small">{stamp(a.start)} to {stamp(a.finish)}{a.critical ? " · critical" : ` · ${hours(a.float_hours)} h float`}</p>
      <p className="small">
        {a.work_order && <>Order {a.work_order} · </>}{a.assets.filter((x) => x !== a.work_order).join(", ") || "No asset"}
        {" · "}Early {stamp(a.early_start)} to {stamp(a.early_finish)}, late {stamp(a.late_start)} to {stamp(a.late_finish)}
      </p>
      {a.held_back_by && <p className="small">Waits {hours(a.held_back_hours)} h for {a.held_back_by.resource}
        {a.held_back_by.activities.length ? `, in use by ${a.held_back_by.activities.join(", ")}` : ""}.</p>}
      {!person ? <p className="small">Choose who you are to change the schedule.</p> : <>
        <div className="row">
          <Field label="Activity" width="lg"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Hours" width="xs"><Input type="number" min="0" step="any" value={dur} onChange={(e) => setDur(e.target.value)} /></Field>
          <Field label="Done (%)" width="xs"><Input type="number" min="0" max="100" value={done} onChange={(e) => setDone(e.target.value)} /></Field>
          <Button disabled={!!busy || !name.trim() || dur === ""} onClick={() => act("Saving", () => api.scheduleChange(a.id, person, {
            name: name.trim(), duration_hours: Number(dur), percent_complete: Number(done || 0) }))}>Save</Button>
        </div>
        <div className="row">
          <Field label="Start no earlier than" width="md"><Input type="datetime-local" value={nb} onChange={(e) => setNb(e.target.value)} /></Field>
          <Button disabled={!!busy || !nb} onClick={() => act("Saving", () => api.scheduleChange(a.id, person, { not_before: new Date(nb).toISOString() }))}>Hold</Button>
          {a.not_before && <Button variant="ghost" disabled={!!busy} onClick={() => act("Saving", () => api.scheduleChange(a.id, person, { clear_not_before: true }))}>Release</Button>}
        </div>
        <h3>Predecessors</h3>
        {a.predecessors.length ? <ul className="plain">{a.predecessors.map((p) => <li key={p.code}>
          {p.code} {TYPE_WORD[p.type]}{p.lag_hours ? `, lag ${hours(p.lag_hours)} h` : ""}{" "}
          <Button variant="link" size="sm" disabled={!!busy} onClick={() => {
            const id = byCode.get(p.code);
            if (id != null) act("Removing the link", () => api.scheduleUnlink(person, id, a.id));
          }}>Remove</Button></li>)}</ul> : <Empty>Starts when the schedule starts.</Empty>}
        <div className="row">
          <Field label="After" width="sm"><Select value={pred} onChange={(e) => setPred(e.target.value)}>
            <option value="">Choose</option>
            {s.activities.filter((x) => x.id !== a.id && !a.predecessors.some((p) => p.code === x.code))
              .map((x) => <option key={x.id} value={x.id}>{x.code} {acronyms(x.name).slice(0, 32)}</option>)}
          </Select></Field>
          <Field label="Link" width="sm"><Select value={type} onChange={(e) => setType(e.target.value as ScheduleLinkRef["type"])}>
            {TYPES.map((t) => <option key={t} value={t}>{TYPE_WORD[t]}</option>)}
          </Select></Field>
          <Field label="Lag (h)" width="xs"><Input type="number" step="any" value={lag} onChange={(e) => setLag(e.target.value)} /></Field>
          <Button disabled={!!busy || !pred} onClick={() => act("Linking", () => api.scheduleLink(person, Number(pred), a.id, type, Number(lag || 0)))}>Add link</Button>
        </div>
        <h3>Needs</h3>
        {Object.keys(a.needs).length ? <ul className="plain">{Object.entries(a.needs).map(([r, q]) => <li key={r}>
          {s.resources.find((x) => x.code === r)?.name ?? r}: {hours(q)}{" "}
          <Button variant="link" size="sm" disabled={!!busy} onClick={() => act("Removing the need", () => api.scheduleDemand(a.id, person, r, null))}>Remove</Button>
        </li>)}</ul> : <Empty>Needs no shared resource.</Empty>}
        <div className="row">
          <Field label="Resource" width="sm"><Select value={res} onChange={(e) => setRes(e.target.value)}>
            <option value="">Choose</option>
            {s.resources.map((r) => <option key={r.code} value={r.code}>{r.name} ({r.code})</option>)}
          </Select></Field>
          <Field label="Quantity" width="xs"><Input type="number" min="0" step="any" value={qty} onChange={(e) => setQty(e.target.value)} /></Field>
          <Button disabled={!!busy || !res || !Number(qty)} onClick={() => act("Saving the need", () => api.scheduleDemand(a.id, person, res, Number(qty)))}>Set need</Button>
        </div>
        <div className="row">
          {removing ? <>
            <span className="small">Removes {a.code} and its links from the schedule.</span>
            <Button variant="danger" disabled={!!busy} onClick={() => act("Removing", () => api.scheduleRemove(a.id, person))}>Confirm remove</Button>
            <Button variant="ghost" onClick={() => setRemoving(false)}>Cancel</Button>
          </> : <Button variant="ghost" onClick={() => setRemoving(true)}>Remove activity</Button>}
        </div>
      </>}
      {busy && <p role="status">{busy}…</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}

function AddActivity({ project, person, onChange, onClose }: { project: string; person: string; onChange: () => void; onClose: () => void }) {
  const { busy, error, run } = useAction();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [dur, setDur] = useState("");
  const [after, setAfter] = useState("");
  const add = () => run("Adding", () => api.scheduleAdd(project, { by: person, code: code.trim(), name: name.trim(), duration_hours: Number(dur),
    after: after.split(",").map((x) => x.trim()).filter(Boolean) }), () => { setCode(""); setName(""); setDur(""); setAfter(""); onChange(); });
  return (
    <Card title="Add activity" actions={<Button size="sm" variant="ghost" onClick={onClose}>Close</Button>}>
      <div className="row">
        <Field label="Code" width="xs"><Input value={code} onChange={(e) => setCode(e.target.value)} /></Field>
        <Field label="Activity" width="lg"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Hours" width="xs"><Input type="number" min="0" step="any" value={dur} onChange={(e) => setDur(e.target.value)} /></Field>
        <Field label="After (codes)" width="sm"><Input value={after} placeholder="A0100, A0110 SS+4" onChange={(e) => setAfter(e.target.value)} /></Field>
        <Button disabled={!!busy || !code.trim() || !name.trim() || dur === ""} onClick={add}>Add</Button>
      </div>
      {error && <p role="alert" className="error">{error}</p>}
    </Card>
  );
}
