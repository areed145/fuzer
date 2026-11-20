import { person as personName } from "../wording";
import { when } from "../format";
import { lazy, Suspense, useEffect, useState, type FormEvent } from "react";
import { api, type Route, type RouteExplanation, type RouteStep, type WorkRoleRow } from "../api";
import { href, Status, useAsync } from "../lib";
import { Badge, Button, Card, EmptyState, Field, Input, PageHeader, Table, Text } from "../ui";
import { duration, usePerson } from "../work";
import { ExceptionTypes } from "./Exceptions";

const BpmnView = lazy(() => import("../BpmnView"));

/** Where each type of work goes when it arrives (#507, #509): one table, each type's process drawn with the people
 * who hold its lanes, and a dry run that writes nothing. Workflows are edited on Workflows (under Work); role SLAs on Users. */
export function RoutingPage({ query }: { query: URLSearchParams }) {
  const routes = useAsync(() => api.routes(), []);
  const type = query.get("type");
  const rows = routes.data ?? [];
  const chosen = rows.find((r) => r.type === type) ?? null;
  const [person] = usePerson();
  const roles = useAsync(() => api.rolesCatalog(), []);
  const queues = (roles.data ?? []).filter((r) => r.kind === "person").map((r) => r.name);
  const byRole = new Map((roles.data ?? []).map((r) => [r.name, r]));
  return <section>
    <PageHeader>Routing</PageHeader>
    {!routes.data ? <Status of={routes} />
      : rows.length === 0 ? <EmptyState title="No types of work yet">Source packs and modules declare them.</EmptyState>
      : <Table variant="list" className="routing-table phone-rows">
        <thead><tr><th>Type</th><th>Goes to</th><th>Due</th><th>Also</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.type} aria-current={r.type === type ? "true" : undefined}>
          <td><a href={href("/routing", { type: r.type })} title={r.type}><strong>{r.label}</strong></a></td>
          <td className="small"><Starts r={r} /></td>
          <td className="small">{dueText(r, byRole.get(r.role ?? ""))}</td>
          <td className="small muted">{also(r, byRole.get(r.role ?? ""))}</td>
        </tr>)}</tbody>
      </Table>}
    {chosen && <RouteDetail key={chosen.type} route={chosen} />}
    {/* Under the table once it is in place: shown sooner, the table arriving above it pushed it off the screen (#1069). */}
    {(routes.data || routes.error) && <ExceptionTypes person={person} queues={queues} />}
  </section>;
}

const words = (name: string | null) => (name ?? "").replace(/^role:/, "").replace(/_/g, " ");

function Starts({ r }: { r: Route }) {
  if (!r.process) return <Text muted>waits for the IOC to dispatch</Text>;
  // Most types start the plain queue process, so only a different process is worth naming.
  const process = <a href={href(`/workflows/${r.process}`)}>{words(r.process)}</a>;
  if (!r.role) return <>the {process} workflow</>;
  return <><a href={href("/teams", { role: r.role })}>{words(r.role)} queue</a>{r.process !== "queue_item" && <> via {process}</>}</>;
}

/** An ISO 8601 duration ("PT4H", "P1D") as hours, or null when it isn't one. */
export function isoHours(d: string | null | undefined): number | null {
  const m = /^P(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?)?$/.exec(d ?? "");
  if (!m || !(m[1] || m[2] || m[3])) return null;
  return Number(m[1] ?? 0) * 24 + Number(m[2] ?? 0) + Number(m[3] ?? 0) / 60;
}
const hoursText = (h: number) => (h >= 48 && h % 24 === 0 ? `${h / 24} days` : `${Number(h.toFixed(1))} h`);

/** Who else hears of it and what is unusual about a type: who an overdue step escalates to, where it was declared when
 * not in code, and whether repeats join one event (#1078). */
function also(r: Route, role?: WorkRoleRow) {
  return [role?.escalate_to && `escalates to the ${words(role.escalate_to)} when late`,
    r.joins && `repeats within ${r.join_hours} h join one event`,
    r.declared_in !== "code" && `from a ${r.declared_in}${r.source_system ? ` (${r.source_system})` : ""}`].filter(Boolean).join(" · ");
}

