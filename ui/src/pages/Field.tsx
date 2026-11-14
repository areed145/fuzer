/** Field jobs on My work (#503): what a person has been sent to, laid out for a phone. Each job is one exception routed
 * to them: where to go, the asset and what happened there lately, what was seen, and the few things to say back (on
 * site, done, could not do, a note). What they report lands on the exception as evidence the IOC sees. My jobs was its
 * own page; it is part of My work now, so an engineer sent on a field check finds it in the same place as everything
 * else waiting on them. */
import { useEffect, useRef, useState } from "react";
import { api, type RoundPoint, type RoundRun, type FieldHandover, type FieldJob, type FieldPermit, type FieldReading, type PlanItem } from "../api";
import { Badge, Button, ButtonLink, Card, ConfirmAction, Input, Notice, Select } from "../ui";
import { FieldPhoto, readingText, shrink, useDictation } from "../FieldPhoto";
import { OUTBOX_CHANGED, asNote, describe, discard, dismissCleared, flush, moveReport, sendHandoverAccept, sendPlanChange, sendReport,
  sendTake, useOutbox, type FlushResult, type QueuedTake, type QueuedWrite } from "../fieldOffline";
import { NEEDS_SIGNAL } from "../signal";
import { useSignal } from "../useSignal";
import { Status, entityHref, href, useAsync } from "../lib";
import { Due, reportedBy, when } from "../work";
import { FindingEvidence } from "../Finding";
import { taskHref, useAct } from "./workflows/shared";

/** Reports kept on this phone while there was no signal (P5.3c): the app's one sender (P5.17a) sends them as soon as
 * the signal returns, from whichever page is open; this card counts them and can send now by hand. One the server
 * refused stays here with its reason (P5.17f), for the operator to send as a note, send to another job, or discard. */
export function Outbox({ onSent, jobs = [] }: { onSent: () => void; jobs?: { id: number; title: string }[] }) {
  const { waiting, refused, cleared } = useOutbox();
  const [busy, setBusy] = useState(false);
  const sentRef = useRef(onSent);
  sentRef.current = onSent;
  useEffect(() => {
    const done = (e: Event) => { if ((e as CustomEvent<FlushResult | undefined>).detail?.sent) sentRef.current(); };
    window.addEventListener(OUTBOX_CHANGED, done);
    return () => window.removeEventListener(OUTBOX_CHANGED, done);
  }, []);
  const gone = cleared && <Notice tone={cleared.lost ? "danger" : "warn"} className="field-cleared" role="alert">
    Your offline copy was cleared by the phone; fetching again.
    {cleared.lost > 0 && <> {cleared.lost === 1 ? "1 report" : `${cleared.lost} reports`} made without signal went with it and
      never reached the IOC: tell them what {cleared.lost === 1 ? "it" : "they"} said.</>}
    {" "}<Button variant="ghost" onClick={dismissCleared}>Got it</Button>
  </Notice>;
  if (!waiting.length && !refused.length) return gone || null;
  const n = waiting.length;
  const send = async () => { setBusy(true); try { await flush(); } finally { setBusy(false); } };
  return (<>{gone}
    <Card className="field-outbox" title={n ? `${n} report${n > 1 ? "s" : ""} waiting to send` : "Not accepted"}>
      {n > 0 && <p className="small">Made without signal and kept on this phone. They send, with the time you made them, when the signal returns.</p>}
      {n > 0 && <Button disabled={busy} onClick={send}>{busy ? "Sending…" : "Send now"}</Button>}
      {refused.map((w) => <Refused key={w.client_id} w={w} jobs={jobs} />)}
    </Card>
  </>);
}

