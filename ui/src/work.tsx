// Shared bits of the work pages (#97): who is acting, due dates, how history reads, evidence.

import { Badge as UIBadge, Disclosure, ErrorText, Facts, List, Text } from "./ui";
import { when } from "./format";
import { type ReactNode } from "react";
import { type WorkEvent } from "./api";
import { Markdown } from "./Markdown";
import { entityHref, fieldLabel, fmt, href } from "./lib";
import { display, isId } from "./entityRefs";
import { useEntityNames } from "./useEntityNames";
import { person as personName, queue, say } from "./wording";

export { loadPerson, usePerson } from "./identityStore";

const HOUR = 3600_000;

export function span(ms: number): string {
  const h = Math.abs(ms) / HOUR;
  return h < 1 ? `${Math.max(1, Math.round(h * 60))} min` : h < 48 ? `${Math.round(h)} h` : `${Math.round(h / 24)} d`;
}

export function Due({ at, closed }: { at: string | null; closed?: boolean }) {
  if (!at) return <Text small muted>no due date</Text>;
  const left = new Date(at).getTime() - Date.now();
  if (closed) return <Text small muted>was due {when(at)}</Text>;
  if (left < 0) return <UIBadge tone="danger">overdue {span(left)}</UIBadge>;
  if (left < 24 * HOUR) return <UIBadge tone="warn">due in {span(left)}</UIBadge>;
  return <Text small muted>due in {span(left)}</Text>;
}

/** How many reports a case's sources are, and from how many systems: four upsets from one detector are one
 * system reporting four times, not four systems. */
export function reportedBy(sources: { source?: string | null }[]): string {
  const systems = new Set(sources.map((x) => x.source ?? "unknown"));
  if (systems.size === 1) return `Reported ${sources.length} times by ${[...systems][0]}`;
  return systems.size === sources.length ? `Reported by ${systems.size} systems`
    : `Reported ${sources.length} times by ${systems.size} systems`;
}

export function Actor({ who }: { who: string }) {
  if (who.startsWith("agent:")) return <UIBadge tone="info">{who.slice(6).replace(/_/g, " ")} agent</UIBadge>;
  if (who === "system") return <UIBadge>fuzer</UIBadge>;
  return <strong>{personName(who)}</strong>;
}

export function holder(assignee: string | null, role: string | null, kind?: string): string {
  if (assignee) return personName(assignee);
  if (kind === "agent") return `${role} agent`;
  return role ? queue(role) : "nobody";
}

const to = (t: unknown) => {
  const s = String(t ?? "");
  // an escalation writes where it came from as words around the role's key ("the data_steward queue")
  return s.startsWith("role:") ? `the ${queue(s)}` : s.startsWith("agent:") ? `the ${s.slice(6)} agent` : say(s);
};

const UNITS: Record<string, string> = { D: "day", H: "hour", M: "minute" };

/** An ISO 8601 duration as words: PT4H is "4 hours", P1DT2H is "1 day 2 hours". */
export function duration(iso: string): string {
  const parts = [...iso.matchAll(/(\d+)([DHM])/g)].map(([, n, u]) => `${n} ${UNITS[u]}${n === "1" ? "" : "s"}`);
  return parts.join(" ") || iso;
}

/** A missed SLA as a person says it (#1071): "Triage was due within 4 hours. It passed to the ops lead queue." The
 * recorded reason stays in the tooltip. */
