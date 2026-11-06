/** Signed-in identity; attribution-only names are limited to explicit trusted local mode. */
import { useState } from "react";
import { api } from "./api";
import { href, useAsync } from "./lib";
import { usePerson } from "./work";
import { Button, Input } from "./ui";
import { SignOut, useAuth, usePermission } from "./Auth";

export function Identity() {
  const session = useAuth();
  const configure = usePermission("configure");
  if (session?.mode === "microsoft") {
    const user = session.user;
    return <div className="identity small">
      <strong>{user?.display_name}</strong>
      <p className="muted">{user?.owner ? "Owner · all human roles" : user?.roles.join(", ") || "No application roles"}</p>
      {configure && <p><a href={href("/users")}>Manage users</a></p>}
      <SignOut />
    </div>;
  }
  return <LocalIdentity />;
}

function LocalIdentity() {
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
        <Input id="me" list="people" value={draft} placeholder="your name" onChange={(e) => setDraft(e.target.value)} autoFocus={!!person} />
        <datalist id="people">{(people.data ?? []).map((p) => <option key={p.name} value={p.name} />)}</datalist>
        <Button type="submit" size="sm" disabled={!draft.trim()}>Use this name</Button>
        {person && <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>}
        <p className="muted small">Used to record decisions, not a sign-in.</p>
      </form>
    );
  }
  return (
    <div className="identity small">
      <span className="muted">You are </span>
      <Button variant="link" title="change" onClick={() => { setDraft(person); setEditing(true); }}><strong>{person}</strong></Button>
      <div className="muted">
        {me ? me.roles.join(", ") || "no roles" : <a href={href("/users", { view: "workflow" })}>not on the roster: manage assignments</a>}
        {me?.away_until && new Date(me.away_until) > new Date() && " · away"}
        {covering.length > 0 && ` · covering for ${covering.map((p) => p.name).join(", ")}`}
      </div>
    </div>
  );
}
