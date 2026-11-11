import "../../styles/work.css";
import { Button, ConfirmAction, Select , Badge as UIBadge} from "../../ui";
import { useAuth, usePermission } from "../../Auth";
import { usePerson } from "../../work";
/** Shared by the Workflows pages: links, urgency order, step badges and an "assign to" picker. */
import { useState } from "react";
import { api, type WorkRow } from "../../api";
import { href, notifyChanged, useAsync } from "../../lib";

export const taskHref = (id: number) => href(`/inbox/task/${id}`);
export const caseHref = (id: number) => href(`/cases/${id}`);
export const processHref = (key: string, tab?: string) => href(`/processes/${key}`, tab ? { tab } : undefined);

const IDLE_MINUTES = 10;

/** An agent step no worker has claimed for a while (#393). Agents only run when an agent worker is running, which
 * `fuzer up` skips without an AI provider; say so instead of letting the step look busy. */
export function AgentIdle({ step, configured, onTakeOver }: {
  step: { kind: string; unclaimed?: boolean; created_at?: string | null } | null | undefined;
  /** False: no agents provider, so no worker will ever pick it up; say so straight away. */
  configured?: boolean;
  /** Offered with the note: hand the agent's step to yourself and do it as a person step. */
  onTakeOver?: () => void;
}) {
  if (!step || step.kind !== "agent" || !step.unclaimed || !step.created_at) return null;
  const take = onTakeOver && <> <Button size="sm" onClick={onTakeOver}>Take it over</Button></>;
  if (configured === false) return <span className="small agent-idle" role="note">
    No AI provider is configured for agents, so no agent worker runs and this step will wait. Take it over to do it
    yourself, or configure a provider in <a href={href("/settings")}>application settings</a>.{take}
  </span>;
  const minutes = (Date.now() - new Date(step.created_at).getTime()) / 60_000;
  if (!(minutes >= IDLE_MINUTES)) return null;
  const waited = minutes < 120 ? `${Math.round(minutes)} min` : minutes < 2880 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} d`;
  return <span className="small agent-idle" role="note">
    Waiting for an agent worker: none has picked this step up in {waited}. The agent worker may not be running; it
    needs an AI provider, which <a href={href("/settings")}>application settings</a> show.{take}
  </span>;
}

/** A case's priority as shown: a 0–1 score to two places, a value in USD rounded. Zero means it has not been scored
 * yet, which reads as no priority rather than "priority 0.00" (#405). */
export function priorityText(p: number | null | undefined): string | null {
  if (p == null || p === 0) return null;
  return p < 2 ? p.toFixed(2) : Math.round(p).toLocaleString();
}

/** Most urgent first: overdue (longest first), then due soonest, then highest priority. */
export function byUrgency<T extends { due_at?: string | null; due?: string | null; priority: number | null }>(a: T, b: T) {
  const due = (x: T) => (x.due_at ?? x.due) ? new Date((x.due_at ?? x.due)!).getTime() : Infinity;
  return due(a) - due(b) || (b.priority ?? -1) - (a.priority ?? -1);
}

export function StepBadges({ row }: { row: Pick<WorkRow, "requires_human" | "kind" | "escalated_at"> & { covering_for?: string | null } }) {
  return (
    <>
      {row.requires_human && <UIBadge className="badge human">needs a person</UIBadge>}
      {row.kind === "fix" && <UIBadge className="badge late">failed: fix and retry</UIBadge>}
      {row.escalated_at && <UIBadge className="badge late">escalated</UIBadge>}
      {row.covering_for && <UIBadge className="badge">covering for {row.covering_for}</UIBadge>}
    </>
  );
}

/** A person or a role's queue (role:<role>), optionally an agent (agent:<role>). */
export function AssignPicker({ value, onChange, agents = false, placeholder = "choose…", label }: {
  value: string; onChange: (v: string) => void; agents?: boolean; placeholder?: string; label?: string;
}) {
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">{placeholder}</option>
      <optgroup label="People">
        {(people.data ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}{p.away_until && new Date(p.away_until) > new Date() ? " (away)" : ""}</option>)}
      </optgroup>
      <optgroup label="Role queues">
        {(roles.data ?? []).filter((r) => r.kind === "person").map((r) => <option key={r.name} value={`role:${r.name}`}>{r.name} queue</option>)}
      </optgroup>
      {agents && (
        <optgroup label="Agents">
          {(roles.data ?? []).filter((r) => r.kind === "agent").map((r) => <option key={r.name} value={`agent:${r.name}`}>{r.name} agent</option>)}
        </optgroup>
      )}
    </Select>
  );
}

/** A button's async action with busy and error state; success refreshes every view. */
export function useAct() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async <T,>(name: string, fn: () => Promise<T>, after?: (r: T) => void) => {
    setBusy(name);
    setError(null);
    try {
      const r = await fn();
      after?.(r);
      notifyChanged();
      return r;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return { busy, error, run };
}

/** A sample IOC team for a fictional demo field: an ops lead who triages, a field operator who gets dispatched, a
 * maintenance planner, a production engineer (short gas days, deferment) and a data steward (source problems), so
 * every queue the demo's work routes to has someone in it. Offered only where the roster is empty or holds only
 * sample people (a demo that added an earlier, smaller sample team gets the rest), the demo is on and names are
 * attribution, not accounts (local mode), so a real deployment's roster is never touched. Uses the ordinary roster
 * write. */
export const SAMPLE_TEAM: { name: string; roles: string[]; does: string }[] = [
  { name: "riley", roles: ["ops_lead", "engineer"], does: "IOC ops lead: triages and dispatches" },
  { name: "sam", roles: ["operator"], does: "field operator: gets sent to look" },
  { name: "pat", roles: ["maintenance_planner"], does: "maintenance planner: raises notifications" },
  { name: "jordan", roles: ["production_engineer"], does: "production engineer: explains short gas days and deferment" },
  { name: "casey", roles: ["data_steward"], does: "data steward: fixes source problems" },
];

export function SampleTeam({ onAdded }: { onAdded?: () => void }) {
  const session = useAuth();
  const configure = usePermission("configure");
  const [person, setPerson] = usePerson();
  const demo = useAsync(() => api.demoConfig(), []);
  const people = useAsync(() => api.workPeople(), []);
  const { busy, error, run } = useAct();
  if (session?.mode === "microsoft" || !configure || !demo.data?.enabled || !people.data) return null;
  const sample = new Set(SAMPLE_TEAM.map((p) => p.name));
  if (people.data.some((p) => !sample.has(p.name))) return null;  // someone's own roster: leave it alone
  const have = new Set(people.data.map((p) => p.name));
  const missing = SAMPLE_TEAM.filter((p) => !have.has(p.name));
  if (!missing.length) return null;
  const fresh = !people.data.length;
  const names = missing.map((p) => p.name);
  const list = names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names.at(-1)}` : names[0];
  const add = () => run("Adding the sample team", async () => {
    for (const p of missing) await api.setWorkPerson(p.name, p.roles, person || "demo");
  }, () => { if (!person) setPerson("riley"); people.reload(); onAdded?.(); });
  return (
    <div className="notice small sample-team">
      {fresh
        ? <p><strong>Nobody is on the team yet.</strong> Add a sample team to dispatch work to people:</p>
        : <p><strong>Some queues have nobody in them.</strong> Add the rest of the sample team:</p>}
      <ul className="plain">{missing.map((p) => <li key={p.name}><strong>{p.name}</strong> · {p.does}</li>)}</ul>
      <ConfirmAction label={fresh ? "Add the sample team" : "Add the rest of the sample team"} busy={!!busy}
        consequence={`Adds ${list} to this project's shared roster with the roles shown${fresh ? ", and sets you to riley if you haven't picked a name" : ""}. They are sample names for a demo field; remove them on Teams & routing.`} onConfirm={add} />
      {" "}<a href={href("/teams")}>Or add your own people on Teams &amp; routing</a>
      {error && <p role="alert" className="error">{error}</p>}
    </div>
  );
}