function escalation(e: WorkEvent): ReactNode {
  const d = e.data;
  const why = String(d.why ?? "");
  const step = /^'([^']+)' was due/.exec(why)?.[1] ?? /on '([^']+)'/.exec(why)?.[1] ?? d.step ?? e.element ?? "The step";
  const iso = /SLA (P[0-9A-Z]+)/.exec(why)?.[1];
  const sla = iso ? duration(iso) : /was due within ([^,]+),/.exec(why)?.[1];
  const moved = d.to && d.to !== d.from;
  return <span title={why || undefined}><strong>{step}</strong> was due{sla ? <> within {sla}</> : d.due ? <> {when(d.due)}</> : null}
    {d.from ? <> while it was with {to(d.from)}</> : null}.{moved ? <> It passed to {to(d.to)}.</> : <> It is overdue.</>}</span>;
}

/** One line saying what an event did, plus anything worth showing under it. */
export function describe(e: WorkEvent): { text: ReactNode; body?: ReactNode } {
  const d = e.data;
  const step = d.step ?? e.element;
  switch (e.kind) {
    case "created": return { text: <>opened the case{d.assignee ? <> for {personName(d.assignee)}</> : ""}</>, body: d.reason && <Text as="p" small>{d.reason}</Text> };
    case "started": return { text: <>started the {String(d.process_key ?? "").replace(/_/g, " ")} process</> };
    case "step_started": return { text: <><strong>{step}</strong> is waiting for {d.assignee ? personName(d.assignee) : (d.agent ? `the ${d.agent} agent` : to(`role:${d.role}`))}</> };
    case "claimed": return { text: <>took <strong>{e.element}</strong>{d.worker ? ` (worker ${d.worker}, try ${d.attempt})` : ""}</> };
    case "released": return { text: <>let go of <strong>{e.element}</strong></> };
    case "completed": return { text: <>completed <strong>{step}</strong></>, body: <DataView data={d.data} /> };
    case "agent_result": return { text: <>finished <strong>{step}</strong></>, body: <DataView data={d.data} /> };
    case "handed_off": return { text: <>handed <strong>{e.element ?? "the case"}</strong> from {to(d.from)} to {to(d.to)}</>, body: d.reason && <Text as="p" small>{d.reason}</Text> };
    case "escalated": return { text: escalation(e) };
    case "timer_fired": return { text: <>timer <strong>{e.element}</strong> fired</> };
    case "message": return { text: <>sent {d.name} {d.delivered ? "and the workflow moved on" : "(kept as evidence)"}</>, body: <DataView data={d.payload} /> };
    case "agent_failed": return { text: <>failed try {d.attempt} at <strong>{e.element}</strong>; retrying</>, body: <ErrorText>{d.error}</ErrorText> };
    case "failed": return { text: <><strong>{e.element}</strong> couldn't go on; it is a task for {to(`role:${d.role}`)}</>, body: <ErrorText>{d.error}</ErrorText> };
    case "step_cancelled": return { text: <><strong>{e.element}</strong> was cancelled</>, body: d.why && <Text as="p" small>{d.why}</Text> };
    case "completed_process": return { text: <>workflow {d.state}: <strong>{d.outcome}</strong></> };
    case "state_changed": return { text: <>case is now <strong>{d.state}</strong>{d.outcome ? ` (${d.outcome})` : ""}</>, body: d.reason && <Text as="p" small>{d.reason}</Text> };
    case "commented": return { text: <>commented</>, body: <p className="section-text small">{d.text}</p> };
    case "attached": return { text: <>attached an answer from Ask</>, body: <div className="attached-answer">
      <Text as="p" small><strong>{d.question}</strong></Text><Markdown text={String(d.answer ?? "")} /></div> };
    case "cancelled": return { text: <>cancelled the workflow</>, body: d.reason && <Text as="p" small>{d.reason}</Text> };
    case "migrated": return { text: <>picked up the current version of its workflow</> };
    case "adopted": return { text: <>now runs as the <strong>{d.process_key}</strong> process, still with {to(d.assignee ?? (d.role ? `role:${d.role}` : null))}</> };
    case "auto_assigned": return { text: <><strong>{step ?? e.element}</strong> went to {personName(d.to)} (the {d.role} role assigns automatically)</> };
    case "delegated": return { text: <><strong>{e.element ?? "a step"}</strong> went to {personName(d.to)} instead of {personName(d["for"])}, who is away</>, body: d.reason && <Text as="p" small>{d.reason}</Text> };
    case "item_changed": return { text: <>changed {[d.priority && `priority ${d.priority.from ?? "–"} → ${d.priority.to}`, d.due && `due → ${when(d.due.to)}`].filter(Boolean).join(", ")}</>, body: d.reason && <Text as="p" small>{d.reason}</Text> };
    case "checklist_added": return { text: <>added to the checklist</>, body: <List small>{(d.items ?? []).map((t: string, i: number) => <li key={i}>{t}</li>)}</List> };
    case "checked": return { text: <>{d.state === "done" ? "ticked off" : d.state === "skipped" ? "marked not needed" : "opened again"} <strong>{d.item}</strong></>,
      body: d.note && <Text as="p" small>{d.note}</Text> };
    case "checklist_edited": return { text: <>reworded <strong>{d.from}</strong> to <strong>{d.to}</strong></> };
    case "checklist_sent": return { text: <>sent <strong>{d.item}</strong> to {to(d.to)} as <a href={href(`/cases/${d.case}`)}>case {d.case}</a></>,
      body: d.note && <Text as="p" small>{d.note}</Text> };
    case "checklist_removed": return { text: <>took <strong>{d.item}</strong> off the checklist</> };
    default: return { text: <>{e.kind.replace(/_/g, " ")}</> };
  }
}

/** The keys the engine keeps in a case's data to route and correlate it (#509): never something a person reads. */
export const ENGINE_FIELDS = ["draft", "task_key", "case_type", "role", "source"] as const;

/** Form data, agent results and message payloads: short values inline, long text as Markdown. */
export function DataView({ data, omit = [] }: { data: unknown; omit?: readonly string[] }) {
  if (data === null || data === undefined) return null;
  if (typeof data !== "object" || Array.isArray(data)) return <pre className="data">{fmt(data)}</pre>;
  const entries = Object.entries(data as Record<string, unknown>).filter(([k]) => !omit.includes(k));
  if (!entries.length) return null;
  return (
    <Facts items={entries.map(([k, v]) => [fieldLabel(k), <Value v={v} />] as const)} />
  );
}

const LIMIT_WORDS: Record<string, string> = { lo: "low", lolo: "low-low", hi: "high", hihi: "high-high" };

/** Short machine values as a person reads them (#14 of the walkthrough): a source-local time without the "T" (its
 * timezone isn't known, so none is added; the recorded value stays in the tooltip), and a detection key
 * (`excursions:alarm:lo`) as words ("alarm low excursions"). Anything else is shown as recorded. */
export function readable(v: string): string {
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(v)) return when(v);
  const agent = /^agent:([a-z_]+)$/.exec(v);
  if (agent) return `${agent[1].replace(/_/g, " ")} agent`;
  const m = /^([a-z]+):([a-z_]+):([a-z]+)$/.exec(v);
  if (m) return `${m[2].replace(/_/g, " ")} ${LIMIT_WORDS[m[3]] ?? m[3]} ${m[1].replace(/_/g, " ")}`;
  return v;
}

const LIST_SHOWN = 4;

function Value({ v }: { v: unknown }) {
  if (typeof v === "string") {
    if (isId(v)) return <EntityLinks ids={[v]} />;
    const shown = readable(v);
    if (shown !== v) return <span title={v}>{shown}</span>;
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
      <Disclosure className="data-fold" label={<>{typeof o.reason === "string" ? o.reason : `${n} fields`}</>}>
        <DataView data={Object.fromEntries(Object.entries(o).filter(([k]) => k !== "reason"))} />
      </Disclosure>
    );
  }
  if (typeof v === "boolean") return <>{v ? "yes" : "no"}</>;
  // A list of plain values reads as a list, and a long one (forty record keys, say) shows its first few: printed
  // whole as JSON it pushed everything after it screens down a phone.
  if (Array.isArray(v) && v.length > 0 && v.every((x) => x === null || typeof x !== "object")) {
    const words = v.map((x) => (typeof x === "string" ? readable(x) : fmt(x)));
    if (words.length <= LIST_SHOWN) return <>{words.join(", ")}</>;
    return <>{words.slice(0, LIST_SHOWN).join(", ")}
      <Disclosure className="data-fold" label={<>and {words.length - LIST_SHOWN} more</>}><Text small>{words.slice(LIST_SHOWN).join(", ")}</Text></Disclosure></>;
  }
  return <Text mono>{fmt(v)}</Text>;
}

