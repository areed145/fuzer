import { useState } from "react";
import { api, type ModelRollout, type ModelVersion, type Standing, type StandingDeviation } from "../api";
import { Status, useAsync } from "../lib";
import { ActionStatus, Badge, Button, Card, ConfirmAction, Disclosure, Field, Input, Select, Table } from "../ui";
import { loadPerson, usePerson } from "../work";

const TONE: Record<NonNullable<Standing["verdict"]>, "ok" | "warn" | "danger" | "muted"> = {
  standard: "ok", justified: "ok", drifted: "danger", no_model: "muted",
};
const LABEL: Record<NonNullable<Standing["verdict"]>, string> = {
  standard: "runs the standard", justified: "differs, with reasons", drifted: "drifted", no_model: "no operating model",
};
const STATE: Record<ModelVersion["state"], "ok" | "warn" | "danger" | "muted"> = {
  published: "ok", approved: "warn", proposed: "muted", rejected: "danger",
};
const ADMIN = "workflow_admin";
const PARTS = ["settings", "roles", "work_types", "forms"] as const;
const pct = (v: number | null | undefined) => (v == null ? "none due" : `${v}%`);

type Act = (label: string, fn: () => Promise<unknown>, done: string) => Promise<void>;

/** Conformance (P8.2) and change control (P8.3): each asset the person may open against its operating model, and
 * changes to the standard from proposal through test run, review and publishing to rollout and rollback. */
