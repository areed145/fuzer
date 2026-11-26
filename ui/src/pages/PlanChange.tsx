import { useState, type CSSProperties } from "react";
import { Badge, Button, Card as UICard, ErrorText, Heading, Input, Line, List, Select, Table, Text, Textarea, confirmDialog, promptDialog } from "../ui";
import { count, money, quantity, when, whenDay } from "../format";
import { api, type OutageWindow, type PlanAssign, type PlanChange, type PlanChanged, type PlanKpis, type PlanMove, type PlanNotice } from "../api";
import { entityHref, Status, useAction, useAsync } from "../lib";
import { DowntimeList, NotificationList } from "../Maintenance";
import { Actor } from "../work";
import { caseHref } from "./workflows/shared";

/** A bar's place on the diff's track, as a share of its span (CSS percentages, not a reading). */
const box = (from: number, to: number, t0: number, span: number): CSSProperties => {
  const at = (100 * (from - t0)) / span, wide = Math.max(0.8, (100 * (to - from)) / span);
  return { left: `${at}%`, width: `${wide}%` };
};
const ms = (at: string) => new Date(at).getTime();

/** A plan change on its case (P5.21): what broke, the plan levelled again around it as a before and after Gantt with
 * the reason for each move, the finish, deferred oil and overloads before and after, and the constraints the planner
 * types to work it out again. */
export function CasePlanChange({ id, person, readOnly }: { id: number; person: string; readOnly: boolean }) {
  const pc = useAsync(() => api.planChangeOf(id), [id]);
  if (!pc.data) return <Status of={pc} />;
  return <PlanChangeCard c={pc.data} person={person} readOnly={readOnly} put={pc.put} />;
}

function PlanChangeCard({ c, person, readOnly, put }:
  { c: PlanChange; person: string; readOnly: boolean; put: (c: PlanChange) => void }) {
  const [text, setText] = useState("");
  const a = useAction();
  const p = c.proposal;
  const edit = !readOnly && c.open;
  const assign = p?.assign;
  const windows = p?.windows;
  const booked = assign?.filter((x) => x.crew).length ?? 0;
  const done = c.state === "approved" || c.state === "reverted";
  return (
    <UICard title="Plan change" className="replan">
      <Text as="p">{c.say}: {c.constraint}.</Text>
      {assign && <Assign c={c} lines={assign} edit={edit} person={person} put={put} />}
      {windows && <Windows c={c} windows={windows} edit={edit} person={person} put={put} />}
      {p && !assign && <>
        {p.kpis && <Kpis before={p.kpis.before} after={p.kpis.after} />}
        {p.moves.length ? <Diff moves={p.moves} /> : <Text as="p">Nothing on the plan moves.</Text>}
        {p.pull_forward.length > 0 && <>
          <Text as="p" small>Worth pulling forward into the window:</Text>
          <List small>{p.pull_forward.map((f) => <li key={f.item_id}>{f.title}{f.order && <> ({f.order})</>},
            planned {when(f.start)}: {f.because}</li>)}</List>
        </>}
        <Text as="p" small>{p.basis}.</Text>
      </>}
      {c.constraints.length > 0 && <List small aria-label="Constraints typed">{c.constraints.map((k, n) => (
        <li key={`${n}-${k.text}`}>{k.text}{edit && <> <Button size="sm" variant="secondary" disabled={!!a.busy || !person}
          onClick={() => a.run("remove", () => api.planConstraintRemove(c.exception_id, person, n), put)}>Remove</Button></>}</li>
      ))}</List>}
      {edit && p && <Line wrap>
        <Button variant="primary" disabled={!!a.busy || !person || (!!windows && !windows.length)} onClick={async () => (await confirmDialog(assign
          ? `Book ${booked === 1 ? "this job" : `these ${booked} jobs`} on the crews proposed?`
          : windows ? `Move the outage to window ${(p.picked ?? 0) + 1}, queue SAP's schedule change for you to approve and draft the notices to its crews and customers?`
          : `Move ${p.moves.length === 1 ? "this job" : `these ${p.moves.length} jobs`} on the plan, queue SAP's schedule change for you to approve and draft the notices?`,
          { confirm: "Approve" })) && a.run("approve", () => api.planChangeApprove(c.exception_id, person), put)}>Approve</Button>
      </Line>}
      {done && <Approved c={c} person={person} readOnly={readOnly || c.state === "reverted"} put={put} />}
      {done && c.changed.length > 0 && <Changed changed={c.changed} />}
      {c.state === "reverted" && <Text as="p">Reverted by <Actor who={c.reverted_by ?? ""} /> {when(c.reverted_at)}.</Text>}
      {c.state === "approved" && !readOnly && c.changed.length > 0 && <Line wrap>
        <Button variant="secondary" disabled={!!a.busy || !person} onClick={async () => (await confirmDialog(
          `Put back the ${c.changed.length === 1 ? "change" : `${c.changed.length} changes`} it made? SAP changes not yet sent are dropped.`,
          { confirm: "Revert", danger: true })) && a.run("revert", () => api.planChangeRevert(c.exception_id, person), put)}>Revert</Button>
      </Line>}
      {c.history.length > 0 && <History history={c.history} />}
      {edit && !assign && <form onSubmit={(e) => { e.preventDefault(); a.run("constraint", () => api.planConstraint(c.exception_id, person, text),
        (x) => { put(x); setText(""); }); }}>
        <Line wrap>
          <Input aria-label="Constraint" placeholder={c.forms[0]} value={text} onChange={(e) => setText(e.target.value)} />
          <Button type="submit" variant="primary" disabled={!!a.busy || !person || !text.trim()}>Work it out again</Button>
        </Line>
        <Text small muted>Fuzer reads: {c.forms.join("; ")}.</Text>
      </form>}
      <ErrorText>{a.error}</ErrorText>
    </UICard>
  );
}

