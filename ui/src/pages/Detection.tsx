import { useState } from "react";
import { api, type DetectorParam, type DetectorRow, type EquipmentClass } from "../api";
import { Status, notifyChanged, useAsync } from "../lib";
import { loadPerson } from "../work";
import { ActionStatus, Badge, Button, Card, Field, Input, Select, Table } from "../ui";

const when = (at: string | null | undefined) => (at ? at.slice(0, 16).replace("T", " ") : "");
const show = (v: unknown) =>
  v === null || v === undefined || v === "" ? "—" : typeof v === "boolean" ? (v ? "yes" : "no") : String(v);
const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

function State({ d }: { d: DetectorRow }) {
  if (!d.on) return <Badge>Off</Badge>;
  if (!d.applicable) return <Badge tone="warn" title={d.note ?? undefined}>Not applicable</Badge>;
  return <Badge tone="ok">On</Badge>;
}

function LastRun({ d }: { d: DetectorRow }) {
  const r = d.last_run;
  if (!r) return <span className="muted">Not run yet</span>;
  const what = r.status === "ran" ? (r.found === null ? "Ran" : `Ran, ${r.found} found`)
    : r.status === "failed" ? "Failed" : r.status === "off" ? "Off" : "Not applicable";
  return <span title={r.note ?? undefined}>{what}<div className="muted small">{when(r.at)}</div></span>;
}

function Score({ d }: { d: DetectorRow }) {
  if (d.score) {
    return <span>Recall {pct(d.score.recall)}, precision {pct(d.score.precision)}
      <div className="muted small">{d.score.since} to {d.score.until}</div></span>;
  }
  return <span className="muted">{d.eval?.startsWith("replay:") ? "Not replayed yet" : "Fake plant test"}</span>;
}

/** The detectors on this asset: on or off, whether they apply, their last run and replay score, and their
 * thresholds, with a value for one equipment class where the detector reads one. */
export function Detection() {
  const list = useAsync(() => api.detectors(), []);
  const [open, setOpen] = useState<string | null>(null);
  const person = loadPerson() || "anonymous";
  const saved = () => { list.reload(); notifyChanged(); };
  return <>
    <Status loading={list.loading && !list.data} error={list.error} empty={list.data && list.data.detectors.length === 0}>
      No detectors are registered.
    </Status>
    {list.data && <Card>
      <Table variant="settings">
        <thead>
          <tr><th>Detector</th><th>Kind</th><th>Runs</th><th>State</th><th>Last run</th><th>Replay score</th><th /></tr>
        </thead>
        <tbody>
          {list.data.detectors.map((d) => <DetectorLine key={d.name} d={d} classes={list.data!.classes}
            project={list.data!.project} person={person} open={open === d.name}
            toggle={() => setOpen(open === d.name ? null : d.name)} saved={saved} />)}
        </tbody>
      </Table>
    </Card>}
  </>;
}

function DetectorLine({ d, classes, project, person, open, toggle, saved }: {
  d: DetectorRow; classes: EquipmentClass[]; project: string | null; person: string; open: boolean;
  toggle: () => void; saved: () => void;
}) {
  return <>
    <tr>
      <td>{d.title}<div className="muted small">{d.about}</div></td>
      <td>{d.kind_label}</td>
      <td>{d.cadence_label}</td>
      <td><State d={d} />{!d.applicable && d.note && <div className="muted small">{d.note}</div>}</td>
      <td><LastRun d={d} /></td>
      <td><Score d={d} /></td>
      <td><Button size="sm" onClick={toggle}>{open ? "Close" : "Change"}</Button></td>
    </tr>
    {open && <tr className="setting-edit">
      <td colSpan={7}>
        <Switch d={d} project={project} person={person} saved={saved} />
        {d.settings.map((s) => <Param key={s.key} d={d} s={s} classes={classes} person={person} saved={saved} />)}
      </td>
    </tr>}
  </>;
}