/** The process data a case started with, as a person reads it (#496): without the keys and ids that only link
 * records (`key`, `upset_id`, `exception_id`, ...) and without fields left empty. */
// Routing bookkeeping the page already says in words (the queue, the kind of case) or that only the engine reads.
const ENGINE_KEYS = new Set(["key", "role", "type", "case_type", "task_key", "review_item",
  "change", "from_case", "decided_by"]);  // the last three show as the task's "What to change" (#1145)

export function startedWith(data: Record<string, unknown> | null | undefined): Record<string, unknown> | null {
  if (!data) return null;
  return Object.fromEntries(Object.entries(data).filter(([k, v]) =>
    !ENGINE_KEYS.has(k) && !k.endsWith("_id") && v !== "" && v !== null && v !== undefined));
}

/** Entities by name, from one lookup. The id stays in the tooltip. */
export function EntityLinks({ ids }: { ids: string[] }) {
  const names = useEntityNames(ids);
  // Equipment reads as the tag it is installed at, so that tag beside it would say the same thing twice.
  const shownAt = new Set(ids.map((id) => names?.[id]?.at).filter(Boolean));
  const shown = ids.filter((id) => names?.[id]?.at || !shownAt.has(names?.[id]?.label ?? ""));
  return <>{shown.map((id, i) => <span key={id}>{i > 0 && ", "}<a href={entityHref(id)} title={id}>{display(id, names)}</a></span>)}</>;
}