function Kpis({ before, after }: { before: PlanKpis; after: PlanKpis }) {
  const rows: [string, string, string][] = [
    ["Finish", when(before.finish, "none"), when(after.finish, "none")],
    ["Deferred oil", quantity(before.deferred_oil, before.deferred_unit), quantity(after.deferred_oil, after.deferred_unit)],
    ["Overloads", String(before.overloads), String(after.overloads)],
  ];
  return (
    <Table variant="compare" density="compact" aria-label="Before and after">
      <thead><tr><th></th><th>Now</th><th>With this change</th></tr></thead>
      <tbody>{rows.map(([k, b, x]) => <tr key={k}><th scope="row">{k}</th><td>{b}</td><td>{x}</td></tr>)}</tbody>
    </Table>
  );
}

/** Each moved job on one track: its dates now as an outline, and where it goes as a bar. */
function Diff({ moves }: { moves: PlanMove[] }) {
  const times = moves.flatMap((m) => [m.before.start, m.before.end, m.after.start, m.after.end].map(ms));
  const t0 = Math.min(...times);
  const span = Math.max(Math.max(...times) - t0, 3600e3);
  return (
    <ul className="plan-rows replan-diff" aria-label="Moves">
      {moves.map((m) => (
        <li key={m.item_id} className="plan-row">
          <div>
            <Line wrap>
              {m.case ? <a href={caseHref(m.case)}>{m.title}</a> : m.title}
              {(m.order ?? m.code) && <Text small mono>{m.order ?? m.code}</Text>}
            </Line>
            <Text small>{when(m.before.start)} to {when(m.after.start)}: {m.because}</Text>
          </div>
          <div className="plan-track replan-track" aria-hidden="true">
            <span className="replan-before" style={box(ms(m.before.start), ms(m.before.end), t0, span)} />
            <span className="replan-after" style={box(ms(m.after.start), ms(m.after.end), t0, span)} />
          </div>
        </li>
      ))}
    </ul>
  );
}

/** Auto-assign (P5.23b): each field job with no crew, the crew proposed and why; the planner picks another (or none)
 * while it is open, and a pick that breaks a rule or a crew's capacity says so. */
