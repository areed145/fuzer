import { Button, ButtonLink, Notice, PageHeader, Table, Card as UICard, Input, Select, Badge as UIBadge, Empty } from "../../ui";
/** My work (#503): everything waiting on you, most urgent first, for everyone from the ops lead to the field operator:
 * escalated to you, jobs the IOC sent you (with the field report buttons), steps assigned to you (and to anyone you're
 * covering for), what waits unclaimed in your roles' queues, and what you're planned onto. Inbox and My jobs were two
 * pages for "what is waiting on me"; `#/field` redirects here. */
import { useEffect, useState } from "react";
import { api, type FieldJob, type WorkInbox, type NotifyPreference, type WorkNotification, type WorkRow } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { Due, ScenarioClock, holder, usePerson, when } from "../../work";
import { useAuth } from "../../Auth";
import { switchProject } from "../../authState";
import { SampleTeam, StepBadges, byUrgency, caseHref, priorityText, taskHref, useAct } from "./shared";
import { ComingUp, FieldJobCard, Outbox, RoundsThisShift } from "../Field";
import { fieldJobs } from "../../fieldOffline";

/** What My work holds, counted once for this page's badges and for Home (#556), so both show the same numbers.
 * A field job's card carries its case's step, so that case is not counted twice. */
export function myWorkCounts(d: WorkInbox, jobs: FieldJob[]) {
  const onCard = new Set(jobs.map((j) => j.item_id).filter((id): id is number => id != null));
  const all = [...d.mine, ...d.queue].filter((r) => !onCard.has(r.item_id));
  return {
    sent: jobs.filter((j) => !j.queued).length, mine: d.mine.length, queue: d.queue.length,
    overdue: all.filter((r) => r.overdue).length, escalated: all.filter((r) => r.escalated_at).length,
    top: [...all].sort(byUrgency).slice(0, 3),
  };
}

