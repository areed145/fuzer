/** Rules the register learned from stewards (P6.5): once people have answered enough of one source's values the same
 * way, the way they answered is proposed as a rule, with the values it would answer. Accepted, it answers them now and
 * every like value after each rebuild; withdrawn, its answers are retired. */
import { api, type LearnedProposal, type LearnedRule } from "../../api";
import { Button, Card, ConfirmAction, Disclosure, Table } from "../../ui";
import { entityHref, useAsync } from "../../lib";
import { usePerson } from "../../work";
import { useAct } from "../workflows/shared";
import { usePermission } from "../../Auth";

export function LearnedRules({ onChange }: { onChange?: () => void }) {
  const data = useAsync(() => api.learnedRules(), []);
  const [person] = usePerson();
  const operate = usePermission("operate");
  const a = useAct();
  const done = () => { data.reload(); onChange?.(); };
  const proposals = data.data?.proposals ?? [];
  const accepted = (data.data?.rules ?? []).filter((r) => r.status === "accepted");
  if (!proposals.length && !accepted.length) return null;
  const can = operate && !!person;
  return (
    <Card title="Rules learned from your answers">
      {proposals.map((p) => <Proposal key={`${p.source}|${p.kind}|${p.prefix}|${p.suffix}`} p={p} busy={!!a.busy} can={can}
        onAccept={() => a.run("accept", () => api.decideLearned(true, p, person, `${p.taught} like answers`), done)}
        onReject={() => a.run("reject", () => api.decideLearned(false, p, person), done)} />)}
      {accepted.length > 0 && <Table density="compact" className="phone-rows">
        <thead><tr><th>Accepted rule</th><th>Answers</th><th>By</th><th /></tr></thead>
        <tbody>{accepted.map((r: LearnedRule) => (
          <tr key={r.id}>
            <td>{r.rule}</td><td data-label="Answers">{r.answers}</td>
            <td data-label="By" className="small">{r.decided_by}, {r.decided_at.slice(0, 10)}</td>
            <td>{can && <Button variant="link" disabled={!!a.busy}
              onClick={() => a.run("withdraw", () => api.withdrawLearned(r.id, person), done)}>Withdraw</Button>}</td>
          </tr>
        ))}</tbody>
      </Table>}
      {a.error && <p className="error small">{a.error}</p>}
    </Card>
  );
}

function Proposal({ p, busy, can, onAccept, onReject }: {
  p: LearnedProposal; busy: boolean; can: boolean; onAccept: () => void; onReject: () => void;
}) {
  return (
    <div className="learned-proposal">
      <p><strong>{p.rule}.</strong> Learned from {p.taught} answers, such as{" "}
        {p.examples.slice(0, 3).map((e, i) => <span key={e.id}>{i ? ", " : ""}<code>{e.value}</code></span>)}.</p>
      <Disclosure label={`It would answer ${p.would_answer} more ${p.would_answer === 1 ? "value" : "values"} on ${p.rows} ${p.rows === 1 ? "row" : "rows"}`}>
        <ul className="plain small">{p.values.map((v) => (
          <li key={v.value}><code>{v.value}</code> names <a href={entityHref(v.fuzer_id)}>{v.key}</a> ({v.rows} {v.rel.replace(/_/g, " ")})</li>
        ))}</ul>
        {p.would_answer > p.values.length && <p className="small muted">The first {p.values.length} of {p.would_answer}.</p>}
      </Disclosure>
      {can && <div className="row">
        <ConfirmAction label="Accept rule" busy={busy}
          consequence={`Records an alias for each of the ${p.would_answer} values now, and for every like ${p.source.toUpperCase()} value after each rebuild. Withdrawing it retires them.`}
          onConfirm={onAccept} />
        <Button disabled={busy} onClick={onReject}>Not a rule</Button>
      </div>}
    </div>
  );
}
