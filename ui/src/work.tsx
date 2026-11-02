// Shared bits of the work pages (#97): who is acting, due dates, how history reads, evidence.

import { useEffect, useState, type ReactNode } from "react";
import type { WorkEvent } from "./api";
import { Markdown } from "./Markdown";
import { entityHref, fmt, notifyChanged } from "./lib";

const NAME_KEY = "fuzer.reviewer";

export function loadPerson(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

/** The person acting in the work pages, remembered per browser (the same name the review pages use). */
export function usePerson(): [string, (name: string) => void] {
  const [name, setName] = useState(loadPerson);
  useEffect(() => {
    try {
      localStorage.setItem(NAME_KEY, name);
    } catch {
      /* private mode: name just isn't remembered */
    }
  }, [name]);
  return [name, (n: string) => {
    setName(n);
    setTimeout(notifyChanged);
  }];
}

const HOUR = 3600_000;

function span(ms: number): string {
  const h = Math.abs(ms) / HOUR;
  return h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`;
}

export function Due({ at, closed }: { at: string | null; closed?: boolean }) {
  if (!at) return <span className="muted small">no due date</span>;
  const left = new Date(at).getTime() - Date.now();
  if (closed) return <span className="muted small">was due {when(at)}</span>;
  if (left < 0) return <span className="badge late">overdue {span(left)}</span>;
  if (left < 24 * HOUR) return <span className="badge soon">due in {span(left)}</span>;
  return <span className="muted small">due in {span(left)}</span>;
}

export function when(at: string): string {
  return new Date(at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function Actor({ who }: { who: string }) {
  if (who.startsWith("agent:")) return <span className="badge actor-agent">{who.slice(6)} agent</span>;
  if (who === "system") return <span className="badge actor-system">Fuzer</span>;
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
    case "created": return { text: <>created the item</> };
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
          <dt>{k.replace(/_/g, " ")}</dt>
          <dd><Value v={v} /></dd>
        </div>
      ))}
    </dl>
  );
}

function Value({ v }: { v: unknown }) {
  if (typeof v === "string") {
    if (/^fz-[a-z_]+-[0-9a-f]{12}$/.test(v)) return <a className="mono" href={entityHref(v)}>{v}</a>;
    return v.length > 80 || v.includes("\n") || v.includes("[[figure:") ? <Markdown text={v} /> : <>{v}</>;
  }
  if (v && typeof v === "object" && !Array.isArray(v) && typeof (v as any).answer === "string") {
    return <Markdown text={(v as any).answer} />;
  }
  if (v && typeof v === "object" && !Array.isArray(v)) return <DataView data={v} />;
  return <span className="mono">{fmt(v)}</span>;
}
