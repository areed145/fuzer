/** A case's checklist: what the case asks for, ticked off as it is done, on the card where the case is taken and
 * worked. Each item names the entities it is about; one not needed is marked so, with why. While the case is open,
 * whoever may work it adds, rewords or removes items. An item for another queue (a permit, a spares decision) is sent
 * as its own case, part of this one; the item is done when that case is. Closed, the list is the record. */
import { useState } from "react";
import { api, type CheckItem, type WorkItem } from "../../api";
import { AssignPicker, caseHref, progressText } from "./shared";
import { useAction } from "../../lib";
import { when } from "../../format";
import { Button, Checkbox, ErrorText, Input, Line, Text } from "../../ui";
import { promptDialog } from "../../ui";
import { Actor, EntityLinks, holder } from "../../work";

export function CaseChecklist({ it, person, readOnly, reload }: {
  it: WorkItem; person: string; readOnly: boolean; reload: () => void;
}) {
  const items = it.checklist ?? [];
  const [adding, setAdding] = useState("");
  // adding, rewording and removing are for shaping the list, not for working it: one tap away
  const [shaping, setShaping] = useState(false);
  const [sending, setSending] = useState<number | null>(null);
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const a = useAction();
  const closed = !!it.closed_at;
  const editable = !readOnly && !closed && !!person;
  if (!items.length && !editable) return null;
  const change = (c: CheckItem, body: Parameters<typeof api.changeChecklist>[3]) =>
    a.run(`c${c.id}`, () => api.changeChecklist(it.id, c.id, person, body), reload);
  const skip = async (c: CheckItem) => {
    const why = await promptDialog("Why is it not needed?", { confirm: "Not needed" });
    if (why?.trim()) change(c, { state: "skipped", note: why.trim() });
  };
  const reword = async (c: CheckItem) => {
    const text = await promptDialog("New wording", { confirm: "Save", value: c.text });
    if (text?.trim() && text.trim() !== c.text) change(c, { text: text.trim() });
  };
  const remove = (c: CheckItem) => a.run(`r${c.id}`, () => api.removeChecklist(it.id, c.id, person), reload);
  const add = () => a.run("add", () => api.addChecklist(it.id, person, [{ text: adding.trim() }]), () => { setAdding(""); reload(); });
  const send = (c: CheckItem) => a.run(`s${c.id}`, () => api.sendChecklist(it.id, c.id, person, to, note.trim() || undefined),
    () => { setSending(null); setTo(""); setNote(""); reload(); });
  // a sent item waits on its case; one whose case was cancelled can be sent again
  const waiting = (c: CheckItem) => !!c.child && !c.child.closed_at;
  const done = items.filter((c) => c.state !== "open").length;
  return (
    <div className="case-checklist" aria-label="Checklist">
      {items.length > 0 && <Text as="p" small muted>Checklist · {progressText({ done, total: items.length })}</Text>}
      <ol className="plain case-checklist-items">
        {items.map((c) => (
          <li key={c.id} className={`case-check ${c.state}`}>
            <Checkbox checked={c.state === "done"} disabled={!editable || c.state === "skipped" || !!a.busy}
              onChange={(on) => change(c, { state: on ? "done" : "open" })}>
              <span className="case-check-text">{c.text}</span>
            </Checkbox>
            <Text as="div" small muted>{[
              c.subjects.length > 0 && <EntityLinks key="s" ids={c.subjects} />,
              c.child && <span key="c">sent as <a href={caseHref(c.child.id)}>case {c.child.id}</a>{c.child.closed_at
                ? `, ${c.child.state} ${when(c.child.closed_at)}` : <>, with {holder(c.child.assignee, c.child.role)}</>}</span>,
              c.state === "done" && c.done_by && <span key="d">done by <Actor who={c.done_by} /> {c.done_at && when(c.done_at)}</span>,
              c.state === "skipped" && <span key="k">not needed{c.note ? `: ${c.note}` : ""}{c.done_by && <>, said <Actor who={c.done_by} /></>}</span>,
              editable && c.state === "open" && !waiting(c) && <Button key="n" variant="link" disabled={!!a.busy} onClick={() => skip(c)}>Not needed</Button>,
              editable && c.state === "open" && !waiting(c) && sending !== c.id && <Button key="a" variant="link" disabled={!!a.busy}
                onClick={() => { setSending(c.id); setTo(""); setNote(""); }}>Send as a case</Button>,
              editable && c.state === "skipped" && <Button key="o" variant="link" disabled={!!a.busy} onClick={() => change(c, { state: "open" })}>Needed after all</Button>,
              editable && shaping && <Button key="w" variant="link" disabled={!!a.busy} onClick={() => reword(c)}>Reword</Button>,
              editable && shaping && <Button key="r" variant="link" disabled={!!a.busy} onClick={() => remove(c)}>Remove</Button>,
            ].filter(Boolean).flatMap((x, i) => (i ? [" · ", x] : [x]))}</Text>
            {sending === c.id && <Line wrap>
              <AssignPicker value={to} onChange={setTo} label="Send to" placeholder="send to…" />
              <Input aria-label="What they should know" width="md" placeholder="What they should know" value={note}
                onChange={(e) => setNote(e.target.value)} />
              <Button variant="secondary" disabled={!to || !!a.busy} onClick={() => send(c)}>Send</Button>
              <Button variant="link" onClick={() => setSending(null)}>Cancel</Button>
            </Line>}
          </li>
        ))}
      </ol>
      {/* most cases have no checklist: one link starts one, rather than an empty field on every case */}
      {editable && !shaping && <Button variant="link" onClick={() => setShaping(true)}>{items.length ? "Change the list" : "Add a checklist"}</Button>}
      {editable && shaping && <Line wrap>
        <Input aria-label="Add a checklist item" width="lg" maxLength={300} placeholder="Add to the checklist"
          value={adding} onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && adding.trim()) add(); }} />
        <Button variant="secondary" disabled={!adding.trim() || !!a.busy} onClick={add}>Add</Button>
        <Button variant="link" onClick={() => setShaping(false)}>{items.length ? "Done changing" : "Cancel"}</Button>
      </Line>}
      <ErrorText>{a.error}</ErrorText>
    </div>
  );
}