/** When it is due: the type's own hours, else its queue's SLA in hours, never the bare words "role SLA" (#1078). */
function dueText(r: Route, role?: WorkRoleRow) {
  if (!r.due) {
    const h = isoHours(role?.default_sla);
    return h == null ? <Text muted>no time set</Text>
      : <>{hoursText(h)} <Text muted>to act</Text></>;
  }
  const map = Object.entries(r.due.hours).map(([p, h]) => `${p}: ${h} h`).join(", ");
  return <>{map || "off"}{r.due.setting && <Text muted> (setting <Text mono>{r.due.setting}</Text>)</Text>}</>;
}

function RouteDetail({ route }: { route: Route }) {
  const [form, setForm] = useState({ refs: "", key: "", priority: "", to: "" });
  const [asked, setAsked] = useState(form);
  const plan = useAsync(() => api.explainRoute(route.type, { ...asked, refs: asked.refs.split(",") }), [route.type, asked]);
  const run = (e: FormEvent) => { e.preventDefault(); setAsked(form); };
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const x = plan.data;
  return <Card title={<>{route.label} <Badge>{route.case_type.replace(/_/g, " ")}</Badge></>} className="route-detail">
    {route.description && <Text as="p" small muted>{route.description}</Text>}
    <form className="row" onSubmit={run} aria-label="Try an intake">
      <Field label="Refs" hint="comma separated, e.g. tag:P-101"><Input value={form.refs} onChange={set("refs")} /></Field>
      <Field label="Key" width="sm"><Input value={form.key} onChange={set("key")} /></Field>
      <Field label="Priority" width="sm"><Input value={form.priority} onChange={set("priority")} /></Field>
      <Field label="Dispatch to" width="sm" hint="a person or role:name"><Input value={form.to} onChange={set("to")} /></Field>
      <Button type="submit">Try it</Button>
    </form>
    {!x ? <Status of={plan} /> : <Explanation x={x} />}
  </Card>;
}

function Explanation({ x }: { x: RouteExplanation }) {
  const steps = x.process?.steps ?? [];
  const nobody = steps.filter((s) => s.kind === "person" && !s.holders?.length);
  return <>
    <p role="status" className="route-summary"><strong>{x.summary}</strong></p>
    {(x.matched.length > 0 || x.unmatched.length > 0) && <Text as="p" small>
      {x.matched.length > 0 && <>Matches {x.matched.map((m) => m.label).join(", ")}. </>}
      {x.unmatched.length > 0 && <Text muted>No match for {x.unmatched.join(", ")}.</Text>}</Text>}
    {x.due && <Text as="p" small>Due {when(x.due)}.</Text>}
    {nobody.length > 0 && <p role="alert" className="notice small">Nobody holds {nobody.map((s) => words(s.role)).join(", ")}:
      work there waits unseen. <a href={href("/users", { view: "workflow" })}>Assign someone</a>.</p>}
    {x.process?.published && <ProcessPicture processKey={x.process.key} steps={steps} />}
    {steps.length > 0 && <Table variant="list" className="phone-rows">
      <thead><tr><th>Step</th><th>Lane</th><th>Held by</th><th>SLA</th><th>Escalates to</th></tr></thead>
      <tbody>{steps.map((s) => <tr key={s.element}>
        <td>{s.step}</td>
        <td>{s.kind === "agent" ? `${words(s.role)} agent` : words(s.role)}</td>
        <td>{s.kind === "agent" ? <Text muted>agent worker</Text>
          : s.holders?.length ? s.holders.map(personName).join(", ") : <Badge tone="warn">nobody</Badge>}</td>
        <td>{s.sla ? duration(s.sla) : <Text muted>none</Text>}{s.sla_from === "role" && <Text small muted> (role)</Text>}</td>
        <td className="small">{s.escalation.length ? s.escalation.map(words).join(" → ") : <Text muted>stays with its role</Text>}</td>
      </tr>)}</tbody>
    </Table>}
    {x.process && <Text as="p" small><a href={href(`/workflows/${x.process.key}`)}>Edit the {x.process.key} workflow</a>
      {" · "}<a href={href("/users", { view: "workflow" })}>role SLAs and escalation</a></Text>}
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
    `<span class="ui-badge badge ui-badge-${s.holders?.length ? "muted" : "warn"}">${esc(s.holders?.length ? s.holders.map(personName).join(", ") : "nobody")}</span>`]));
  return <Suspense fallback={<div className="status">Loading diagram…</div>}><BpmnView xml={xml} overlays={overlays} /></Suspense>;
}
