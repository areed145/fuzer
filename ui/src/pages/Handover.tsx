/** Shift handover (#467): what changed this shift, drafted from live work at each shift change, every line citing its
 * case or exception. The outgoing lead adds notes; the incoming lead acknowledges it, which is kept with who and when.
 * Notes and Acknowledge lead, so a phone opens on them (#1351). Each section lists its first few lines and links the
 * rest, so a shift reads in under three phone screens; a line's time is said with its date (P5.26i). Past handovers
 * stay readable. */
import { useState } from "react";
import { when } from "../format";
import { api, type Handover } from "../api";
import { Badge, Button, Card, PageHeader, Table, Empty, Textarea, ErrorText, Line, Text, List, Heading } from "../ui";
import { Status, entityHref, href, useAsync, useAction } from "../lib";
import { usePerson } from "../work";
import { caseHref } from "./workflows/shared";
import { workHref } from "../pageTabs";
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

/** Lines shown per section (P5.26i): the rest are a link to the List filtered to them, or a tap away. */
const FIRST = 3;
const FIRST_READ = ["Escalated or overdue", "Back from the field", "In the field", "Active bypasses", "Open permits",
  "Records to check", "New exceptions", "Closed", "Dispatched", "Rounds"];
const slug = (name: string) => name.toLowerCase().replace(/[^a-z]+/g, "-");
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
      <PageHeader>
        Shift handover</PageHeader>
      {/* Drafting writes the running shift's handover: offered until there is one, and never over a past one shown (E12);
          once drafted, its lines are read live (fuzer.ops.handover.get), so there is nothing to bring up to date. */}
      {operate && !picked && list.data && list.data[0]?.status !== "in progress" && <Text as="p" small><Button variant="secondary" disabled={!!a.busy} onClick={() =>
        a.run("draft", () => api.draftHandover(person), reload)} title="Writes the handover for the shift in progress">Draft the handover</Button>
        <ErrorText inline>{a.error}</ErrorText></Text>}
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
                <td>{r.acknowledged_by ? `${personName(r.acknowledged_by)}, ${when(r.acknowledged_at!)}` : <Text muted>{r.status}</Text>}</td></tr>
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
  const acked = h.status === "acknowledged";
  // acknowledged, or replaced by a later shift's handover: read as it was, nothing to add (E12)
  const fixed = acked || !!h.read_only;
  // The outgoing lead's draft of the shift still running: it goes to the incoming lead at the shift change, so it
  // can't be acknowledged yet.
  const running = h.status === "in progress";
  // What needs the incoming lead first leads and sections with nothing fold into one line; the standing backlog is
  // All work's, so a handover reads in a minute on a phone.
  const sections = Object.entries(h.facts.sections)
    .sort(([a], [b]) => rank(a) - rank(b));
  // a section that can't be said (no bypass register loaded) is unknown, never "nothing in" it
  const unknown = h.facts.unknown ?? {};
  const said = sections.filter(([name]) => !(name in unknown));
  const busy = said.filter(([name, lines]) => lines.length + (h.facts.more?.[name] ?? 0) > 0);
  const quiet = said.filter(([name, lines]) => lines.length + (h.facts.more?.[name] ?? 0) === 0).map(([name]) => name);
  return (
    <Card title={`Shift ${when(h.shift_start)} to ${when(h.shift_end)}`}>
      <Text as="p" small>
        <Badge tone={acked ? "ok" : "warn"}>{acked ? `acknowledged by ${personName(h.acknowledged_by)} ${when(h.acknowledged_at!)}`
          : running ? `shift in progress: goes to the incoming lead ${when(h.shift_end)}` : h.status}</Badge>
        {h.item_id && <> · <a href={caseHref(h.item_id)}>its case</a></>}
        {/* a running shift's lines are as of now; a past one says when it was drafted */}
        {!running && <Text muted> · drafted {when(h.drafted_at)}</Text>}
      </Text>
      {/* Notes and Acknowledge lead (#1351): the banner opens the page here, and on a phone they were eight screens down. */}
      {h.notes && (fixed || !canAct) && <p>{h.notes} <Text small muted>({personName(h.notes_by)}, {when(h.notes_at!)})</Text></p>}
      {!fixed && canAct && (
        <>
          <Textarea rows={2} className="handover-notes" aria-label="Outgoing notes" placeholder="Outgoing notes: what the lines don't say"
            value={notes ?? h.notes ?? ""} onChange={(e) => setNotes(e.target.value)} />
          <Line wrap>
            <Button variant="secondary" disabled={!!a.busy || !(notes ?? "").trim()} onClick={() =>
              a.run("notes", () => api.handoverNotes(h.id, person, notes!), () => { setNotes(null); reload(); })}>Save notes</Button>
            {!running && <Button variant="primary" disabled={!!a.busy} onClick={() =>
              a.run("ack", () => api.acknowledgeHandover(h.id, person), reload)}>I have read it: acknowledge</Button>}
            {h.notes && notes === null && <Text small muted>Notes by {personName(h.notes_by)}, {when(h.notes_at!)}</Text>}
          </Line>
        </>
      )}
      {h.acknowledged_note && <Text as="p" small>Incoming: {h.acknowledged_note}</Text>}
      <ErrorText>{a.error}</ErrorText>
      {busy.length === 0 && <Empty className="small">Nothing to hand over.</Empty>}
      {busy.map(([name, lines]) => {
        const more = h.facts.more?.[name] ?? 0;
        const total = lines.length + more;
        const open = shown.has(name);
        const visible = open ? lines : lines.slice(0, FIRST);
        // the rest in the List filtered to them where it can show them, else a tap away here
        const list = h.facts.links?.[name];
        return <div key={name} id={`handover-${slug(name)}`} className="handover-section">
          <Heading level={3} detail={total}>{name}</Heading>
          <List small>
            {visible.map((l, i) => {
              // The line opens its case (else its exception); "open the case" after every line was noise (#11).
              const target = l.cites.find((c) => c.startsWith("case:")) ?? l.cites.find((c) => c.startsWith("exception:"));
              const rest = l.cites.filter((c) => !c.startsWith("case:") && !c.startsWith("exception:"));
              const text = l.at ? l.text.replace("{at}", when(l.at)) : l.text;
              return <li key={i}>{target ? <a className="handover-line" href={citeHref(target)}>{text}</a> : text}
                {rest.map((c) => <span key={c}> · <a className="small" href={citeHref(c)}>{citeLabel(c)}</a></span>)}</li>;
            })}
            {open && more > 0 && <li className="muted">and {more} more</li>}
          </List>
          {total > FIRST && list && <a className="small" href={href(list)}>{total - FIRST} more in All work</a>}
          {lines.length > FIRST && !list && <Button size="sm" variant="ghost" aria-expanded={open}
            onClick={() => setShown((s) => { const n = new Set(s); if (open) n.delete(name); else n.add(name); return n; })}>
            {open ? "Show fewer" : `${total - FIRST} more`}</Button>}
        </div>;
      })}
      {(quiet.length > 0 || Object.keys(unknown).length > 0) && <Text as="p" small muted>
        {[quiet.length > 0 && `Nothing in ${quiet.join(", ").toLowerCase()}.`,
          ...Object.entries(unknown).map(([name, why]) => `${name} unknown: ${why}.`)].filter(Boolean).join(" ")}</Text>}
      <Text as="p" small><a href={workHref("open")}>The rest of the open work is in All work</a></Text>
    </Card>
  );
}
