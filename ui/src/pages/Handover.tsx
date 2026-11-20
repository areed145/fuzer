/** Shift handover (#467): what the incoming ops lead needs to know, drafted from live work at each shift change,
 * every line citing its case or exception. The outgoing lead adds notes; the incoming lead acknowledges it, which
 * is kept with who and when. Past handovers stay readable. */
import { useState } from "react";
import { when } from "../format";
import { api, type Handover } from "../api";
import { Badge, Button, Card, PageHeader, Table, Empty, Textarea, ErrorText } from "../ui";
import { Status, entityHref, href, useAsync, useAction } from "../lib";
import { usePerson } from "../work";
import { caseHref } from "./workflows/shared";
import { usePermission } from "../Auth";
import { person as personName } from "../wording";

const citeHref = (c: string) => {
  const [kind, id] = c.split(":");
  return kind === "case" ? caseHref(Number(id)) : kind === "exception" ? href("/exceptions")
    : kind === "entity" ? entityHref(id) : undefined;
};

/** A citation as words (#1072): "open the case", "the permit"; never the raw `case:11` or a row number. */
const citeLabel = (c: string) => {
  const [kind, id] = c.split(":");
  return kind === "case" ? "open the case" : kind === "exception" ? "in All work"
    : kind === "entity" ? `the ${id.split("-")[1]?.replace(/_/g, " ") || "record"}` : kind.replace(/_/g, " ");
};

/** Lines shown per section until the reader asks for the rest. */
const FIRST = 5;
const FIRST_READ = ["Escalated or overdue", "Back from the field", "In the field", "Active bypasses", "Open permits",
  "Records to check", "New exceptions", "Dispatched", "Rounds", "Open work"];
const rank = (name: string) => { const i = FIRST_READ.indexOf(name); return i < 0 ? FIRST_READ.length - 1 : i; };

export function HandoverPage({ query }: { query: URLSearchParams }) {
  const picked = query.get("id");
  const list = useAsync(() => api.handovers(), []);
  const [person] = usePerson();
  const operate = usePermission("operate");
  const a = useAction();
  const id = picked ? Number(picked) : list.data?.[0]?.id;
  const h = useAsync(() => (id ? api.handover(id) : Promise.resolve(null)), [id]);
  const reload = () => { list.reload(); h.reload(); };
  return (
    <section>
      <PageHeader help="workflow">
        Shift handover</PageHeader>
      {operate && <p className="small"><Button variant="secondary" disabled={!!a.busy} onClick={() =>
        a.run("draft", () => api.draftHandover(person), reload)} title="Writes the handover for the shift in progress, or brings it up to date">Draft the handover</Button>
        <ErrorText inline>{a.error}</ErrorText></p>}
      {/* The handover shown leads and the past ones follow once it is in: drawn sooner, its lines filling in pushed the
          past handovers out of view (#1069). */}
      <Status loading={(list.loading && !list.data) || (!!id && h.data === undefined && !h.error)} error={list.error ?? h.error} />
      {list.data?.length === 0 && <Empty>No handover yet. One is drafted at each shift change.</Empty>}
      {h.data && <HandoverView h={h.data} person={person} canAct={operate} reload={reload} />}
      {(list.data?.length ?? 0) > 0 && (!id || h.data !== undefined || !!h.error) && (
        <Card title="Past handovers">
          <Table density="compact">
            <thead><tr><th>Shift</th><th>Lines</th><th>Acknowledged</th></tr></thead>
            <tbody>{list.data!.map((r) => (
              <tr key={r.id}><td><a href={href("/handover", { id: String(r.id) })}>{when(r.shift_start)} to {when(r.shift_end)}</a></td>
                <td>{r.lines ?? 0}</td>
                <td>{r.acknowledged_by ? `${personName(r.acknowledged_by)}, ${when(r.acknowledged_at!)}` : <span className="muted">{r.state.replace("_", " ")}</span>}</td></tr>
            ))}</tbody>
          </Table>
        </Card>
      )}
    </section>
  );
}