function Refused({ w, jobs }: { w: QueuedWrite; jobs: { id: number; title: string }[] }) {
  const { busy, error, run } = useAct();
  const [to, setTo] = useState("");
  const note = asNote(w);
  const from = (w.payload as { exception_id?: number }).exception_id;
  const others = w.kind === "field_report" ? jobs.filter((j) => j.id !== from) : [];
  return <div className="field-refused">
    <p role="alert" className="small"><strong>Not accepted:</strong> {w.refused?.reason}</p>
    <p className="small">{describe(w)}. Kept on this phone.</p>
    <div className="row field-actions">
      {note && <Button disabled={!!busy} onClick={() => run("Sending", note)}>Send as a note</Button>}
      {others.length > 0 && <>
        <Select aria-label="Send it to another of your jobs" value={to} onChange={(e) => setTo(e.target.value)}>
          <option value="">another job…</option>
          {others.map((j) => <option key={j.id} value={j.id}>{j.title}</option>)}
        </Select>
        <Button disabled={!!busy || !to} onClick={() => run("Sending", () => moveReport(w, Number(to)))}>Send there</Button>
      </>}
      <ConfirmAction label="discard" busy={!!busy}
        consequence="Throw this away from the phone. The IOC never gets it, and it can't be brought back."
        onConfirm={() => void run("Discarding", () => discard(w))} />
    </div>
    {error && <p role="alert" className="error small">{error}</p>}
  </div>;
}

const SAID: Record<string, string> = { on_site: "On site", done: "Done", could_not_do: "Could not do", note: "Note" };

