import { Button, Empty, Input, Table, Card as UICard } from "../../ui";
/** Survivorship and match rules (#225): the systems' rank (the default priority) and field / match rules. */
import { useState } from "react";
import { api, type MatchCalibration } from "../../api";
import { Status, useAsync } from "../../lib";
import { reviewer, useAction } from "./common";

export function Rules() {
  const r = useAsync(() => api.rules(), []);
  const a = useAction();
  const [field, setField] = useState("");
  const [prio, setPrio] = useState("review, sap");
  const [conflict, setConflict] = useState(false);
  if (!r.data) return <Status loading={r.loading} error={r.error} />;
  return (
    <>
      <UICard className="">
        <h2>Default priority <span className="muted small">the systems' rank: lower wins; a reviewer's decision always comes first</span></h2>
        <Table variant="list" density="compact">
          <tbody>
            {r.data.ranks.map((s) => (
              <tr key={s.name}>
                <td><code>{s.name}</code></td>
                <td>
                  <Input width="xs" type="number" defaultValue={s.rank ?? ""}
                    onBlur={(e) => {
                      const v = e.target.value === "" ? null : Number(e.target.value);
                      if (v !== s.rank) a.run("rank", () => api.setRank(s.name, v, reviewer()), r.reload);
                    }} />
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </UICard>
      <MatchConfidence rows={r.data.calibration ?? []} reload={r.reload} />
      <UICard className="">
        <h2>Field and match rules</h2>
        <Table variant="list" density="compact">
          <thead><tr><th>Scope</th><th>Name</th><th>Rule</th><th>By</th><th /></tr></thead>
          <tbody>
            {r.data.rules.map((x) => (
              <tr key={x.id}>
                <td>{x.scope}</td><td><code>{x.name}</code></td><td className="mono">{JSON.stringify(x.config)}</td>
                <td className="muted">{x.updated_by}</td>
                <td><Button variant="link" onClick={() => a.run("del", () => api.deleteRule(x.scope, x.name, reviewer()), r.reload)}>remove</Button></td>
              </tr>
            ))}
          </tbody>
        </Table>
        <h3>Set a field rule</h3>
        <div className="row">
          <Input placeholder="field (manufacturer)" value={field} onChange={(e) => setField(e.target.value)} />
          <Input placeholder="priority: review, sap, maximo" value={prio} onChange={(e) => setPrio(e.target.value)} />
          <label className="small"><Input type="checkbox" checked={conflict} onChange={(e) => setConflict(e.target.checked)} /> review conflicts</label>
          <Button variant="primary" disabled={!field || !!a.busy}
            onClick={() => a.run("set", () => api.setRule("field", field, {
              priority: prio.split(",").map((s) => s.trim()).filter(Boolean), review_on_conflict: conflict,
            }, reviewer()), r.reload)}>Save</Button>
          <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("rematch", () => api.rematch())}>Rebuild</Button>
        </div>
        {a.error && <p className="error small">{a.error}</p>}
      </UICard>
    </>
  );
}

const pct = (x: number | null | undefined) => (x == null ? "not measured" : `${Number((x * 100).toFixed(1))}%`);

/** Match confidence per source (P2.2): the thresholds in use and what was measured at them against the stewards'
 * decisions and the answer key, with the lower bound that says how much the pairs behind it can show. */
function MatchConfidence({ rows, reload }: { rows: MatchCalibration[]; reload: () => void }) {
  const a = useAction();
  const changes = rows.filter((x) => x.recommended && x.in_use &&
    (x.recommended.auto_accept !== x.in_use.auto_accept || x.recommended.review_min !== x.in_use.review_min));
  return (
    <UICard className="match-confidence">
      <h2>Match confidence</h2>
      <p className="small">A match at or above straight-through merges on its own; between review and straight-through
        a person decides; below review it is left apart. Each is measured against what stewards decided and the
        answer key, aiming at {pct(rows[0]?.target ?? 0.99)} correct.</p>
      {!rows.length ? <Empty className="small">Not measured yet. Measure after a rebuild.</Empty> : (
        <Table variant="list" density="compact">
          <thead><tr><th>Source</th><th>Straight-through</th><th>Measured there</th><th>Review from</th></tr></thead>
          <tbody>{rows.map((x) => {
            const at = x.at_auto_accept;
            return <tr key={`${x.kind}/${x.source}`}>
              <th scope="row"><code>{x.source}</code> <span className="muted small">{x.kind}</span></th>
              <td>{x.in_use ? x.in_use.auto_accept.toFixed(2) : "?"}
                {x.recommended && x.in_use && x.recommended.auto_accept !== x.in_use.auto_accept &&
                  <span className="muted small"> measured: {x.recommended.auto_accept.toFixed(2)}</span>}</td>
              <td>{at && at.pairs ? <>{pct(at.precision)} correct of {at.pairs} pairs
                <span className="muted small"> (at least {pct(at.lower)}; finds {pct(at.recall)} of true matches)</span></>
                : <span className="muted small">no labelled pair scored this high</span>}</td>
              <td>{x.in_use ? x.in_use.review_min.toFixed(2) : "?"}</td>
            </tr>;
          })}</tbody>
        </Table>
      )}
      {rows.length > 0 && <p className="small">Measured {new Date(rows[0].measured_at).toISOString().slice(0, 16).replace("T", " ")} UTC
        on {rows.reduce((n, x) => n + x.pairs, 0)} labelled pairs ({rows[0].labels}); a record that fits two items always goes to a person.</p>}
      <div className="row">
        <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("measure", () => api.calibrate(reviewer()), reload)}>
          {a.busy === "measure" ? "Measuring…" : "Measure"}</Button>
        {changes.length > 0 && <Button variant="primary" disabled={!!a.busy}
          onClick={() => a.run("adopt", () => api.adoptCalibration(reviewer()), reload)}>
          Use the measured thresholds for {changes.map((x) => x.source).join(", ")} (from the next rebuild)</Button>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </UICard>
  );
}
