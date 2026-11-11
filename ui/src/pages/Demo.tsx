import "../styles/investigation.css";
import { useEffect, useRef } from "react";
import { api, type Stats } from "../api";
import { href, Status, timestamp, useAsync } from "../lib";
import { usePermission } from "../Auth";
import { usePerson } from "../work";
import { Badge, Button, ButtonLink, Card, EmptyState, PageHeader } from "../ui";

const STEPS = [
  { key: "start", label: "Get oriented" },
  { key: "evidence", label: "Trace an issue" },
  { key: "agents", label: "Meet the agents" },
  { key: "workflow", label: "Follow the work" },
  { key: "finish", label: "Take it forward" },
] as const;

export function DemoPage({ query, stats }: {
  query: URLSearchParams; stats?: Stats;
}) {
  const index = Math.max(0, STEPS.findIndex((s) => s.key === query.get("step")));
  const step = STEPS[index];
  const heading = useRef<HTMLHeadingElement>(null);
  const ai = usePermission("ai"), configure = usePermission("configure");
  const catalog = useAsync(() => step.key === "agents" ? api.assistantCatalog() : Promise.resolve(undefined), [step.key]);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [step.key]);
  return <section className="investigation">
    <PageHeader subtitle="A guided introduction to connected evidence, specialist agents and human-owned workflows.">Demo</PageHeader>
    <div className="investigation-context">
      <Badge>Synthetic Okan scenario</Badge>{" "}<Badge>Optional paid AI</Badge>{" "}<Badge>Shared workflow state</Badge>
      <p>The Okan investigation uses March 2024 records with April follow-up, not live plant conditions.
        The tour never generates data, calls AI or changes work on page load.</p>
    </div>
    <nav aria-label="Demo steps"><ol className="investigation-steps demo-steps">
      {STEPS.map((s, i) => <li key={s.key}><a href={href("/demo", { step: s.key })}
        aria-current={s.key === step.key ? "step" : undefined}><span>{i + 1}</span>{s.label}</a></li>)}
    </ol></nav>
    <Card className="investigation-stage">
      <p className="muted small">Step {index + 1} of {STEPS.length}</p>
      <h2 ref={heading} tabIndex={-1} id="demo-step">{step.label}</h2>
      {step.key === "start" && <>
        <p>New to Fuzer? Start with one asset, inspect where its records came from, then compare
          specialist findings and see how work passes between agents and accountable people.</p>
        <ol><li>Trace an Okan issue through its asset, evidence and cited finding.</li>
          <li>Choose a specialist or a cross-domain question; review and explicitly confirm any paid call in Ask.</li>
          <li>Inspect loaded cases, process history and your task queue. Deliberate task actions affect shared work.</li>
          <li>Leave with evidence you can check and a clear next destination.</li></ol>
        <p>No AI access or provider? The evidence journey and workflow inspection still work.
          Demo visibility does not grant application permissions or workflow assignments.</p>
      </>}
      {step.key === "evidence" && <>
        <h3>New to Fuzer? Investigate an Okan issue</h3>
        <p>Follow train B's March 2024 issue from its asset to source evidence and a finding.
          Fictional, historical data. Read-only, with no setup or AI key needed.</p>
        {stats?.pack === "okan" && stats.records > 0
          ? <ButtonLink variant="primary" href={href("/investigate/okan")}>Start guided investigation</ButtonLink>
          : <EmptyState title="Okan demonstration records are not confirmed loaded">
            <p>Opening the investigation checks its actual prerequisites; missing records are not a successful demo.</p>
            <ButtonLink href={href("/investigate/okan")}>Check investigation availability</ButtonLink>
            {configure && <p><a href={href("/sources")}>Inspect Sources</a> before a deliberate load.</p>}
          </EmptyState>}
        <p>Beyond the walkthrough, <a href={href("/network")}>Network</a> connects assets,
          <a href={href("/downtime")}> Downtime</a> ranks recorded losses and
          <a href={href("/")}> Home</a> charts production and capacity evidence. Coverage depends on loaded sources.</p>
      </>}
      {step.key === "agents" && <>
        <p>The catalog below comes from Fuzer's capability registry, not a claim that every required source
          or provider is ready. Ask shows agent/tool activity, citations and reported usage when a question runs.</p>
        {!ai && <p>Paid questions require AI access from an owner. You can still inspect the capability descriptions.</p>}
        {ai && <ButtonLink href={href("/ask", { demo: "agents", q: "Using the loaded historical Okan records, compare production losses, equipment reliability and work-order readiness. Cite evidence with dates and units, identify missing inputs, and propose questions for human review, not operating instructions." })}>
          Prepare a cross-domain question
        </ButtonLink>}
        <Status loading={catalog.loading && !catalog.data} error={catalog.error} onRetry={catalog.reload} />
        {catalog.data && !catalog.error && (catalog.data.agents.length ? catalog.data.agents.map((agent) => <details className="investigation-evidence" key={agent.id}>
          <summary><strong>{agent.label}</strong> · {agent.description}</summary>
          <h3>Registered skills</h3><ul>{agent.skills.map((s) => <li key={s}>{s}</li>)}</ul>
          <h3>Limitations and human decisions</h3>
          <ul>{[...agent.limits, ...agent.human_decisions].map((s) => <li key={s}>{s}</li>)}</ul>
          {ai && <ButtonLink href={href("/ask", { demo: "agents", q: `Review the loaded historical Okan evidence from the perspective of ${agent.label}. ${agent.description} Cite supporting records, dates and units; state missing prerequisites and leave decisions to accountable people.` })}>
            Prepare a question for {agent.label}
          </ButtonLink>}
        </details>) : <EmptyState title="Agent catalog unavailable">
          <p>Refresh the catalog or ask an administrator to check the server.</p>
          <Button onClick={catalog.reload}>Refresh catalog</Button>
        </EmptyState>)}
        {catalog.data?.unavailable.length ? <><h3>Missing capability prerequisites</h3><ul>
          {catalog.data.unavailable.map((u) => <li key={`${u.agent}:${u.capability}`}>{u.capability}: {u.needed}</li>)}
        </ul></> : null}
        {configure && <p><a href={href("/settings", { filter: "agents." })}>Inspect provider configuration</a>.
          Reference presence is not proof of a working provider; questions may incur charges.</p>}
      </>}
      {step.key === "workflow" && <Workflow />}
      {step.key === "finish" && <>
        <p>You can now trace source evidence, distinguish a specialist's answer from a human decision,
          and inspect a case's process and recorded history. Visiting these steps does not certify that
          an agent ran or a task completed.</p>
        <ul><li>Check findings against their citations and event dates, not ingestion timestamps.</li>
          <li>Review agent activity and usage in Ask; missing evidence remains unknown.</li>
          <li>Inspect the case and task history to verify any saved change. A completed task is not proof of downstream execution.</li></ul>
        <ButtonLink href={href("/browse")}>Explore loaded evidence</ButtonLink>{" "}
        <ButtonLink href={href("/exceptions", { list: "ranked" })}>Open Work</ButtonLink>
        {configure && <p><a href={href("/settings", { filter: "demo.enabled" })}>Hide or enable Demo application-wide</a>.</p>}
      </>}
    </Card>
    <div className="investigation-controls">
      <ButtonLink href={index ? href("/demo", { step: STEPS[index - 1].key }) : href("/")}>
        {index ? `Back: ${STEPS[index - 1].label}` : "Return home"}</ButtonLink>
      <ButtonLink variant="primary" href={index < STEPS.length - 1 ? href("/demo", { step: STEPS[index + 1].key }) : href("/")}>
        {index < STEPS.length - 1 ? `Next: ${STEPS[index + 1].label}` : "Finish demo"}</ButtonLink>
    </div>
  </section>;
}

