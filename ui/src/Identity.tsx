/** Signed-in identity; attribution-only names are limited to explicit trusted local mode. */
import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import { href, notifyChanged, useAsync } from "./lib";
import { usePerson } from "./work";
import { Button, Input, Select, ErrorText } from "./ui";
import { getProject, switchProject, type AuthSession } from "./authState";
import { SignOut, useAuth, usePermission } from "./Auth";

/** Which project this tab works in. Only projects the server says this person may open are offered; the server
 * checks the grant again on every request, so this list is a convenience, not the permission. */
export function ProjectPicker({ session }: { session: AuthSession | null }) {
  const options = (session?.mode === "microsoft" ? session.user?.projects : session?.projects) ?? [];
  const current = session?.project ?? "";
  const picked = getProject();
  const known = options.includes(picked);
  useEffect(() => {
    // A project picked earlier that this person may no longer open: back to the server's own project.
    if (session && picked && !known) switchProject("");
  }, [session, picked, known]);
  if (options.length < 2 && (!current || options.includes(current))) return null;
  return <label className="identity small project-picker">
    <span className="muted">Project</span>
    <Select value={current} aria-label="Project" onChange={(e) => switchProject(e.target.value)}>
      {!options.includes(current) && <option value={current}>{current ? projectName(current) : "Default"}</option>}
      {options.map((name) => <option key={name} value={name}>{projectName(name)}</option>)}
    </Select>
  </label>;
}

/** A project's id ("okan") read as a name ("Okan"). */
const projectName = (id: string) => id.charAt(0).toUpperCase() + id.slice(1);

/** Initials for the account badge: "Adam Reeder" → "AR". */
const initials = (name: string) => name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("") || "?";

/** On a phone the account is a round badge in the top bar, as iOS apps do: it opens a sheet with the project, who you
 * are, your roles, Acting as, Manage users and Sign out. The Menu sheet no longer carries them. */
export function AccountButton() {
  const session = useAuth();
  const [person] = usePerson();
  const [open, setOpen] = useState(false);
  const sheet = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    sheet.current?.querySelector<HTMLElement>("button, a[href], select, input")?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const close = () => setOpen(false);
    window.addEventListener("keydown", key);
    window.addEventListener("hashchange", close);
    return () => { window.removeEventListener("keydown", key); window.removeEventListener("hashchange", close); button.current?.focus(); };
  }, [open]);
  const name = session?.mode === "microsoft" ? session.user?.display_name ?? "" : person;
  if (session?.mode === "microsoft" && !session.user) return null;
  return <>
    <Button ref={button} type="button" variant="ghost" className="account-button" aria-haspopup="dialog" aria-expanded={open}
      aria-label={name ? `Account: ${name}` : "Account"} title={name || "Account"} onClick={() => setOpen(true)}>
      <span aria-hidden="true">{name ? initials(name) : "?"}</span>
    </Button>
    {open && <>
      <div className="account-scrim" onClick={() => setOpen(false)} />
      <div ref={sheet} className="account-sheet" role="dialog" aria-modal="true" aria-label="Account">
        <div className="account-sheet-head">
          <span className="account-badge" aria-hidden="true">{name ? initials(name) : "?"}</span>
          <h2>{name || "Account"}</h2>
          <Button type="button" variant="link" className="account-close" onClick={() => setOpen(false)}>Done</Button>
        </div>
        <Identity />
      </div>
    </>}
  </>;
}

export function Identity() {
  const session = useAuth();
  const configure = usePermission("configure");
  if (session?.mode === "microsoft") {
    const user = session.user;
    return <><ProjectPicker session={session} /><div className="identity small">
      <strong>{user?.display_name}</strong>
      {user?.inspect ? <ActingAs actor={user.actor} />
        : <p>{user?.owner ? "Owner · all human roles" : user?.roles.join(", ") || "No application roles"}</p>}
      {configure && <p><a href={href("/users")}>{user?.inspect ? "See users" : "Manage users"}</a></p>}
      <SignOut />
    </div></>;
  }
  return <><ProjectPicker session={session} /><LocalIdentity /></>;
}

const spaced = (role: string) => role.replace(/_/g, " ");

/** The demo account can be any role: it holds every human role until one is picked here, and then gets that role's
 * queue, jobs and Home. Kept per project on the server, so every demo session in the project acts as the same role. */
function ActingAs({ actor }: { actor: string }) {
  const data = useAsync(async () => {
    const [roles, people] = await Promise.all([api.rolesCatalog(), api.workPeople()]);
    return { roles: roles.filter((r) => r.kind === "person").map((r) => r.name).sort(),
      picked: people.find((p) => p.name === actor)?.assigned_roles ?? [] };
  }, [actor]);
  const [error, setError] = useState("");
  const picked = data.data?.picked ?? [];
  const value = picked.length === 1 ? picked[0] : "";
  const pick = (role: string) => {
    setError("");
    api.actAs(role ? [role] : []).then(() => notifyChanged()).catch((e: Error) => setError(e.message));
  };
  return <>
    <label className="acting-as">
      <span className="muted">Acting as</span>
      <Select value={value} aria-label="Acting as" disabled={!data.data} onChange={(e) => pick(e.target.value)}>
        <option value="">{picked.length > 1 ? picked.map(spaced).join(", ") : "Every role"}</option>
        {(data.data?.roles ?? []).map((role) => <option key={role} value={role}>{spaced(role)}</option>)}
      </Select>
    </label>
    <ErrorText>{error}</ErrorText>
  </>;
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
