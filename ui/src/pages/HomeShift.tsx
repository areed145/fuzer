/** Home is my shift (P5.1a, #649): one ranked list of everything waiting on the person and overdue in their roles,
 * across every field they hold roles in, with what changed since they last looked and what is coming. A row in
 * another field opens there in one click (the tab switches field and lands on the item). */
import { api, type WorkHome, type WorkRow } from "../api";
import { Badge, Button, Card, Empty } from "../ui";
import { Status, href, useAsync } from "../lib";
import { Actor, Due, holder, usePerson, when } from "../work";
import { useAuth } from "../Auth";
import { switchProject } from "../authState";
import { StepBadges, byUrgency, caseHref, taskHref } from "./workflows/shared";
import { myWorkCounts } from "./workflows/Inbox";

/** One count linking to its view. `children` is "one|many" when the noun changes with the number. */
function Count({ n, to, children, late }: { n: number; to: string; children: string; late?: boolean }) {
  const [one, many = one] = children.split("|");
  return <li><a href={to} className={late && n > 0 ? "late" : undefined}><strong>{n}</strong> {n === 1 ? one : many}</a></li>;
}

const TOP = 10;
const SAID: Record<string, string> = {
  created: "opened", handed_off: "handed on", escalated: "escalated", commented: "commented", state_changed: "changed state",
  completed_process: "finished", cancelled: "cancelled", gated: "stopped for a person", failed: "a step failed",
  agent_failed: "an agent step failed", delegated: "delegated", item_changed: "changed",
};

type Row = WorkRow & { project: string };
const late = (r: Row) => !!r.overdue || !!r.escalated_at;
const ranked = (a: Row, b: Row) => Number(late(b)) - Number(late(a)) || byUrgency(a, b);

export function YourShift() {
  const [person] = usePerson();
  const session = useAuth();
  const here = session?.project ?? "";
  const fields = useAsync(() => (person ? api.inboxFields(person) : Promise.resolve(null)), [person]);
  const here_ = useAsync(() => (person ? api.inbox(person) : Promise.resolve(null)), [person]);
  const since = useAsync(() => (person ? api.workHome(person, true) : Promise.resolve(null)), [person]);
  const field = useAsync(() => (person ? api.fieldJobsBrief(person) : Promise.resolve(null)), [person]);
  if (!person) return <Card className="home-card home-today" title="My work" as="section"><Empty className="small">Choose your name under “You are” in the menu.</Empty></Card>;
  // This field's own work, plus every other field they hold roles in (the server lists only those they may open).
  const local: Row[] = here_.data ? [...here_.data.mine, ...here_.data.queue].map((r) => ({ ...r, project: here })) : [];
  const away: Row[] = (fields.data ?? []).filter((f) => f.project !== here)
    .flatMap((f) => [...f.mine, ...f.queue].map((r) => ({ ...r, project: f.project })));
  const rows = [...local, ...away].sort(ranked);
  const projects = new Set(rows.map((r) => r.project));
  const jobs = field.data?.jobs ?? [];
  const c = here_.data ? myWorkCounts(here_.data, jobs) : null;
  const my = href("/inbox");
  const coming = (field.data?.coming_up ?? []).slice(0, 3);
  const all = since.data?.changes ?? [];
  const changes = all.filter((x) => !x.watching);
  const watched = all.filter((x) => x.watching);
  return (
    <Card className="home-card home-today" title="My work" as="section" actions={<a href={my}>Open My work</a>}>
      <Status loading={here_.loading && !here_.data} error={here_.error} onRetry={here_.reload} />
      {c && <ul className="home-counts">
        {c.escalated > 0 && <Count n={c.escalated} to={my} late>escalated to you</Count>}
        {c.sent > 0 && <Count n={c.sent} to={my}>sent to you</Count>}
        <Count n={c.mine} to={my}>assigned to you</Count>
        <Count n={c.queue} to={my}>unclaimed in your roles</Count>
        {c.overdue > 0 && <Count n={c.overdue} to={my} late>overdue</Count>}
        {away.length > 0 && <li className="small muted">{away.length} more in your other field{projects.size > 2 ? "s" : ""}, ranked below</li>}
      </ul>}
      {here_.data && (rows.length ? <ol className="home-list home-shift">{rows.slice(0, TOP).map((r) => <ShiftRow key={`${r.project}-${r.task_id ?? "i"}-${r.item_id}`}
        r={r} here={here} multi={projects.size > 1} />)}</ol> : <Empty className="small">Nothing waiting for you.</Empty>)}
      {rows.length > TOP && <p className="small"><a href={href("/inbox")}>{rows.length - TOP} more on My work</a></p>}
      <h3>Since you last looked <span className="muted small">{since.data?.seen_at ? when(since.data.seen_at) : "in the last day"}</span></h3>
      {changes.length ? <Changes rows={changes} /> : <Empty className="small">Nothing new on your work.</Empty>}
      {(watched.length > 0 || (since.data?.watching?.length ?? 0) > 0) && <>
        <h3>Watching <span className="muted small">not assigned to you</span></h3>
        {watched.length ? <Changes rows={watched} /> : <Empty className="small">Nothing new on what you watch.</Empty>}
      </>}
      {coming.length > 0 && <>
        <h3>Coming up</h3>
        <ul className="plain small">{coming.map((i) => <li key={i.id}>{when(i.start_at)} · {i.title}</li>)}</ul>
      </>}
    </Card>
  );
}

function Changes({ rows }: { rows: WorkHome["changes"] }) {
  return <ul className="plain small home-changes">{rows.slice(0, 8).map((c) => <li key={c.id}>
    <a href={caseHref(c.item_id)}>{c.title}</a> · {SAID[c.kind] ?? c.kind.replace(/_/g, " ")} by <Actor who={c.actor} />
    {typeof c.data.text === "string" && c.data.text ? <span className="muted">: {c.data.text.slice(0, 100)}</span> : null}
    <span className="muted"> · {when(c.at)}</span></li>)}</ul>;
}

function ShiftRow({ r, here, multi }: { r: Row; here: string; multi: boolean }) {
  const to = r.task_id ? taskHref(r.task_id) : caseHref(r.item_id);
  return (
    <li>
      <span className="row">
        {r.project === here
          ? <a href={to} className="work-title">{r.title}</a>
          : <Button variant="link" onClick={() => switchProject(r.project, to)}>{r.title}</Button>}
        {multi && <Badge>{r.project || "this field"}</Badge>}
        <Due at={r.due_at} />
      </span>
      <span className="small muted"><strong>{r.name ?? "open"}</strong> · {r.type.replace(/_/g, " ")} · with {holder(r.assignee, r.role, r.kind)}{" "}
        <StepBadges row={r} /></span>
    </li>
  );
}
