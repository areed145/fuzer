import { api, type ModelVersion, type Standing } from "../api";
import { Status, useAsync } from "../lib";
import { Badge, Button, Card, Table } from "../ui";

const TONE: Record<NonNullable<Standing["verdict"]>, "ok" | "warn" | "danger" | "muted"> = {
  standard: "ok", justified: "ok", drifted: "danger", no_model: "muted",
};
const LABEL: Record<NonNullable<Standing["verdict"]>, string> = {
  standard: "runs the standard", justified: "differs, with reasons", drifted: "drifted", no_model: "no operating model",
};
const STATE: Record<ModelVersion["state"], "ok" | "warn" | "danger" | "muted"> = {
  published: "ok", approved: "warn", proposed: "muted", rejected: "danger",
};
const pct = (v: number | null | undefined) => (v == null ? "none due" : `${v}%`);

/** Conformance (P8.2): for each asset the person may open, the operating model it runs, where it differs and
 * whether each difference is justified (named, owned and in date), and whether its work follows the standard in
 * practice. Reads only. */
export function Standards() {
  const c = useAsync(() => api.conformance(28), []);
  const v = useAsync(() => api.modelVersions(), []);
  const d = c.data;
  return <div>
    <h2>Standards</h2>
    <p>Each asset against its operating model. A difference is justified when it has a reason, an owner and a review
      date that hasn't passed; past it, the owner gets the review as work.</p>
    <Button disabled={c.loading} onClick={() => { c.reload(); v.reload(); }}>Refresh</Button>
    <Status loading={c.loading && !d} error={c.error} />
    {d && <p className="small">Current standard: {d.current.model} v{d.current.version}. Work in practice over the
      last {d.days} days.</p>}
    {d?.projects.map((p) => <Card key={p.project}
      title={<>{p.project} {p.verdict && <Badge tone={TONE[p.verdict]}>{LABEL[p.verdict]}</Badge>}</>}>
      {p.error ? <p>Could not read it: {p.error}</p> : p.verdict === "no_model" ? <p>{p.note}</p> : <>
        <p>{p.model} v{p.version}{p.current === false ? " (not the current version)" : ""},{" "}
          {p.applied ? "applied as it is" : "not applied yet"}. {p.deviations?.length
            ? `${p.deviations.length} difference${p.deviations.length === 1 ? "" : "s"}, ${p.unnamed} without a reason, ${p.overdue} past review.`
            : "No differences."}</p>
        {!!p.deviations?.length && <Table density="compact" className="phone-rows">
          <thead><tr><th>Where</th><th>Change</th><th>Why</th><th>Owner</th><th>Review by</th></tr></thead>
          <tbody>{p.deviations.map((x) => <tr key={`${x.part}:${x.key}`}>
            <td data-label="Where">{x.part.replace("_", " ")}: {x.key}</td>
            <td data-label="Change">{x.change}</td>
            <td data-label="Why">{x.reason ?? <Badge tone="danger">no reason given</Badge>}</td>
            <td data-label="Owner">{x.owner ?? "nobody"}</td>
            <td data-label="Review by">{x.review_by ?? "not set"}{x.overdue && <> <Badge tone="danger">overdue</Badge></>}</td>
          </tr>)}</tbody>
        </Table>}
        {p.practice && <ul className="small">
          <li>Steps on time: {pct(p.practice.sla.met_pct)} of {p.practice.sla.due} due
            ({p.practice.sla.late} late, {p.practice.sla.open_past_due} still open past due).</li>
          <li>Overrides: {p.practice.overrides.sent_not_ready} sent to the field though not ready,{" "}
            {p.practice.overrides.started_without_permit} started without a permit.</li>
          <li>Steps skipped: {p.practice.skipped} left undone when their case closed.</li>
          <li>Rounds done: {pct(p.practice.rounds.done_pct)} of {p.practice.rounds.rounds}
            ({p.practice.rounds.partly_done} partly, {p.practice.rounds.missed} missed).</li>
        </ul>}
      </>}
    </Card>)}
    {d && d.left_out.length > 0 && <p className="small">Not shown, because you aren't granted them:{" "}
      {d.left_out.join(", ")}.</p>}
    <h3>Changes to the standard</h3>
    <p>A change is proposed, test-run on closed cases, reviewed by someone other than its author, published, and then
      rolled out asset by asset. Each rollout can be rolled back. Use <code>fuzer model-change</code> to act.</p>
    <Status loading={v.loading && !v.data} error={v.error} />
    {v.data && <Table density="compact" className="phone-rows">
      <thead><tr><th>Version</th><th>State</th><th>What</th><th>Proposed by</th><th>Test run</th><th>Review</th></tr></thead>
      <tbody>{v.data.versions.map((x) => <tr key={`${x.name}:${x.version}`}>
        <td data-label="Version">{x.name} v{x.version}</td>
        <td data-label="State"><Badge tone={STATE[x.state]}>{x.state}</Badge></td>
        <td data-label="What">{x.summary}</td>
        <td data-label="Proposed by">{x.proposed_by}</td>
        <td data-label="Test run">{x.test_run ? `${x.test_run.summary.cases} cases on ${x.test_run.project}${x.test_run.problems.length ? `, ${x.test_run.problems.length} problems` : ""}` : "none"}</td>
        <td data-label="Review">{x.reviewed_by ? `${x.reviewed_by}: ${x.review_note}` : "none"}</td>
      </tr>)}</tbody>
    </Table>}
    {v.data && v.data.rollouts.length > 0 && <Table density="compact" className="phone-rows">
      <thead><tr><th>Asset</th><th>Rolled out</th><th>From</th><th>State</th><th>By</th></tr></thead>
      <tbody>{v.data.rollouts.map((r) => <tr key={r.id}>
        <td data-label="Asset">{r.project}</td>
        <td data-label="Rolled out">{r.name} v{r.version}</td>
        <td data-label="From">{r.from_name} v{r.from_version}</td>
        <td data-label="State">{r.state.replace("_", " ")}{r.rollback_reason ? `: ${r.rollback_reason}` : ""}</td>
        <td data-label="By">{r.rolled_back_by ?? r.by}</td>
      </tr>)}</tbody>
    </Table>}
  </div>;
}
