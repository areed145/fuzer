/** Asset context completeness (P2.5): what Fuzer knows about each asset, what is missing and what each gap holds
 * back. The asset page shows one asset; Master data shows completeness by area and class and the top gaps, each an
 * open case in the data steward's queue. */
import { api, type AssetContext, type ContextRollup, type PlacementSuggestion } from "../../api";
import { Badge, Button, Card, Empty, Table } from "../../ui";
import { Status, entityHref, useAsync } from "../../lib";
import { caseHref } from "../workflows/shared";
import { reviewer, useAction } from "../sources/common";

const pct = (score: number | null) => (score == null ? null : Math.round(score * 100));

/** The asset page's panel: one row per kind of context, and for each gap what it holds back. */
export function AssetContextCard({ id }: { id: string }) {
  const c = useAsync(() => api.assetContext(id), [id]);
  if (!c.data?.known) return c.error ? <Status error={c.error} onRetry={c.reload} /> : null;  // not part of an asset
  const a: AssetContext = c.data;
  const gaps = Object.fromEntries(a.gaps.map((g) => [g.check, g]));
  const score = pct(a.score);
  return (
    <Card as="section" className="asset-context" title={<>Context{a.of && <> of <a href={entityHref(a.fuzer_id)}>{a.label}</a></>}</>}
      actions={<>{score != null && <span className="small">{score}% of what {a.class_name.toLowerCase()} usually have</span>}
        {a.item_id && <a className="small" href={caseHref(a.item_id)}>Open case</a>}</>}>
      <Table density="compact" variant="keyValue" headingCase="verbatim">
        <tbody>{a.known.map((k) => {
          const g = gaps[k.check];
          const holds = g ? [...g.detectors.map((d) => d.title), g.prevents] : [];
          return (
            <tr key={k.check}>
              <th scope="row">{k.label}</th>
              <td>
                {k.has ? <Badge tone="ok">Known</Badge> : g ? <Badge tone="warn">Missing</Badge> : <Badge>Not recorded</Badge>}
                {g && <div className="small">Holds back {holds.join("; ")}. {g.usual}.</div>}
              </td>
            </tr>
          );
        })}</tbody>
      </Table>
      {!!a.suggestions?.length && <Suggestions id={a.fuzer_id} items={a.suggestions} onDone={c.reload} />}
    </Card>
  );
}

const WHAT = { part_of: "Area", shown_on: "Drawing", applies_to: "Document" } as const;

/** Ranked proposals for what the gaps lack (P2.1d), each with its evidence; an answer is kept by later rebuilds. */
function Suggestions({ id, items, onDone }: { id: string; items: PlacementSuggestion[]; onDone: () => void }) {
  const a = useAction();
  return (
    <>
      <h3>Suggested</h3>
      <Table density="compact">
        <thead><tr><th>For</th><th>Suggestion</th><th>Why</th><th>Confidence</th><th /></tr></thead>
        <tbody>{items.map((s) => {
          const key = `${s.rel}-${s.target}`;
          const answer = (accept: boolean) => void a.run(key, () => api.answerPlacement(id, s.rel, s.target, accept, reviewer()), onDone);
          return (
            <tr key={key}>
              <td>{WHAT[s.rel]}</td>
              <td><a href={entityHref(s.target)}>{s.target_label}</a></td>
              <td className="small">{s.basis.join("; ")}</td>
              <td>{Math.round(s.confidence * 100)}%</td>
              <td><Button size="sm" disabled={!!a.busy} onClick={() => answer(true)}>Yes</Button>{" "}
                <Button size="sm" variant="ghost" disabled={!!a.busy} onClick={() => answer(false)}>No</Button></td>
            </tr>
          );
        })}</tbody>
      </Table>
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

/** Master data's Asset context tab: the top gaps (open cases), then completeness by area and by class. */
export function ContextTab() {
  const s = useAsync(() => api.contextSummary(), []);
  if (!s.data) return <Status loading={s.loading} error={s.error} onRetry={s.reload} />;
  const label = Object.fromEntries(s.data.checks.map((c) => [c.check, c.label.toLowerCase()]));
  return <>
    <Card title="Gaps that hold back the most">
      {!s.data.top.length ? <Empty>No asset lacks context that its class usually has.</Empty> : (
        <Table density="compact">
          <thead><tr><th>Asset</th><th>Missing</th><th>Checks held back</th><th>Case</th></tr></thead>
          <tbody>{s.data.top.map((r) => (
            <tr key={r.fuzer_id}>
              <td><a href={entityHref(r.fuzer_id)}>{r.label}</a>
                <div className="small muted">{[r.description, r.class_name, r.criticality ? `criticality ${r.criticality}` : "criticality not assessed"].filter(Boolean).join(" · ")}</div></td>
              <td>{r.missing.map((m) => label[m] ?? m).join(", ")}</td>
              <td>{r.blocked.length}</td>
              <td>{r.item_id ? <a href={caseHref(r.item_id)}>Open</a> : ""}</td>
            </tr>
          ))}</tbody>
        </Table>
      )}
    </Card>
    <Rollup title="By area" rows={s.data.areas} />
    <Rollup title="By class" rows={s.data.classes} />
  </>;
}

function Rollup({ title, rows }: { title: string; rows: ContextRollup[] }) {
  return (
    <Card title={title}>
      {!rows.length ? <Empty>No assets yet.</Empty> : (
        <Table density="compact">
          <thead><tr><th>{title.replace("By ", "").replace(/^./, (x) => x.toUpperCase())}</th><th>Assets</th><th>Complete</th><th>Most missing</th></tr></thead>
          <tbody>{rows.map((r) => {
            const score = pct(r.score);
            return (
              <tr key={r.name}>
                <td>{r.name}</td>
                <td>{r.assets}</td>
                <td>{score == null ? <span className="muted">too few to say</span> : `${score}%`}</td>
                <td>{r.gaps.map((g) => `${g.label.toLowerCase()} (${g.assets})`).join(", ")}</td>
              </tr>
            );
          })}</tbody>
        </Table>
      )}
    </Card>
  );
}
