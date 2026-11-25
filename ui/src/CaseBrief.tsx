/** The specialist brief on a case (P4.3): what the owning specialist found before anyone opened it, its proposed
 * disposition with a confidence and what it could not check. What it cost is AI usage's, not the case's (#1348). The
 * person who decides says whether they followed the proposal; the brief never decides. */
import { useState } from "react";
import { useAction } from "./lib";
import { percent, when } from "./format";
import { api, type WorkItem } from "./api";
import { Actor } from "./work";
import { Badge, Button, Card, Disclosure, Input, Notice, Table, ErrorText, Line, Text } from "./ui";

const pct = (x: number | null | undefined) => (x == null ? "no confidence given" : `${percent(x)} confident`);
const FOLLOWED = { yes: "Followed", partly: "Partly followed", no: "Not followed" } as const;

export function CaseBrief({ it, person, readOnly = false, reload }: {
  it: WorkItem; person?: string; readOnly?: boolean; reload?: () => void;
}) {
  const v = it.brief;
  const [note, setNote] = useState("");
  const act = useAction();
  if (!v || (!v.expected && !v.entry)) return null;
  const e = v.entry;
  if (!e) {
    if (it.closed_at) return null;
    return <Card className="case-brief" title="Specialist brief"><Text as="p" small muted>The specialist has not briefed this case yet. Due {when(v.due)}.</Text></Card>;
  }
  if (e.missing) return <Card className="case-brief" title="Specialist brief"><Text as="p" small>No brief: {e.missing}.</Text></Card>;
  const b = e.brief ?? {};
  const p = b.proposal ?? {};
  const evidence = b.evidence ?? [], analysis = b.analysis ?? [], similar = b.similar ?? [], gaps = b.could_not_check ?? [];
  return (
    <Card className="case-brief" title="Specialist brief">
      {b.what_happened && <p>{b.what_happened}</p>}
      {p.disposition && (
        <Notice tone="info">
          <strong>Proposed: {p.disposition}</strong> <Text small>({pct(p.confidence)})</Text>
          {p.why && <Text as="p" small>{p.why}</Text>}
        </Notice>
      )}
      {gaps.length > 0 && (
        <Text as="div" small><strong>Could not check</strong>
          <ul>{gaps.map((c, i) => <li key={i}>{c}</li>)}</ul>
        </Text>
      )}
      {(e.lessons ?? []).length > 0 && (
        <Text as="div" small><strong>Reviewed lessons it was given</strong>
          <ul>{(e.lessons ?? []).map((l) => <li key={l.id}>{l.title}{l.by && <> (approved by <Actor who={l.by} />)</>}
            {l.review_overdue && <> <Badge tone="warn">review overdue</Badge></>}</li>)}</ul>
        </Text>
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
        <Text as="p" small>{FOLLOWED[v.verdict.followed]} by <Actor who={v.verdict.by} /> {when(v.verdict.at)}
          {v.verdict.note && <>: {v.verdict.note}</>}</Text>
      ) : !readOnly && person && p.disposition ? (
        <Line small>
          <span>Did you follow the proposal?</span>
          <Input aria-label="Note on the proposal" placeholder="Note (optional)" value={note}
            onChange={(ev) => setNote(ev.target.value)} />
          {(["yes", "partly", "no"] as const).map((f) => (
            <Button key={f} disabled={!!act.busy}
              onClick={() => act.run(f, () => api.briefVerdict(it.id, person, f, note || undefined), reload)}>
              {FOLLOWED[f]}</Button>
          ))}
          <ErrorText>{act.error}</ErrorText>
        </Line>
      ) : null}
    </Card>
  );
}
