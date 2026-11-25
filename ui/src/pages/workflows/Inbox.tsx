import { Button, ButtonLink, Notice, PageHeader, Table, Card as UICard, Input, Select, Badge as UIBadge, Empty, ErrorText, Line, Text, Field, Heading } from "../../ui";
import { when } from "../../format";
/** My work (#503): everything waiting on you, most urgent first, for everyone from the ops lead to the field operator:
 * escalated to you, jobs the IOC sent you (with the field report buttons), steps assigned to you (and to anyone you're
 * covering for), what waits unclaimed in your roles' queues, and what you're planned onto. One page for every role at
 * every width (#1345): the Work tab opens it, and `#/field`, `#/work` and the List's old Waiting on me redirect here. */
import { useEffect, useState } from "react";
import { api, type FieldJob, type WorkInbox, type NotifyPreference, type WorkNotification, type WorkRow } from "../../api";
import { Status, href, useAsync, useRouteState, useAction } from "../../lib";
import { usePerson } from "../../work";
import { useAuth } from "../../Auth";
import { switchProject } from "../../authState";
import { SampleTeam, WorkLine, byUrgency, caseHref } from "./shared";
import { ComingUp, FieldJobCard, NoteToIOC, Outbox, RoundsThisShift } from "../Field";
import { fieldJobs } from "../../fieldOffline";
import { cap, person as personName } from "../../wording";
import { FieldModeOffer } from "../../theme";
import { waitingToDispatch } from "../Exceptions";

/** What My work holds, counted once for this page's badges and for Home (#556), so both show the same numbers.
 * A field job's card carries its case's step, so that case is not counted twice. */
export function myWorkCounts(d: WorkInbox, jobs: Pick<FieldJob, "item_id" | "queued">[]) {
  const onCard = new Set(jobs.map((j) => j.item_id).filter((id): id is number => id != null));
  const all = [...d.mine, ...d.queue].filter((r) => !onCard.has(r.item_id));
  return {
    // The server's totals, not the listed rows: the lists stop at a page, the counts don't.
    sent: jobs.filter((j) => !j.queued).length, mine: d.counts?.mine.total ?? d.mine.length,
    queue: d.counts?.queue.total ?? d.queue.length,
    overdue: d.counts ? d.counts.mine.overdue + d.counts.queue.overdue : all.filter((r) => r.overdue).length, escalated: all.filter((r) => r.escalated_at).length,
    top: [...all].sort(byUrgency).slice(0, 3),
  };
}

