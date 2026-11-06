import { Button, Table, Card as UICard, Input , Badge as UIBadge} from "../ui";
import { useState } from "react";
import { api, type WorkRoleSummary } from "../api";
import { Status, notifyChanged, useAsync } from "../lib";

const label = (role: string) => role.replace(/_/g, " ");

/** Settings > People (#202): who can take work, and in which roles' queues. */
export function PeopleCard({ by }: { by: string }) {
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.workRoleSummary(), []);
  const [message, setMessage] = useState<string>();
  const changed = (msg: string) => {
    setMessage(msg);
    people.reload();
    roles.reload();
    notifyChanged();
  };
  const all = roles.data ?? [];
  return (
    <UICard className="" id="people">
      <h2>People</h2>
      <p className="muted small">
        Who can take work in Fuzer, and their roles. The Work tab shows each person the unclaimed steps of their
        roles' queues. Removing someone puts the steps they had claimed back in their role's queue.
      </p>
      <Status loading={people.loading && !people.data} error={people.error || roles.error} />
      {people.data && (
        <Table variant="settings">
          <thead>
            <tr><th>Name</th><th>Roles</th><th /></tr>
          </thead>
          <tbody>
            {people.data.length === 0 && (
              <tr><td colSpan={3} className="muted">Nobody yet. Add yourself below to see your roles' work.</td></tr>
            )}
            {people.data.map((p) => (
              <PersonRow key={p.name} name={p.name} roles={p.roles} all={all} by={by} changed={changed} />
            ))}
          </tbody>
        </Table>
      )}
      <AddPerson all={all} by={by} taken={(people.data ?? []).map((p) => p.name)} changed={changed} />
      {message && <div className="status small">{message}</div>}
    </UICard>
  );
}

function RolePicker({ all, value, onChange }: {
  all: WorkRoleSummary[]; value: string[]; onChange: (roles: string[]) => void;
}) {
  return (
    <span className="row small">
      {all.map((r) => (
        <label key={r.role} title={hint(r)}>
          <Input
            type="checkbox"
            checked={value.includes(r.role)}
            onChange={(e) => onChange(e.target.checked ? [...value, r.role] : value.filter((x) => x !== r.role))}
          />{" "}
          {label(r.role)}
          {r.waiting > 0 && <UIBadge className="badge">{r.waiting} waiting</UIBadge>}
        </label>
      ))}
    </span>
  );
}

function hint(r: WorkRoleSummary): string {
  const used = r.processes.length ? `Steps in: ${r.processes.join(", ")}.` : "No published process has steps for it yet.";
  return `${used} ${r.waiting} unclaimed in its queue.`;
}

function useAction(changed: (msg: string) => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const run = async (fn: () => Promise<string>) => {
    setBusy(true);
    setError(null);
    try {
      changed(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

function PersonRow({ name, roles, all, by, changed }: {
  name: string; roles: string[]; all: WorkRoleSummary[]; by: string; changed: (msg: string) => void;
}) {
  const [value, setValue] = useState(roles);
  const [confirming, setConfirming] = useState(false);
  const { busy, error, run } = useAction(changed);
  const dirty = [...value].sort().join() !== [...roles].sort().join();
  const save = () => run(async () => {
    await api.setWorkPerson(name, value, by);
    return `Saved ${name}'s roles.`;
  });
  const remove = () => run(async () => {
    const out = await api.removeWorkPerson(name, by);
    const back = out.released_tasks.length + out.released_items.length;
    return `Removed ${name}.` + (back ? ` ${back} claimed step(s) went back to their role's queue.` : "");
  });
  return (
    <tr>
      <td><b>{name}</b></td>
      <td><RolePicker all={all} value={value} onChange={setValue} /></td>
      <td className="row small">
        {dirty && <Button variant="primary" disabled={busy} onClick={save}>Save</Button>}
        {confirming ? (
          <>
            <Button disabled={busy} onClick={remove}>Remove {name}</Button>
            <Button disabled={busy} onClick={() => setConfirming(false)}>Keep</Button>
          </>
        ) : (
          <Button disabled={busy} onClick={() => setConfirming(true)}>Remove</Button>
        )}
        {error && <span className="error">{error}</span>}
      </td>
    </tr>
  );
}

function AddPerson({ all, by, taken, changed }: {
  all: WorkRoleSummary[]; by: string; taken: string[]; changed: (msg: string) => void;
}) {
  const [name, setName] = useState(by === "anonymous" ? "" : by);
  const [roles, setRoles] = useState<string[]>([]);
  const { busy, error, run } = useAction(changed);
  const n = name.trim();
  const add = () => run(async () => {
    await api.setWorkPerson(n, roles, by === "anonymous" ? n : by);
    setName("");
    setRoles([]);
    return `Added ${n}.`;
  });
  return (
    <div className="row small">
      <Input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
      <RolePicker all={all} value={roles} onChange={setRoles} />
      <Button variant="primary" disabled={busy || !n || taken.includes(n)} onClick={add}
        title={taken.includes(n) ? `${n} is already here: change their roles above` : undefined}>
        Add person
      </Button>
      {error && <span className="error">{error}</span>}
    </div>
  );
}
