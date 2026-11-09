import "../../styles/work.css";
import { Select , Badge as UIBadge} from "../../ui";
/** Shared by the Workflows pages: links, urgency order, step badges and an "assign to" picker. */
import { useState } from "react";
import { api, type WorkRow } from "../../api";
import { href, notifyChanged, useAsync } from "../../lib";

export const taskHref = (id: number) => href(`/inbox/task/${id}`);
export const caseHref = (id: number) => href(`/cases/${id}`);
export const processHref = (key: string, tab?: string) => href(`/processes/${key}`, tab ? { tab } : undefined);

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
export function AssignPicker({ value, onChange, agents = false }: { value: string; onChange: (v: string) => void; agents?: boolean }) {
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">choose…</option>
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
