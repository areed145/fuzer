/** Shift handover (#467): what the incoming ops lead needs to know, drafted from live work at each shift change,
 * every line citing its case or exception. The outgoing lead adds notes; the incoming lead acknowledges it, which
 * is kept with who and when. Past handovers stay readable. */
import { useState } from "react";
import { api, type Handover } from "../api";
import { Badge, Button, Card, PageHeader, Table, Empty } from "../ui";
import { Status, href, useAsync } from "../lib";
import { usePerson, when } from "../work";
import { caseHref, useAct } from "./workflows/shared";
import { usePermission } from "../Auth";

const citeHref = (c: string) => {
  const [kind, id] = c.split(":");
  return kind === "case" ? caseHref(Number(id)) : kind === "exception" ? href("/exceptions") : undefined;
};

export function HandoverPage({ query }: { query: URLSearchParams }) {
  const picked = query.get("id");
  const list = useAsync(() => api.handovers(), []);
  const [person] = usePerson();
  const operate = usePermission("operate");
  const a = useAct();
  const id = picked ? Number(picked) : list.data?.[0]?.id;
  const h = useAsync(() => (id ? api.handover(id) : Promise.resolve(null)), [id]);
  const reload = () => { list.reload(); h.reload(); };
  return (
    <section>
      <PageHeader subtitle="What changed since the last shift." help="workflow">
        Shift handover</PageHeader>
      {operate && <p className="small"><Button variant="secondary" disabled={!!a.busy} onClick={() =>
        a.run("draft", () => api.draftHandover(person), reload)}>Draft (or refresh) the handover for the last shift change</Button>
        {a.error && <span className="error"> {a.error}</span>}</p>}
      <Status loading={list.loading && !list.data} error={list.error} />
      {list.data?.length === 0 && <Empty>No handover yet. One is drafted at each shift change.</Empty>}
      {h.data && <HandoverView h={h.data} person={person} canAct={operate} reload={reload} />}
      {(list.data?.length ?? 0) > 0 && (
        <Card title="Past handovers">
          <Table density="compact">
            <thead><tr><th>Shift</th><th>Lines</th><th>Acknowledged</th></tr></thead>
            <tbody>{list.data!.map((r) => (
              <tr key={r.id}><td><a href={href("/handover", { id: String(r.id) })}>{when(r.shift_start)} to {when(r.shift_end)}</a></td>
                <td>{r.lines ?? 0}</td>
                <td>{r.acknowledged_by ? `${r.acknowledged_by}, ${when(r.acknowledged_at!)}` : <span className="muted">{r.state.replace("_", " ")}</span>}</td></tr>
            ))}</tbody>
          </Table>
        </Card>
      )}
    </section>
  );
}

function HandoverView({ h, person, canAct, reload }: { h: Handover; person: string; canAct: boolean; reload: () => void }) {
  const [notes, setNotes] = useState<string | null>(null);
  const a = useAct();
  const acked = h.state === "acknowledged";
  return (
    <Card title={`Shift ${when(h.shift_start)} to ${when(h.shift_end)}`}>
      <p className="small">
        <Badge tone={acked ? "ok" : "warn"}>{acked ? `acknowledged by ${h.acknowledged_by} ${when(h.acknowledged_at!)}` : h.state.replace("_", " ")}</Badge>
        {h.item_id && <> · <a href={caseHref(h.item_id)}>its case</a></>}
        <span className="muted"> · drafted {when(h.drafted_at)}</span>
      </p>
      {Object.entries(h.facts.sections).map(([name, lines]) => (
        <div key={name}>
          <h3>{name} <span className="muted small">{lines.length + (h.facts.more?.[name] ?? 0)}</span></h3>
          {lines.length === 0 ? <Empty className="small">Nothing.</Empty> : (
            <ul className="small">
              {lines.map((l, i) => (
                <li key={i}>{l.text}{" "}
                  {l.cites.map((c) => <a key={c} className="muted" href={citeHref(c)}>[{c}]</a>)}</li>
              ))}
              {(h.facts.more?.[name] ?? 0) > 0 && <li className="muted">and {h.facts.more![name]} more</li>}
            </ul>
          )}
        </div>
      ))}
      <h3>Outgoing notes</h3>
      {h.notes && notes === null && <p>{h.notes} <span className="muted small">({h.notes_by}, {when(h.notes_at!)})</span></p>}
      {!acked && canAct && (
        <>
          <textarea rows={3} className="handover-notes" aria-label="Outgoing notes" placeholder="What the lines don't say: plans, risks, asks"
            value={notes ?? h.notes ?? ""} onChange={(e) => setNotes(e.target.value)} />
          <div className="row">
            <Button variant="secondary" disabled={!!a.busy || !(notes ?? "").trim()} onClick={() =>
              a.run("notes", () => api.handoverNotes(h.id, person, notes!), () => { setNotes(null); reload(); })}>Save notes</Button>
            <Button variant="primary" disabled={!!a.busy} onClick={() =>
              a.run("ack", () => api.acknowledgeHandover(h.id, person), reload)}>I have read it: acknowledge</Button>
          </div>
        </>
      )}
      {h.acknowledged_note && <p className="small">Incoming: {h.acknowledged_note}</p>}
      {a.error && <p className="error small">{a.error}</p>}
    </Card>
  );
}
