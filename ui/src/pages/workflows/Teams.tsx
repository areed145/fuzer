import { ActionStatus, Button, Card, ConfirmAction, Input, PageHeader, Table, Select , Badge as UIBadge} from "../../ui";
/** Shared workflow administration in Users and contextual read-only team workload in Work. */
import { useState } from "react";
import { api, type WorkRoleRow, type WorkloadPerson } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { usePerson, when } from "../../work";
import { caseHref, processHref, useAct } from "./shared";
import { usePermission } from "../../Auth";

const ASSIGN: Record<string, string> = {
  queue: "wait in the queue for someone to claim", round_robin: "go to members in turn", least_loaded: "go to the least loaded member",
};

export function TeamsPage({ query, administration = false }: { query: URLSearchParams; administration?: boolean }) {
  const configure = usePermission("configure");
  const roles = useAsync(() => api.rolesCatalog(), []);
  const work = useAsync(() => api.workload(), []);
  const [person] = usePerson();
  const [message, setMessage] = useState("");
  const focus = query.get("role");
  if (!roles.data || !work.data) return <><Status loading={roles.loading || work.loading} error={roles.error ?? work.error} />
    {(roles.error || work.error) && <Button onClick={() => { roles.reload(); work.reload(); }}>Retry team workload</Button>}</>;
  const inspect = () => { setMessage(""); roles.reload(); work.reload(); };
  const reload = () => { inspect(); setMessage("Workflow change saved. Review updated assignments, coverage and workload below."); };
  const persons = roles.data.filter((r) => r.kind === "person");
  const uncovered = persons.filter((r) => r.uncovered);
  return (
    <section>
      <ActionStatus>{message && <p>{message}</p>}</ActionStatus>
      <Status loading={false} error={roles.error ?? work.error} />
      {administration ? <h2>Workflow assignments &amp; routing</h2> : <PageHeader>Team workload</PageHeader>}
      {!administration && <p>This operational view shows coverage and workload, not account access.
        {configure && <> Manage roles, assignments and coverage in <a href={href("/users", { view: "workflow", role: focus ?? undefined })}>Users</a>.</>}
        {" "}<a href={href("/cases")}>Open work cases</a></p>}
      <p className="muted">Who does the work. Each step goes to a role; a role's default SLA covers steps that set none, overdue
        steps move along its escalation, and its assignment decides whether steps wait to be claimed or go to someone. People
        away hand their steps to whoever covers for them.</p>
      {uncovered.length > 0 && (
        <p className="notice small">No one available for {uncovered.map((r) => <strong key={r.name}>{r.name} </strong>)}: their steps
          will wait. {administration ? "Assign an available person below." : "Ask a configuration administrator to update coverage in Users."}</p>
      )}
      {administration && <nav className="row" aria-label="Workflow administration sections">
        {[["workflow-roles", "Roles & routing"], ["workflow-people", "People"], ["workflow-agents", "Agents"]].map(([id, label]) =>
          <a key={id} href={href("/users", { ...Object.fromEntries(query), view: "workflow" })} onClick={event => {
            event.preventDefault();
            const heading = document.getElementById(id);
            heading?.focus();
            heading?.scrollIntoView({ block: "start" });
          }}>{label}</a>)}
      </nav>}
      <h2 id="workflow-roles" tabIndex={-1}>Roles</h2>
      {persons.map((r) => <RoleCard key={`${r.name}:${r.description}:${r.default_sla}:${r.assignment}:${r.escalate_to}`}
        r={r} roles={persons} person={person} open={focus === r.name} reload={reload} editable={administration && configure} />)}
      {administration && configure && <details><summary>Advanced: add a workflow role</summary><NewRole person={person} reload={reload} /></details>}
      <h2 id="workflow-people" tabIndex={-1}>People</h2>
      <People people={work.data.people} roles={persons} person={person} reload={reload}
        inspect={inspect} editable={administration && configure} focus={query.get("person")} />
      <h2 id="workflow-agents" tabIndex={-1}>Agents</h2>
      <Table variant="list" density="compact">
        <thead><tr><th>Agent</th><th>What it does</th><th>Steps waiting</th><th>Retrying</th><th>Used in</th></tr></thead>
        <tbody>
          {roles.data.filter((r) => r.kind === "agent").map((r) => {
            const q = work.data!.agents.find((x) => x.role === r.name);
            return (
              <tr key={r.name}><td><code>{r.name}</code></td><td>{r.description}</td><td>{q?.waiting ?? 0}</td>
                <td>{q?.failing ? <UIBadge className="badge late">{q.failing}</UIBadge> : 0}</td>
                <td>{r.processes.map((p) => <a key={p} href={processHref(p, "routing")}>{p} </a>)}</td></tr>
            );
          })}
        </tbody>
      </Table>
      {work.data.agent_failures.length > 0 && (
        <details className="small">
          <summary>Recent agent failures ({work.data.agent_failures.length})</summary>
          <ul>{work.data.agent_failures.map((f, i) => (
            <li key={i}>{when(f.at)} <code>{f.actor}</code> {f.item_id && <a href={caseHref(f.item_id)}>{f.title ?? `case ${f.item_id}`}</a>}:{" "}
              <span className="muted">{String(f.data.error ?? "")}</span></li>))}</ul>
        </details>
      )}
    </section>
  );
}

