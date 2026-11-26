import { useState, type CSSProperties } from "react";
import { Badge, Button, Card as UICard, ErrorText, Input, Line, List, Select, Table, Text, Textarea, confirmDialog } from "../ui";
import { quantity, when } from "../format";
import { api, type PlanAssign, type PlanChange, type PlanKpis, type PlanMove, type PlanNotice } from "../api";
import { Status, useAction, useAsync } from "../lib";
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
  const booked = assign?.filter((x) => x.crew).length ?? 0;
  return (
    <UICard title="Plan change" className="replan">
      <Text as="p">{c.say}: {c.constraint}.</Text>
      {assign && <Assign c={c} lines={assign} edit={edit} person={person} put={put} />}
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
        <Button variant="primary" disabled={!!a.busy || !person} onClick={async () => (await confirmDialog(assign
          ? `Book ${booked === 1 ? "this job" : `these ${booked} jobs`} on the crews proposed?`
          : `Move ${p.moves.length === 1 ? "this job" : `these ${p.moves.length} jobs`} on the plan, queue SAP's schedule change for you to approve and draft the notices?`,
          { confirm: "Approve" })) && a.run("approve", () => api.planChangeApprove(c.exception_id, person), put)}>Approve</Button>
      </Line>}
      {c.state === "approved" && <Approved c={c} person={person} readOnly={readOnly} put={put} />}
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

const AUDIENCE: Record<PlanNotice["audience"], string> = { crew: "Crew", field: "Field operator", downstream: "Downstream" };

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
