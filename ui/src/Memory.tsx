import { useState } from "react";
import { whenDay } from "./format";
import { api, type AssistantFact } from "./api";
import { entityHref, useAsync } from "./lib";
import { Button, Input, Card as UICard, Textarea, ErrorText, Line, Text, List } from "./ui";
import { loadPerson } from "./work";

/** Who is editing: the name the Review page remembers. */
export const person = () => loadPerson() || "anonymous";

const period = (f: AssistantFact) =>
  f.valid_from && f.valid_to ? `${f.valid_from} to ${f.valid_to}`
    : f.valid_from ? `since ${f.valid_from}` : f.valid_to ? `until ${f.valid_to}` : "";

/** What the assistant learned in conversations about this entity and its units; engineers correct, end or delete. */
export function MemoryCard({ id }: { id: string }) {
  const r = useAsync(() => api.facts(id), [id]);
  const [error, setError] = useState<string>();
  if (!r.data?.length) return null;
  const act = (p: Promise<unknown>) => p.then(() => { setError(undefined); r.reload(); }, (e) => setError(String(e)));
  return (
    <UICard as="section" title="What the assistant remembers">
      <ErrorText>{error}</ErrorText>
      <List plain>
        {r.data.map((f) => <FactRow key={f.id} f={f} act={act} />)}
      </List>
    </UICard>
  );
}

function FactRow({ f, act }: { f: AssistantFact; act: (p: Promise<unknown>) => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(f.text);
  const [from, setFrom] = useState(f.valid_from ?? "");
  const [to, setTo] = useState(f.valid_to ?? "");
  const ended = f.status === "ended";
  if (editing) {
    return (
      <li className="fact">
        <Textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label="Fact" />
        <Line small>
          <label>From <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>To <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <Button variant="primary" onClick={() => {
            const clear = [...(!from && f.valid_from ? ["valid_from" as const] : []),
                           ...(!to && f.valid_to ? ["valid_to" as const] : [])];
            act(api.editFact(f.id, person(), { text, valid_from: from || undefined, valid_to: to || undefined, clear }));
            setEditing(false);
          }}>Save</Button>
          <Button variant="link" onClick={() => setEditing(false)}>Cancel</Button>
        </Line>
      </li>
    );
  }
  return (
    <li className={ended ? "fact muted" : "fact"}>
      <Text small mono>fact:{f.id}</Text> {ended ? <s>{f.text}</s> : f.text}
      <Text as="div" small muted>
        {period(f) && <span>{period(f)} · </span>}
        {ended && <span>ended{f.ended_at ? ` ${whenDay(f.ended_at)}` : ""} by {f.ended_by}
          {f.invalidated_by ? `, replaced by fact:${f.invalidated_by}` : ""} · </span>}
        learned {whenDay(f.learned_at)}
        {f.episode_question ? <span title={f.episode_question}> from “{f.episode_question.slice(0, 60)}”</span> : ""}
        {f.edited_by && ` · edited by ${f.edited_by}`}
        {f.entities.length > 1 && <span> · about {f.entities.map((e, i) => (
          <a key={e.id} href={entityHref(e.id)}>{i ? ", " : ""}{e.label ?? e.id}</a>))}</span>}
        {" "}
        <Button variant="link" onClick={() => setEditing(true)}>correct</Button>
        {!ended && <Button variant="link" title="No longer true: kept, with today as its end date"
                           onClick={() => act(api.endFact(f.id, person()))}>end</Button>}
        <Button variant="link" title="Wrong from the start" onClick={() => act(api.deleteFact(f.id))}>delete</Button>
      </Text>
    </li>
  );
}
