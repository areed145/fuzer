/** A schedule on the integrated plan (P5.18, #1176): a turnaround, project or campaign laid out like a planner's
 * scheduling tool. Activities with durations, logic and the resources they need; the critical path and float from
 * the logic alone; and dates levelled against every shared resource, other planned work on them included. A planner
 * edits an activity in place and the schedule recalculates; Schedule puts the levelled dates on the plan. */
import { type CSSProperties, useMemo, useState } from "react";
import { api, type ScheduleAnalysis, type ScheduleDay, type ScheduleLinkRef, type ScheduleRow } from "../api";
import { Badge, Button, ButtonLink, Card, Empty, Field, Input, Notice, PageHeader, Select, Table } from "../ui";
import { Status, useAsync } from "../lib";
import { acronyms, dayStamp, stamp } from "../wording";
import { useAct } from "./workflows/shared";

type Dates = "levelled" | "early";
const TYPES: ScheduleLinkRef["type"][] = ["FS", "SS", "FF", "SF"];
const TYPE_WORD: Record<ScheduleLinkRef["type"], string> = { FS: "finish to start", SS: "start to start", FF: "finish to finish", SF: "start to finish" };
const hours = (n: number | null | undefined) => (n == null ? "" : Number.isInteger(n) ? String(n) : n.toFixed(1));
const link = (p: ScheduleLinkRef) => `${p.code}${p.type === "FS" ? "" : ` ${p.type}`}${p.lag_hours ? ` ${p.lag_hours > 0 ? "+" : ""}${hours(p.lag_hours)}h` : ""}`;
const needs = (n: Record<string, number>) => Object.entries(n).map(([r, q]) => `${r} ${hours(q)}`).join(", ");
/** "Apr 8 06:00": a grid date short enough to leave room for the bars. */
const when = (at: string) => new Date(at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const span = (a: ScheduleRow, dates: Dates) => (dates === "levelled" ? [a.start, a.finish] : [a.early_start, a.early_finish]);

export function SchedulePage({ code, person }: { code: string; person: string | null }) {
  const data = useAsync(() => api.schedule(code), [code]);
  const list = useAsync(() => api.schedules(), []);
  const [dates, setDates] = useState<Dates>("levelled");
  const [open, setOpen] = useState<number | null>(null);
  const { busy, error, run } = useAct();
  const [applied, setApplied] = useState<string | null>(null);
  const s = data.data;
  if (!s) return <Status loading={data.loading} error={data.error} onRetry={data.reload} />;
  const p = s.project;
  const over = s.overruns_window_by_days > 0;
  const apply = () => person && run("Scheduling", () => api.scheduleApply(p.code, person), (r) => {
    setApplied(`Levelled dates are on the plan. Finishes ${stamp(r.finish)}.`);
    data.reload();
  });
  return (
    <section>
      <PageHeader subtitle={`${p.kind[0].toUpperCase()}${p.kind.slice(1)} · ${dayStamp(p.start_at)}${p.finish_by ? ` to ${dayStamp(p.finish_by)}` : ""}`}
        actions={<>
          <ButtonLink href="#/plan">Plan</ButtonLink>
          <Button variant="primary" disabled={!person || !!busy} title={person ? undefined : "Choose who you are to schedule"}
            onClick={apply}>Schedule</Button>
        </>}>{acronyms(p.title)}</PageHeader>
      <div className="row">
        {(list.data?.length ?? 0) > 1 && <label className="small">Schedule <Select aria-label="Schedule" value={p.code}
          onChange={(e) => { location.hash = `#/plan?schedule=${encodeURIComponent(e.target.value)}`; }}>
          {list.data!.map((x) => <option key={x.code} value={x.code}>{x.code}</option>)}
        </Select></label>}
        <label className="small">Dates <Select aria-label="Dates" value={dates} onChange={(e) => setDates(e.target.value as Dates)}>
          <option value="levelled">Levelled to resources</option>
          <option value="early">Early (logic only)</option>
        </Select></label>
        <Badge tone={over ? "danger" : "ok"}>Finishes {stamp(s.levelled_finish)}</Badge>
        <Badge>Logic alone {stamp(s.unconstrained_finish)}</Badge>
        <Badge tone="danger">{s.critical_path.length} critical</Badge>
        {s.overloads_if_unlevelled.length > 0 && <Badge>{s.overloads_if_unlevelled.length} overloads levelled out</Badge>}
      </div>
      {over && <Notice tone="warn">Levelled, this runs {s.overruns_window_by_days} days past the window. The finish waits
        on {[...new Set(s.levelled_chain.map((c) => c.waited_for).filter(Boolean))].join(", ") || "the logic"}.</Notice>}
      {busy && <p role="status">{busy}…</p>}
      {error && <p role="alert" className="error">{error}</p>}
      {applied && !busy && <p role="status">{applied}</p>}
      <Activities s={s} dates={dates} open={open} setOpen={setOpen} person={person} onChange={data.reload} />
      {person && <AddActivity project={p.code} person={person} onChange={data.reload} />}
      <Resources s={s} dates={dates} />
    </section>
  );
}

/** The activity grid with a bar per activity on one timeline, like a scheduling tool's Gantt view. */
function Activities({ s, dates, open, setOpen, person, onChange }: {
  s: ScheduleAnalysis; dates: Dates; open: number | null; setOpen: (id: number | null) => void; person: string | null; onChange: () => void;
}) {
  const { t0, t1, ticks } = useMemo(() => {
    const all = s.activities.flatMap((a) => [a.start, a.finish, a.early_start, a.late_finish]).map((x) => new Date(x).getTime());
    if (s.project.finish_by) all.push(new Date(s.project.finish_by).getTime());
    const t0 = Math.min(...all), t1 = Math.max(...all, t0 + 3_600_000);
    const day = 86_400_000, step = Math.max(1, Math.ceil((t1 - t0) / day / 6)) * day;
    const first = Math.ceil(t0 / day) * day, ticks: number[] = [];
    for (let t = first; t < t1; t += step) ticks.push(t);
    return { t0, t1, ticks };
  }, [s]);
  const pct = (at: string | number) => ((new Date(at).getTime() - t0) / (t1 - t0)) * 100;
  const bar = (from: string, to: string): CSSProperties => ({ left: `${pct(from)}%`, width: `${Math.max(0.6, pct(to) - pct(from))}%` });
  const at = (t: string | number): CSSProperties => ({ left: `${pct(t)}%` });
  const ends = s.project.finish_by;
  return (
    <Card title="Activities" className="sched-card">
      <Table density="compact" className="sched-grid">
        <thead><tr>
          <th>Code</th><th>Activity</th><th>Hours</th><th className="sched-dates">Start</th><th className="sched-dates">Finish</th><th>Float (h)</th><th className="sched-more">Predecessors</th><th className="sched-more">Needs</th>
          <th className="sched-axis-cell"><span className="sched-axis" aria-hidden="true">
            {ticks.map((t) => <span key={t} className="sched-tick" style={at(t)}>{dayStamp(new Date(t).toISOString())}</span>)}
          </span><span className="sr-only">Timeline</span></th>
        </tr></thead>
        <tbody>{s.activities.map((a) => {
          const [from, to] = span(a, dates);
          return [
            <tr key={a.id} className={`${a.critical ? "sched-critical" : ""} status-${a.status}`}>
              <td><Button variant="link" aria-expanded={open === a.id} onClick={() => setOpen(open === a.id ? null : a.id)}>{a.code ?? a.id}</Button></td>
              <td>{acronyms(a.name)}{a.critical && <> <Badge tone="danger">critical</Badge></>}
                {a.held_back_hours > 0 && <> <Badge tone="warn">waits {hours(a.held_back_hours)} h</Badge></>}</td>
              <td>{hours(a.duration_hours)}</td>
              <td className="sched-dates">{when(from)}</td>
              <td className="sched-dates">{when(to)}</td>
              <td>{hours(a.float_hours)}</td>
              <td className="sched-more">{a.predecessors.map(link).join(", ")}</td>
              <td className="sched-more">{needs(a.needs)}</td>
              <td className="sched-track-cell" data-label=""><span className="sched-track" aria-hidden="true">
                {ends && <span className="sched-window-end" style={at(ends)} />}
                {dates === "early" && a.float_hours > 0 && <span className="sched-float" style={bar(a.early_finish, a.late_finish)} />}
                <span className={`sched-bar${a.critical ? " critical" : ""}`} style={bar(from, to)} />
              </span></td>
            </tr>,
            open === a.id && <tr key={`${a.id}-edit`} className="sched-edit-row"><td colSpan={9}>
              <Editor a={a} s={s} person={person} onChange={onChange} />
            </td></tr>,
          ];
        })}</tbody>
      </Table>
      {!s.activities.length && <Empty>No activities yet.</Empty>}
    </Card>
  );
}

function Editor({ a, s, person, onChange }: { a: ScheduleRow; s: ScheduleAnalysis; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const [name, setName] = useState(a.name);
  const [dur, setDur] = useState(hours(a.duration_hours));
  const [done, setDone] = useState(hours(a.percent_complete ?? 0));
  const [pred, setPred] = useState("");
  const [type, setType] = useState<ScheduleLinkRef["type"]>("FS");
  const [lag, setLag] = useState("0");
  const [res, setRes] = useState("");
  const [qty, setQty] = useState("1");
  const [removing, setRemoving] = useState(false);
  const byCode = new Map(s.activities.map((x) => [x.code, x.id]));
  const act = (label: string, fn: () => Promise<unknown>) => person && run(label, fn, onChange);
  return (
    <div className="sched-editor">
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

function AddActivity({ project, person, onChange }: { project: string; person: string; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [dur, setDur] = useState("");
  const [after, setAfter] = useState("");
  const add = () => run("Adding", () => api.scheduleAdd(project, { by: person, code: code.trim(), name: name.trim(), duration_hours: Number(dur),
    after: after.split(",").map((x) => x.trim()).filter(Boolean) }), () => { setCode(""); setName(""); setDur(""); setAfter(""); onChange(); });
  return (
    <Card title="Add activity">
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

/** A histogram per resource: each day's peak use (this schedule, then other planned work) against its capacity. */
function Resources({ s, dates }: { s: ScheduleAnalysis; dates: Dates }) {
  const profiles = s.profiles?.[dates === "levelled" ? "levelled" : "unlevelled"] ?? {};
  const codes = Object.keys(profiles);
  if (!codes.length) return null;
  return (
    <Card title="Resources" className="sched-resources">
      <div className="sched-histograms">{codes.map((r) => {
        const days = profiles[r];
        const info = s.resources.find((x) => x.code === r);
        const use = (d: ScheduleDay) => d.schedule + d.other;
        const top = Math.max(1, ...days.map(use), ...days.map((d) => d.capacity ?? 0));
        const worst = days.reduce((m, d) => (use(d) - (d.capacity ?? Infinity) > use(m) - (m.capacity ?? Infinity) ? d : m), days[0]);
        const overDays = days.filter((d) => d.capacity != null && use(d) > d.capacity + 1e-6).length;
        const h = (n: number): CSSProperties => ({ height: `${(n / top) * 100}%` });
        const cap = (d: ScheduleDay): CSSProperties => ({ bottom: `${((d.capacity ?? 0) / top) * 100}%` });
        return <figure key={r} className="sched-histogram">
          <figcaption><strong>{info?.name ?? r}</strong> <span className="small">{r} · capacity {hours(info?.capacity)}{info?.unit ? ` ${info.unit}` : ""}</span>
            {overDays > 0 && <> <Badge tone="danger">over on {overDays} {overDays === 1 ? "day" : "days"}</Badge></>}</figcaption>
          <div className="sched-cols" role="img" aria-label={`${info?.name ?? r}: peak ${hours(use(worst))} against ${hours(worst.capacity)} on ${dayStamp(worst.day)}`}>
            {days.map((d) => <span key={d.day} className={`sched-col${d.capacity != null && use(d) > d.capacity + 1e-6 ? " over" : ""}`}
              title={`${dayStamp(d.day)}: ${hours(d.schedule)} this schedule${d.other ? `, ${hours(d.other)} other work` : ""}, capacity ${hours(d.capacity)}`}>
              {d.other > 0 && <span className="sched-col-other" style={h(d.other)} />}
              <span className="sched-col-mine" style={h(d.schedule)} />
              {d.capacity != null && <span className="sched-cap" style={cap(d)} />}
            </span>)}
          </div>
          <div className="sched-cols-axis small"><span>{dayStamp(days[0].day)}</span><span>{dayStamp(days[days.length - 1].day)}</span></div>
        </figure>;
      })}</div>
    </Card>
  );
}
