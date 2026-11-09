// Shared bits of the work pages (#97): who is acting, due dates, how history reads, evidence.

import { Badge as UIBadge } from "./ui";
import { type ReactNode } from "react";
import { api, type WorkEvent } from "./api";
import { Markdown } from "./Markdown";
import { entityHref, fieldLabel, fmt, useAsync } from "./lib";
import { display, isId } from "./entityRefs";
import { useEntityNames } from "./useEntityNames";

export { loadPerson, usePerson } from "./identityStore";

const HOUR = 3600_000;

export function span(ms: number): string {
  const h = Math.abs(ms) / HOUR;
  return h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`;
}

export function Due({ at, closed }: { at: string | null; closed?: boolean }) {
  if (!at) return <span className="muted small">no due date</span>;
  const left = new Date(at).getTime() - Date.now();
  if (closed) return <span className="muted small">was due {when(at)}</span>;
  if (left < 0) return <UIBadge className="badge late">overdue {span(left)}</UIBadge>;
  if (left < 24 * HOUR) return <UIBadge className="badge soon">due in {span(left)}</UIBadge>;
  return <span className="muted small">due in {span(left)}</span>;
}

/** When the loaded data stops well before today (a historical or synthetic scenario), say which clock each time
 * uses: events carry the scenario's dates, while due dates, SLAs and "opened" are when Fuzer recorded the work (#389). */
export function ScenarioClock() {
  const stats = useAsync(() => api.stats(), []);
  const through = stats.data?.data_through;
  if (!through) return null;
  const behind = (Date.now() - new Date(`${through}T23:59:59`).getTime()) / (24 * HOUR);
  if (behind < 2) return null;
  return <p className="small muted scenario-clock" role="note">
    The loaded data runs to <strong>{new Date(`${through}T12:00:00`).toLocaleDateString(undefined, { dateStyle: "medium" })}</strong>.
    Event dates in titles and evidence are from the data; due dates, SLAs and opened times are on today's clock, from
    when Fuzer raised the work.
  </p>;
}

export function when(at: string): string {
  return new Date(at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function Actor({ who }: { who: string }) {
  if (who.startsWith("agent:")) return <UIBadge className="badge actor-agent">{who.slice(6)} agent</UIBadge>;
  if (who === "system") return <UIBadge className="badge actor-system">fuzer</UIBadge>;
  return <strong>{who}</strong>;
}

export function holder(assignee: string | null, role: string | null, kind?: string): string {
  if (assignee) return assignee;
  if (kind === "agent") return `${role} agent`;
  return role ? `${role.replace(/_/g, " ")} queue` : "nobody";
}

const to = (t: unknown) => {
  const s = String(t ?? "");
  return s.startsWith("role:") ? `the ${s.slice(5).replace(/_/g, " ")} queue` : s.startsWith("agent:") ? `the ${s.slice(6)} agent` : s;
};

/** One line saying what an event did, plus anything worth showing under it. */
export function describe(e: WorkEvent): { text: ReactNode; body?: ReactNode } {
  const d = e.data;
  const step = d.step ?? e.element;
  switch (e.kind) {
    case "created": return { text: <>created the item{d.assignee ? <> for {d.assignee}</> : ""}</>, body: d.reason && <p className="small">{d.reason}</p> };
    case "started": return { text: <>started process <span className="mono">{d.process_key}</span> v{d.version}</> };
    case "step_started": return { text: <><strong>{step}</strong> is waiting for {d.assignee ?? (d.agent ? `the ${d.agent} agent` : to(`role:${d.role}`))}</> };
    case "claimed": return { text: <>took <strong>{e.element}</strong>{d.worker ? ` (worker ${d.worker}, try ${d.attempt})` : ""}</> };
    case "released": return { text: <>let go of <strong>{e.element}</strong></> };
    case "completed": return { text: <>completed <strong>{step}</strong></>, body: <DataView data={d.data} /> };
    case "agent_result": return { text: <>finished <strong>{step}</strong></>, body: <DataView data={d.data} /> };
    case "handed_off": return { text: <>handed <strong>{e.element ?? "the item"}</strong> from {to(d.from)} to {to(d.to)}</>, body: d.reason && <p className="small">{d.reason}</p> };
    case "escalated": return { text: <>escalated <strong>{e.element}</strong></>, body: <p className="small">{d.why}</p> };
    case "timer_fired": return { text: <>timer <strong>{e.element}</strong> fired</> };
    case "message": return { text: <>sent {d.name} {d.delivered ? "and the process moved on" : "(kept as evidence)"}</>, body: <DataView data={d.payload} /> };
    case "agent_failed": return { text: <>failed try {d.attempt} at <strong>{e.element}</strong>; retrying</>, body: <p className="small error">{d.error}</p> };
    case "failed": return { text: <><strong>{e.element}</strong> couldn't go on; it is a task for {to(`role:${d.role}`)}</>, body: <p className="small error">{d.error}</p> };
    case "step_cancelled": return { text: <><strong>{e.element}</strong> was cancelled</>, body: d.why && <p className="small">{d.why}</p> };
    case "completed_process": return { text: <>process {d.state}: <strong>{d.outcome}</strong></> };
    case "state_changed": return { text: <>item is now <strong>{d.state}</strong>{d.outcome ? ` (${d.outcome})` : ""}</>, body: d.reason && <p className="small">{d.reason}</p> };
    case "commented": return { text: <>commented</>, body: <p className="section-text small">{d.text}</p> };
    case "cancelled": return { text: <>cancelled the process</>, body: d.reason && <p className="small">{d.reason}</p> };
    case "migrated": return { text: <>moved the process to v{d.version}</> };
    case "auto_assigned": return { text: <><strong>{step ?? e.element}</strong> went to {d.to} (the {d.role} role assigns automatically)</> };
    case "delegated": return { text: <><strong>{e.element ?? "a step"}</strong> went to {d.to} instead of {d["for"]}, who is away</>, body: d.reason && <p className="small">{d.reason}</p> };
    case "item_changed": return { text: <>changed {[d.priority && `priority ${d.priority.from ?? "–"} → ${d.priority.to}`, d.due && `due → ${when(d.due.to)}`].filter(Boolean).join(", ")}</>, body: d.reason && <p className="small">{d.reason}</p> };
    default: return { text: <>{e.kind.replace(/_/g, " ")}</> };
  }
}

/** Form data, agent results and message payloads: short values inline, long text as Markdown. */
export function DataView({ data }: { data: unknown }) {
  if (data === null || data === undefined) return null;
  if (typeof data !== "object" || Array.isArray(data)) return <pre className="data">{fmt(data)}</pre>;
  const entries = Object.entries(data as Record<string, unknown>);
  if (!entries.length) return null;
  return (
    <dl className="data">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt>{fieldLabel(k)}</dt>
          <dd><Value v={v} /></dd>
        </div>
      ))}
    </dl>
  );
}

function Value({ v }: { v: unknown }) {
  if (typeof v === "string") {
    if (isId(v)) return <EntityLinks ids={[v]} />;
    return v.length > 80 || v.includes("\n") || v.includes("[[figure:") ? <Markdown text={v} /> : <>{v}</>;
  }
  if (Array.isArray(v) && v.length > 0 && v.every((x) => typeof x === "string" && isId(x))) return <EntityLinks ids={v as string[]} />;
  if (v && typeof v === "object" && !Array.isArray(v) && typeof (v as any).answer === "string") {
    return <Markdown text={(v as any).answer} />;
  }
  if (v && typeof v === "object" && !Array.isArray(v)) {
    // A nested record (a priority score's breakdown, say) folds under its own reason, so the facts a person
    // decides on aren't pushed off the screen by how they were scored.
    const o = v as Record<string, unknown>;
    const n = Object.keys(o).length;
    if (n <= 3 && !Object.values(o).some((x) => x && typeof x === "object")) return <DataView data={v} />;
    return (
      <details className="data-fold">
        <summary>{typeof o.reason === "string" ? o.reason : `${n} fields`}</summary>
        <DataView data={Object.fromEntries(Object.entries(o).filter(([k]) => k !== "reason"))} />
      </details>
    );
  }
  return <span className="mono">{fmt(v)}</span>;
}

/** Entities by name, from one lookup. The id stays in the tooltip. */
export function EntityLinks({ ids }: { ids: string[] }) {
  const names = useEntityNames(ids);
  return <>{ids.map((id, i) => <span key={id}>{i > 0 && ", "}<a href={entityHref(id)} title={id}>{display(id, names)}</a></span>)}</>;
}
