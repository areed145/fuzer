/** Inbox: everything waiting for you, most urgent first: escalated to you, assigned to you (and to anyone you're
 * covering for), unclaimed in your roles' queues; plus the data review queue's count. */
import { useState } from "react";
import { api, type WorkRow } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { Due, holder, usePerson } from "../../work";
import { StepBadges, byUrgency, caseHref, taskHref, useAct } from "./shared";

export function InboxPage() {
  const [person] = usePerson();
  const inbox = useAsync(() => (person ? api.inbox(person) : Promise.resolve(null)), [person]);
  const stats = useAsync(() => api.stats(), []);
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  if (!person) return <section><h1>Inbox</h1><p>Say who you are in the sidebar (bottom left) to see your work.</p></section>;
  if (!inbox.data) return <Status loading={inbox.loading} error={inbox.error} />;
  const d = inbox.data;
  const keep = (r: WorkRow) => (!type || r.type === type) &&
    (!q || `${r.title} ${r.name ?? ""} ${r.type}`.toLowerCase().includes(q.toLowerCase()));
  const all = [...d.mine, ...d.queue];
  const escalated = all.filter((r) => r.escalated_at && keep(r)).sort(byUrgency);
  const mine = d.mine.filter((r) => !r.escalated_at && keep(r)).sort(byUrgency);
  const queue = d.queue.filter((r) => !r.escalated_at && keep(r)).sort(byUrgency);
  const overdue = all.filter((r) => r.overdue).length;
  const reviews = stats.data ? Object.values(stats.data.open_reviews).reduce((a, b) => a + b, 0) : 0;
  const types = [...new Set(all.map((r) => r.type))].sort();
  const pickedRows = all.filter((r) => r.task_id && picked.has(r.task_id));
  const toggle = (id: number) => setPicked((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  return (
    <section>
      <h1>Inbox</h1>
      <p className="row small">
        <span className="badge">{d.mine.length} assigned to you</span>
        <span className="badge">{d.queue.length} unclaimed in your roles ({d.roles.join(", ") || "none"})</span>
        {overdue > 0 && <span className="badge late">{overdue} overdue</span>}
        {escalated.length > 0 && <span className="badge late">{escalated.length} escalated</span>}
        {reviews > 0 && <a className="badge" href={href("/review")}>{reviews} data items to review</a>}
        {d.covering?.length ? <span className="badge">covering for {d.covering.join(", ")}</span> : null}
      </p>
      <div className="row">
        <input placeholder="filter by case or step" value={q} onChange={(e) => setQ(e.target.value)} />
        <select value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">every kind of case</option>
          {types.map((t) => <option key={t} value={t}>{t.replace(/_/g, " ")}</option>)}
        </select>
      </div>
      {pickedRows.length > 0 && <Bulk rows={pickedRows} person={person} onDone={() => setPicked(new Set())} />}
      {!all.length && <p className="muted">Nothing waiting for you. <a href={href("/cases")}>All cases</a></p>}
      <Group title="Escalated to you" rows={escalated} person={person} picked={picked} toggle={toggle}
        hint="past their SLA and moved to you or your role" />
      <Group title="Assigned to you" rows={mine} person={person} picked={picked} toggle={toggle} />
      <Group title="Unclaimed in your roles" rows={queue} person={person} picked={picked} toggle={toggle}
        hint="claim one to make it yours" />
    </section>
  );
}

function Group({ title, rows, person, picked, toggle, hint }: {
  title: string; rows: WorkRow[]; person: string; picked: Set<number>; toggle: (id: number) => void; hint?: string;
}) {
  if (!rows.length) return null;
  return (
    <div className="card">
      <h2>{title} <span className="muted small">{rows.length}{hint ? ` · ${hint}` : ""}</span></h2>
      <table className="results inbox">
        <tbody>{rows.map((r) => <Row key={`${r.task_id ?? "i"}-${r.item_id}`} r={r} person={person} picked={picked} toggle={toggle} />)}</tbody>
      </table>
    </div>
  );
}

function Row({ r, person, picked, toggle }: { r: WorkRow; person: string; picked: Set<number>; toggle: (id: number) => void }) {
  const a = useAct();
  const open = r.task_id ? taskHref(r.task_id) : caseHref(r.item_id);
  return (
    <tr>
      <td style={{ width: "1.5em" }}>
        {r.task_id && r.bulk.length > 0 && <input type="checkbox" checked={picked.has(r.task_id)} onChange={() => toggle(r.task_id!)} />}
      </td>
      <td>
        <a href={open} className="work-title">{r.title}</a>
        <div className="small muted">
          <strong>{r.name ?? "open"}</strong> · <span className="badge">{r.type.replace(/_/g, " ")}</span> · with {holder(r.assignee, r.role, r.kind)}
          {r.priority != null && <> · priority {r.priority < 2 ? r.priority.toFixed(2) : Math.round(r.priority).toLocaleString()}</>}
        </div>
        <div className="row small"><StepBadges row={r} /></div>
      </td>
      <td className="nowrap"><Due at={r.due_at} /></td>
      <td className="nowrap">
        {r.task_id && !r.assignee && (
          <button className="secondary" disabled={!!a.busy} onClick={() => a.run("claim", () => api.claim(r.task_id!, person))}>Claim</button>
        )}{" "}
        <a className="button" href={open}>Open</a>
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
    <div className="card bulk-bar actions">
      <strong>{rows.length} selected</strong>
      <input placeholder="reason (kept on each case)" value={reason} onChange={(e) => setReason(e.target.value)} />
      {shared.length ? shared.map((l) => (
        <button key={l} className="primary" disabled={!!a.busy} onClick={() => a.run(l, () => api.bulkComplete(rows.map((r) => r.task_id!), person, l, reason || undefined),
          (out) => { setMsg(`${l}: ${out.done.length} done${out.refused.length ? `, ${out.refused.length} refused` : ""}`); onDone(); })}>{l}</button>
      )) : <span className="muted small">These steps share no bulk action.</span>}
      {msg && <span className="small">{msg}</span>}
      {a.error && <span className="error small">{a.error}</span>}
    </div>
  );
}