const day = (at: string) => new Date(at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

/** What they are planned onto over the next two weeks, so tomorrow's job is no surprise today. On the day, they
 * say when they've started and when it's done; the plan, and the IOC, see it. */
export function ComingUp({ items, person, onChange }: { items: PlanItem[]; person: string; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const soon = Date.now() + 86_400_000;
  // Said without signal, it is kept on the phone and sent when the signal returns (P5.17e).
  const waiting = useOutbox().waiting.filter((w) => w.kind === "plan_change").map((w) => w.payload as { item: number; status: string });
  const set = (i: PlanItem, status: string) =>
    run(status === "done" ? "Done" : "Started", () => sendPlanChange(i.id, person, status), (how) => { if (how === "sent") onChange(); });
  return (
    <Card className="field-coming" title="Coming up">
      <ul className="plain">{items.map((i) => <li key={i.id}>
        <strong>{day(i.start_at)}</strong>{day(i.end_at) !== day(i.start_at) ? `–${day(i.end_at)}` : ""} · {i.title}{" "}
        <span className="muted small">{i.kind.replace(/_/g, " ")}{i.needs_shutdown ? " · in the shutdown" : ""}
          {i.crew?.startsWith("role:") ? ` · for ${i.crew.slice(5)}` : ""}</span>
        {i.status === "in_progress" && <> <Badge tone="ok">started</Badge></>}
        {waiting.filter((c) => c.item === i.id).slice(-1).map((c) => <span key="kept"> <Badge tone="warn">
          {c.status === "done" ? "Done" : "Started"} · waiting to send</Badge></span>)}
        {i.assets.length > 0 && <div className="small">{i.assets.map((a) =>
          <a key={a.fuzer_id} href={entityHref(a.fuzer_id)}>{a.label ?? a.fuzer_id} </a>)}</div>}
        {i.notes && <div className="small muted">{i.notes}</div>}
        {new Date(i.start_at).getTime() <= soon && <div className="row field-actions">
          {i.status !== "in_progress" && <Button size="sm" disabled={!!busy} onClick={() => set(i, "in_progress")}>Started</Button>}
          <Button size="sm" variant="primary" disabled={!!busy} onClick={() => set(i, "done")}>Done</Button>
        </div>}
      </li>)}</ul>
      {busy && <p role="status">{busy}…</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </Card>
  );
}

/** This shift's operator rounds (P5.4): each round, its area and how far it has got; open one to take its readings
 * and checks in order (P5.4b). A reading outside its limits, or a failed check, goes to the IOC as a case. */
export function RoundsThisShift({ person }: { person: string }) {
  const runs = useAsync(() => api.rounds(), []);
  const [open, setOpen] = useState<number | null>(null);
  const kept = useOutbox().waiting.filter((w) => w.kind === "round_take").map((w) => w.payload as QueuedTake);
  // Takes kept on the phone that have now gone: read the rounds again, so they show as taken by the server.
  const [sent, setSent] = useState(0);
  const reload = runs.reload;
  useEffect(() => {
    const done = (e: Event) => { if ((e as CustomEvent<FlushResult | undefined>).detail?.sent) { reload(); setSent((n) => n + 1); } };
    window.addEventListener(OUTBOX_CHANGED, done);
    return () => window.removeEventListener(OUTBOX_CHANGED, done);
  }, [reload]);
  const list = Array.isArray(runs.data) ? runs.data : [];
  if (!list.length) return null;
  return (
    <section className="field-jobs" aria-labelledby="rounds-this-shift">
      <h2 id="rounds-this-shift">Rounds this shift <span className="muted small">{list.length} · until {when(list[0].shift_end)}</span></h2>
      <ul className="plain">{list.map((r) => <li key={r.id}>
        <Button size="sm" aria-expanded={open === r.id} onClick={() => setOpen(open === r.id ? null : r.id)}>
          {r.round}{r.area ? ` (${r.area})` : ""}</Button>{" "}
        <RoundProgress run={r} onPhone={kept.filter((t) => t.run === r.id)} />
        {open === r.id && <RoundPoints key={sent} id={r.id} person={person} canTake={r.state === "open"} onTaken={runs.reload}
          onPhone={kept.filter((t) => t.run === r.id)} />}
      </li>)}</ul>
    </section>
  );
}

function RoundProgress({ run, onPhone }: { run: RoundRun; onPhone: QueuedTake[] }) {
  // Points taken on the phone and not yet sent (P5.17d) count too, and say so.
  const fresh = new Set(onPhone.map((t) => t.point)).size;
  const taken = Math.min(run.points, run.taken + fresh);
  return <span className="small">
    <Badge tone={taken >= run.points ? "ok" : undefined}>{taken} of {run.points} taken</Badge>
    {fresh > 0 && <> <Badge tone="warn">{fresh} on this phone</Badge></>}
    {(run.found ?? 0) > 0 && <> <Badge tone="warn">{run.found} found something</Badge></>}
  </span>;
}

/** Outside a reading point's limits, from the limits kept with the round: so the phone can say so without signal. */
const outside = (p: RoundPoint, v: number) => (p.low != null && v < p.low) || (p.high != null && v > p.high);
const limits = (p: RoundPoint) => `${p.low ?? "…"} to ${p.high ?? "…"} ${p.unit ?? ""}`.trim();

function RoundPoints({ id, person, canTake, onTaken, onPhone }: { id: number; person: string; canTake: boolean;
  onTaken: () => void; onPhone: QueuedTake[] }) {
  const r = useAsync(() => api.round(id), [id]);
  if (!r.data || !Array.isArray(r.data.points)) return <Status loading={r.loading} error={r.error} onRetry={r.reload} />;
  const taken = () => { r.reload(); onTaken(); };
  return <ol className="small">{r.data.points.map((p) => {
    const mine = onPhone.filter((t) => t.point === p.id).at(-1);
    return <li key={p.id}>
      <strong>{p.what}</strong> <span className="muted">{p.asset_label ?? ""}</span>
      {p.kind === "reading" && (p.low != null || p.high != null) && <span className="muted"> · {limits(p)}</span>}
      {p.kind === "check" && <span className="muted"> · check</span>}
      {mine ? <> <Badge tone="warn">
        {p.kind === "reading" ? `${mine.value} ${p.unit ?? ""}`.trim() : mine.passed ? "Passed" : "Found something"} · on this phone</Badge>
        {(mine.passed === false || (p.kind === "reading" && mine.value != null && outside(p, mine.value))) &&
          <span className="muted"> · {p.kind === "reading" ? `outside ${limits(p)}, ` : ""}goes to the IOC when sent</span>}</>
      : p.taken && <> <Badge tone={p.taken.passed === false ? "warn" : "ok"}>
        {p.kind === "reading" ? `${p.taken.value} ${p.unit ?? ""}`.trim() : p.taken.passed ? "Passed" : "Found something"}
        {p.taken.passed === false && p.kind === "reading" ? ", outside its limits" : ""}</Badge>
        {p.taken.exception_id != null && <span className="muted"> · sent to the IOC</span>}</>}
      {canTake && <TakePoint run={id} point={p} person={person} onTaken={taken} />}
    </li>;
  })}</ol>;
}

function TakePoint({ run, point, person, onTaken }: { run: number; point: RoundPoint; person: string; onTaken: () => void }) {
  const [value, setValue] = useState(point.taken?.value != null ? String(point.taken.value) : "");
  const [note, setNote] = useState("");
  const [failing, setFailing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Without signal the take is kept on the phone and sent with the time it was taken (P5.17d).
  const send = (t: { value?: number; passed?: boolean; note?: string }) => {
    setError(null);
    sendTake({ run, point: point.id, by: person, ...t }).then((how) => { setFailing(false); setNote(""); if (how === "sent") onTaken(); },
      (e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  };
  const label = `${point.what}${point.asset_label ? ` on ${point.asset_label}` : ""}`;
  if (point.kind === "reading") {
    const n = Number(value);
    return <div className="toolbar">
      <Input width="sm" inputMode="decimal" aria-label={`Reading for ${label}`} placeholder={point.unit ?? "reading"}
        value={value} onChange={(e) => setValue(e.target.value)} />
      <Button size="sm" disabled={value.trim() === "" || !Number.isFinite(n)} onClick={() => send({ value: n })}>
        {point.taken ? "Correct" : "Save"}</Button>
      {error && <span className="error" role="alert">{error}</span>}
    </div>;
  }
  return <div className="toolbar">
    <Button size="sm" onClick={() => send({ passed: true })}>Passed</Button>
    <Button size="sm" aria-expanded={failing} onClick={() => setFailing(!failing)}>Found something</Button>
    {failing && <>
      <Input aria-label={`What you found at ${label}`} value={note} onChange={(e) => setNote(e.target.value)} />
      <Button size="sm" disabled={!note.trim()} onClick={() => send({ passed: false, note: note.trim() })}>Send to the IOC</Button>
    </>}
    {error && <span className="error" role="alert">{error}</span>}
  </div>;
}

/** A handover at shift change, the outgoing note first (P5.11c). */
function HandoverNote({ h }: { h: FieldHandover }) {
  return (
    <div className="field-sent" role="status">
      <p><strong>{h.mine_to_accept ? `Handed over to you by ${h.from}` : `Handed over to ${h.to}: with you until they accept`}</strong>
        <span className="muted small"> · {when(h.at)}</span></p>
      <p className="small">Done: {h.done}</p>
      <p className="small">Left: {h.left}</p>
      {h.unsafe && <p className="small error">Unsafe to leave: {h.unsafe}</p>}
      {h.permit && <p className="small muted">Permit {h.permit.label} {h.permit.state ?? ""}{h.permit.why_not ? ` · ${h.permit.why_not}` : ""}</p>}
    </div>
  );
}

/** Hand a job in progress to the incoming crew at shift change (P5.11c). */
function HandOver({ job, person, onChange }: { job: FieldJob; person: string; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState("");
  const [done, setDone] = useState("");
  const [left, setLeft] = useState("");
  const [unsafe, setUnsafe] = useState("");
  if (!open) return <Button size="sm" onClick={() => setOpen(true)}>Hand over at shift change</Button>;
  const ready = to.trim() && done.trim() && left.trim();
  return (
    <fieldset className="field-evidence">
      <legend className="small">Hand over to the incoming crew</legend>
      <Input aria-label="Hand over to" placeholder="who takes it" value={to} onChange={(e) => setTo(e.target.value)} />
      <Input aria-label="What was done" placeholder="what was done" value={done} onChange={(e) => setDone(e.target.value)} />
      <Input aria-label="What is left" placeholder="what is left" value={left} onChange={(e) => setLeft(e.target.value)} />
      <Input aria-label="Unsafe to leave" placeholder="anything unsafe to leave (optional)" value={unsafe}
        onChange={(e) => setUnsafe(e.target.value)} />
      <Button variant="primary" disabled={!ready || !!busy} onClick={() => run("Handing over", () => api.fieldHandover(job.id,
        { by: person, to: to.trim(), done: done.trim(), left: left.trim(), unsafe: unsafe.trim() || undefined }), onChange)}>Hand over</Button>
      {error && <p role="alert" className="error small">{error}</p>}
    </fieldset>
  );
}

/** The permit to work a job runs under, with its isolations, and whether it may start (P5.11a). */
function PermitPanel({ permit, inHand, onInHand }: { permit: FieldPermit; inHand: boolean; onInHand: (v: boolean) => void }) {
  const p = permit.permit;
  if (permit.unchecked) return (  // no permit records to check this against (#742): confirm one in hand, kept on the case
    <div className="field-permit">
      <h3 className="small">Permit to work <Badge tone="warn">check permit</Badge></h3>
      <p className="small">{permit.note}</p>
      <label className="small"><input type="checkbox" checked={inHand} onChange={(e) => onInHand(e.target.checked)} />
        {" "}Permit in hand</label>
    </div>
  );
  return (
    <div className="field-permit">
      <h3 className="small">Permit to work {permit.blocked
        ? <Badge tone="warn">blocked: no valid permit</Badge>
        : p?.valid ? <Badge tone="ok">valid</Badge> : null}</h3>
      {p ? <p className="small"><strong>{p.label}</strong> {p.type ?? ""} · {p.state ?? "no state"}
        {p.valid_to ? ` · to ${p.valid_to}` : ""}{p.why_not ? <span className="muted"> · {p.why_not}</span> : null}
        {p.work ? <><br />{p.work}</> : null}</p>
        : <p className="small muted">No permit covers this location.</p>}
      {p && p.isolations.length > 0 && <ul className="plain small">{p.isolations.map((i) => <li key={i.isolation}>
        {i.isolation} · {i.point ?? ""} {i.method ? <span className="muted">({i.method})</span> : null}{" "}
        <Badge tone={["applied", "verified"].includes((i.state ?? "").toLowerCase()) ? "ok" : "warn"}>{i.state ?? "no state"}</Badge>
      </li>)}</ul>}
      {permit.blocked && permit.blocker && <p className="small" role="status">{permit.blocker}</p>}
    </div>
  );
}

export function FieldJobCard({ job, person, onChange }: { job: FieldJob; person: string; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const [note, setNote] = useState("");
  const [what, setWhat] = useState("");
  const [value, setValue] = useState("");
  const [unit, setUnit] = useState("");
  const [photos, setPhotos] = useState<string[]>([]);
  const [problem, setProblem] = useState<string | null>(null);
  const [kept, setKept] = useState(false);
  const [why, setWhy] = useState("");
  const [inHand, setInHand] = useState(false);
  const blocked = !!job.permit?.blocked;
  const signal = useSignal();
  // Reports for this job still on the phone (P5.17b): shown as waiting, not as done, until the server has them.
  const mine = useOutbox().waiting.filter((w) => (w.payload as { exception_id?: number }).exception_id === job.id);
  const waiting = mine.filter((w) => w.kind === "field_report");
  const dictate = useDictation((t) => setNote((n) => (n.trim() ? `${n.trim()} ${t}` : t)));
  const reading: FieldReading | undefined = value.trim() ? { value: Number(value), unit: unit.trim(), what: what.trim() || null } : undefined;
  const say = (status: string) => {
    const sent = note.trim();
    if (reading && (!Number.isFinite(reading.value) || !reading.unit)) return setProblem("A reading needs a number and its unit.");
    setProblem(null);
    // Clear only the note that went: words typed while the request was in flight stay.
    const override = status === "on_site" && blocked ? why.trim() || undefined : undefined;
    const permit_in_hand = status === "on_site" && job.permit?.unchecked ? inHand : undefined;
    return run(SAID[status], () => sendReport({ exception_id: job.id, by: person, status, note: sent || undefined, reading, photos, override,
      permit_in_hand }),
      (how) => {
        setNote((n) => (n.trim() === sent ? "" : n)); setValue(""); setWhat(""); setPhotos([]);
        setKept(how === "queued");
        if (how === "sent") onChange();
      });
  };
  const addPhoto = async (files: FileList | null) => {
    try {
      const added = await Promise.all(Array.from(files ?? []).map((f) => shrink(f)));
      setPhotos((p) => [...p, ...added].slice(0, 4));
    } catch {
      setProblem("That photo could not be read.");
    }
  };
  const last = job.field[job.field.length - 1];
  return (
    <Card className="field-job" title={<>{job.title} <Badge>{job.label}</Badge></>}>
      {job.handover && <HandoverNote h={job.handover} />}
      {job.handover?.mine_to_accept && <div className="row field-actions">
        {mine.some((w) => w.kind === "handover_accept") ? <Badge tone="warn">Accepted · waiting to send</Badge>
          : <Button variant="primary" disabled={!!busy}
          onClick={() => run("Accepting", () => sendHandoverAccept(job.id, person), (how) => { if (how === "sent") onChange(); })}>Accept the handover</Button>}
      </div>}
      <p className="row small">
        <Due at={job.due} />
        {last && <Badge tone="ok">{SAID[last.status] ?? last.status} · {when(last.at)}</Badge>}
        {waiting.length > 0 && <Badge tone="warn">{waiting.length === 1 ? `${SAID[(waiting[0].payload as { status: string }).status] ?? "Report"} waiting to send`
          : `${waiting.length} reports waiting to send`}</Badge>}
        <span className="muted">seen {job.reports ?? job.raised_count}× since {when(job.first_seen)}</span>
      </p>
      {job.sent && (job.sent.reason || !job.queued) && <p className="field-sent">
        <strong>{job.sent.reason ? job.sent.reason : "No instructions given"}</strong>
        {job.sent.by && <span className="muted small"> · sent by {job.sent.by.replace(/^agent:/, "")}{job.sent.at ? ` ${when(job.sent.at)}` : ""}</span>}</p>}
      {job.assets.length > 0 && <div>
        <h3 className="small">Where</h3>
        <ul className="plain">{job.assets.map((a) => <li key={a.fuzer_id}>
          <a href={entityHref(a.fuzer_id)}>{a.label ?? a.fuzer_id}</a> <span className="muted small">{a.kind}</span>
          {a.lat != null && a.lon != null && <> · <a href={`https://www.google.com/maps/search/?api=1&query=${a.lat},${a.lon}`}
            target="_blank" rel="noreferrer">directions</a></>}
        </li>)}</ul>
      </div>}
      {job.unmatched.length > 0 && <p className="small muted">Also named: {job.unmatched.join(", ")}</p>}
      {job.sources?.length > 1 && <div>
        <h3 className="small">{reportedBy(job.sources)}</h3>
        <ul className="plain small">{job.sources.map((x) => <li key={x.id}>
          <strong>{x.label}</strong>: {x.title} <span className="muted">· {x.source ?? ""}</span></li>)}</ul>
      </div>}
      {!!job.warnings?.length && <ul className="plain small field-warnings" aria-label="Before you start">
        {job.warnings.map((w) => <li key={w}><Badge tone="warn">check</Badge> {w}</li>)}</ul>}
      {job.permit && <PermitPanel permit={job.permit} inHand={inHand} onInHand={setInHand} />}
      {(job.checklist?.length ?? 0) > 0 && <div>
        <h3 className="small">Checks before restart</h3>
        <ol className="small">{job.checklist!.map((c) => <li key={c}>{c}</li>)}</ol>
      </div>}
      <div>
        <h3 className="small">What was found</h3>
        <FindingEvidence finding={job.finding} checks={false} />
      </div>
      {job.history.length > 0 && <div>
        <h3 className="small">Lately at this asset</h3>
        <ul className="plain small">{job.history.map((h) => <li key={h.id}>
          {when(h.first_seen)} · {h.title} <span className="muted">({h.state})</span></li>)}</ul>
      </div>}
      {job.field.length > 0 && <div>
        <h3 className="small">Field log</h3>
        <ul className="plain small">{job.field.map((f, i) => <li key={i}>
          {when(f.at)} · <strong>{SAID[f.status] ?? f.status}</strong>{f.note ? `: ${f.note}` : ""}
          {f.reading && <> · <strong>{readingText(f.reading)}</strong></>} <span className="muted">{f.by}</span>
          {f.photos?.length ? <div className="field-photos">{f.photos.map((p) => <FieldPhoto key={p} exceptionId={job.id} photoId={p} />)}</div> : null}
        </li>)}</ul>
      </div>}
      {job.handover?.mine_to_accept ? null : job.queued ? <div className="row field-actions">
        <p className="small muted">Waiting in the {job.role?.replace(/_/g, " ") ?? ""} queue for whoever is free.</p>
        <Button variant="primary" disabled={!!busy || !signal.live}
          onClick={() => run("Taking it", () => api.dispatch(job.id, person, person, "taken from the queue"), onChange)}>Take it</Button>
        {!signal.live && <span className="small muted">{NEEDS_SIGNAL}</span>}
      </div> : <>
      <label className="small" htmlFor={`note-${job.id}`}>Note for the IOC (a reading, what you found, why it couldn't be done)</label>
      <textarea id={`note-${job.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      {dictate && <Button size="sm" onClick={dictate.toggle} aria-pressed={dictate.listening}>
        {dictate.listening ? "Stop dictating" : "Dictate the note"}</Button>}
      <fieldset className="field-evidence">
        <legend className="small">A reading</legend>
        <div className="row">
          <Input aria-label="What was read" placeholder="what (e.g. bearing temperature)" value={what} onChange={(e) => setWhat(e.target.value)} />
          <Input aria-label="Reading" type="number" inputMode="decimal" step="any" placeholder="value" value={value}
            onChange={(e) => setValue(e.target.value)} />
          <Input aria-label="Unit" placeholder="unit (°C, barg, mm/s)" value={unit} onChange={(e) => setUnit(e.target.value)} />
        </div>
        <label className="small" htmlFor={`photo-${job.id}`}>Photos {photos.length ? `(${photos.length})` : ""}</label>
        <input id={`photo-${job.id}`} type="file" accept="image/*" capture="environment" multiple
          onChange={(e) => { void addPhoto(e.target.files); e.target.value = ""; }} />
        {photos.length > 0 && <div className="field-photos">{photos.map((p, i) => <span key={i} className="field-photo">
          <img src={p} alt={`Photo ${i + 1} to send`} />
          <Button size="sm" aria-label={`Remove photo ${i + 1}`} onClick={() => setPhotos((x) => x.filter((_, j) => j !== i))}>Remove</Button>
        </span>)}</div>}
      </fieldset>
      {problem && <p role="alert" className="error small">{problem}</p>}
      {kept && <p role="status" className="small">No signal: kept on this phone. It sends, with this time, when the signal returns.</p>}
      {blocked && <>
        <label className="small" htmlFor={`why-${job.id}`}>Why start without a valid permit</label>
        <Input id={`why-${job.id}`} value={why} onChange={(e) => setWhy(e.target.value)} />
      </>}
      <div className="row field-actions">
        <Button disabled={!!busy || (blocked && !why.trim())}
          title={blocked && !why.trim() ? "No valid permit: say why it starts anyway" : undefined}
          onClick={() => say("on_site")}>On site</Button>
        <Button variant="primary" disabled={!!busy} onClick={() => say("done")}>Done</Button>
        <Button disabled={!!busy || !note.trim()} title={note.trim() ? undefined : "Say why in the note first"}
          onClick={() => say("could_not_do")}>Could not do</Button>
        <Button disabled={!!busy || !note.trim()} onClick={() => say("note")}>Add note</Button>
        {job.task_id && job.own_step && <ButtonLink href={taskHref(job.task_id)}>Open step: {job.task_name}</ButtonLink>}
        {job.item_id && !job.task_id && <ButtonLink href={href(`/cases/${job.item_id}`)}>Case</ButtonLink>}
      </div>
      {!job.handover && <HandOver job={job} person={person} onChange={onChange} />}</>}
      {busy && <p role="status">{busy}…</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </Card>
  );
}
