import "../../styles/work.css";
import { Button, ConfirmAction, Disclosure, Field, Input, Select , Badge as UIBadge} from "../../ui";
import { useAuth, usePermission } from "../../Auth";
import { usePerson } from "../../work";
/** Shared by the Workflows pages: links, urgency order, step badges and an "assign to" picker. */
import { useState } from "react";
import { api, type WorkRow } from "../../api";
import { href, notifyChanged, useAsync } from "../../lib";
import { queue } from "../../wording";
import { ATTENTION_TONE } from "../../kinds";

/** A step named for the engine, not for the work: where it would be a title or a row's lead, the work is used. */
export const GENERIC_STEP = /^(work the case|do the task)$/i;
export const taskHref = (id: number) => href(`/inbox/task/${id}`);
export const caseHref = (id: number) => href(`/cases/${id}`);
export const processHref = (key: string, tab?: string) => href(`/workflows/${key}`, tab ? { tab } : undefined);

const IDLE_MINUTES = 10;

/** An agent step no worker has claimed for a while (#393). Agents only run when an agent worker is running, which
 * `fuzer up` skips without an AI provider; say so instead of letting the step look busy, and say when it goes to a
 * person queue if nobody picks it up (P0.3). */
export function AgentIdle({ step, configured, onTakeOver }: {
  step: { kind: string; unclaimed?: boolean; created_at?: string | null; fallback_at?: string | null } | null | undefined;
  /** False: no agents provider, so no worker will ever pick it up; say so straight away. */
  configured?: boolean;
  /** Offered with the note: hand the agent's step to yourself and do it as a person step. */
  onTakeOver?: () => void;
}) {
  if (!step || step.kind !== "agent" || !step.unclaimed || !step.created_at) return null;
  const take = onTakeOver && <> <Button size="sm" onClick={onTakeOver}>Take it over</Button></>;
  const fallback = step.fallback_at
    ? ` It goes to a person queue at ${new Date(step.fallback_at).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })} unless one does.`
    : " It waits until one does.";
  if (configured === false) return <span className="small agent-idle" role="note">
    No AI provider is configured for agents, so no agent worker will pick this step up.{fallback} Take it over to do
    it yourself, or configure a provider in <a href={href("/settings")}>application settings</a>.{take}
  </span>;
  const minutes = (Date.now() - new Date(step.created_at).getTime()) / 60_000;
  if (!(minutes >= IDLE_MINUTES)) return null;
  const waited = minutes < 120 ? `${Math.round(minutes)} min` : minutes < 2880 ? `${Math.round(minutes / 60)} h` : `${Math.round(minutes / 1440)} d`;
  return <span className="small agent-idle" role="note">
    Waiting for an agent worker: none has picked this step up in {waited}.{fallback} The agent worker may not be
    running; it needs an AI provider, which <a href={href("/settings")}>application settings</a> show.{take}
  </span>;
}

/** A case's priority as shown: a 0–1 score to two places, a value in USD rounded. Zero means it has not been scored
 * yet, which reads as no priority rather than "priority 0.00" (#405). */
export function priorityText(p: number | null | undefined): string | null {
  if (p == null || p === 0) return null;
  return p < 2 ? p.toFixed(2) : Math.round(p).toLocaleString();
}

export type PriorityLevel = { level: "High" | "Medium" | "Low"; tone: "danger" | "warn" | "info"; why: string };

/** A priority as a person reads it (#1071): High, Medium or Low and why, whatever scale the source used. Priorities
 * arrive on three scales: a 0–1 score (high from 0.8, medium from 0.5), an SAP notification's priority 1 (very high)
 * to 4 (low), stored as 5 minus it so higher comes first, and a value in USD for work ranked by what is at stake. */
