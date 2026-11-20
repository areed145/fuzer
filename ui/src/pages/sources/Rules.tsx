import { Button, Empty, Input, Table, Card as UICard, ErrorText, Line, Text, Checkbox, Heading } from "../../ui";
import { percent, when } from "../../format";
/** Survivorship and match rules (#225): the systems' rank (the default priority) and field / match rules. */
import { useState } from "react";
import { api, type MatchCalibration } from "../../api";
import { Status, useAsync, useAction } from "../../lib";
import { reviewer } from "./common";

export function Rules() {
  const r = useAsync(() => api.rules(), []);
  const a = useAction();
  const [field, setField] = useState("");
  const [prio, setPrio] = useState("review, sap");
  const [conflict, setConflict] = useState(false);
  if (!r.data) return <Status of={r} />;
  return (
    <>
      <UICard title="Default priority, lower rank wins">
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
      <UICard title="Field and match rules">
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
        <Heading level={3}>Set a field rule</Heading>
        <Line>
          <Input placeholder="field (manufacturer)" value={field} onChange={(e) => setField(e.target.value)} />
          <Input placeholder="priority: review, sap, maximo" value={prio} onChange={(e) => setPrio(e.target.value)} />
          <Checkbox checked={conflict} onChange={setConflict}>review conflicts</Checkbox>
          <Button variant="primary" disabled={!field || !!a.busy}
            onClick={() => a.run("set", () => api.setRule("field", field, {
              priority: prio.split(",").map((s) => s.trim()).filter(Boolean), review_on_conflict: conflict,
            }, reviewer()), r.reload)}>Save</Button>
          <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("rematch", () => api.rematch())}>Rebuild</Button>
        </Line>
        <ErrorText>{a.error}</ErrorText>
      </UICard>
    </>
  );
}

const pct = (x: number | null | undefined) => (x == null ? "not measured" : percent(x, 1));

/** Match confidence per source (P2.2): the thresholds in use and what was measured at them against the stewards'
 * decisions and the answer key, with the lower bound that says how much the pairs behind it can show. */
function MatchConfidence({ rows, reload }: { rows: MatchCalibration[]; reload: () => void }) {
  const a = useAction();
  const changes = rows.filter((x) => x.recommended && x.in_use &&
    (x.recommended.auto_accept !== x.in_use.auto_accept || x.recommended.review_min !== x.in_use.review_min));
  return (
    <UICard className="match-confidence" title="Match confidence">
      {!rows.length ? <Empty className="small">Not measured yet. Measure after a rebuild.</Empty> : (
        <Table variant="list" density="compact">
          <thead><tr><th>Source</th><th>Straight-through</th><th>Measured there</th><th>Review from</th></tr></thead>
          <tbody>{rows.map((x) => {
            const at = x.at_auto_accept;
            return <tr key={`${x.kind}/${x.source}`}>
              <th scope="row"><code>{x.source}</code> <Text small muted>{x.kind}</Text></th>
              <td>{x.in_use ? x.in_use.auto_accept.toFixed(2) : "?"}
                {x.recommended && x.in_use && x.recommended.auto_accept !== x.in_use.auto_accept &&
                  <Text small muted> measured: {x.recommended.auto_accept.toFixed(2)}</Text>}</td>
              <td>{at && at.pairs ? <>{pct(at.precision)} correct of {at.pairs} pairs
                <Text small muted> (at least {pct(at.lower)}; finds {pct(at.recall)} of true matches)</Text></>
                : <Text small muted>no labelled pair scored this high</Text>}</td>
              <td>{x.in_use ? x.in_use.review_min.toFixed(2) : "?"}</td>
            </tr>;
          })}</tbody>
        </Table>
      )}
      {rows.length > 0 && <Text as="p" small>Measured {when(rows[0].measured_at)}
        on {rows.reduce((n, x) => n + x.pairs, 0)} labelled pairs ({rows[0].labels}); a record that fits two items always goes to a person.</Text>}
      <Line>
        <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("measure", () => api.calibrate(reviewer()), reload)}>
          {a.busy === "measure" ? "Measuring…" : "Measure"}</Button>
        {changes.length > 0 && <Button variant="primary" disabled={!!a.busy}
          onClick={() => a.run("adopt", () => api.adoptCalibration(reviewer()), reload)}>
          Use the measured thresholds for {changes.map((x) => x.source).join(", ")} (from the next rebuild)</Button>}
      </Line>
      <ErrorText>{a.error}</ErrorText>
    </UICard>
  );
}
