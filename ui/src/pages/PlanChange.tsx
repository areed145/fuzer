import { useState, type CSSProperties } from "react";
import { Button, Card as UICard, ErrorText, Input, Line, List, Table, Text } from "../ui";
import { quantity, when } from "../format";
import { api, type PlanChange, type PlanKpis, type PlanMove } from "../api";
import { Status, useAction, useAsync } from "../lib";
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
  return (
    <UICard title="Plan change" className="replan">
      <Text as="p">{c.say}: {c.constraint}.</Text>
      {p && <>
        <Kpis before={p.kpis.before} after={p.kpis.after} />
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
      {edit && <form onSubmit={(e) => { e.preventDefault(); a.run("constraint", () => api.planConstraint(c.exception_id, person, text),
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