export function InboxPage() {
  const [person] = usePerson();
  const inbox = useAsync(() => (person ? api.inbox(person) : Promise.resolve(null)), [person]);
  // Jobs dispatched to them (or waiting in their queues) and what they are planned onto: the field side of their work.
  // Kept on the phone, so the jobs still read without signal (P5.3c).
  const field = useAsync(() => (person ? fieldJobs(person) : Promise.resolve(null)), [person]);
  // Exceptions nothing routes wait on Exceptions for the IOC to dispatch: say so here, where the ops lead looks.
  const exceptions = useAsync(() => api.exceptions({ state: "open" }), []);
  // The shift handover is reached from here, not the nav (#504): a banner until the incoming lead acknowledges it.
  const handovers = useAsync(() => api.handovers(), []);
  // What is new for them, read here so the page can wait for it (NewForYou draws it).
  const told = useAsync(() => (person ? api.notifications(person) : Promise.resolve(null)), [person]);
  // Kept in the link, so Back from a job returns to My work filtered as it was (P5.14c).
  const [q, setQ] = useRouteState("q");
  const [type, setType] = useRouteState("type");
  // One list (#1208 review): the chips pick what it holds, kept in the link like the filters.
  const [show, setShow] = useRouteState("show");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  useEffect(() => setPicked(new Set()), [person]);
  // What the field just reported, said where its card was: the card leaves the list once the IOC has it (#1344).
  const [reported, setReported] = useState<{ id: number; said: string; at: number; list: "sent" | "waiting" } | null>(null);
  useEffect(() => { if (reported) document.getElementById("field-reported")?.scrollIntoView({ block: "nearest" }); }, [reported]);
  if (!person) return <section><PageHeader>My work</PageHeader><UICard title="Pick your name to see your work">
    <p>Choose your name under “You are” in the menu.</p>
    <ButtonLink href={href("/teams")}>View team workload</ButtonLink>
    <SampleTeam />
  </UICard></section>;
  const offline = field.data?.offline;
  if (!inbox.data && !offline) return <Status of={inbox} />;
  // The handover banner, what is new, the counts and the jobs sent to them all sit above the lists: drawn as each
  // answered, every one pushed the lists down the page (#1069). The page waits for them, failures included.
  const settled = (a: { data?: unknown; error?: unknown }) => a.data !== undefined || !!a.error;
  if (!offline && ![field, handovers, told, exceptions].every(settled)) return <Status loading />;
  const none = { total: 0, overdue: 0, due_soon: 0 };
  const d: WorkInbox = inbox.data ?? { person, roles: [], mine: [], queue: [], counts: { mine: none, queue: none } };
  const keep = (r: WorkRow) => (!type || r.type === type) &&
    (!q || `${r.title} ${r.name ?? ""} ${r.type}`.toLowerCase().includes(q.toLowerCase()));
  const jobs = field.data?.jobs ?? [];
  const coming = field.data?.coming_up ?? [];
  // Field work leads an operator's page; anyone else (an ops lead a field check escalated to) sends it out again from
  // its row, so a job waiting in their queue is not offered to them as a field card with "Take it".
  const fieldRole = d.roles.includes("operator");
  const sent = jobs.filter((j) => !j.queued && keep(j as unknown as WorkRow));
  const waitingJobs = jobs.filter((j) => j.queued && keep(j as unknown as WorkRow)
    && (fieldRole || !j.role || j.role === "operator"));
  // A job's card carries its case's step, its instruction and the report buttons, so the same case isn't listed twice.
  const onCard = new Set([...sent, ...waitingJobs].map((j) => j.item_id).filter((id): id is number => id != null));
  // Done or Could not do is said where the job's card was, in its place until OK.
  const cards = (list: FieldJob[], name: "sent" | "waiting") => {
    const said = reported?.list === name && <Notice key="reported" id="field-reported" role="status" className="row">
      <span>{reported.said}</span><Button size="sm" onClick={() => setReported(null)}>OK</Button></Notice>;
    const out = list.map((j, i) => said && reported?.id === j.id ? said
      : <FieldJobCard key={j.id} job={j} person={person} onChange={field.reload}
        onReported={(text) => setReported({ id: j.id, said: text, at: i, list: name })} />);
    if (said && !list.some((j) => j.id === reported?.id)) out.splice(Math.min(reported!.at, out.length), 0, said);
    return out;
  };
  const all =[...d.mine, ...d.queue].filter((r) => !onCard.has(r.item_id));
  const mineIds = new Set(d.mine.map((r) => r.task_id ?? -r.item_id));
  const isMine = (r: WorkRow) => mineIds.has(r.task_id ?? -r.item_id);
  // Escalated first, then the most urgent: one list, as Home's Needs you lists it.
  const ranked = all.filter(keep).sort((a, b) => Number(!!b.escalated_at) - Number(!!a.escalated_at) || byUrgency(a, b));
  const CHIPS: { key: string; label: string; rows: WorkRow[]; danger?: boolean }[] = [
    { key: "", label: "All", rows: ranked },
    { key: "mine", label: "Assigned to you", rows: ranked.filter(isMine) },
    { key: "queue", label: "Unclaimed in your roles", rows: ranked.filter((r) => !isMine(r)) },
    { key: "escalated", label: "Escalated", rows: ranked.filter((r) => r.escalated_at), danger: true },
  ];
  const chip = CHIPS.find((c) => c.key === (show ?? "")) ?? CHIPS[0];
  const counts = myWorkCounts(d, jobs);
  const undispatched = d.roles.includes("ops_lead")
    ? (exceptions.data ?? []).filter(waitingToDispatch).length : 0;
  const types = [...new Set(all.map((r) => r.type))].sort();
  const pickedRows = all.filter((r) => r.task_id && picked.has(r.task_id));
  const lead = d.roles.includes("ops_lead");
  // The latest handover whose shift has ended: one still being written by the outgoing lead isn't ready to read.
  const handover = lead ? handovers.data?.find((h) => new Date(h.shift_end).getTime() <= Date.now()) : undefined;
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  return (
    <section>
      <PageHeader
        actions={lead ? <><ButtonLink href={href("/handover")}>Shift handover</ButtonLink>
          <ButtonLink href={href("/teams")}>Team workload</ButtonLink></> : undefined}>
        My work</PageHeader>
      {handover && handover.state !== "acknowledged" && <Notice tone="warn" className="row">
        <strong>Shift handover ready, {when(handover.shift_end)}.</strong>
        <span>{handover.lines ?? 0} lines{handover.notes_by ? `, notes from ${personName(handover.notes_by)}` : ""}.</span>
        <ButtonLink variant="primary" href={href("/handover", { id: String(handover.id) })}>Read and acknowledge</ButtonLink>
      </Notice>}
      {offline && <Notice tone="warn">No signal: these are your jobs as kept on this phone{offline.since ? ` ${when(offline.since)}` : ""}.
        Reports wait here and send when the signal returns.</Notice>}
      <FieldModeOffer />
      <Outbox onSent={() => { field.reload(); inbox.reload(); }} jobs={jobs.filter((j) => !j.queued)} />
      {/* What changed on your work since you last looked is Home's Needs you; a field operator, whose Home is this
          page, reads it beside their jobs below. */}
      <nav className="chips work-views" aria-label="Show in my work">
        {CHIPS.map((c) => <Button key={c.key} className="chip"
          aria-pressed={c === chip} onClick={() => setShow(c.key)}>
          {c.label} {c.danger && c.rows.length ? <UIBadge tone="danger">{c.rows.length}</UIBadge> : <Text muted>{c.rows.length}</Text>}</Button>)}
        {undispatched > 0 && <a className="chip" href={href("/exceptions")}>To dispatch <UIBadge tone="danger">{undispatched}</UIBadge></a>}
      </nav>
      <Text as="p" small muted>Your roles: {d.roles.map((r) => r.replace(/_/g, " ")).join(", ") || "none"}
        {d.covering?.length ? `. Covering for ${d.covering.join(", ")}` : ""}
        {counts.overdue > 0 ? `. ${counts.overdue} overdue` : ""}.</Text>
      <Line>
        <Input aria-label="Filter my work by case or step" placeholder="filter by case or step" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select aria-label="Filter my work by case type" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">All kinds</option>
          {types.map((t) => <option key={t} value={t}>{cap(t.replace(/_/g, " "))}</option>)}
        </Select>
      </Line>
      {pickedRows.length > 0 && <Bulk rows={pickedRows} person={person} onDone={() => setPicked(new Set())} />}
      {!all.length && !jobs.length && <Empty>Nothing waiting for you{undispatched > 0 ? <>, but {undispatched} exceptions are <a href={href("/exceptions")}>waiting to be dispatched</a></> : ""}.</Empty>}
      {!all.length && !jobs.length && <SampleTeam onAdded={inbox.reload} />}
      {(sent.length > 0 || reported?.list === "sent") && <section className="field-jobs" aria-labelledby="sent-to-you">
        <Heading id="sent-to-you" detail={sent.length}>Your field jobs</Heading>
        {cards(sent, "sent")}
      </section>}
      {!offline && fieldRole && <NoteToIOC person={person} />}
      {!offline && fieldRole && <NewForYou person={person} told={told} onCard={onCard} />}
      <Group title={chip.key ? chip.label : "Waiting on you"} rows={chip.rows} person={person} picked={picked} toggle={toggle}
        hint={chip.key === "queue" ? "claim one to make it yours" : chip.key === "escalated" ? "past their SLA and moved to you or your role" : undefined} />
      {(waitingJobs.length > 0 || reported?.list === "waiting") && <section className="field-jobs" aria-labelledby="waiting-jobs">
        <Heading id="waiting-jobs" detail={waitingJobs.length}>{d.roles.includes("operator") ? "Field jobs" : "Jobs"} waiting in your queues</Heading>
        {cards(waitingJobs, "waiting")}
      </section>}
      {(d.roles.includes("operator") || offline) && <RoundsThisShift person={person} />}
      {coming.length > 0 && <ComingUp items={coming} person={person} onChange={field.reload} />}
      <OtherFields person={person} />
      <HowYouAreTold person={person} />
    </section>
  );
}