function Assign({ c, lines, edit, person, put }:
  { c: PlanChange; lines: PlanAssign[]; edit: boolean; person: string; put: (c: PlanChange) => void }) {
  const a = useAction();
  const resources = useAsync(() => (edit ? api.planResources() : Promise.resolve([])), [edit]);
  const crews = (Array.isArray(resources.data) ? resources.data : []).filter((r) => r.kind === "crew");
  if (!lines.length) return <Text as="p">Every field job on these dates has a crew.</Text>;
  return <>
    <Table density="compact" aria-label="Crews proposed">
      <thead><tr><th>Job</th><th>When</th><th>Crew</th><th>Why</th></tr></thead>
      <tbody>{lines.map((x) => <tr key={x.item_id}>
        <td>{x.case ? <a href={caseHref(x.case)}>{x.title}</a> : x.title}{x.order && <Text small mono> {x.order}</Text>}</td>
        <td>{when(x.start)}</td>
        <td>{edit && crews.length ? <Select aria-label={`Crew for ${x.title}`} value={x.crew ?? ""} disabled={!!a.busy || !person}
          onChange={(e) => a.run("pick", () => api.planAssignPick(c.exception_id, person, x.item_id, e.target.value), put)}>
          <option value="">No crew</option>
          {crews.map((r) => <option key={r.code} value={r.code}>{r.name}</option>)}
        </Select> : x.name ?? "No crew"}</td>
        <td>{x.because}{x.breaks.map((b) => <Text as="div" small key={b}><Badge tone="danger">breaks</Badge> {b}</Text>)}</td>
      </tr>)}</tbody>
    </Table>
    <ErrorText>{a.error}</ErrorText>
  </>;
}

/** An outage's windows (P5.22c): three proposed, each with what it costs the customers it hits and its reasons, the
 * cheapest recommended. The planner picks another while it is open, saying why. Beside them, the maintenance history
 * of what its jobs work on (P5.22d). */
function Windows({ c, windows, edit, person, put }:
  { c: PlanChange; windows: OutageWindow[]; edit: boolean; person: string; put: (c: PlanChange) => void }) {
  const a = useAction();
  const p = c.proposal!;
  const pick = async (w: OutageWindow) => {
    const why = w.n === p.recommended ? null
      : await promptDialog(`Why window ${w.n + 1} rather than the recommended window ${(p.recommended ?? 0) + 1}?`, { confirm: "Pick" });
    if (w.n === p.recommended || why) a.run("pick", () => api.planWindowPick(c.exception_id, person, w.n, why), put);
  };
  return <div className="replan-outage"><div>
    {!windows.length && <Text as="p">{p.recommend}</Text>}
    <List plain aria-label="Windows">{windows.map((w) => <li key={w.n} className="replan-window">
      <Line wrap>
        <strong>Window {w.n + 1}: {when(w.start)} to {when(w.end)}</strong>
        {w.n === p.recommended && <Badge tone="ok">recommended</Badge>}
        {w.n === p.picked && <Badge tone="info">picked</Badge>}
        <Text small>{money(w.usd)} · {count(w.volume_bbl)} bbl</Text>
        {edit && w.n !== p.picked && <Button size="sm" variant="secondary" disabled={!!a.busy || !person} onClick={() => pick(w)}>Pick</Button>}
      </Line>
      <List small>{w.reasons.map((r) => <li key={r}>{r}</li>)}</List>
    </li>)}</List>
    {windows.length > 0 && <Text as="p" small>{p.recommend}.</Text>}
    {!!p.past?.length && <Text as="p" small>Similar past work: {p.past.map((x) =>
      `${x.key}, ${x.hours} h from ${whenDay(x.start)}${x.comment ? ` (${x.comment})` : ""}`).join("; ")}.</Text>}
    <ErrorText>{a.error}</ErrorText>
  </div>
    {!!p.maintenance?.length && <section aria-label="Maintenance history">
      <Heading level={3}>Maintenance history</Heading>
      {p.maintenance.map((x) => <div key={x.id}>
        <Text as="p" small><a href={entityHref(x.id)}>{x.label}</a></Text>
        {x.notifications.length > 0 && <NotificationList items={x.notifications} />}
        {x.downtime.length > 0 && <DowntimeList rows={x.downtime} />}
      </div>)}
    </section>}
  </div>;
}

/** Each thing done to the plan change, oldest first: Fuzer working it out, what a person typed or picked, the
 * approval, a notice sent, the revert. */
