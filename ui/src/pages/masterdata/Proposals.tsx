/** Claude's proposals for the steward (#476): which entity an unresolved value means, whether two records are one
 * item, which source's value is right, whether two spellings are one value. Each shows Claude's pick, confidence,
 * reason and evidence; nothing changes until a person accepts it (or picks another option). Bulk accept appears only
 * once `stewardship.bulk_accept_at` is set. */
import { useState } from "react";
import { confirmDialog, ErrorText, Line, Text, List } from "../../ui";
import { percent } from "../../format";
import { api, type Proposal } from "../../api";
import { Badge, Button, Card, Select, Table, Disclosure, Empty } from "../../ui";
import { Status, entityHref, useAsync, useAction } from "../../lib";
import { usePerson } from "../../work";
import { usePermission } from "../../Auth";

const KIND: Record<Proposal["kind"], string> = {
  unresolved: "Which asset", match: "Same item?", conflict: "Which value", spelling: "One spelling?",
};

function what(p: Proposal): string {
  const c = p.context as Record<string, any>;
  if (p.kind === "unresolved") return `“${c.value}” (${(c.seen ?? []).map((s: any) => `${s.source} ${s.link}`).join("; ")})`;
  if (p.kind === "match") return `${c.left?.source} ${c.left?.key ?? ""} and ${c.right?.source} ${c.right?.key ?? ""} at ${c.left?.tag ?? ""}`;
  if (p.kind === "conflict") return `${c.field} on ${c.entity}`;
  return `${c.field}: ${(c.values ?? []).map((v: any) => `“${v.value}” (${v.entities})`).join(" or ")}`;
}

export function ProposalsTab() {
  const [status, setStatus] = useState("open");
  const rows = useAsync(() => api.proposals(status), [status]);
  const [person] = usePerson();
  const operate = usePermission("operate");
  const a = useAction();
  const at = rows.data?.bulk_accept_at ?? 0;
  const confident = (rows.data?.proposals ?? []).filter((p) => p.answer && p.answer.choice !== "none" && p.answer.confidence >= at);
  return (
    <Card title="Claude's proposals">
      <Line>
        <Select aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="open">Open</option><option value="accepted">Accepted</option><option value="rejected">Rejected</option>
        </Select>
        {operate && person && <Button size="sm" disabled={!!a.busy || rows.data?.configured === false}
          title={rows.data?.configured === false ? "No model provider is configured (Settings › LLM)" : "Answers are cached; only new or changed questions are asked"}
          onClick={() => void a.run("ask", () => api.runProposals(), rows.reload)}>{a.busy === "ask" ? "Asking Claude…" : "Ask Claude"}</Button>}
        {operate && person && at > 0 && status === "open" && confident.length > 0 &&
          <Button size="sm" disabled={!!a.busy} onClick={async () => {
            if (!(await confirmDialog(`Accept the ${confident.length} proposals at ${percent(at)} confidence or more?`))) return;
            void a.run("bulk", () => api.acceptConfident(person), rows.reload);
          }}>Accept {confident.length} at ≥{percent(at)}</Button>}
      </Line>
      <ErrorText>{a.error}</ErrorText>
      <Status of={rows} />
      {rows.data && !rows.data.proposals.length && <Empty>{status === "open"
        ? "No open proposals. Ask Claude to look at what is unmatched, unsure or inconsistent." : `No ${status} proposals.`}</Empty>}
      {rows.data && rows.data.proposals.length > 0 && <Table density="compact">
        <thead><tr><th>Question</th><th>Claude</th><th>Why</th><th /></tr></thead>
        <tbody>{rows.data.proposals.map((p) => <Row key={p.id} p={p} done={rows.reload} />)}</tbody>
      </Table>}
    </Card>
  );
}

function Row({ p, done }: { p: Proposal; done: () => void }) {
  const [person] = usePerson();
  const operate = usePermission("operate");
  const [choice, setChoice] = useState(p.answer && p.answer.choice !== "none" ? p.answer.choice : "");
  const a = useAction();
  const picked = p.options.find((o) => o.id === p.answer?.choice);
  return (
    <tr>
      <td><Badge>{KIND[p.kind]}</Badge> {what(p)}</td>
      <td>{p.answer ? <>
        {p.answer.choice === "none" ? <Text muted>not sure</Text>
          : picked?.fuzer_id ? <a href={entityHref(picked.fuzer_id)}>{picked.text}</a> : picked?.text}
        {" "}<Text small muted>{percent(p.answer.confidence)}</Text></> : <Text muted>not asked yet</Text>}</td>
      <td className="small">{p.answer?.reason}
        {(p.answer?.evidence ?? []).length > 0 && <Disclosure label={<>evidence</>}>
          <List plain>{p.answer!.evidence.map((e, i) => <li key={i}><code>{e}</code></li>)}</List></Disclosure>}</td>
      <td>{p.status !== "open" ? <Text small muted>{p.status} by {p.decided_by}</Text>
        : operate && person && <Line>
          <Select aria-label="Option to accept" value={choice} onChange={(e) => setChoice(e.target.value)}>
            <option value="">choose…</option>
            {p.options.map((o) => <option key={o.id} value={o.id}>{o.text}</option>)}
          </Select>
          <Button size="sm" variant="primary" disabled={!choice || !!a.busy}
            onClick={() => void a.run("accept", () => api.acceptProposal(p.id, person, choice), done)}>Accept</Button>
          <Button size="sm" variant="link" disabled={!!a.busy}
            onClick={() => void a.run("reject", () => api.rejectProposal(p.id, person), done)}>Reject</Button>
          <ErrorText inline>{a.error}</ErrorText>
        </Line>}</td>
    </tr>
  );
}
