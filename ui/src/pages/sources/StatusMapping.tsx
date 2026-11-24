/** Status mapping (step 8 of the work model): each system's statuses, the Fuzer state each means, and what a Fuzer move
 * sends back. A status no row maps holds its job and asks the data steward; adding the row here answers it. */
import { useState } from "react";
import { api, type StatusMapRow } from "../../api";
import { Badge, Button, Card, Empty, ErrorText, Field, Input, Line, Select, Table, Text } from "../../ui";
import { Status, useAction, useAsync } from "../../lib";
import { usePerson } from "../../work";

const JOB_STATES: [string, string][] = [["proposed", "Planned"], ["scheduled", "Scheduled"], ["in_progress", "In progress"],
  ["done", "Done"], ["cancelled", "Cancelled"]];
const NEED_STATES: [string, string][] = [["open", "Open"], ["closed", "Closed"]];
const statesFor = (objectType: string) => objectType === "notification" ? NEED_STATES : JOB_STATES;
const stateWord = (objectType: string, s: string) => statesFor(objectType).find((x) => x[0] === s)?.[1] ?? s;
const words = (s: string) => s.replace(/_/g, " ");

export function StatusMapping({ system: only }: { system?: string | null }) {
  const [person] = usePerson();
  const [system, setSystem] = useState(only ?? "");
  const rows = useAsync(() => api.statusMap(), []);
  const all = Array.isArray(rows.data) ? rows.data : [];
  const systems = [...new Set(all.map((r) => r.system))];
  const shown = all.filter((r) => !system || r.system === system);
  const groups = [...new Set(shown.map((r) => `${r.system}:${r.object_type}`))];
  return (
    <>
      <Line wrap>
        <Field label="System" width="sm"><Select value={system} onChange={(e) => setSystem(e.target.value)}>
          <option value="">Every system</option>{systems.map((s) => <option key={s} value={s}>{s.toUpperCase()}</option>)}
        </Select></Field>
      </Line>
      <Status of={rows} />
      {Array.isArray(rows.data) && (groups.length ? groups.map((g) => {
        const [sys, type] = g.split(":");
        return <Card key={g} title={`${sys.toUpperCase()} ${words(type)}`}>
          <Table variant="list" className="phone-rows" aria-label={`${sys.toUpperCase()} ${words(type)} statuses`}>
            <thead><tr><th>Status there</th><th>Fuzer state</th><th>A move here sends back</th><th></th></tr></thead>
            <tbody>{shown.filter((r) => r.system === sys && r.object_type === type).map((r) =>
              <MapRow key={r.id} row={r} person={person} onChange={rows.reload} />)}</tbody>
          </Table>
        </Card>;
      }) : <Empty>No statuses mapped yet.</Empty>)}
      {person ? <AddRow person={person} systems={systems} onAdded={rows.reload} />
        : <Text as="p" small>Choose who you are to change the mapping.</Text>}
    </>
  );
}

function MapRow({ row, person, onChange }: { row: StatusMapRow; person: string | null; onChange: () => void }) {
  const { busy, error, run } = useAction();
  const [editing, setEditing] = useState(false);
  const [state, setState] = useState(row.state);
  const [move, setMove] = useState(row.move ?? "");
  const save = () => person && run("Saving", () => api.statusMapSet({ by: person, system: row.system,
    object_type: row.object_type, status: row.status, user_status: row.user_status, state, move: move.trim() || null,
    note: row.note }), () => { setEditing(false); onChange(); });
  return (
    <tr>
      <td><Text mono>{row.status}</Text>{row.user_status && <> with <Text mono>{row.user_status}</Text></>}
        {row.note && <Text as="div" small>{row.note}</Text>}</td>
      <td>{editing ? <Select aria-label={`Fuzer state for ${row.status}`} value={state} onChange={(e) => setState(e.target.value)}>
        {statesFor(row.object_type).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select>
        : <Badge tone={row.state === "done" || row.state === "closed" ? "ok" : "muted"}>{stateWord(row.object_type, row.state)}</Badge>}</td>
      <td>{editing ? <Input aria-label={`What a move to this state sends back for ${row.status}`} value={move} placeholder="Nothing"
        onChange={(e) => setMove(e.target.value)} /> : row.move ?? <Text small>Nothing</Text>}</td>
      <td>{person && (editing
        ? <Line><Button size="sm" variant="primary" disabled={!!busy} onClick={save}>Save</Button>
          <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button></Line>
        : <Button size="sm" variant="ghost" aria-label={`Change ${row.status}`} onClick={() => setEditing(true)}>Change</Button>)}
        <ErrorText>{error}</ErrorText></td>
    </tr>
  );
}

function AddRow({ person, systems, onAdded }: { person: string; systems: string[]; onAdded: () => void }) {
  const { busy, error, run } = useAction();
  const [open, setOpen] = useState(false);
  const [system, setSystem] = useState(systems[0] ?? "sap");
  const [type, setType] = useState("order");
  const [status, setStatus] = useState("");
  const [user, setUser] = useState("");
  const [state, setState] = useState("scheduled");
  const [move, setMove] = useState("");
  if (!open) return <Button variant="ghost" onClick={() => setOpen(true)}>Map a status</Button>;
  const states = statesFor(type);
  const add = () => run("Adding", () => api.statusMapSet({ by: person, system: system.trim().toLowerCase(),
    object_type: type.trim().toLowerCase(), status: status.trim(), user_status: user.trim(), state, move: move.trim() || null }),
  () => { setStatus(""); setUser(""); setMove(""); setOpen(false); onAdded(); });
  return (
    <Card title="Map a status" actions={<Button size="sm" variant="ghost" onClick={() => setOpen(false)}>Close</Button>}>
      <Line wrap>
        <Field label="System" width="sm"><Input value={system} list="status-map-systems" onChange={(e) => setSystem(e.target.value)} /></Field>
        <datalist id="status-map-systems">{systems.map((s) => <option key={s} value={s} />)}</datalist>
        <Field label="Object" width="sm"><Select value={type} onChange={(e) => {
          setType(e.target.value); setState(statesFor(e.target.value)[0][0]); }}>
          {["order", "notification", "work_order", "activity"].map((t) => <option key={t} value={t}>{words(t)}</option>)}</Select></Field>
        <Field label="Status there" width="sm"><Input value={status} placeholder="REL" onChange={(e) => setStatus(e.target.value)} /></Field>
        <Field label="With user status" width="sm" hint="Only when both together mean something else"><Input value={user} placeholder="WMAT" onChange={(e) => setUser(e.target.value)} /></Field>
      </Line>
      <Line wrap>
        <Field label="Fuzer state" width="sm"><Select value={state} onChange={(e) => setState(e.target.value)}>
          {states.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select></Field>
        <Field label="A move here sends back" width="md"><Input value={move} placeholder="Nothing" onChange={(e) => setMove(e.target.value)} /></Field>
      </Line>
      <Button variant="primary" disabled={!!busy || !status.trim() || !system.trim()} onClick={add}>{busy ?? "Map it"}</Button>
      <ErrorText>{error}</ErrorText>
    </Card>
  );
}
