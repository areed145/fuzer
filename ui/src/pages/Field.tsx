/** Field jobs on My work (#503): what a person has been sent to, laid out for a phone. Each job is one exception routed
 * to them: where to go, the asset and what happened there lately, what was seen, and the few things to say back (on
 * site, done, could not do, a note). What they report lands on the exception as evidence the IOC sees. My jobs was its
 * own page; it is part of My work now, so an engineer sent on a field check finds it in the same place as everything
 * else waiting on them. */
import { useState } from "react";
import { api, type FieldJob, type PlanItem } from "../api";
import { Badge, Button, ButtonLink, Card } from "../ui";
import { entityHref, href } from "../lib";
import { Due, reportedBy, when } from "../work";
import { taskHref, useAct } from "./workflows/shared";

const SAID: Record<string, string> = { on_site: "On site", done: "Done", could_not_do: "Could not do", note: "Note" };

const day = (at: string) => new Date(at).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });

/** What they are planned onto over the next two weeks, so tomorrow's job is no surprise today. On the day, they
 * say when they've started and when it's done; the plan, and the IOC, see it. */
export function ComingUp({ items, person, onChange }: { items: PlanItem[]; person: string; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const soon = Date.now() + 86_400_000;
  const set = (i: PlanItem, status: string) =>
    run(status === "done" ? "Done" : "Started", () => api.planChange(i.id, person, { status }), onChange);
  return (
    <Card className="field-coming" title="Coming up">
      <ul className="plain">{items.map((i) => <li key={i.id}>
        <strong>{day(i.start_at)}</strong>{day(i.end_at) !== day(i.start_at) ? `–${day(i.end_at)}` : ""} · {i.title}{" "}
        <span className="muted small">{i.kind.replace(/_/g, " ")}{i.needs_shutdown ? " · in the shutdown" : ""}
          {i.crew?.startsWith("role:") ? ` · for ${i.crew.slice(5)}` : ""}</span>
        {i.status === "in_progress" && <> <Badge tone="ok">started</Badge></>}
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

export function FieldJobCard({ job, person, onChange }: { job: FieldJob; person: string; onChange: () => void }) {
  const { busy, error, run } = useAct();
  const [note, setNote] = useState("");
  const say = (status: string) => {
    const sent = note.trim();
    // Clear only the note that went: words typed while the request was in flight stay.
    return run(SAID[status], () => api.fieldReport(job.id, person, status, sent || undefined),
      () => { setNote((n) => (n.trim() === sent ? "" : n)); onChange(); });
  };
  const last = job.field[job.field.length - 1];
  return (
    <Card className="field-job" title={<>{job.title} <Badge>{job.label}</Badge></>}>
      <p className="row small">
        <Due at={job.due} />
        {last && <Badge tone="ok">{SAID[last.status] ?? last.status} · {when(last.at)}</Badge>}
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
      {(job.checklist?.length ?? 0) > 0 && <div>
        <h3 className="small">Checks before restart</h3>
        <ol className="small">{job.checklist!.map((c) => <li key={c}>{c}</li>)}</ol>
      </div>}
      {job.seen.length > 0 && <div>
        <h3 className="small">What was seen</h3>
        <dl className="kv small">{job.seen.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
      </div>}
      {job.history.length > 0 && <div>
        <h3 className="small">Lately at this asset</h3>
        <ul className="plain small">{job.history.map((h) => <li key={h.id}>
          {when(h.first_seen)} · {h.title} <span className="muted">({h.state})</span></li>)}</ul>
      </div>}
      {job.field.length > 0 && <div>
        <h3 className="small">Field log</h3>
        <ul className="plain small">{job.field.map((f, i) => <li key={i}>
          {when(f.at)} · <strong>{SAID[f.status] ?? f.status}</strong>{f.note ? `: ${f.note}` : ""} <span className="muted">{f.by}</span></li>)}</ul>
      </div>}
      {job.queued ? <div className="row field-actions">
        <p className="small muted">Waiting in the {job.role?.replace(/_/g, " ") ?? ""} queue for whoever is free.</p>
        <Button variant="primary" disabled={!!busy}
          onClick={() => run("Taking it", () => api.dispatch(job.id, person, person, "taken from the queue"), onChange)}>Take it</Button>
      </div> : <>
      <label className="small" htmlFor={`note-${job.id}`}>Note for the IOC (a reading, what you found, why it couldn't be done)</label>
      <textarea id={`note-${job.id}`} rows={2} value={note} onChange={(e) => setNote(e.target.value)} />
      <div className="row field-actions">
        <Button disabled={!!busy} onClick={() => say("on_site")}>On site</Button>
        <Button variant="primary" disabled={!!busy} onClick={() => say("done")}>Done</Button>
        <Button disabled={!!busy || !note.trim()} title={note.trim() ? undefined : "Say why in the note first"}
          onClick={() => say("could_not_do")}>Could not do</Button>
        <Button disabled={!!busy || !note.trim()} onClick={() => say("note")}>Add note</Button>
        {job.task_id && job.own_step && <ButtonLink href={taskHref(job.task_id)}>Open step: {job.task_name}</ButtonLink>}
        {job.item_id && !job.task_id && <ButtonLink href={href(`/cases/${job.item_id}`)}>Case</ButtonLink>}
      </div></>}
      {busy && <p role="status">{busy}…</p>}
      {error && <p role="alert" className="error">{error}</p>}
    </Card>
  );
}
