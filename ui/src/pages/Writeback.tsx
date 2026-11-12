import { Badge as UIBadge, Button, Card as UICard, Empty, Input, Table } from "../ui";
import { useState } from "react";
import { api, type Writeback, type WritebackDetail } from "../api";
import { Status, useAsync } from "../lib";
import { Actor, DataView, when } from "../work";
import { caseHref, useAct } from "./workflows/shared";

const STATE: Record<Writeback["state"], string> = {
  drafted: "drafted",
  awaiting_approval: "waiting for approval",
  approved: "approved, waiting for its batch",
  sent: "sent",
  confirmed: "confirmed in the system",
  failed: "failed: back with the approver",
  rejected: "not sent",
};
const tone = (s: Writeback["state"]) => (s === "confirmed" || s === "sent" ? "ok" : s === "failed" ? "late" : "");
const fromCloseout = (w: Writeback) => (w.source_ref ?? "").startsWith("closeout:");

/** The changes proposed to source systems on this case (P5.7): nothing is written until a named person in the right
 * role approves it, it goes in the system's next batch, and a failure comes back here. */
export function CaseWritebacks({ itemId, person, readOnly, reload }:
  { itemId: number; person: string; readOnly: boolean; reload: () => void }) {
  const list = useAsync(() => api.writebacks({ item_id: itemId }), [itemId]);
  const rows = Array.isArray(list.data) ? list.data : [];
  if (!rows.length) return null;
  return <>{rows.map((w) => <WritebackPanel key={w.id} id={w.id} person={person} readOnly={readOnly}
    reload={() => { list.reload(); reload(); }} />)}</>;
}

function WritebackPanel({ id, person, readOnly, reload }: { id: number; person: string; readOnly: boolean; reload: () => void }) {
  const d = useAsync(() => api.writeback(id), [id]);
  const [text, setText] = useState<string | null>(null);
  const [why, setWhy] = useState("");
  const a = useAct();
  if (!d.data) return <Status loading={d.loading} error={d.error} />;
  const w: WritebackDetail = d.data;
  const system = w.system.toUpperCase();
  const done = () => { d.reload(); reload(); setText(null); setWhy(""); };
  const hasText = typeof w.payload.text === "string";
  const decide = !readOnly && (w.state === "awaiting_approval" || w.state === "failed") && !fromCloseout(w);
  const owner = !readOnly && w.state === "sent" && w.adapter === "export";
  const payload = hasText && text !== null ? { ...w.payload, text } : undefined;
  return (
    <UICard className="" title={`${w.label} for ${system}${w.record ? ` ${w.record}` : ""}`}>
      <p className="small">
        <UIBadge className={`badge ${tone(w.state)}`}>{STATE[w.state]}</UIBadge>{" "}
        {w.approvers?.length ? <>Approved by a {w.approvers.join(" or ").replace(/_/g, " ")}</> : null}
        {w.cadence && <>, then sent {w.cadence}</>}.
        {w.adapter === "export" && <span className="muted"> {system} has no write API here: it goes into an export file for
          its owner, who confirms once it is applied.</span>}
      </p>
      {w.error && <p role="alert" className="error small">{w.error}</p>}
      {hasText ? (
        <textarea rows={3} className="lesson-text" aria-label="Change to write" disabled={!decide}
          value={text ?? String(w.payload.text)} onChange={(e) => setText(e.target.value)} />
      ) : <DataView data={w.payload} />}
      {decide && (
        <div className="row wrap">
          <Button variant="primary" disabled={!!a.busy || !person} onClick={() => a.run("approve", () =>
            api.approveWriteback(id, person, payload), done)}>{w.state === "failed" ? "Approve it again" : `Approve for ${system}`}</Button>
          <Input placeholder="why not (kept in its history)" value={why} onChange={(e) => setWhy(e.target.value)} />
          <Button variant="secondary" disabled={!!a.busy || !person} onClick={() => a.run("reject", () =>
            api.rejectWriteback(id, person, why || "not sent"), done)}>Don't send</Button>
        </div>
      )}
      {owner && (
        <div className="row wrap">
          <Button variant="primary" disabled={!!a.busy || !person} onClick={() => a.run("confirm", () =>
            api.confirmWriteback(id, person, why || undefined), done)}>Applied in {system}</Button>
          <Input placeholder="what went wrong, or a note" value={why} onChange={(e) => setWhy(e.target.value)} />
          <Button variant="secondary" disabled={!!a.busy || !person || !why} onClick={() => a.run("fail", () =>
            api.failWriteback(id, person, why), done)}>Couldn't apply it</Button>
        </div>
      )}
      {a.error && <p role="alert" className="error small">{a.error}</p>}
      <ul className="plain small">{w.history.map((h) => (
        <li key={h.id}>{STATE[h.state as Writeback["state"]] ?? h.state} · <Actor who={h.by} /> · {when(h.at)}
          {h.note && <span className="muted"> · {h.note}</span>}</li>
      ))}</ul>
    </UICard>
  );
}

/** What is queued for this system and what went (P5.7), on its Sources card. */
export function SystemWritebacks({ system }: { system: string }) {
  const list = useAsync(() => api.writebacks({ system }), [system]);
  const rows = Array.isArray(list.data) ? list.data : [];
  if (!list.data) return <Status loading={list.loading} error={list.error} />;
  return (
    <>
      <h3>Changes written back</h3>
      {!rows.length ? <Empty className="small">Nothing proposed for {system} yet.</Empty> : (
        <Table density="compact">
          <thead><tr><th>Change</th><th>Record</th><th>State</th><th>Approved by</th><th>Goes at</th><th>Case</th></tr></thead>
          <tbody>{rows.slice(0, 50).map((w) => (
            <tr key={w.id}>
              <td>{w.kind.replace(/_/g, " ")}</td>
              <td>{w.record ?? ""}</td>
              <td><UIBadge className={`badge ${tone(w.state)}`}>{STATE[w.state]}</UIBadge>
                {w.error && <div className="small error">{w.error}</div>}</td>
              <td>{w.approved_by ? <Actor who={w.approved_by} /> : <span className="muted">{w.approver_role?.replace(/_/g, " ") ?? ""}</span>}</td>
              <td className="small">{w.state === "approved" && w.due_at ? when(w.due_at) : w.sent_at ? `sent ${when(w.sent_at)}` : ""}</td>
              <td>{w.item_id ? <a href={caseHref(w.item_id)}>case {w.item_id}</a> : ""}</td>
            </tr>
          ))}</tbody>
        </Table>
      )}
    </>
  );
}