function useChange(saved: () => void) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState("");
  const run = async (label: string, fn: (reason: string) => Promise<unknown>, after: string) => {
    setError(null);
    setDone("");
    if (reason.trim().length < 3) return setError("Give a short reason: it goes in the history.");
    setBusy(label);
    try {
      await fn(reason.trim());
      setDone(after);
      setReason("");
      saved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return { reason, setReason, busy, error, done, run };
}

function Switch({ d, project, person, saved }: { d: DetectorRow; project: string | null; person: string; saved: () => void }) {
  const c = useChange(saved);
  const next = !d.on;
  return <div className="row small">
    <Input aria-label={`Reason to switch ${d.title}`} className="grow" placeholder="reason for the change"
      value={c.reason} onChange={(e) => c.setReason(e.target.value)} />
    <Button disabled={!!c.busy || !project} title={project ? undefined : "Pick a project first"}
      onClick={() => c.run(next ? "Switching on" : "Switching off", (r) => api.switchDetector(d.name, next, person, r),
        next ? "Switched on from its next run." : "Switched off from its next run. Open work it raised stays open.")}>
      {next ? "Switch on" : "Switch off"}
    </Button>
    {d.switched && <span className="muted">Last switched by {d.switched.updated_by}, {when(d.switched.updated_at)}</span>}
    <ActionStatus busy={c.busy} error={c.error}>{c.done}</ActionStatus>
  </div>;
}

function Param({ d, s, classes, person, saved }: {
  d: DetectorRow; s: DetectorParam; classes: EquipmentClass[]; person: string; saved: () => void;
}) {
  const c = useChange(saved);
  const [cls, setCls] = useState("");
  const set = s.classes.find((x) => x.qualifier === cls);
  const [value, setValue] = useState(String(s.value ?? ""));
  const name = (code: string) => classes.find((x) => x.code === code)?.name ?? code;
  const target = cls ? { class: cls } : {};
  return <div className="setting-param">
    <p><b>{s.description}</b>: {show(s.value)} <Badge>{s.from}</Badge>
      {s.classes.map((x) => <span key={x.qualifier} className="muted small"> · {name(x.qualifier)}: {show(x.value)} ({x.updated_by})</span>)}
    </p>
    <div className="row small">
      {s.by_class && <Field label="Equipment class" width="sm">
        <Select value={cls} onChange={(e) => {
          setCls(e.target.value);
          setValue(String(s.classes.find((x) => x.qualifier === e.target.value)?.value ?? s.value ?? ""));
        }}>
          <option value="">All classes</option>
          {classes.map((x) => <option key={x.code} value={x.code}>{x.name} ({x.units})</option>)}
        </Select>
      </Field>}
      <Field label="Value" width="xs">
        <Input value={value} onChange={(e) => setValue(e.target.value)}
          type={s.type === "int" || s.type === "float" ? "number" : "text"} step={s.type === "float" ? "any" : undefined}
          min={s.min} max={s.max} />
      </Field>
      <Input aria-label={`Reason for ${s.description}`} className="grow" placeholder="reason for the change"
        value={c.reason} onChange={(e) => c.setReason(e.target.value)} />
      <Button variant="primary" disabled={!!c.busy}
        onClick={() => c.run("Saving", (r) => api.tuneDetector(d.name, s.key, { ...target, value }, person, r),
          "Saved. Applies from the detector's next run; earlier detections are not rerun.")}>Save</Button>
      <Button disabled={!!c.busy || (cls ? !set : s.from !== "project")}
        onClick={() => c.run("Resetting", (r) => api.tuneDetector(d.name, s.key, { ...target, reset: true }, person, r),
          cls ? "Back to the value for all classes." : "Back to the global value.")}>Reset</Button>
    </div>
    <ActionStatus busy={c.busy} error={c.error}>{c.done}</ActionStatus>
  </div>;
}