function HandoverView({ h, person, canAct, reload }: { h: Handover; person: string; canAct: boolean; reload: () => void }) {
  const [notes, setNotes] = useState<string | null>(null);
  const a = useAction();
  const [shown, setShown] = useState<Set<string>>(new Set());
  const acked = h.state === "acknowledged";
  // The outgoing lead's draft of the shift still running: it goes to the incoming lead at the shift change, so it
  // can't be acknowledged yet.
  const running = new Date(h.shift_end).getTime() > Date.now();
  // What needs the incoming lead first leads; the standing backlog (Open work) comes last, and sections with nothing
  // fold into one line, so a handover reads in a minute on a phone instead of listing every open case.
  const sections = Object.entries(h.facts.sections)
    .sort(([a], [b]) => rank(a) - rank(b));
  const busy = sections.filter(([name, lines]) => lines.length + (h.facts.more?.[name] ?? 0) > 0);
  const quiet = sections.filter(([name, lines]) => lines.length + (h.facts.more?.[name] ?? 0) === 0).map(([name]) => name);
  return (
    <Card title={`Shift ${when(h.shift_start)} to ${when(h.shift_end)}`}>
      <p className="small">
        <Badge tone={acked ? "ok" : "warn"}>{acked ? `acknowledged by ${personName(h.acknowledged_by)} ${when(h.acknowledged_at!)}`
          : running ? `shift in progress: goes to the incoming lead ${when(h.shift_end)}` : h.state.replace("_", " ")}</Badge>
        {h.item_id && <> · <a href={caseHref(h.item_id)}>its case</a></>}
        <span className="muted"> · drafted {when(h.drafted_at)}</span>
      </p>
      {busy.length === 0 && <Empty className="small">Nothing to hand over.</Empty>}
      {busy.map(([name, lines]) => {
        const more = h.facts.more?.[name] ?? 0;
        const total = lines.length + more;
        const open = shown.has(name);
        const visible = open ? lines : lines.slice(0, FIRST);
        return <div key={name}>
          <h3>{name} <span className="muted small">{total}</span></h3>
          <ul className="small">
            {visible.map((l, i) => (
              <li key={i}>{l.text}{" "}
                {l.cites.filter((c, _, all) => !(c.startsWith("exception:") && all.some((x) => x.startsWith("case:")))).map((c, n) => <span key={c}>{n > 0 && " · "}<a className="small" href={citeHref(c)}>{citeLabel(c)}</a></span>)}</li>
            ))}
            {open && more > 0 && <li className="muted">and {more} more</li>}
          </ul>
          {lines.length > FIRST && <Button size="sm" variant="ghost" aria-expanded={open}
            onClick={() => setShown((s) => { const n = new Set(s); if (open) n.delete(name); else n.add(name); return n; })}>
            {open ? "Show fewer" : `Show all ${total}`}</Button>}
        </div>;
      })}
      {quiet.length > 0 && <p className="small muted">Nothing in {quiet.join(", ").toLowerCase()}.</p>}
      <h3>Outgoing notes</h3>
      {h.notes && notes === null && <p>{h.notes} <span className="muted small">({personName(h.notes_by)}, {when(h.notes_at!)})</span></p>}
      {!acked && canAct && (
        <>
          <Textarea rows={3} className="handover-notes" aria-label="Outgoing notes" placeholder="What the lines don't say: plans, risks, asks"
            value={notes ?? h.notes ?? ""} onChange={(e) => setNotes(e.target.value)} />
          <div className="row">
            <Button variant="secondary" disabled={!!a.busy || !(notes ?? "").trim()} onClick={() =>
              a.run("notes", () => api.handoverNotes(h.id, person, notes!), () => { setNotes(null); reload(); })}>Save notes</Button>
            {!running && <Button variant="primary" disabled={!!a.busy} onClick={() =>
              a.run("ack", () => api.acknowledgeHandover(h.id, person), reload)}>I have read it: acknowledge</Button>}
          </div>
        </>
      )}
      {h.acknowledged_note && <p className="small">Incoming: {h.acknowledged_note}</p>}
      <ErrorText>{a.error}</ErrorText>
    </Card>
  );
}