function RoleCard({ r, roles, person, open, reload, editable }: { r: WorkRoleRow; roles: WorkRoleRow[]; person: string; open: boolean;
                                                       reload: () => void; editable: boolean }) {
  const [v, setV] = useState({ description: r.description ?? "", default_sla: r.default_sla ?? "", escalate_to: r.escalate_to ?? "",
                               assignment: r.assignment });
  const a = useAct();
  const changed = v.description !== (r.description ?? "") || v.default_sla !== (r.default_sla ?? "") ||
    v.escalate_to !== (r.escalate_to ?? "") || v.assignment !== r.assignment;
  return (
    <details className="card" open={open}>
      <summary>
        <strong>{r.name}</strong> <span className="muted small">{r.description}</span>{" "}
        <span className="small">· {r.members.filter((m) => !m.away).length} available{r.members.some((m) => m.away) && `, ${r.members.filter((m) => m.away).length} away`}
          {" "}· {r.waiting} waiting{r.overdue ? <>, <UIBadge className="badge late">{r.overdue} overdue</UIBadge></> : null}
          {" "}· SLA {r.default_sla ?? "set per step"}{r.escalate_to && ` → ${r.escalate_to}`}</span>
        {r.uncovered && <UIBadge className="badge late"> no one available</UIBadge>}
      </summary>
      <fieldset className="workflow-routing-fields" disabled={!editable || !!a.busy} aria-label={`${r.name} routing configuration`}>
      <Table variant="keyValue" density="compact">
        <tbody>
          <tr><th>What it's for</th><td><Input aria-label={`${r.name} description`} width="lg" value={v.description} onChange={(e) => setV({ ...v, description: e.target.value })} /></td></tr>
          <tr><th>Default SLA</th><td><Input aria-label={`${r.name} default SLA`} value={v.default_sla} placeholder="e.g. PT4H, P1D (empty: steps set their own)"
            onChange={(e) => setV({ ...v, default_sla: e.target.value })} /> <span className="muted">for steps that set none</span></td></tr>
          <tr><th>When it passes, goes to</th><td>
            <Select aria-label={`${r.name} escalation role`} value={v.escalate_to} onChange={(e) => setV({ ...v, escalate_to: e.target.value })}>
              <option value="">stays with this role</option>
              {roles.filter((x) => x.name !== r.name).map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
            </Select> <span className="muted">(a step's own escalateTo wins; chains up to three hops)</span></td></tr>
          <tr><th>New steps</th><td>
            <Select aria-label={`${r.name} assignment policy`} value={v.assignment} onChange={(e) => setV({ ...v, assignment: e.target.value as WorkRoleRow["assignment"] })}>
              {Object.entries(ASSIGN).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </Select></td></tr>
          <tr><th>Members</th><td>{r.members.length ? r.members.map((m) => (
            <UIBadge key={m.name} className="badge">{m.name}{m.away && ` (away${m.delegate ? `, ${m.delegate} covers` : ""})`}</UIBadge>))
            : <span className="muted">none: give the role to someone under People</span>}</td></tr>
          <tr><th>Used in</th><td>{r.processes.length ? r.processes.map((p) => <a key={p} href={processHref(p, "routing")}>{p} </a>)
            : <span className="muted">no published process</span>}</td></tr>
        </tbody>
      </Table>
      </fieldset>
      {editable && <>
      <div className="row">
        <ConfirmAction label={`Save routing for ${r.name}`} disabled={!changed} busy={!!a.busy}
          consequence={`Update shared ${r.name} routing defaults? Step-specific settings still take precedence. Application access and account assignments do not change.`}
          onConfirm={() => a.run("save", () => api.setRole(r.name, { description: v.description || null, default_sla: v.default_sla || null,
            escalate_to: v.escalate_to || null, assignment: v.assignment }, person), reload)} />
        {!r.processes.length && !r.members.length && (
          <ConfirmAction label={`Delete workflow role ${r.name}`} busy={!!a.busy}
            consequence={`Delete unused workflow role ${r.name}? The server verifies it has no members or published process use.`}
            onConfirm={() => a.run("del", () => api.deleteRole(r.name, person), reload)} />
        )}
        {r.waiting > 0 && <a className="small" href={href("/cases", { who: `role:${r.name}` })}>see its {r.waiting} waiting</a>}
      </div>
      <ActionStatus busy={a.busy ? "Updating workflow routing..." : null} error={a.error} />
      </>}
    </details>
  );
}

function NewRole({ person, reload }: { person: string; reload: () => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [kind, setKind] = useState<"person" | "agent">("person");
  const a = useAct();
  return (
    <div className="row">
      <Input aria-label="New workflow role name" placeholder="new role, e.g. integrity_engineer" value={name} onChange={(e) => setName(e.target.value)} />
      <Input aria-label="New workflow role description" width="lg" placeholder="what it's for" value={desc} onChange={(e) => setDesc(e.target.value)} />
      <Select aria-label="Workflow role kind" value={kind} onChange={(e) => setKind(e.target.value as "person" | "agent")}><option value="person">people</option><option value="agent">an agent</option></Select>
      <ConfirmAction label="Add workflow role" disabled={!name.trim()} busy={!!a.busy}
        consequence={`Add ${name} as a ${kind === "person" ? "human routing" : "separate automated agent"} role? This does not grant application permissions. Owners span human roles only.`}
        onConfirm={() => a.run("add", () => api.setRole(name, { kind, description: desc || null }, person), () => { setName(""); setDesc(""); reload(); })} />
      <ActionStatus busy={a.busy ? "Adding workflow role..." : null} error={a.error} />
    </div>
  );
}

function People({ people, roles, person, reload, inspect, editable, focus }: { people: WorkloadPerson[]; roles: WorkRoleRow[]; person: string;
  reload: () => void; inspect: () => void; editable: boolean; focus: string | null }) {
  const [name, setName] = useState("");
  const a = useAct();
  return (
    <>
      {editable && <p>For signed-in users, assign roles directly from their account under Accounts &amp; access.
        Existing roster names are preserved, not silently linked to matching display names. Owners always retain all human
        workflow roles. Assignment changes do not grant application permissions.</p>}
      {focus && <p>Focused roster identity: <code>{focus}</code>. <a href={href("/users", { view: "workflow" })}>Show everyone</a></p>}
      {editable ? people.filter(p => !focus || p.name === focus).map(p => <PersonCard
        key={`${p.name}:${p.roles.join(",")}:${p.assigned_roles?.join(",")}:${p.away_until}:${p.delegate}:${p.account_enabled}:${p.owner}`}
        p={p} roles={roles} people={people} person={person} reload={reload} inspect={inspect} />)
        : <Table variant="list" density="compact">
          <thead><tr><th>Person</th><th>Effective roles</th><th>Assigned work</th><th>Overdue</th><th>Availability</th></tr></thead>
          <tbody>{people.filter(p => !focus || p.name === focus).map(p => <tr key={p.name}>
            <td>{p.display_name || p.name}</td><td>{p.account_enabled === false ? "None (account disabled)"
              : p.owner ? "All human roles (owner)" : p.roles.join(", ") || "None"}</td>
            <td><a href={href("/cases", { who: p.name })}>{p.assigned}</a></td><td>{p.overdue}</td>
            <td>{p.account_enabled === false ? "Account disabled" : p.away ? <>Away until {p.away_until ? when(p.away_until) : "unknown"}{p.delegate && `, ${p.delegate} covers`}</> : "Available"}</td>
          </tr>)}</tbody>
        </Table>}
      {!people.filter(p => !focus || p.name === focus).length && <p>No matching roster entries. Assign a signed-in account in Accounts &amp; access or add an explicit legacy identity below.</p>}
      {editable && <details><summary>Advanced: preserve or add a legacy roster identity</summary>
      <p>Use an existing stable account identity for Microsoft users. A roster entry is not a sign-in account or access grant.</p>
      <div className="row">
        <Input aria-label="Roster identity" placeholder="add a person" value={name} onChange={(e) => setName(e.target.value)} />
        <ConfirmAction label="Add roster identity" disabled={!name.trim() || people.some(p => p.name === name.trim())} busy={!!a.busy}
          consequence={`Add ${name.trim()} to the workflow roster with no roles? This does not create, enable or impersonate a Microsoft account.`}
          onConfirm={() => a.run("add", () => api.setWorkPerson(name.trim(), [], person), () => { setName(""); reload(); })} />
        <ActionStatus busy={a.busy ? "Adding roster identity..." : null} error={a.error} />
      </div>
      </details>}
    </>
  );
}

function PersonCard({ p, roles, people, person, reload, inspect }: { p: WorkloadPerson; roles: WorkRoleRow[]; people: WorkloadPerson[];
                                                          person: string; reload: () => void; inspect: () => void }) {
  const [awayOpen, setAwayOpen] = useState(false);
  const [until, setUntil] = useState("");
  const [delegate, setDelegate] = useState(p.delegate ?? "");
  const a = useAct();
  return (
    <Card className="access-account" title={p.display_name || p.name}>
      <p className="small">Roster identity: <code>{p.name}</code>. {p.account_enabled === false
        ? "Account disabled: saved assignments remain, but effective workflow access is empty."
        : p.account_enabled === true ? "Linked to a signed-in account." : "Legacy roster identity, not automatically linked to a sign-in account."}</p>
      <p><a href={href("/cases", { who: p.name })}>{p.assigned} assigned work items</a> · {p.overdue} overdue</p>
      <p>Saved workflow assignments: {(p.assigned_roles ?? p.roles).join(", ") || "None"}</p>
      <p>Effective workflow roles: {p.account_enabled === false ? "None (account disabled)"
        : p.owner ? "All human roles (owner)" : p.roles.join(", ") || "None"}</p>
      {p.owner ? <p>{p.account_enabled === false
        ? "Owner status is retained, but human workflow roles become effective only when the account is enabled."
        : "All human roles from owner status; explicit assignments preserved."}</p>
        : <WorkflowAssignments key={(p.assigned_roles ?? p.roles).join(",")} name={p.name} roles={roles}
            assigned={p.assigned_roles ?? p.roles} reload={reload} onInspect={inspect} />
      }
      <details><summary>Availability and coverage</summary>
        {p.away ? <>until {when(p.away_until!)}{p.delegate && `, ${p.delegate} covers`}{" "}
          <ConfirmAction label="Mark available" busy={!!a.busy}
            consequence="Clear the away period and its delegation? Inspect current work for any handoff already made."
            onConfirm={() => a.run("back", () => api.setAway(p.name, person, null), reload)} /></>
          : awayOpen ? (
            <span className="row">
              <Input aria-label={`Away until for ${p.name}`} type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
              <Select aria-label={`Coverage for ${p.name}`} value={delegate} onChange={(e) => setDelegate(e.target.value)}>
                <option value="">steps go back to the queue</option>
                {people.filter((x) => x.name !== p.name && x.account_enabled !== false).map((x) => <option key={x.name} value={x.name}>{x.display_name || x.name} covers</option>)}
              </Select>
              <ConfirmAction label="Set away" disabled={!until || !Number.isFinite(new Date(until).getTime())} busy={!!a.busy}
                consequence={`Set ${p.name} away until ${until}? Waiting work is handed to ${delegate || "its role queues"}. Application access does not change.`}
                onConfirm={() => a.run("away", () => api.setAway(p.name, person, new Date(until).toISOString(), delegate || null), () => { setAwayOpen(false); reload(); })} />
              <Button disabled={!!a.busy} onClick={() => setAwayOpen(false)}>Cancel away change</Button>
            </span>
          ) : <Button variant="link" onClick={() => setAwayOpen(true)}>set away</Button>}
      </details>
      {!p.owner && <details><summary>Remove workflow roster entry</summary><ConfirmAction label="Remove from roster" busy={!!a.busy}
        consequence={`Remove ${p.name} from the workflow roster? Claimed work returns to its queues. This does not disable a sign-in account.`}
        onConfirm={() => a.run("rm", () => api.removeWorkPerson(p.name, person), reload)} /></details>}
      <ActionStatus busy={a.busy ? "Updating workflow routing..." : null} error={a.error} />
    </Card>
  );
}

export function WorkflowAssignments({ name, roles, assigned, reload, onInspect }: {
  name: string; roles: WorkRoleRow[]; assigned: string[]; reload: () => void; onInspect: () => void;
}) {
  const [selected, setSelected] = useState(assigned);
  const [inspected, setInspected] = useState(false);
  const [person] = usePerson();
  const a = useAct();
  const dirty = [...selected].sort().join() !== [...assigned].sort().join();
  return <div>
    <fieldset disabled={!!a.busy}><legend>Saved workflow roles for {name}</legend>
      {roles.map(r => <label key={r.name} className="small choice-inline"><Input type="checkbox" checked={selected.includes(r.name)}
        onChange={e => setSelected(e.target.checked ? [...selected, r.name] : selected.filter(v => v !== r.name))} /> {r.name} </label>)}
      {!roles.length && <p>No human workflow roles defined. Add one in the advanced workflow role controls.</p>}
    </fieldset>
    <ConfirmAction label="Save workflow assignments" busy={!!a.busy} disabled={!dirty}
      consequence={`Change ${name}'s routing eligibility to ${selected.join(", ") || "none"}? Application access and already claimed work do not change.`}
      onConfirm={() => { setInspected(false); a.run("roles", () => api.setWorkPerson(name, selected, person), reload); }} />
    <ActionStatus busy={a.busy ? "Saving workflow assignments..." : null} error={inspected ? null : a.error} />
    {a.error && !inspected && <Button onClick={() => { setSelected(assigned); setInspected(true); onInspect(); }}>Reload saved assignments</Button>}
  </div>;
}