function History({ history }: { history: PlanChange["history"] }) {
  return <List small aria-label="History">{history.map((h, n) => <li key={n}>
    {when(h.at)} <Actor who={h.by} />: {h.did}</li>)}</List>;
}

const FIELD: Record<string, string> = { start_at: "Start", end_at: "End", not_before: "Starts no earlier than", status: "Status",
  crew: "Who", moved_by: "Moved by", moved_because: "Moved because", released_at: "Released", released_by: "Released by",
  release_reason: "Why it was released" };
const TIMES = new Set(["start_at", "end_at", "not_before", "released_at"]);
const shown = (field: string, v: unknown) => (v == null || v === "" ? "none"
  : TIMES.has(field) ? when(String(v)) : String(v).replace(/_/g, " "));

/** What approving changed, before and after: each job's fields, each booking on a crew, each capacity window added.
 * Revert puts these back. */
function Changed({ changed }: { changed: PlanChanged[] }) {
  const rows = changed.flatMap((x): [string, string, string, string, number | null][] => x.what === "job"
    ? Object.entries(x.fields).map(([f, [b, a]]) => [x.code ?? x.title, FIELD[f] ?? f, shown(f, b), shown(f, a), x.case])
    : x.what === "booking"
      ? [[x.title ?? String(x.id), `Booked on ${x.name}`, x.before == null ? "none" : String(x.before), x.after == null ? "none" : String(x.after), null]]
      : x.what === "schedule"
        ? [[x.title, "Schedule starts", when(x.before), when(x.after), null]]
        : [[x.title ?? x.resource, `${x.name ?? x.resource} from ${when(x.start)} to ${when(x.end)}`, "none", String(x.after), null]]);
  return (
    <Table density="compact" aria-label="What it changed">
      <thead><tr><th>Job</th><th>What</th><th>Before</th><th>After</th></tr></thead>
      <tbody>{rows.map(([job, what, b, a, item], n) => <tr key={n}>
        <td>{item ? <a href={caseHref(item)}>{job}</a> : job}</td><td>{what}</td><td>{b}</td><td>{a}</td></tr>)}</tbody>
    </Table>
  );
}

/** Once approved: who approved it, each SAP schedule change waiting in the write-back queue, and the notices to send. */
function Approved({ c, person, readOnly, put }: { c: PlanChange; person: string; readOnly: boolean; put: (c: PlanChange) => void }) {
  return <>
    <Text as="p">Approved by <Actor who={c.approved_by ?? ""} /> {when(c.approved_at)}.</Text>
    {c.writebacks.length > 0 && <List small aria-label="SAP changes">{c.writebacks.map((w) => <li key={w.id}>
      {w.item_id ? <a href={caseHref(w.item_id)}>SAP order {w.record}</a> : <>SAP order {w.record}</>}: {w.state.replace(/_/g, " ")}</li>)}
    </List>}
    {c.notices.map((x) => <Notice key={x.n} id={c.exception_id} x={x} person={person} readOnly={readOnly} put={put} />)}
  </>;
}

const AUDIENCE: Record<PlanNotice["audience"], string> = { crew: "Crew", field: "Field operator", downstream: "Downstream",
  customer: "Customer" };

function Notice({ id, x, person, readOnly, put }:
  { id: number; x: PlanNotice; person: string; readOnly: boolean; put: (c: PlanChange) => void }) {
  const [text, setText] = useState(x.text);
  const a = useAction();
  const sent = !!x.sent_at;
  return (
    <div className="replan-notice">
      <Line wrap small><strong>{AUDIENCE[x.audience]}: {x.name}</strong>
        {sent && <Text muted>sent by <Actor who={x.sent_by ?? ""} /> {when(x.sent_at)}</Text>}</Line>
      <Textarea rows={3} aria-label={`Notice to ${x.name}`} disabled={sent || readOnly} value={text}
        onChange={(e) => setText(e.target.value)} />
      {!sent && !readOnly && <Button size="sm" variant="primary" disabled={!!a.busy || !person || !text.trim()}
        onClick={() => a.run("send", () => api.planNoticeSend(id, x.n, person, text), put)}>Send</Button>}
      <ErrorText>{a.error}</ErrorText>
    </div>
  );
}
