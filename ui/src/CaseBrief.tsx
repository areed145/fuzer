/** The specialist brief on a case (P4.3): what the owning specialist found before anyone opened it, its proposed
 * disposition with a confidence, what it could not check and what it cost against its budget. The person who
 * decides says whether they followed the proposal; the brief never decides. Sits above the decision. */
import { useState } from "react";
import { api, type WorkItem } from "./api";
import { useAct } from "./pages/workflows/shared";
import { Actor, when } from "./work";
import { Badge, Button, Card, Disclosure, Input, Notice, Table } from "./ui";

const usd = (x: number | undefined) => `$${(x ?? 0).toFixed(2)}`;
const pct = (x: number | null | undefined) => (x == null ? "no confidence given" : `${Math.round(x * 100)} % confident`);
const FOLLOWED = { yes: "Followed", partly: "Partly followed", no: "Not followed" } as const;

export function CaseBrief({ it, person, readOnly = false, reload }: {
  it: WorkItem; person?: string; readOnly?: boolean; reload?: () => void;
}) {
  const v = it.brief;
  const [note, setNote] = useState("");
  const act = useAct();
  if (!v || (!v.expected && !v.entry)) return null;
  const e = v.entry;
  if (!e) {
    if (it.closed_at) return null;
    return <Card title="Specialist brief"><p className="small muted">The specialist has not briefed this case yet. Due {when(v.due)}.</p></Card>;
  }
  if (e.missing) return <Card title="Specialist brief"><p className="small">No brief: {e.missing}.</p></Card>;
  const b = e.brief ?? {};
  const p = b.proposal ?? {};
  const evidence = b.evidence ?? [], analysis = b.analysis ?? [], similar = b.similar ?? [], gaps = b.could_not_check ?? [];
  return (
    <Card title="Specialist brief" actions={<span className="small">
      {usd(e.ledger?.cost_usd)}{e.budget_usd ? <> of {usd(e.budget_usd)} budget</> : null}{" "}
      {e.over_budget && <Badge tone="warn">over budget</Badge>}{" "}
      {e.late && <Badge tone="warn">late</Badge>}</span>}>
      {b.what_happened && <p>{b.what_happened}</p>}
      {p.disposition && (
        <Notice tone="info">
          <strong>Proposed: {p.disposition}</strong> <span className="small">({pct(p.confidence)})</span>
          {p.why && <p className="small">{p.why}</p>}
          <p className="small muted">A proposal for you to decide on. The specialist takes no action.</p>
        </Notice>
      )}
      {gaps.length > 0 && (
        <div className="small"><strong>Could not check</strong>
          <ul>{gaps.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </div>
      )}
      {(e.lessons ?? []).length > 0 && (
        <div className="small"><strong>Reviewed lessons it was given</strong>
          <ul>{(e.lessons ?? []).map((l) => <li key={l.id}>{l.title}{l.by && <> (approved by <Actor who={l.by} />)</>}
            {l.review_overdue && <> <Badge tone="warn">review overdue</Badge></>}</li>)}</ul>
        </div>
      )}
      {evidence.length + analysis.length + similar.length > 0 && (
        <Disclosure label={<>Evidence, analysis and similar cases</>}>
          {evidence.length > 0 && <Table density="compact" variant="list">
            <thead><tr><th>Record</th><th>Shows</th></tr></thead>
            <tbody>{evidence.map((x, i) => <tr key={i}><td className="mono small">{x.ref}</td><td className="small">{x.says}</td></tr>)}</tbody>
          </Table>}
          {analysis.length > 0 && <Table density="compact" variant="list">
            <thead><tr><th>Method</th><th>Result</th></tr></thead>
            <tbody>{analysis.map((x, i) => <tr key={i}><td className="mono small">{x.method}</td><td className="small">{x.says}</td></tr>)}</tbody>
          </Table>}
          {similar.length > 0 && <Table density="compact" variant="list">
            <thead><tr><th>Similar case</th><th>What fixed it</th></tr></thead>
            <tbody>{similar.map((x, i) => <tr key={i}><td className="mono small">{x.case}</td><td className="small">{x.fixed_by}</td></tr>)}</tbody>
          </Table>}
        </Disclosure>
      )}
      {v.verdict ? (
        <p className="small">{FOLLOWED[v.verdict.followed]} by <Actor who={v.verdict.by} /> {when(v.verdict.at)}
          {v.verdict.note && <>: {v.verdict.note}</>}</p>
      ) : !readOnly && person && p.disposition ? (
        <div className="row small">
          <span>Did you follow the proposal?</span>
          <Input aria-label="Note on the proposal" placeholder="Note (optional)" value={note}
            onChange={(ev) => setNote(ev.target.value)} />
          {(["yes", "partly", "no"] as const).map((f) => (
            <Button key={f} disabled={!!act.busy}
              onClick={() => act.run(f, () => api.briefVerdict(it.id, person, f, note || undefined), reload)}>
              {FOLLOWED[f]}</Button>
          ))}
          {act.error && <p role="alert" className="error small">{act.error}</p>}
        </div>
      ) : null}
    </Card>
  );
}
