/** The `note` panel: what people know that the numbers don't, pinned to this report, scope and period, with live
 * values (`{production.oil}`) the server resolves for the note's own period. A note in the definition (`text`) is a
 * fixed line, such as a section's heading. Notes pinned to shorter periods inside this one show with their day. */
import { useState } from "react";
import { api } from "../../api";
import type { LiveValue, NotePanelResult, PersonNote } from "../../api/commentary";
import { usePermission } from "../../Auth";
import { ago, quantity, whenDay } from "../../format";
import { useAction } from "../../lib";
import { Markdown } from "../../Markdown";
import { usePerson } from "../../work";
import { person } from "../../wording";
import { ActionStatus, Button, Line, List, Text, Textarea, confirmDialog } from "../../ui";
import { PanelFrame, usePanel } from "../panels";
import type { PanelProps } from "../spec";

const TOKEN = /\{[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+(?:@[A-Za-z0-9_.:-]+)?\}/g;

/** The body with each live value in place, in bold; one the server could not resolve reads "no value". */
export function withValues(body: string, values: Record<string, LiveValue> = {}): string {
  return body.replace(TOKEN, (t) => {
    const v = values[t];
    if (!v || v.value === null || v.value === undefined) return "**no value**";
    return `**${quantity(v.value, v.unit)}**`;
  });
}

const period = (n: PersonNote) => n.start === n.at ? whenDay(n.at) : `${whenDay(n.start)} to ${whenDay(n.at)}`;

function Note({ n, mine, onSave, onRemove, busy }: { n: PersonNote; mine: boolean; busy: boolean;
  onSave: (body: string) => Promise<unknown>; onRemove: () => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  if (draft !== null) {
    return <li>
      <Textarea aria-label="Note" rows={3} width="full" value={draft} onChange={(e) => setDraft(e.target.value)} />
      <Line wrap>
        <Button size="sm" variant="primary" disabled={busy || !draft.trim()} onClick={() => onSave(draft).then(() => setDraft(null))}>Save</Button>
        <Button size="sm" disabled={busy} onClick={() => setDraft(null)}>Cancel</Button>
      </Line>
    </li>;
  }
  const values = n.values ?? {};
  const missing = Object.values(values).filter((v) => v.error).map((v) => `${v.key}: ${v.error}`);
  return <li>
    <Markdown text={withValues(n.body, values)} />
    <Line wrap small muted>
      <span>{person(n.by)} · {ago(n.written_at)}{n.edited_at ? " · edited" : ""}{n.own ? "" : ` · ${period(n)}`}</span>
      {mine && <Button variant="link" size="sm" disabled={busy} onClick={() => setDraft(n.body)}>Edit</Button>}
      {mine && <Button variant="link" size="sm" disabled={busy} onClick={onRemove}>Remove</Button>}
    </Line>
    {missing.length > 0 && <Text as="div" small muted>{missing.join(" · ")}</Text>}
  </li>;
}

export function NotePanel(props: PanelProps) {
  const { report, panel, scope, window, at } = props;
  const key = report.definition.key;
  const operate = usePermission("operate");
  const [me] = usePerson();
  const where = { scope, window, at };
  const r = usePanel<NotePanelResult>(props);
  const [draft, setDraft] = useState("");
  const { busy, error, run } = useAction();
  const fixed = "text" in panel && panel.text;
  if (fixed) {
    return <section className="report-note">
      {r.data?.text ? <Markdown text={withValues(r.data.text, r.data.values)} /> : <Markdown text={panel.text!.replace(TOKEN, "…")} />}
    </section>;
  }
  const notes = r.data?.notes ?? [];
  // the live-value hint names a measure this report already shows
  const example = report.definition.rows.flatMap((row) => row.panels)
    .flatMap((p) => ("panels" in p ? p.panels : [p]))
    .map((p) => ("measure" in p ? p.measure : "measures" in p ? p.measures?.[0] : undefined)).find(Boolean);
  const add = () => run("add", () => api.addReportNote(key, where, draft), () => { setDraft(""); r.reload(); });
  const remove = async (n: PersonNote) => {
    if (!(await confirmDialog("Remove this note? It stays in the record.", { confirm: "Remove", danger: true }))) return;
    await run("remove", () => api.removeReportNote(key, n.id), r.reload);
  };
  return <PanelFrame title={panel.title ?? "Notes"} detail={report.window.label} result={r.data} loading={r} className="report-note">
    {notes.length > 0 && <List plain>{notes.map((n) => <Note key={n.id} n={n} mine={!!me && n.by === me} busy={!!busy}
      onSave={(body) => run("edit", () => api.editReportNote(key, n.id, body), r.reload)} onRemove={() => remove(n)} />)}</List>}
    {r.data && notes.length === 0 && !operate && <Text small muted>No notes for this period.</Text>}
    {operate && <>
      <Textarea aria-label="Add a note" rows={2} width="full" value={draft} placeholder="What the numbers don't show"
        onChange={(e) => setDraft(e.target.value)} />
      <Line wrap small muted>
        <Button size="sm" variant="primary" disabled={!!busy || !draft.trim()} onClick={add}>Add note</Button>
        {/* how to put a live value in, for the person writing one, not everyone who opens the report */}
        {example && draft.trim() && <span>{`{${example}}`} shows a live value</span>}
      </Line>
    </>}
    <ActionStatus busy={busy} error={error} />
  </PanelFrame>;
}