export function Standards() {
  const c = useAsync(() => api.conformance(28), []);
  const v = useAsync(() => api.modelVersions(), []);
  const people = useAsync(() => api.workPeople(), []);
  const [person] = usePerson();
  const admin = !!people.data?.find((p) => p.name === person)?.roles.includes(ADMIN);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const d = c.data;
  const projects = Object.fromEntries((d?.projects ?? []).filter((p) => !p.error && p.version != null)
    .map((p) => [p.project, `${p.model} v${p.version}`]));

  const act: Act = async (label, fn, message) => {
    setBusy(`${label}…`); setError(null); setDone(null);
    try {
      await fn();
      setDone(message);
    } catch (e) {
      setError(`${label} failed: ${(e as Error).message}.`);
    } finally {
      setBusy(null);
      c.reload(); v.reload();
    }
  };

  return <div>
    <h2>Standards</h2>
    <Button disabled={c.loading} onClick={() => { c.reload(); v.reload(); }}>Refresh</Button>
    <Status loading={c.loading && !d} error={c.error} />
    {d && <p className="small">Current standard: {d.current.model} v{d.current.version}. Work in practice over the
      last {d.days} days.</p>}
    {!person && <p className="small">Say who you are in the sidebar (bottom left) to propose, review or roll out.</p>}
    <ActionStatus busy={busy} error={error}>{done}</ActionStatus>
    {d?.projects.map((p) => <Card key={p.project}
      title={<>{p.project} {p.verdict && <Badge tone={TONE[p.verdict]}>{LABEL[p.verdict]}</Badge>}</>}>
      {p.error ? <p>Could not read it: {p.error}</p> : p.verdict === "no_model" ? <p>{p.note}</p> : <>
        <p>{p.model} v{p.version}{p.current === false ? " (not the current version)" : ""},{" "}
          {p.applied ? "applied as it is" : "not applied yet"}. {p.deviations?.length
            ? `${p.deviations.length} difference${p.deviations.length === 1 ? "" : "s"}, ${p.unnamed} without a reason, ${p.overdue} past review.`
            : "No differences."}</p>
        {!!p.deviations?.length && <Table density="compact" className="phone-rows">
          <thead><tr><th>Where</th><th>Change</th><th>Why</th><th>Owner</th><th>Review by</th><th>Standard</th></tr></thead>
          <tbody>{p.deviations.map((x) => <tr key={`${x.part}:${x.key}`}>
            <td data-label="Where">{x.part.replace("_", " ")}: {x.key}</td>
            <td data-label="Change">{x.change}</td>
            <td data-label="Why">{x.reason ?? <Badge tone="danger">no reason given</Badge>}</td>
            <td data-label="Owner">{x.owner ?? "nobody"}</td>
            <td data-label="Review by">{x.review_by ?? "not set"}{x.overdue && <> <Badge tone="danger">overdue</Badge></>}</td>
            <td data-label="Standard"><Promote project={p.project} deviation={x} person={person} busy={!!busy} act={act} /></td>
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

    <Card title="Changes to the standard">
      <Status loading={v.loading && !v.data} error={v.error} />
      {v.data && <Table density="compact" className="phone-rows">
        <thead><tr><th>Version</th><th>State</th><th>What</th><th>Proposed by</th><th>Test run</th><th>Review</th><th>Next</th></tr></thead>
        <tbody>{v.data.versions.map((x) => <tr key={`${x.name}:${x.version}`}>
          <td data-label="Version">{x.name} v{x.version}</td>
          <td data-label="State"><Badge tone={STATE[x.state]}>{x.state}</Badge></td>
          <td data-label="What">{x.summary}</td>
          <td data-label="Proposed by">{x.proposed_by}</td>
          <td data-label="Test run">{x.test_run ? <TestRun run={x.test_run} /> : "none"}</td>
          <td data-label="Review">{x.reviewed_by ? `${x.reviewed_by}: ${x.review_note}` : "none"}</td>
          <td data-label="Next"><Next version={x} person={person} admin={admin} busy={!!busy} act={act}
            projects={projects} rollouts={v.data!.rollouts} /></td>
        </tr>)}</tbody>
      </Table>}
      <Disclosure label="Propose a change">
        <Propose person={person} busy={!!busy} act={act} />
      </Disclosure>
    </Card>

    {v.data && v.data.rollouts.length > 0 && <Card title="Rollouts">
      <Table density="compact" className="phone-rows">
        <thead><tr><th>Asset</th><th>Rolled out</th><th>From</th><th>State</th><th>By</th><th>Rollback</th></tr></thead>
        <tbody>{v.data.rollouts.map((r) => <tr key={r.id}>
          <td data-label="Asset">{r.project}</td>
          <td data-label="Rolled out">{r.name} v{r.version}</td>
          <td data-label="From">{r.from_name} v{r.from_version}</td>
          <td data-label="State">{r.state.replace("_", " ")}{r.rollback_reason ? `: ${r.rollback_reason}` : ""}</td>
          <td data-label="By">{r.rolled_back_by ?? r.by}</td>
          <td data-label="Rollback">{r.state === "live" && <Rollback rollout={r} person={person} busy={!!busy} act={act} />}</td>
        </tr>)}</tbody>
      </Table>
    </Card>}
  </div>;
}

function TestRun({ run }: { run: NonNullable<ModelVersion["test_run"]> }) {
  const s = run.summary;
  return <>{s.cases} closed case{s.cases === 1 ? "" : "s"} on {run.project}: {s.met_before} met the old SLA, {s.met_after} would
    meet the new one{run.problems.length > 0 && <>. <Badge tone="danger">{run.problems.length} problem{run.problems.length === 1 ? "" : "s"}</Badge>
    {" "}{run.problems.join("; ")}</>}</>;
}

/** What moves a version on: a test run and a review while proposed, publishing once approved, rollout once
 * published. Each disabled action says why. */
function Next({ version: x, person, admin, busy, act, projects, rollouts }: {
  version: ModelVersion; person: string; admin: boolean; busy: boolean; act: Act; projects: Record<string, string>;
  rollouts: ModelRollout[];
}) {
  const [note, setNote] = useState("");
  const live = new Set(rollouts.filter((r) => r.state === "live" && r.version === x.version).map((r) => r.project));
  const targets = Object.entries(projects).filter(([p, runs]) => !live.has(p) && runs !== `${x.name} v${x.version}`)
    .map(([p]) => p);
  const [target, setTarget] = useState("");
  const chosen = targets.includes(target) ? target : targets[0] ?? "";
  if (x.state === "proposed") {
    const why = !person ? "Say who you are first." : person === x.proposed_by ? "Someone other than its author reviews it."
      : !admin ? `Reviewing needs the ${ADMIN} role.` : !x.test_run ? "Test-run it first." : null;
    return <>
      <Button disabled={busy} onClick={() => act("Test run", () => api.testRunModelChange(x.version),
        `v${x.version} was test-run on this project's closed cases.`)}>Test run</Button>
      {why ? <p className="small">{why}</p> : <>
        <Field label="Review note"><Input value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        <Button variant="primary" disabled={busy || !note.trim() || !!x.test_run?.problems.length}
          onClick={() => act("Approve", () => api.reviewModelChange(x.version, true, note, loadPerson()), `v${x.version} approved.`)}>
          Approve</Button>{" "}
        <Button disabled={busy || !note.trim()}
          onClick={() => act("Reject", () => api.reviewModelChange(x.version, false, note, loadPerson()), `v${x.version} rejected.`)}>
          Reject</Button>
      </>}
    </>;
  }
  if (x.state === "approved") {
    return admin ? <ConfirmAction label="Publish" busy={busy}
      consequence={`v${x.version} becomes a version every asset can be rolled out to. Nothing changes on an asset until it is rolled out.`}
      onConfirm={() => act("Publish", () => api.publishModelChange(x.version, loadPerson()), `v${x.version} published.`)} />
      : <p className="small">Publishing needs the {ADMIN} role.</p>;
  }
  if (x.state === "published") {
    if (!targets.length) return <span className="small">Every asset shown runs it.</span>;
    return <>
      <Field label="Asset" width="sm"><Select value={chosen} onChange={(e) => setTarget(e.target.value)}>
        {targets.map((p) => <option key={p}>{p}</option>)}</Select></Field>
      {admin ? <ConfirmAction label="Roll out" busy={busy} disabled={!chosen}
        consequence={`${chosen} starts running ${x.name} v${x.version}: its roles, settings, processes and forms change to this version. Its named setting differences are kept, and it can be rolled back.`}
        onConfirm={() => act("Roll out", () => api.rolloutModel(chosen, x.version, loadPerson()), `${chosen} now runs v${x.version}.`)} />
        : <p className="small">Rolling out needs the {ADMIN} role.</p>}
    </>;
  }
  return null;
}

function Rollback({ rollout: r, person, busy, act }: { rollout: ModelRollout; person: string; busy: boolean; act: Act }) {
  const [reason, setReason] = useState("");
  if (!person) return <span className="small">Say who you are first.</span>;
  return <>
    <Field label="Why" width="sm"><Input value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
    <ConfirmAction label="Roll back" busy={busy} disabled={!reason.trim()}
      consequence={`${r.project} goes back to ${r.from_name} v${r.from_version}: its roles, settings, processes and forms return to that version.`}
      onConfirm={() => act("Roll back", () => api.rollbackModel(r.project, reason, loadPerson()),
        `${r.project} is back on ${r.from_name} v${r.from_version}.`)} />
  </>;
}

/** A difference that proved better, proposed as the standard. */
function Promote({ project, deviation: x, person, busy, act }: {
  project: string; deviation: StandingDeviation; person: string; busy: boolean; act: Act;
}) {
  const [summary, setSummary] = useState("");
  if (x.change === "missing" || !person) return null;
  return <Disclosure label="Propose as the standard">
    <Field label="Why it should be the standard"><Input value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
    <Button disabled={busy || !summary.trim()} onClick={() => act("Propose",
      () => api.proposeModelChange({ summary, by: loadPerson(), promote: { part: x.part, key: x.key, project } }),
      `${project}'s ${x.key} was proposed as a new version of the standard.`)}>Propose</Button>
  </Disclosure>;
}

/** A change written out: a part, a key and its new value as JSON (empty removes it). A process is changed in the
 * process editor and then proposed from the asset's difference. */
function Propose({ person, busy, act }: { person: string; busy: boolean; act: Act }) {
  const [part, setPart] = useState<string>("settings");
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [summary, setSummary] = useState("");
  let parsed: unknown = null;
  let bad: string | null = null;
  if (value.trim()) {
    try { parsed = JSON.parse(value); } catch { bad = "The value isn't valid JSON."; }
  }
  if (!person) return <p className="small">Say who you are first.</p>;
  return <div>
    <Field label="Part" width="sm"><Select value={part} onChange={(e) => setPart(e.target.value)}>
      {PARTS.map((p) => <option key={p} value={p}>{p.replace("_", " ")}</option>)}</Select></Field>
    <Field label="Key" width="md"><Input value={key} onChange={(e) => setKey(e.target.value)} /></Field>
    <Field label="New value (JSON; empty removes it)" width="lg" hint={bad ?? undefined}>
      <textarea rows={3} value={value} onChange={(e) => setValue(e.target.value)} aria-invalid={!!bad} /></Field>
    <Field label="What it changes and why" width="lg"><Input value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
    <Button variant="primary" disabled={busy || !key.trim() || !summary.trim() || !!bad} onClick={() => act("Propose",
      () => api.proposeModelChange({ changes: { [part]: { [key.trim()]: parsed } }, summary, by: loadPerson() }),
      `The change to ${key.trim()} was proposed as a new version.`)}>Propose</Button>
  </div>;
}
