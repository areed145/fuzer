import { useState } from "react";
import { api, type AssistantFact } from "./api";
import { entityHref, useAsync } from "./lib";
import { Button, Input , Card as UICard} from "./ui";

/** Who is editing: the name the Review page remembers. */
export const person = () => {
  try {
    return localStorage.getItem("fuzer.reviewer") || "anonymous";
  } catch {
    return "anonymous";
  }
};

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
    <UICard  as="section">
      <h2>
        What the assistant remembers{" "}
        <span className="muted small">learned from answers rated useful, not source records</span>
      </h2>
      {error && <div className="error small">{error}</div>}
      <ul className="plain">
        {r.data.map((f) => <FactRow key={f.id} f={f} act={act} />)}
      </ul>
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
        <textarea rows={2} value={text} onChange={(e) => setText(e.target.value)} aria-label="Fact" />
        <div className="row small">
          <label>From <Input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
          <label>To <Input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
          <Button variant="primary" onClick={() => {
            const clear = [...(!from && f.valid_from ? ["valid_from" as const] : []),
                           ...(!to && f.valid_to ? ["valid_to" as const] : [])];
            act(api.editFact(f.id, person(), { text, valid_from: from || undefined, valid_to: to || undefined, clear }));
            setEditing(false);
          }}>Save</Button>
          <Button variant="link" onClick={() => setEditing(false)}>Cancel</Button>
        </div>
      </li>
    );
  }
  return (
    <li className={ended ? "fact muted" : "fact"}>
      <span className="mono small">fact:{f.id}</span> {ended ? <s>{f.text}</s> : f.text}
      <div className="small muted">
        {period(f) && <span>{period(f)} · </span>}
        {ended && <span>ended{f.ended_at ? ` ${f.ended_at.slice(0, 10)}` : ""} by {f.ended_by}
          {f.invalidated_by ? `, replaced by fact:${f.invalidated_by}` : ""} · </span>}
        learned {f.learned_at.slice(0, 10)}
        {f.episode_question ? <span title={f.episode_question}> from “{f.episode_question.slice(0, 60)}”</span> : ""}
        {f.edited_by && ` · edited by ${f.edited_by}`}
        {f.entities.length > 1 && <span> · about {f.entities.map((e, i) => (
          <a key={e.id} href={entityHref(e.id)}>{i ? ", " : ""}{e.label ?? e.id}</a>))}</span>}
        {" "}
        <Button variant="link" onClick={() => setEditing(true)}>correct</Button>
        {!ended && <Button variant="link" title="No longer true: kept, with today as its end date"
                           onClick={() => act(api.endFact(f.id, person()))}>end</Button>}
        <Button variant="link" title="Wrong from the start" onClick={() => act(api.deleteFact(f.id))}>delete</Button>
      </div>
    </li>
  );
}
