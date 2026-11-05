import { Button, Input, PageHeader, Table } from "../../ui";
/** Teams & routing: the roles steps go to (what they're for, their default SLA, where overdue work goes, how it's
 * assigned), the people who hold them (workload, away and who covers), and the agents (their queues and failures). */
import { useState } from "react";
import { api, type WorkRoleRow, type WorkloadPerson } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { usePerson, when } from "../../work";
import { caseHref, processHref, useAct } from "./shared";

const ASSIGN: Record<string, string> = {
  queue: "wait in the queue for someone to claim", round_robin: "go to members in turn", least_loaded: "go to the least loaded member",
};

export function TeamsPage({ query }: { query: URLSearchParams }) {
  const roles = useAsync(() => api.rolesCatalog(), []);
  const work = useAsync(() => api.workload(), []);
  const [person] = usePerson();
  const focus = query.get("role");
  if (!roles.data || !work.data) return <Status loading={roles.loading || work.loading} error={roles.error ?? work.error} />;
  const reload = () => { roles.reload(); work.reload(); };
  const persons = roles.data.filter((r) => r.kind === "person");
  const uncovered = persons.filter((r) => r.uncovered);
  return (
    <section>
      <PageHeader>Teams &amp; routing</PageHeader>
      <p className="muted">Who does the work. Each step goes to a role; a role's default SLA covers steps that set none, overdue
        steps move along its escalation, and its assignment decides whether steps wait to be claimed or go to someone. People
        away hand their steps to whoever covers for them.</p>
      {uncovered.length > 0 && (
        <p className="notice small">No one available for {uncovered.map((r) => <strong key={r.name}>{r.name} </strong>)}: their steps
          will wait. Give the role to someone below.</p>
      )}
      <h2>Roles</h2>
      {persons.map((r) => <RoleCard key={r.name} r={r} roles={persons} person={person} open={focus === r.name} reload={reload} />)}
      <NewRole person={person} reload={reload} />
      <h2>People</h2>
      <People people={work.data.people} roles={persons} person={person} reload={reload} />
      <h2>Agents</h2>
      <Table variant="list" density="compact">
        <thead><tr><th>Agent</th><th>What it does</th><th>Steps waiting</th><th>Retrying</th><th>Used in</th></tr></thead>
        <tbody>
          {roles.data.filter((r) => r.kind === "agent").map((r) => {
            const q = work.data!.agents.find((x) => x.role === r.name);
            return (
              <tr key={r.name}><td><code>{r.name}</code></td><td>{r.description}</td><td>{q?.waiting ?? 0}</td>
                <td>{q?.failing ? <span className="badge late">{q.failing}</span> : 0}</td>
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

function RoleCard({ r, roles, person, open, reload }: { r: WorkRoleRow; roles: WorkRoleRow[]; person: string; open: boolean;
                                                       reload: () => void }) {
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
          {" "}· {r.waiting} waiting{r.overdue ? <>, <span className="badge late">{r.overdue} overdue</span></> : null}
          {" "}· SLA {r.default_sla ?? "set per step"}{r.escalate_to && ` → ${r.escalate_to}`}</span>
        {r.uncovered && <span className="badge late"> no one available</span>}
      </summary>
      <Table variant="keyValue" density="compact">
        <tbody>
          <tr><th>What it's for</th><td><Input width="lg" value={v.description} onChange={(e) => setV({ ...v, description: e.target.value })} /></td></tr>
          <tr><th>Default SLA</th><td><input value={v.default_sla} placeholder="e.g. PT4H, P1D (empty: steps set their own)"
            onChange={(e) => setV({ ...v, default_sla: e.target.value })} /> <span className="muted">for steps that set none</span></td></tr>
          <tr><th>When it passes, goes to</th><td>
            <select value={v.escalate_to} onChange={(e) => setV({ ...v, escalate_to: e.target.value })}>
              <option value="">stays with this role</option>
              {roles.filter((x) => x.name !== r.name).map((x) => <option key={x.name} value={x.name}>{x.name}</option>)}
            </select> <span className="muted">(a step's own escalateTo wins; chains up to three hops)</span></td></tr>
          <tr><th>New steps</th><td>
            <select value={v.assignment} onChange={(e) => setV({ ...v, assignment: e.target.value as WorkRoleRow["assignment"] })}>
              {Object.entries(ASSIGN).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </select></td></tr>
          <tr><th>Members</th><td>{r.members.length ? r.members.map((m) => (
            <span key={m.name} className="badge">{m.name}{m.away && ` (away${m.delegate ? `, ${m.delegate} covers` : ""})`}</span>))
            : <span className="muted">none: give the role to someone under People</span>}</td></tr>
          <tr><th>Used in</th><td>{r.processes.length ? r.processes.map((p) => <a key={p} href={processHref(p, "routing")}>{p} </a>)
            : <span className="muted">no published process</span>}</td></tr>
        </tbody>
      </Table>
      <div className="row">
        <Button variant="primary" disabled={!changed || !!a.busy}
          onClick={() => a.run("save", () => api.setRole(r.name, { description: v.description || null, default_sla: v.default_sla || null,
            escalate_to: v.escalate_to || null, assignment: v.assignment }, person), reload)}>Save</Button>
        {!r.processes.length && !r.members.length && (
          <Button variant="link" disabled={!!a.busy} onClick={() => a.run("del", () => api.deleteRole(r.name, person), reload)}>delete role</Button>
        )}
        {r.waiting > 0 && <a className="small" href={href("/cases", { who: `role:${r.name}` })}>see its {r.waiting} waiting</a>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
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
      <input placeholder="new role, e.g. integrity_engineer" value={name} onChange={(e) => setName(e.target.value)} />
      <Input width="lg" placeholder="what it's for" value={desc} onChange={(e) => setDesc(e.target.value)} />
      <select value={kind} onChange={(e) => setKind(e.target.value as "person" | "agent")}><option value="person">people</option><option value="agent">an agent</option></select>
      <Button variant="secondary" disabled={!name || !!a.busy}
        onClick={() => a.run("add", () => api.setRole(name, { kind, description: desc || null }, person), () => { setName(""); setDesc(""); reload(); })}>Add role</Button>
      {a.error && <span className="error small">{a.error}</span>}
    </div>
  );
}

function People({ people, roles, person, reload }: { people: WorkloadPerson[]; roles: WorkRoleRow[]; person: string; reload: () => void }) {
  const [name, setName] = useState("");
  const a = useAct();
  return (
    <>
      <Table variant="list" density="compact">
        <thead><tr><th>Person</th><th>Roles</th><th>Has</th><th>Overdue</th><th>Away</th><th /></tr></thead>
        <tbody>{people.map((p) => <PersonRow key={p.name} p={p} roles={roles} people={people} person={person} reload={reload} />)}</tbody>
      </Table>
      <div className="row">
        <input placeholder="add a person" value={name} onChange={(e) => setName(e.target.value)} />
        <Button variant="secondary" disabled={!name || !!a.busy}
          onClick={() => a.run("add", () => api.setWorkPerson(name.trim(), [], person), () => { setName(""); reload(); })}>Add</Button>
        {a.error && <span className="error small">{a.error}</span>}
      </div>
    </>
  );
}

function PersonRow({ p, roles, people, person, reload }: { p: WorkloadPerson; roles: WorkRoleRow[]; people: WorkloadPerson[];
                                                          person: string; reload: () => void }) {
  const [awayOpen, setAwayOpen] = useState(false);
  const [until, setUntil] = useState("");
  const [delegate, setDelegate] = useState(p.delegate ?? "");
  const a = useAct();
  const toggle = (role: string) => {
    const next = p.roles.includes(role) ? p.roles.filter((x) => x !== role) : [...p.roles, role];
    return a.run("roles", () => api.setWorkPerson(p.name, next, person), reload);
  };
  return (
    <tr>
      <td><strong>{p.name}</strong></td>
      <td>{roles.map((r) => (
        <label key={r.name} className="small choice-inline"><input type="checkbox" checked={p.roles.includes(r.name)} disabled={!!a.busy}
          onChange={() => toggle(r.name)} /> {r.name}</label>))}</td>
      <td><a href={href("/cases", { who: p.name })}>{p.assigned}</a></td>
      <td>{p.overdue ? <span className="badge late">{p.overdue}</span> : 0}</td>
      <td>
        {p.away ? <>until {when(p.away_until!)}{p.delegate && `, ${p.delegate} covers`}{" "}
          <Button variant="link" onClick={() => a.run("back", () => api.setAway(p.name, person, null), reload)}>back now</Button></>
          : awayOpen ? (
            <span className="row">
              <input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
              <select value={delegate} onChange={(e) => setDelegate(e.target.value)}>
                <option value="">steps go back to the queue</option>
                {people.filter((x) => x.name !== p.name).map((x) => <option key={x.name} value={x.name}>{x.name} covers</option>)}
              </select>
              <Button variant="secondary" disabled={!until || !!a.busy}
                onClick={() => a.run("away", () => api.setAway(p.name, person, new Date(until).toISOString(), delegate || null), () => { setAwayOpen(false); reload(); })}>Set</Button>
            </span>
          ) : <Button variant="link" onClick={() => setAwayOpen(true)}>set away</Button>}
      </td>
      <td><Button variant="link" disabled={!!a.busy}
        onClick={() => confirm(`Take ${p.name} off the roster? What they have goes back to its queues.`) &&
          a.run("rm", () => api.removeWorkPerson(p.name, person), reload)}>remove</Button>
        {a.error && <div className="error">{a.error}</div>}</td>
    </tr>
  );
}
