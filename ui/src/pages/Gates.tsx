import { Badge as UIBadge, Button, Card as UICard, Input, Table } from "../ui";
import { useState } from "react";
import { api, type GateDecision, type GatePolicy } from "../api";
import { Status, useAsync } from "../lib";
import { Actor, loadPerson, when } from "../work";

const pct = (x: number | null) => (x === null ? "always a person" : `${Math.round(x * 100)}%`);

/** Why a case did or didn't stop for a person (P5.10): each gate decision on it, in plain words. */
export function CaseGates({ id }: { id: number }) {
  const gates = useAsync(() => api.workItemGates(id), [id]);
  const rows = Array.isArray(gates.data) ? gates.data : [];
  if (!rows.length) return null;
  return (
    <UICard className="" title="Gates">
      <ul className="plain gate-list">{rows.map((d) => <GateLine key={d.id} d={d} />)}</ul>
    </UICard>
  );
}

function GateLine({ d }: { d: GateDecision }) {
  return (
    <li>
      <UIBadge className={`badge ${d.fired ? "late" : "ok"}`}>{d.fired ? "Stopped for a person" : "Went ahead"}</UIBadge>
      <span>{d.reason}</span>
      <span className="small">
        {d.confidence !== null && <>confidence {pct(d.confidence)} · </>}needs {pct(d.threshold)}
        {d.criticality && <> · criticality {d.criticality}</>} · <Actor who={d.actor} /> · {when(d.at)}
      </span>
    </li>
  );
}

/** The gate policy in Settings (P5.10): the fixed gates, which nothing changes, and each confidence gate's
 * threshold by asset criticality. A-criticality assets always stop for a person. */
export function HumanGates() {
  const policy = useAsync(() => api.gatePolicy(), []);
  if (!policy.data) return <Status loading={policy.loading} error={policy.error} />;
  const p = policy.data;
  if (!Array.isArray(p.fixed) || !Array.isArray(p.confidence)) return null;  // an older server without gates
  return (
    <UICard className="" title="Human gates">
      <p>Always a person, whatever the confidence: {p.fixed.map((f) => f.label.toLowerCase()).join("; ")}.
        Safety and environmental case types: {p.safety_case_types.join(", ")}.</p>
      <Table variant="settings">
        <thead><tr><th>Automatic or agent decision</th><th>Goes ahead at</th><th>A-criticality</th><th>Other criticality</th><th /></tr></thead>
        <tbody>{p.confidence.map((c) => <GateRow key={c.kind} c={c} saved={policy.reload} />)}</tbody>
      </Table>
    </UICard>
  );
}

function GateRow({ c, saved }: { c: GatePolicy["confidence"][number]; saved: () => void }) {
  const [edit, setEdit] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const others = Object.entries(c.by_criticality).filter(([k]) => k !== "A");
  const save = () => {
    const v = edit?.trim() ?? "";
    const n = v === "" ? null : Number(v) / 100;
    if (n !== null && !(n > 0 && n <= 1)) return setError("Give a percentage from 1 to 100, or leave it empty for always a person.");
    api.setGate(c.kind, loadPerson() || "anonymous", n, Object.fromEntries(others))
      .then(() => { setEdit(null); setError(null); saved(); }, (e: Error) => setError(e.message));
  };
  return (
    <tr>
      <td>{c.label}{c.updated_by && <div className="small">changed by {c.updated_by}{c.updated_at ? `, ${when(c.updated_at)}` : ""}</div>}</td>
      <td>{edit === null ? <b>{pct(c.default)}</b> : <Input aria-label={`Goes ahead at, for ${c.label}`} type="number" min={1}
        max={100} value={edit} placeholder="always a person" onChange={(e) => setEdit(e.target.value)} />}
        {error && <div role="alert" className="error small">{error}</div>}</td>
      <td>{pct(c.by_criticality.A ?? null)}</td>
      <td className="small">{others.length ? others.map(([k, v]) => `${k}: ${pct(v)}`).join(", ") : "as the default"}</td>
      <td>{edit === null
        ? <Button onClick={() => setEdit(c.default === null ? "" : String(Math.round(c.default * 100)))}>Change</Button>
        : <><Button variant="primary" onClick={save}>Save</Button> <Button onClick={() => { setEdit(null); setError(null); }}>Cancel</Button></>}</td>
    </tr>
  );
}