export function InboxPage() {
  const [person] = usePerson();
  const inbox = useAsync(() => (person ? api.inbox(person) : Promise.resolve(null)), [person]);
  // Jobs dispatched to them (or waiting in their queues) and what they are planned onto: the field side of their work.
  // Kept on the phone, so the jobs still read without signal (P5.3c).
  const field = useAsync(() => (person ? fieldJobs(person) : Promise.resolve(null)), [person]);
  const stats = useAsync(() => api.stats(), []);
  // Exceptions nothing routes wait on Exceptions for the IOC to dispatch: say so here, where the ops lead looks.
  const exceptions = useAsync(() => api.exceptions({ state: "open" }), []);
  // The shift handover is reached from here, not the nav (#504): a banner until the incoming lead acknowledges it.
  const handovers = useAsync(() => api.handovers(), []);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  useEffect(() => setPicked(new Set()), [person]);
  if (!person) return <section><PageHeader>My work</PageHeader><UICard title="Pick your name to see your work">
    <p>Choose your name under “You are” in the menu.</p>
    <ButtonLink href={href("/teams")}>View team workload</ButtonLink>
    <SampleTeam />
  </UICard></section>;
  const offline = field.data?.offline;
  if (!inbox.data && !offline) return <Status loading={inbox.loading} error={inbox.error} />;
  const none = { total: 0, overdue: 0, due_soon: 0 };
  const d: WorkInbox = inbox.data ?? { person, roles: [], mine: [], queue: [], counts: { mine: none, queue: none } };
  const keep = (r: WorkRow) => (!type || r.type === type) &&
    (!q || `${r.title} ${r.name ?? ""} ${r.type}`.toLowerCase().includes(q.toLowerCase()));
  const jobs = field.data?.jobs ?? [];
  const coming = field.data?.coming_up ?? [];
  const sent = jobs.filter((j) => !j.queued && keep(j as unknown as WorkRow));
  const waitingJobs = jobs.filter((j) => j.queued && keep(j as unknown as WorkRow));
  // A job's card carries its case's step, its instruction and the report buttons, so the same case isn't listed twice.
  const onCard = new Set(jobs.map((j) => j.item_id).filter((id): id is number => id != null));
  const all = [...d.mine, ...d.queue].filter((r) => !onCard.has(r.item_id));
  const escalated = all.filter((r) => r.escalated_at && keep(r)).sort(byUrgency);
  const mine = d.mine.filter((r) => !onCard.has(r.item_id) && !r.escalated_at && keep(r)).sort(byUrgency);
  const queue = d.queue.filter((r) => !onCard.has(r.item_id) && !r.escalated_at && keep(r)).sort(byUrgency);
  const counts = myWorkCounts(d, jobs);
  const undispatched = d.roles.includes("ops_lead")
    ? (exceptions.data ?? []).filter((r) => !r.item_id && !r.planned && r.state === "open").length : 0;
  const reviews = stats.data ? Object.values(stats.data.open_reviews).reduce((a, b) => a + b, 0) : 0;
  const types = [...new Set(all.map((r) => r.type))].sort();
  const pickedRows = all.filter((r) => r.task_id && picked.has(r.task_id));
  const lead = d.roles.includes("ops_lead");
  const handover = lead ? handovers.data?.[0] : undefined;
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  return (
    <section>
      <PageHeader subtitle="Everything waiting on you." help="workflow"
        actions={lead ? <><ButtonLink href={href("/handover")}>Shift handover</ButtonLink>
          <ButtonLink href={href("/teams")}>Team workload</ButtonLink></> : undefined}>
        My work</PageHeader>
      {handover && handover.state !== "acknowledged" && <Notice tone="warn" className="row">
        <strong>Shift handover ready, {when(handover.shift_end)}.</strong>
        <span>{handover.lines ?? 0} lines{handover.notes_by ? `, notes from ${handover.notes_by}` : ""}.</span>
        <ButtonLink variant="primary" href={href("/handover", { id: String(handover.id) })}>Read and acknowledge</ButtonLink>
      </Notice>}
      {offline && <Notice tone="warn">No signal: these are your jobs as kept on this phone{offline.since ? ` ${when(offline.since)}` : ""}.
        Reports wait here and send when the signal returns.</Notice>}
      <Outbox onSent={() => { field.reload(); inbox.reload(); }} />
      {!offline && <NewForYou person={person} />}
      <p className="row small">
        {counts.sent > 0 && <UIBadge className="badge">{counts.sent} sent to you</UIBadge>}
        <UIBadge className="badge">{counts.mine} assigned to you</UIBadge>
        <UIBadge className="badge">{counts.queue} unclaimed in your roles ({d.roles.join(", ") || "none"})</UIBadge>
        {counts.overdue > 0 && <UIBadge className="badge late">{counts.overdue} overdue</UIBadge>}
        {counts.escalated > 0 && <UIBadge className="badge late">{counts.escalated} escalated</UIBadge>}
        {undispatched > 0 && <a className="badge late" href={href("/exceptions")}>{undispatched} exceptions waiting to be dispatched</a>}
        {reviews > 0 && <a className="badge" href={href("/masterdata", { tab: "questions" })}>{reviews} data items to review</a>}
        {d.covering?.length ? <UIBadge className="badge">covering for {d.covering.join(", ")}</UIBadge> : null}
      </p>
      <ScenarioClock />
      <div className="row">
        <Input aria-label="Filter my work by case or step" placeholder="filter by case or step" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select aria-label="Filter my work by case type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">every kind of case</option>
          {types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
        </Select>
      </div>
      {pickedRows.length > 0 && <Bulk rows={pickedRows} person={person} onDone={() => setPicked(new Set())} />}
      {!all.length && !jobs.length && <Empty>Nothing waiting for you{undispatched > 0 ? <>, but {undispatched} exceptions are <a href={href("/exceptions")}>waiting to be dispatched</a></> : ""}.</Empty>}
      {!all.length && !jobs.length && <SampleTeam onAdded={inbox.reload} />}
      <Group title="Escalated to you" rows={escalated} person={person} picked={picked} toggle={toggle}
        hint="past their SLA and moved to you or your role" />
      {sent.length > 0 && <section className="field-jobs" aria-labelledby="sent-to-you">
        <h2 id="sent-to-you">Sent to you <span className="muted small">{sent.length} · jobs the IOC dispatched, most urgent first</span></h2>
        {sent.map((j) => <FieldJobCard key={j.id} job={j} person={person} onChange={field.reload} />)}
      </section>}
      <Group title="Assigned to you" rows={mine} person={person} picked={picked} toggle={toggle} />
      <Group title="Unclaimed in your roles" rows={queue} person={person} picked={picked} toggle={toggle}
        hint="claim one to make it yours" />
      {waitingJobs.length > 0 && <section className="field-jobs" aria-labelledby="waiting-jobs">
        <h2 id="waiting-jobs">Field jobs waiting in your queues <span className="muted small">{waitingJobs.length} · take one to make it yours</span></h2>
        {waitingJobs.map((j) => <FieldJobCard key={j.id} job={j} person={person} onChange={field.reload} />)}
      </section>}
      {d.roles.includes("operator") && !offline && <RoundsThisShift />}
      {coming.length > 0 && <ComingUp items={coming} person={person} onChange={field.reload} />}
      <OtherFields person={person} />
      <HowYouAreTold person={person} />
    </section>
  );
}

/** What the person was told since they last looked (P5.2, #604): each line opens its case and counts as read. */
function NewForYou({ person }: { person: string }) {
  const told = useAsync(() => api.notifications(person), [person]);
  const unread = (told.data?.notifications ?? []).filter((n) => !n.read_at);
  if (!unread.length) return null;
  const read = (ids?: number[]) => api.markNotificationsRead(person, ids).then(told.reload, told.reload);
  return (
    <UICard className="new-for-you" title={`New for you (${told.data?.unread ?? unread.length})`}
      actions={<Button onClick={() => read()}>Mark all read</Button>}>
      <ul className="notification-list">
        {unread.slice(0, 8).map((n) => <NotificationLine key={n.id} n={n} onOpen={() => void read([n.id])} />)}
      </ul>
    </UICard>
  );
}

const KIND: Record<WorkNotification["kind"], string> = {
  dispatched: "Sent to you", handed_back: "Handed back", escalated: "Escalated", assigned: "Assigned", due_soon: "Due soon",
};

function NotificationLine({ n, onOpen }: { n: WorkNotification; onOpen: () => void }) {
  return (
    <li>
      <UIBadge className={`badge ${n.kind === "escalated" || n.kind === "due_soon" ? "late" : ""}`}>{KIND[n.kind]}</UIBadge>
      <a href={n.link} onClick={onOpen}>{n.text}</a>
      <span className="small">{when(n.created_at)}</span>
    </li>
  );
}

const CHANNEL: Record<NotifyPreference["channel"], string> = {
  in_app: "Only here, on My work", email: "Email", teams: "Teams", slack: "Slack",
};

/** Where the person is told besides My work, and their quiet hours, when nothing is sent (P5.2b, #606). */
function HowYouAreTold({ person }: { person: string }) {
  const pref = useAsync(() => api.notifyPreference(person), [person]);
  const [draft, setDraft] = useState<NotifyPreference | null>(null);
  const [state, setState] = useState<{ error?: string; saved?: boolean }>({});
  if (!pref.data) return null;
  const p = draft ?? pref.data;
  const change = (patch: Partial<NotifyPreference>) => { setDraft({ ...p, ...patch }); setState({}); };
  const save = () => api.setNotifyPreference(person, p, person)
    .then(() => { setDraft(null); setState({ saved: true }); pref.reload(); }, (e: Error) => setState({ error: e.message }));
  const external = p.channel !== "in_app";
  return (
    <UICard className="" title="How you are told">
      <div className="row small">
        <label>Also send to{" "}
          <Select value={p.channel} onChange={(e) => change({ channel: e.target.value as NotifyPreference["channel"] })}>
            {Object.entries(CHANNEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </label>
        {external && <label>{p.channel === "email" ? "Address" : "Webhook kept in"}{" "}
          <Input value={p.address ?? ""} placeholder={p.channel === "email" ? "name@company.com" : "env:NAME"}
            onChange={(e) => change({ address: e.target.value })} /></label>}
        {external && <label>Quiet from{" "}
          <Input type="time" value={p.quiet_from?.slice(0, 5) ?? ""} onChange={(e) => change({ quiet_from: e.target.value || null })} /></label>}
        {external && <label>to{" "}
          <Input type="time" value={p.quiet_to?.slice(0, 5) ?? ""} onChange={(e) => change({ quiet_to: e.target.value || null })} /></label>}
        <Button variant="primary" disabled={!draft} onClick={save}>Save</Button>
        {state.error && <span role="alert" className="error">{state.error}</span>}
        {state.saved && <span role="status">Saved.</span>}
      </div>
    </UICard>
  );
}

/** The same person's work in the other fields they hold a role in (#497): an overarching specialist or monitoring
 * desk sees every field from one Inbox. Fields they may not open aren't listed (the server checks); opening one
 * switches this tab to that field. */
function OtherFields({ person }: { person: string }) {
  const session = useAuth();
  const fields = useAsync(() => api.inboxFields(person), [person]);
  const here = session?.project ?? "";
  const others = (fields.data ?? []).filter((f) => f.project !== here && f.mine.length + f.queue.length > 0);
  if (!others.length) return null;
  return (
    <UICard className="">
      <h2>In your other fields <span className="muted small">{others.length} · you hold roles there too</span></h2>
      {others.map((f) => <div key={f.project}>
        <h3 className="small">{f.project} <span className="muted">· {f.mine.length} assigned to you · {f.queue.length} unclaimed
          in {f.roles.join(", ").replace(/_/g, " ")}</span>{" "}
          <Button size="sm" variant="secondary" onClick={() => switchProject(f.project)}>Work in {f.project}</Button></h3>
        <Table variant="results"><tbody>{[...f.mine, ...f.queue].sort(byUrgency).slice(0, 5).map((r) =>
          <tr key={`${f.project}-${r.task_id ?? "i"}-${r.item_id}`}>
            <td><span className="work-title">{r.title}</span><div className="small muted"><strong>{r.name ?? "open"}</strong> · {r.type.replace(/_/g, " ")} · with {holder(r.assignee, r.role, r.kind)}</div></td>
            <td className="nowrap"><Due at={r.due_at} /></td>
          </tr>)}</tbody></Table>
      </div>)}
    </UICard>
  );
}

function Group({ title, rows, person, picked, toggle, hint }: {
  title: string; rows: WorkRow[]; person: string; picked: Set<number>; toggle: (id: number) => void; hint?: string;
}) {
  if (!rows.length) return null;
  return (
    <UICard className="">
      <h2>{title} <span className="muted small">{rows.length}{hint ? ` · ${hint}` : ""}</span></h2>
      <Table variant="results" className="phone-rows">
        <tbody>{rows.map((r) => <Row key={`${r.task_id ?? "i"}-${r.item_id}`} r={r} person={person} picked={picked} toggle={toggle} />)}</tbody>
      </Table>
    </UICard>
  );
}

function Row({ r, person, picked, toggle }: { r: WorkRow; person: string; picked: Set<number>; toggle: (id: number) => void }) {
  const a = useAct();
  const open = r.task_id ? taskHref(r.task_id) : caseHref(r.item_id);
  return (
    <tr>
      <td className="ui-table-select-cell">
        {r.task_id && r.bulk.length > 0 && <Input type="checkbox" checked={picked.has(r.task_id)} onChange={() => toggle(r.task_id!)} />}
      </td>
      <td>
        <a href={open} className="work-title">{r.title}</a>
        <div className="small muted">
          <strong>{r.name ?? "open"}</strong> · <UIBadge className="badge">{r.type.replace(/_/g, " ")}</UIBadge> · with {holder(r.assignee, r.role, r.kind)}
          {priorityText(r.priority) && <> · priority {priorityText(r.priority)}</>}
        </div>
        {r.handed_reason && <div className={r.handed_reason.startsWith("Back from the field") ? "small field-back" : "small muted"}>
          {r.handed_reason}</div>}
        <div className="row small"><StepBadges row={r} /></div>
      </td>
      <td className="nowrap"><Due at={r.due_at} /></td>
      <td className="nowrap">
        {r.task_id && !r.assignee && (
          <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("claim", () => api.claim(r.task_id!, person))}>Claim</Button>
        )}{" "}
        <ButtonLink href={open}>Open</ButtonLink>
        {a.error && <div className="error small">{a.error}</div>}
      </td>
    </tr>
  );
}

function Bulk({ rows, person, onDone }: { rows: WorkRow[]; person: string; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const a = useAct();
  const shared = rows.map((r) => r.bulk).reduce((x, y) => x.filter((l) => y.includes(l)));
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <UICard className="bulk-bar actions">
      <strong>{rows.length} selected</strong>
      <Input placeholder="reason (kept on each case)" value={reason} onChange={(e) => setReason(e.target.value)} />
      {shared.length ? shared.map((l) => (
        <Button key={l} variant="primary" disabled={!!a.busy} onClick={() => a.run(l, () => api.bulkComplete(rows.map((r) => r.task_id!), person, l, reason || undefined),
          (out) => { setMsg(`${l}: ${out.done.length} done${out.refused.length ? `, ${out.refused.length} refused` : ""}`); onDone(); })}>{l}</Button>
      )) : <span className="muted small">These steps share no bulk action.</span>}
      {msg && <span className="small">{msg}</span>}
      {a.error && <span className="error small">{a.error}</span>}
    </UICard>
  );
}
