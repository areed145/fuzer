import { Button, Input, Table, Card as UICard } from "../../ui";
/** Survivorship and match rules (#225): the systems' rank (the default priority) and field / match rules. */
import { useState } from "react";
import { api } from "../../api";
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
          <input placeholder="field (manufacturer)" value={field} onChange={(e) => setField(e.target.value)} />
          <input placeholder="priority: review, sap, maximo" value={prio} onChange={(e) => setPrio(e.target.value)} />
          <label className="small"><input type="checkbox" checked={conflict} onChange={(e) => setConflict(e.target.checked)} /> review conflicts</label>
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