function Workflow() {
  const [person] = usePerson();
  const operate = usePermission("operate");
  const cases = useAsync(() => api.cases({ state: "all", limit: 8 }), []);
  const inbox = useAsync(() => person ? api.inbox(person) : Promise.resolve(null), [person]);
  const tasks = [...(inbox.data?.mine ?? []), ...(inbox.data?.queue ?? [])].filter((t) => t.task_id).slice(0, 6);
  return <>
    <p>Workflows connect evidence, agent steps, human review and a recorded outcome. These are actual shared
      cases, not a private sandbox and not necessarily part of the compressor scenario. No case is created by this tour.</p>
    <h3>1. Inspect a loaded case</h3>
    <p>Open its process diagram, waiting steps and timeline. Agent steps and human-only decisions remain distinct.</p>
    <Status loading={cases.loading && !cases.data} error={cases.error} onRetry={cases.reload} />
    {cases.data && !cases.error && (cases.data.items.length ? <ul>{cases.data.items.map((c) => <li key={c.id}>
      <a href={href(`/cases/${c.id}`, { demo: "workflow" })}>{c.title}</a> · {c.state} · {c.process_key ?? "No process recorded"}
      <span className="muted small"> · Opened: {timestamp(c.created_at)} (recorded workflow timestamp)</span>
    </li>)}</ul> : <EmptyState title="No workflow cases loaded">
      The workflow demonstration is unavailable. Ask an administrator to inspect the existing Work and Jobs setup; nothing was seeded.
    </EmptyState>)}
    <h3>2. Review your next task</h3>
    <p>{operate ? "Claim, complete or hand off only a task you intend to change. Confirmed actions are audited and may advance the shared process."
      : "You have read-only access. Task changes require operate access and applicable workflow roles; Demo grants neither."}</p>
    <Status loading={inbox.loading && !inbox.data} error={inbox.error} onRetry={inbox.reload} />
    {!person && <p>Your account or local attribution is needed to show your queue. <a href={href("/inbox")}>Open Inbox</a>.</p>}
    {inbox.data && !inbox.error && (tasks.length ? <ul>{tasks.map((t) => <li key={t.task_id}>
      <a href={href(`/inbox/task/${t.task_id}`, { demo: "workflow" })}>{t.name ?? t.title}</a> · {t.kind} · {t.role ?? "No role recorded"}
    </li>)}</ul> : <p>No tasks are assigned or available in your queues. A role assignment is not supplied by Demo.</p>)}
    <h3>3. Verify the recorded result</h3>
    <p>After a deliberate change, inspect the task and case history. After an ambiguous failure,
      inspect saved state before retrying; nothing is automatically replayed.</p>
    <ButtonLink href={href("/processes")}>Inspect process definitions</ButtonLink>{" "}
    <ButtonLink href={href("/inbox")}>Open Inbox</ButtonLink>
  </>;
}
