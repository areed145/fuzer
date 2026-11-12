/** Master data (#476): keeping the register matching the work. Each queue is ranked by how much open work it
 * blocks: refs on open exceptions that matched nothing, link values each source wrote that named nothing, and the
 * decisions stewards made (aliases and pinned matches), each reversible. */
import { useState } from "react";
import { api } from "../../api";
import { Badge, Button, Card, Input, PageHeader, Table, Empty } from "../../ui";
import { Status, entityHref, href, useAsync } from "../../lib";
import { usePerson, when } from "../../work";
import { caseHref, useAct } from "../workflows/shared";
import { usePermission } from "../../Auth";
import { MatchRef } from "./MatchRef";
import { Gaps } from "./Gaps";
import { ContextTab } from "./Context";
import { ProposalsTab } from "./Proposals";
import { Review } from "../Review";

const TABS = [["unmatched", "Unmatched on open work"], ["questions", "Questions"], ["unresolved", "Unresolved values"],
  ["gaps", "Gaps"], ["context", "Asset context"], ["proposals", "Claude's proposals"], ["decisions", "Decisions"]] as const;

/** Keeping the register matching the work, in one place (#505, P2.3): the questions the build leaves (possible
 * matches, conflicting values, changed columns, drawing disagreements, ...) sit beside unmatched refs and the
 * decision log. Each question is also a case in the data steward's queue. */
export function MasterDataPage({ query }: { query: URLSearchParams }) {
  const tab = TABS.some(([k]) => k === query.get("tab")) ? query.get("tab")! : "unmatched";
  const stats = useAsync(() => api.stats(), []);
  const questions = stats.data ? Object.values(stats.data.open_reviews).reduce((a, b) => a + b, 0) : 0;
  return (
    <section>
      <PageHeader subtitle="Keeping the register matching the work.">Master data</PageHeader>
      <div className="tabs" role="tablist">
        {TABS.map(([k, label]) => <a key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : undefined}
          href={href("/masterdata", { tab: k })}>{label}{k === "questions" && questions ? ` (${questions})` : ""}</a>)}
      </div>
      {tab === "unmatched" ? <Unmatched /> : tab === "questions" ? <Review query={query} stats={stats.data ?? undefined} />
        : tab === "unresolved" ? <Unresolved /> : tab === "gaps" ? <Gaps /> : tab === "context" ? <ContextTab />
        : tab === "proposals" ? <ProposalsTab /> : <Decisions />}
    </section>
  );
}

function Unmatched() {
  const rows = useAsync(() => api.unmatchedRefs(), []);
  const [person] = usePerson();
  const operate = usePermission("operate");
  const a = useAct();
  if (!rows.data) return <Status loading={rows.loading} error={rows.error} />;
  if (!rows.data.length) return <Empty>Every open exception matched the register.</Empty>;
  return (
    <Card title="Refs on open exceptions that matched nothing">
      <p className="small muted">Matching one here matches every open exception that carries it, and remembering it makes the
        next one match on its own.</p>
      <Table density="compact">
        <thead><tr><th>Ref</th><th>Open exceptions</th><th>From</th><th /></tr></thead>
        <tbody>{rows.data.map((u) => (
          <tr key={u.ref}>
            <td><code>{u.ref}</code></td>
            <td>{u.open.slice(0, 3).map((e) => <div key={e.id}>{e.item_id ? <a href={caseHref(e.item_id)}>{e.title}</a> : e.title}</div>)}
              {u.exceptions > 3 && <div className="muted">and {u.exceptions - 3} more</div>}</td>
            <td>{u.sources.join(", ")}</td>
            <td>{operate && person && <MatchRef value={u.ref} busy={!!a.busy} onPick={(fid, remember) => a.run("match", async () => {
              for (const e of u.open) await api.resolveRef(e.id, person, u.ref, fid, remember && e.id === u.open[0].id);
            }, rows.reload)} />}</td>
          </tr>
        ))}</tbody>
      </Table>
      {a.error && <p className="error small">{a.error}</p>}
    </Card>
  );
}