export function priorityLevel(p: number | null | undefined, type?: string | null): PriorityLevel | null {
  if (p == null || p <= 0) return null;
  const level = (l: PriorityLevel["level"], why: string): PriorityLevel =>
    ({ level: l, tone: ATTENTION_TONE[l === "High" ? "high" : l === "Medium" ? "medium" : "low"], why });
  if ((type === "sap_notification" && p <= 4) || (p > 1 && p <= 4 && Number.isInteger(p))) {
    const sap = 5 - Math.round(p);
    return level(sap <= 2 ? "High" : sap === 3 ? "Medium" : "Low", `SAP priority ${sap}`);
  }
  if (p <= 1) return level(p >= 0.8 ? "High" : p >= 0.5 ? "Medium" : "Low", `scored ${p.toFixed(2)} of 1`);
  const usd = p >= 1000 ? `$${Math.round(p / 1000).toLocaleString()}k` : `$${Math.round(p)}`;
  return level(p >= 10000 ? "High" : p >= 1000 ? "Medium" : "Low", `${usd} at stake`);
}

/** High, Medium or Low in its colour; why it is that is the tooltip. Low is left out unless asked for, so it doesn't
 * add a word to every row. */
export function PriorityBadge({ p, type, low = false }: { p: number | null | undefined; type?: string | null; low?: boolean }) {
  const l = priorityLevel(p, type);
  if (!l || (l.level === "Low" && !low)) return null;
  return <UIBadge tone={l.tone} title={l.why} className="priority">{l.level}</UIBadge>;
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
        {(people.data ?? []).map((p) => <option key={p.name} value={p.name}>{p.display_name || p.name}{p.away_until && new Date(p.away_until) > new Date() ? " (away)" : ""}</option>)}
      </optgroup>
      <optgroup label="Role queues">
        {(roles.data ?? []).filter((r) => r.kind === "person").map((r) => <option key={r.name} value={`role:${r.name}`}>{queue(r.name)}</option>)}
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


/** Send a case to the field (#1145): its workflow's Field check goes to a field operator or their queue with what to
 * check or do, and their report comes back to whoever sent it. The case's process carries it, not a side hand-off. */
export function SendToField({ exceptionId, person, onDone }: { exceptionId: number; person: string; onDone: () => void }) {
  const people = useAsync(() => api.workPeople(), []);
  const [to, setTo] = useState("");
  const [ask, setAsk] = useState("");
  const [why, setWhy] = useState("");
  const [blocked, setBlocked] = useState<string[] | null>(null);
  const a = useAct();
  const field = (people.data ?? []).filter((p) => p.roles.includes("operator"));
  const send = () => a.run("send", () => api.dispatch(exceptionId, person, to, ask.trim(), blocked ? why.trim() : undefined), () => {
    setTo(""); setAsk(""); setWhy(""); setBlocked(null); onDone();
  }).catch(() => undefined);
  const notReady = a.error?.startsWith("Not ready to dispatch") ?? false;
  return (
    <Disclosure className="small" label={<>Send to the field…</>}>
      <div className="row wrap">
        <Field label="Send to" width="md"><Select value={to} onChange={(e) => setTo(e.target.value)}>
          <option value="">choose a field operator</option>
          {field.map((p) => <option key={p.name} value={p.name}>{p.display_name || p.name}
            {p.away_until && new Date(p.away_until) > new Date() ? " (away)" : ` · holds ${p.holding ?? 0}`}</option>)}
          <option value="role:operator">{queue("operator")}, whoever is free</option>
        </Select></Field>
        <Field label="What to check or do (they see this)" width="lg">
          <Input value={ask} onChange={(e) => setAsk(e.target.value)} /></Field>
      </div>
      {(notReady || blocked) && <Field label="Why send it anyway (kept on the case)" width="lg">
        <Input value={why} onChange={(e) => { setWhy(e.target.value); setBlocked([a.error ?? ""]); }} /></Field>}
      <Button variant="primary" disabled={!to || !ask.trim() || !!a.busy || ((notReady || !!blocked) && !why.trim())}
        title={!ask.trim() ? "Say what to check or do first: they see it on their job" : undefined}
        onClick={send}>Send</Button>
      {a.error && <p role="alert" className="error small">{a.error}</p>}
    </Disclosure>
  );
}