/** What the person was told since they last looked (P5.2, #604): each line opens its case and counts as read. */
function NewForYou({ person, told, onCard }: {
  person: string;
  told: { data?: Awaited<ReturnType<typeof api.notifications>> | null; reload: () => void };
  /** Cases on a field card above: the card says when they are due, so no notice repeats it (#1344). */
  onCard: Set<number>;
}) {
  // One line per case and kind of news, the latest: three escalations of one case read as one.
  const seen = new Set<string>();
  const unread = (told.data?.notifications ?? []).filter((n) => !n.read_at && !(n.item_id != null && onCard.has(n.item_id)))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .filter((n) => { const k = `${n.item_id ?? n.id}:${n.kind}`; if (seen.has(k)) return false; seen.add(k); return true; });
  if (!unread.length) return null;
  const read = (ids?: number[]) => api.markNotificationsRead(person, ids).then(told.reload, told.reload);
  return (
    <UICard className="new-for-you" title={`New for you (${unread.length})`}
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
      <UIBadge tone={n.kind === "escalated" || n.kind === "due_soon" ? "danger" : "muted"}>{KIND[n.kind]}</UIBadge>
      <a href={n.link} onClick={onOpen}>{n.text}</a>
      <span className="small when">{when(n.created_at)}</span>
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
    <UICard title="How you are told">
      <Line small>
        <Field inline label="Also send to"><Select value={p.channel} onChange={(e) => change({ channel: e.target.value as NotifyPreference["channel"] })}>
            {Object.entries(CHANNEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select></Field>
        {external && <label>{p.channel === "email" ? "Address" : "Webhook kept in"}{" "}
          <Input value={p.address ?? ""} placeholder={p.channel === "email" ? "name@company.com" : "env:NAME"}
            onChange={(e) => change({ address: e.target.value })} /></label>}
        {external && <Field inline label="Quiet from"><Input type="time" value={p.quiet_from?.slice(0, 5) ?? ""} onChange={(e) => change({ quiet_from: e.target.value || null })} /></Field>}
        {external && <Field inline label="to"><Input type="time" value={p.quiet_to?.slice(0, 5) ?? ""} onChange={(e) => change({ quiet_to: e.target.value || null })} /></Field>}
        <Button variant="primary" disabled={!draft} onClick={save}>Save</Button>
        <ErrorText inline>{state.error}</ErrorText>
        {state.saved && <span role="status">Saved.</span>}
      </Line>
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
    <UICard title="In your other fields" detail={<>{others.length} · you hold roles there too</>}>
      {others.map((f) => <div key={f.project}>
        <Heading level={3} small detail={`${f.mine.length} assigned to you · ${f.queue.length} unclaimed in ${f.roles.join(", ").replace(/_/g, " ")}`}
          actions={<Button size="sm" variant="secondary" onClick={() => switchProject(f.project)}>Work in {f.project}</Button>}>{f.project}</Heading>
        <Table variant="results"><tbody>{[...f.mine, ...f.queue].sort(byUrgency).slice(0, 5).map((r) =>
          <tr key={`${f.project}-${r.task_id ?? "i"}-${r.item_id}`}>
            <td><WorkLine r={r} title={<span className="work-title">{r.title}</span>} /></td>
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
    <UICard title={title} detail={<>{rows.length}{hint ? ` · ${hint}` : ""}</>}>
      <Table variant="results" className="phone-rows">
        <tbody>{rows.map((r) => <Row key={`${r.task_id ?? "i"}-${r.item_id}`} r={r} person={person} picked={picked} toggle={toggle} />)}</tbody>
      </Table>
    </UICard>
  );
}

function Row({ r, person, picked, toggle }: { r: WorkRow; person: string; picked: Set<number>; toggle: (id: number) => void }) {
  const a = useAction();
  const open = caseHref(r.item_id);
  return (
    <tr>
      <td className="ui-table-select-cell">
        {r.task_id && r.bulk.length > 0 && <Input type="checkbox" checked={picked.has(r.task_id)} onChange={() => toggle(r.task_id!)} />}
      </td>
      <td>
        <WorkLine r={r} title={<a href={open} className="work-title">{r.title}</a>} />
      </td>
      <td className="nowrap">
        {r.task_id && !r.assignee && (
          <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("claim", () => api.claim(r.task_id!, person))}>Claim</Button>
        )}{" "}
        <ButtonLink href={open}>Open</ButtonLink>
        <ErrorText>{a.error}</ErrorText>
      </td>
    </tr>
  );
}

function Bulk({ rows, person, onDone }: { rows: WorkRow[]; person: string; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const a = useAction();
  const shared = rows.map((r) => r.bulk).reduce((x, y) => x.filter((l) => y.includes(l)));
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <UICard className="bulk-bar actions">
      <strong>{rows.length} selected</strong>
      <Input placeholder="reason (kept on each case)" value={reason} onChange={(e) => setReason(e.target.value)} />
      {shared.length ? shared.map((l) => (
        <Button key={l} variant="primary" disabled={!!a.busy} onClick={() => a.run(l, () => api.bulkComplete(rows.map((r) => r.task_id!), person, l, reason || undefined),
          (out) => { setMsg(`${l}: ${out.done.length} done${out.refused.length ? `, ${out.refused.length} refused` : ""}`); onDone(); })}>{l}</Button>
      )) : <Text small muted>These steps share no bulk action.</Text>}
      {msg && <Text small>{msg}</Text>}
      <ErrorText inline>{a.error}</ErrorText>
    </UICard>
  );
}