function Unresolved() {
  const rows = useAsync(() => api.unresolvedValues(), []);
  const [person] = usePerson();
  const operate = usePermission("operate");
  const a = useAct();
  if (!rows.data) return <Status loading={rows.loading} error={rows.error} />;
  if (!rows.data.length) return <Empty>Every link value the last rebuild read named something in the register.</Empty>;
  return (
    <Card title="Link values that named nothing (last rebuild)">
      <p className="small muted">Make one an alias of the entity it means and the next rebuild links those records to it.</p>
      <Table density="compact">
        <thead><tr><th>Value</th><th>Source</th><th>Link</th><th>Rows</th><th /></tr></thead>
        <tbody>{rows.data.map((u) => (
          <tr key={`${u.source}|${u.rel}|${u.value}`}>
            <td><code>{u.value}</code></td><td>{u.source}</td><td>{u.rel.replace(/_/g, " ")}</td><td>{u.rows}</td>
            <td>{u.aliased ? <Badge tone="ok">alias recorded; rebuild to apply</Badge>
              : operate && person && <MatchRef value={u.value} label="Make it an alias" busy={!!a.busy} onPick={(fid) =>
                a.run("alias", () => api.addAlias(u.value, fid, person, u.source, `unresolved ${u.rel} value`), rows.reload)} />}</td>
          </tr>
        ))}</tbody>
      </Table>
      {a.error && <p className="error small">{a.error}</p>}
    </Card>
  );
}

function Decisions() {
  const [q, setQ] = useState("");
  const [all, setAll] = useState(false);
  const rows = useAsync(() => api.stewardDecisions(q, !all), [q, all]);
  const [person] = usePerson();
  const operate = usePermission("operate");
  const a = useAct();
  return (
    <Card title="Decisions">
      <div className="row">
        <Input placeholder="search a value, tag or ref" value={q} onChange={(e) => setQ(e.target.value)} />
        <label className="small"><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> include reversed</label>
      </div>
      <Status loading={rows.loading && !rows.data} error={rows.error} />
      {rows.data && (
        <Table density="compact">
          <thead><tr><th>Decision</th><th>Who</th><th>When</th><th>Why</th><th /></tr></thead>
          <tbody>{rows.data.map((d) => (
            <tr key={`${d.kind}:${d.id}`}>
              <td><Badge>{d.kind.replace("_", " ")}</Badge> {d.fuzer_id ? <a href={entityHref(d.fuzer_id)}>{d.what}</a> : d.what}</td>
              <td>{d.by}</td><td>{when(d.at)}</td><td>{d.reason ?? ""}</td>
              <td>{d.reversed ? <span className="muted">reversed by {d.reversed_by} {d.reversed_at && when(d.reversed_at)}</span>
                : operate && person && <Button size="sm" variant="link" disabled={!!a.busy} onClick={() => {
                  if (!confirm(d.kind === "fix" ? `Reverse “${d.what}”? The values go back now.` : `Reverse “${d.what}”? It stops applying on the next rebuild.`)) return;
                  const undo = d.kind === "alias" ? () => api.retireAlias(Number(d.id), person, "reversed from the decision log")
                    : d.kind === "fix" ? () => api.undoFix(String(d.id), person, "reversed from the decision log")
                    : d.kind === "merge" || d.kind === "split" ? () => api.undoDecision(String(d.id), person, "reversed from the decision log")
                    : () => { const [l, r] = String(d.id).split("|"); return api.undoPin(l, r, person, "reversed from the decision log"); };
                  void a.run("undo", undo, rows.reload);
                }}>Reverse</Button>}</td>
            </tr>
          ))}</tbody>
        </Table>
      )}
      {a.error && <p className="error small">{a.error}</p>}
    </Card>
  );
}
