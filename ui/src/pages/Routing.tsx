import { lazy, Suspense, useEffect, useState, type FormEvent } from "react";
import { api, type Route, type RouteExplanation, type RouteStep } from "../api";
import { href, Status, useAsync } from "../lib";
import { Badge, Button, Card, EmptyState, Field, Input, PageHeader, Table } from "../ui";

const BpmnView = lazy(() => import("../BpmnView"));

/** Where each type of work goes when it arrives (#507, #509): one table, each type's process drawn with the people
 * who hold its lanes, and a dry run that writes nothing. Processes are edited on Processes; role SLAs on Users. */
export function RoutingPage({ query }: { query: URLSearchParams }) {
  const routes = useAsync(() => api.routes(), []);
  const type = query.get("type");
  const rows = routes.data ?? [];
  const chosen = rows.find((r) => r.type === type) ?? null;
  return <section>
    <PageHeader subtitle="What happens to each type of work when it arrives.">Routing</PageHeader>
    {!routes.data ? <Status loading={routes.loading} error={routes.error} />
      : rows.length === 0 ? <EmptyState title="No types of work yet">Source packs and modules declare them.</EmptyState>
      : <Table variant="list" className="routing-table">
        <thead><tr><th>Type</th><th>Declared in</th><th>Joins one event</th><th>Starts</th><th>Due</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.type} aria-current={r.type === type ? "true" : undefined}>
          <td><a href={href("/routing", { type: r.type })}><strong>{r.label}</strong></a> <span className="muted small mono">{r.type}</span></td>
          <td className="small">{r.declared_in}{r.source_system ? ` (${r.source_system})` : ""}</td>
          <td className="small">{r.joins ? `within ${r.join_hours} h` : "no"}</td>
          <td className="small"><Starts r={r} /></td>
          <td className="small">{dueText(r)}</td>
        </tr>)}</tbody>
      </Table>}
    {chosen && <RouteDetail key={chosen.type} route={chosen} />}
  </section>;
}

function Starts({ r }: { r: Route }) {
  if (!r.process) return <span className="muted">waits for the IOC to dispatch</span>;
  return <><a className="mono" href={href(`/processes/${r.process}`)}>{r.process}</a>
    {r.role && <> in the <a href={href("/teams", { role: r.role })}>{r.role}</a> queue</>}</>;
}

function dueText(r: Route) {
  if (!r.due) return <span className="muted">by its role's SLA</span>;
  const map = Object.entries(r.due.hours).map(([p, h]) => `${p}: ${h} h`).join(", ");
  return <>{map || "off"}{r.due.setting && <span className="muted"> (setting <span className="mono">{r.due.setting}</span>)</span>}</>;
}

function RouteDetail({ route }: { route: Route }) {
  const [form, setForm] = useState({ refs: "", key: "", priority: "", to: "" });
  const [asked, setAsked] = useState(form);
  const plan = useAsync(() => api.explainRoute(route.type, { ...asked, refs: asked.refs.split(",") }), [route.type, asked]);
  const run = (e: FormEvent) => { e.preventDefault(); setAsked(form); };
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const x = plan.data;
  return <Card title={<>{route.label} <Badge>{route.case_type.replace(/_/g, " ")}</Badge></>} className="route-detail">
    {route.description && <p className="small muted">{route.description}</p>}
    <form className="row" onSubmit={run} aria-label="Try an intake">
      <Field label="Refs" hint="comma separated, e.g. tag:P-101"><Input value={form.refs} onChange={set("refs")} /></Field>
      <Field label="Key" width="sm"><Input value={form.key} onChange={set("key")} /></Field>
      <Field label="Priority" width="sm"><Input value={form.priority} onChange={set("priority")} /></Field>
      <Field label="Dispatch to" width="sm" hint="a person or role:name"><Input value={form.to} onChange={set("to")} /></Field>
      <Button type="submit">Try it</Button>
    </form>
    {!x ? <Status loading={plan.loading} error={plan.error} /> : <Explanation x={x} />}
  </Card>;
}

function Explanation({ x }: { x: RouteExplanation }) {
  const steps = x.process?.steps ?? [];
  const nobody = steps.filter((s) => s.kind === "person" && !s.holders?.length);
  return <>
    <p role="status" className="route-summary"><strong>{x.summary}</strong></p>
    {(x.matched.length > 0 || x.unmatched.length > 0) && <p className="small">
      {x.matched.length > 0 && <>Matches {x.matched.map((m) => m.label).join(", ")}. </>}
      {x.unmatched.length > 0 && <span className="muted">No match for {x.unmatched.join(", ")}.</span>}</p>}
    {x.due && <p className="small">Due {new Date(x.due).toLocaleString()}.</p>}
    {nobody.length > 0 && <p role="alert" className="notice small">Nobody holds {nobody.map((s) => s.role).join(", ")}:
      work there waits unseen. <a href={href("/users", { view: "workflow" })}>Assign someone</a>.</p>}
    {x.process?.published && <ProcessPicture processKey={x.process.key} steps={steps} />}
    {steps.length > 0 && <Table variant="list">
      <thead><tr><th>Step</th><th>Lane</th><th>Held by</th><th>SLA</th><th>Escalates to</th></tr></thead>
      <tbody>{steps.map((s) => <tr key={s.element}>
        <td>{s.step}</td>
        <td>{s.kind === "agent" ? `${s.role} agent` : s.role}</td>
        <td>{s.kind === "agent" ? <span className="muted">agent worker</span>
          : s.holders?.length ? s.holders.join(", ") : <Badge tone="warn">nobody</Badge>}</td>
        <td>{s.sla ?? <span className="muted">none</span>}{s.sla_from === "role" && <span className="muted small"> (role)</span>}</td>
        <td className="small">{s.escalation.length ? s.escalation.join(" → ") : <span className="muted">stays with its role</span>}</td>
      </tr>)}</tbody>
    </Table>}
    {x.process && <p className="small"><a href={href(`/processes/${x.process.key}`)}>Edit the {x.process.key} process</a>
      {" · "}<a href={href("/users", { view: "workflow" })}>role SLAs and escalation</a></p>}
  </>;
}

/** The published process, each person step labelled with who holds its lane. */
function ProcessPicture({ processKey, steps }: { processKey: string; steps: RouteStep[] }) {
  const [xml, setXml] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api.workDefinitions(processKey).then((defs) => {
      const pub = defs.filter((d) => d.state === "published").sort((a, b) => b.version - a.version)[0];
      return pub ? api.workDefinitionXml(pub.id) : null;
    }).then((x) => live && setXml(x), () => live && setXml(null));
    return () => { live = false; };
  }, [processKey]);
  if (!xml) return null;
  const esc = (t: string) => t.replace(/[&<>"]/g, (c) => `&#${c.charCodeAt(0)};`);
  const overlays = Object.fromEntries(steps.filter((s) => s.kind === "person").map((s) => [s.element,
    `<span class="ui-badge badge ui-badge-${s.holders?.length ? "muted" : "warn"}">${esc(s.holders?.length ? s.holders.join(", ") : "nobody")}</span>`]));
  return <Suspense fallback={<div className="status">Loading diagram…</div>}><BpmnView xml={xml} overlays={overlays} /></Suspense>;
}
