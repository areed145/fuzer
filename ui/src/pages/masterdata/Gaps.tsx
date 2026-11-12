/** Register gaps (#476): fields most of a kind carry and some lack, one value written several ways, and open
 * conflicts between sources. Each says where it is fixed; spellings are fixed here, across every entity at once. */
import { useState } from "react";
import { api, type SpellingGroup } from "../../api";
import { Button, Card, Select, Table, Empty } from "../../ui";
import { Status, entityHref, fieldLabel, fmt, href, useAsync } from "../../lib";
import { usePerson } from "../../work";
import { usePermission } from "../../Auth";
import { useAct } from "../workflows/shared";

export function Gaps() {
  return <>
    <Spellings />
    <Completeness />
    <Conflicts />
  </>;
}

function Spellings() {
  const rows = useAsync(() => api.spellingGroups(), []);
  return (
    <Card title="One value, written several ways">
      <p className="small muted">Case, spacing, punctuation or a company suffix apart. Pick the spelling to keep; it is set on
        the others as a reviewer's choice, so it holds on every rebuild, and it can be reversed from Decisions.</p>
      <Status loading={rows.loading && !rows.data} error={rows.error} />
      {rows.data && !rows.data.length && <Empty>No field has a value written more than one way.</Empty>}
      {rows.data && rows.data.length > 0 && <Table density="compact">
        <thead><tr><th>Field</th><th>Spellings (entities)</th><th /></tr></thead>
        <tbody>{rows.data.map((g) => <SpellingRow key={`${g.field}|${g.usual}`} g={g} done={rows.reload} />)}</tbody>
      </Table>}
    </Card>
  );
}

function SpellingRow({ g, done }: { g: SpellingGroup; done: () => void }) {
  const [person] = usePerson();
  const operate = usePermission("operate");
  const [keep, setKeep] = useState(g.usual);
  const a = useAct();
  const others = g.spellings.filter((s) => s.value !== keep);
  const n = others.reduce((t, s) => t + s.entities, 0);
  return (
    <tr>
      <td>{fieldLabel(g.field)}</td>
      <td>{g.spellings.map((s) => <div key={s.value}><code>{s.value}</code> <span className="muted">({s.entities})</span></div>)}</td>
      <td>{operate && person && <div className="row">
        <Select aria-label={`Spelling of ${g.field} to keep`} value={keep} onChange={(e) => setKeep(e.target.value)}>
          {g.spellings.map((s) => <option key={s.value} value={s.value}>{s.value}</option>)}
        </Select>
        <Button size="sm" disabled={!!a.busy || !n} onClick={() => {
          if (!confirm(`Set ${g.field} to “${keep}” on ${n} entit${n === 1 ? "y" : "ies"}?`)) return;
          void a.run("fix", () => api.setValues(g.field, keep, others.flatMap((s) => s.fuzer_ids), person,
            `one spelling for ${g.field}`), done);
        }}>Use it on {n}</Button>
        {a.error && <span className="error small">{a.error}</span>}
      </div>}</td>
    </tr>
  );
}

function Completeness() {
  const rows = useAsync(() => api.completenessGaps(), []);
  return (
    <Card title="Fields most of a kind have, and some lack">
      <p className="small muted">Fix these in the source system (or set the value on the entity); the next load and rebuild
        bring it in.</p>
      <Status loading={rows.loading && !rows.data} error={rows.error} />
      {rows.data && !rows.data.length && <Empty>No field is missing from a few of a kind that most of it carry.</Empty>}
      {rows.data && rows.data.length > 0 && <Table density="compact">
        <thead><tr><th>Kind</th><th>Field</th><th>Have it</th><th>Lacking (first few)</th></tr></thead>
        <tbody>{rows.data.map((g) => (
          <tr key={`${g.kind}|${g.field}`}>
            <td>{g.kind.replace(/_/g, " ")}</td><td>{fieldLabel(g.field)}</td>
            <td>{g.have} of {g.total} ({Math.round(g.share * 100)}%)</td>
            <td>{(g.examples ?? []).slice(0, 8).map((x, i) => <span key={x.fuzer_id}>{i > 0 && ", "}
              <a href={entityHref(x.fuzer_id)}>{x.label ?? x.fuzer_id}</a></span>)}
              {g.missing > 8 && <span className="muted"> and {g.missing - 8} more</span>}</td>
          </tr>
        ))}</tbody>
      </Table>}
    </Card>
  );
}

function Conflicts() {
  const rows = useAsync(() => api.conflictGaps(), []);
  return (
    <Card title="Sources that disagree">
      <p className="small muted">The field rule asks a person to pick. Decide them on the <a href={href("/review")}>Review</a> page.</p>
      <Status loading={rows.loading && !rows.data} error={rows.error} />
      {rows.data && !rows.data.length && <Empty>No open conflicts.</Empty>}
      {rows.data && rows.data.length > 0 && <Table density="compact">
        <thead><tr><th>Entity</th><th>Field</th><th>Values</th></tr></thead>
        <tbody>{rows.data.map((c) => (
          <tr key={c.id}>
            <td><a href={entityHref(c.fuzer_id)}>{c.label ?? c.fuzer_id}</a></td><td>{fieldLabel(c.field)}</td>
            <td>{c.values.map((v) => <div key={`${v.source}:${v.source_key}`}>{fmt(v.value)} <span className="muted">({v.source})</span></div>)}</td>
          </tr>
        ))}</tbody>
      </Table>}
    </Card>
  );
}
