import { useState } from "react";
import { Button, Card as UICard, Disclosure, Input, Table, ErrorText } from "../../ui";
import { api } from "../../api";
import { Status, entityHref, useAction, useAsync } from "../../lib";
import { usePermission } from "../../Auth";
import { count, money, quantity } from "../../format";

const q = (v: number | null | undefined, unit?: string | null) => (v == null ? "?" : quantity(v, unit));

/** A proposal's working, on its case and its step: what it is worth and the sum that makes it (the value its scan
 * stored, the same one Home, the board and the daily report read), each move with its wells, the tests it rests on and
 * how old they are, and the engineer's decision. Without it the case was "Do the task" over a dollar figure. */
export function OpportunityPanel({ opportunityKey, person, readOnly, onDecided }: {
  opportunityKey: string; person: string; readOnly?: boolean; onDecided: () => void;
}) {
  const view = useAsync(() => api.opportunity(opportunityKey), [opportunityKey]);
  const operate = usePermission("operate");
  const a = useAction();
  const [why, setWhy] = useState("");
  const o = view.data;
  const w = o?.worth;
  const tests = o?.evidence.records.length ?? 0;
  const ageDays = o?.evidence.last ? Math.round((Date.now() - Date.parse(o.evidence.last)) / 86_400_000) : null;
  const decide = (d: "accepted" | "rejected" | "project") =>
    a.run(d, () => api.decideOpportunity(opportunityKey, d, person, d === "rejected" ? why : undefined), () => {
      view.reload(); onDecided();
    });
  return (
    <UICard title="The proposal">
      <Status of={view} />
      {o && <>
        {w ? <p>Worth <strong>{money(w.usd, w.currency || "USD")}</strong>: {q(w.gain, w.gain_unit)} more oil
          for {q(w.days)} days ({quantity(w.oil_bbl, "bbl")}) at {money(w.price_bbl, w.currency || "USD")} a barrel.</p>
          : <p className="small">Not valued: the scan found no rate to gain.</p>}
        {typeof o.detail.basis === "string" && <p className="small">How: {o.detail.basis}.</p>}
        <p className="small">
          {tests > 0 && <>Rests on {count(tests)} record{tests === 1 ? "" : "s"}
            {o.evidence.first && <>, tested {o.evidence.first}{o.evidence.last !== o.evidence.first ? ` to ${o.evidence.last}` : ""}</>}
            {ageDays != null && ageDays > 90 && <strong>: the newest is {Math.round(ageDays / 30)} months old</strong>}. </>}
          {typeof o.detail.confidence === "string" && <>Confidence {o.detail.confidence}. </>}
          {typeof o.detail.confirm_by === "string" && <>Confirm by {o.detail.confirm_by}.</>}
        </p>
        {o.moves.length > 0 && <Disclosure open className="card" label={<>{o.moves.length} move{o.moves.length === 1 ? "" : "s"}</>}>
          <Table density="compact">
            <thead><tr><th>From</th><th>To</th><th>Gas moved</th><th>Oil gained</th></tr></thead>
            <tbody>{o.moves.map((m, i) => <tr key={i}>
              <td>{m.from_id ? <a href={entityHref(m.from_id)}>{m.from}</a> : m.from}</td>
              <td>{m.to_id ? <a href={entityHref(m.to_id)}>{m.to}</a> : m.to}</td>
              <td>{q(m.gas, m.gas_unit)}</td>
              <td>{q(m.gain, m.gain_unit)}</td>
            </tr>)}</tbody>
          </Table>
        </Disclosure>}
        {tests > 0 && <Disclosure className="card" label={<>The records it rests on</>}>
          <ul className="small">{o.evidence.records.map((r) => <li key={r}>{r.split(":").slice(1).join(" ").replace(/_/g, " ")}</li>)}</ul>
        </Disclosure>}
        {o.status !== "open" ? <p className="small muted">{o.status} by {o.decided_by ?? "?"}{o.reason ? `: ${o.reason}` : ""}.</p>
          : operate && !readOnly && <div className="row wrap">
            <Button variant="primary" disabled={!!a.busy} onClick={() => decide("accepted")}>Accept</Button>
            <Button disabled={!!a.busy} onClick={() => decide("project")}>Make it a project</Button>
            <Input aria-label="Why reject it" placeholder="why reject it" value={why} onChange={(e) => setWhy(e.target.value)} />
            <Button disabled={!!a.busy || !why.trim()} onClick={() => decide("rejected")}>Reject</Button>
          </div>}
        <ErrorText>{a.error}</ErrorText>
      </>}
    </UICard>
  );
}
