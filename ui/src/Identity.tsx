/** Who you are, for the whole app (until sign-in arrives): set once in the sidebar, used by every page that acts. */
import { useState } from "react";
import { api } from "./api";
import { href, useAsync } from "./lib";
import { usePerson } from "./work";
import { Button, Input } from "./ui";

export function Identity() {
  const [person, setPerson] = usePerson();
  const [editing, setEditing] = useState(!person);
  const [draft, setDraft] = useState(person);
  const people = useAsync(() => api.workPeople(), []);
  const me = people.data?.find((p) => p.name === person);
  const covering = (people.data ?? []).filter((p) => p.delegate === person && p.away_until && new Date(p.away_until) > new Date());
  if (editing) {
    return (
      <form className="identity" onSubmit={(e) => { e.preventDefault(); setPerson(draft.trim()); setEditing(false); }}>
        <label className="small muted" htmlFor="me">You are</label>
        <Input id="me" list="people" value={draft} placeholder="your name" onChange={(e) => setDraft(e.target.value)} autoFocus={!person} />
        <datalist id="people">{(people.data ?? []).map((p) => <option key={p.name} value={p.name} />)}</datalist>
      </form>
    );
  }
  return (
    <div className="identity small">
      <span className="muted">You are </span>
      <Button variant="link" title="change" onClick={() => { setDraft(person); setEditing(true); }}><strong>{person}</strong></Button>
      <div className="muted">
        {me ? me.roles.join(", ") || "no roles" : <a href={href("/teams")}>not on the roster: add yourself</a>}
        {me?.away_until && new Date(me.away_until) > new Date() && " · away"}
        {covering.length > 0 && ` · covering for ${covering.map((p) => p.name).join(", ")}`}
      </div>
    </div>
  );
}
