import { useState } from "react";
import {
  api, type Closeouts, type DetectorCloseouts, type DetectorParam, type DetectorRow, type EquipmentClass, type PromotedRule,
  type ProviderRow, type Trust, type TuningProposal, type WatchPattern, type WatchProposal,
} from "../api";
import { Status, notifyChanged, useAsync } from "../lib";
import { loadPerson } from "../work";
import { ActionStatus, Badge, Button, Card, ConfirmAction, Empty, Field, Input, Select, Table } from "../ui";

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
  if (d.score?.shadow) {
    return <span>{d.score.shadow.map((b) => <div key={b.bundle}>
      {b.bundle}: matched {pct(b.matched)} of {b.proposals} proposals to what no rule caught, precision {pct(b.precision)}
      {b.model === "scripted" && <span className="muted small"> (scripted model)</span>}
    </div>)}<div className="muted small">In shadow, on replay</div></span>;
  }
  if (d.score) {
    return <span>Recall {pct(d.score.recall)}, precision {pct(d.score.precision)}
      <div className="muted small">{d.score.since} to {d.score.until}</div></span>;
  }
  return <span className="muted">{d.eval?.startsWith("replay:") ? "Not replayed yet" : "Fake plant test"}</span>;
}

/** How what it raised was closed out: confirmed and false-alarm rates of the cases closed out so far. */
function CloseoutRates({ c }: { c: Closeouts }) {
  if (!c.cases) return <span className="muted">No cases yet</span>;
  if (!c.closed) return <span className="muted">{c.cases} open, none closed out</span>;
  return <span>Confirmed {pct(c.confirmed_rate)}, false alarm {pct(c.false_alarm_rate)}
    <div className="muted small">{c.closed} of {c.cases} cases closed out</div></span>;
}

/** A detector's close-outs: what share was noise (false alarms and duplicates), per class when it has cases on more
 * than one. */
function DetectorRates({ c }: { c: DetectorCloseouts }) {
  if (!c.cases) return <span className="muted">No cases yet</span>;
  if (!c.closed) return <span className="muted">{c.cases} open, none closed out</span>;
  return <span>Confirmed {pct(c.confirmed_rate)}, noise {pct(c.noise_rate)}
    <div className="muted small">{c.false_alarm} false alarm{c.false_alarm === 1 ? "" : "s"}, {c.duplicate} duplicate
      {c.duplicate === 1 ? "" : "s"}; {c.closed} of {c.cases} cases closed out</div></span>;
}

function ClassRates({ d, classes }: { d: DetectorRow; classes: EquipmentClass[] }) {
  const rows = d.closeouts?.by_class.filter((r) => r.closed) ?? [];
  if (rows.length < 2) return null;
  const name = (code: string) => (code ? classes.find((x) => x.code === code)?.name ?? code : "Not classified");
  return <p className="small">Close-outs by class: {rows.map((r, i) => <span key={r.class}>{i ? "; " : ""}
    <b>{name(r.class)}</b> {r.closed} closed, noise {pct(r.noise_rate)}</span>)}</p>;
}

/** The detectors on this asset: on or off, whether they apply, their last run and replay score, and their
 * thresholds, with a value for one equipment class where the detector reads one. */
export function Detection({ focus }: { focus?: string | null } = {}) {
  const list = useAsync(() => api.detectors(), []);
  const [open, setOpen] = useState<string | null>(focus ?? null);
  const person = loadPerson() || "anonymous";
  const saved = () => { list.reload(); notifyChanged(); };
  const proposals = useAsync(() => api.tuningProposals(), []);
  const decided = () => { proposals.reload(); saved(); };
  return <>
    {(proposals.data?.length ?? 0) > 0 && <Card title="Tuning proposals">
      {proposals.data!.map((p) => <Proposal key={p.id} p={p} person={person} decided={decided} />)}
    </Card>}
    <Status loading={list.loading && !list.data} error={list.error} empty={list.data && list.data.detectors.length === 0}>
      No detectors are registered.
    </Status>
    {list.data && <Card>
      <Table variant="settings">
        <thead>
          <tr><th>Detector</th><th>Kind</th><th>Runs</th><th>State</th><th>Last run</th><th>Replay score</th>
            <th>Close-outs</th><th /></tr>
        </thead>
        <tbody>
          {list.data.detectors.map((d) => <DetectorLine key={d.name} d={d} classes={list.data!.classes}
            project={list.data!.project} person={person} open={open === d.name}
            toggle={() => setOpen(open === d.name ? null : d.name)} saved={saved} />)}
        </tbody>
      </Table>
    </Card>}
    {list.data && (list.data.providers?.length ?? 0) > 0 && <Card title="Detection providers">
      <Table variant="settings">
        <thead><tr><th>Provider</th><th>Raises</th><th>Trust</th><th>Close-outs</th><th /></tr></thead>
        <tbody>
          {list.data.providers.map((p) => <ProviderLine key={`${p.system}/${p.name}`} p={p} classes={list.data!.classes}
            levels={list.data!.trust_levels} person={person} open={open === `provider:${p.system}/${p.name}`}
            toggle={() => setOpen(open === `provider:${p.system}/${p.name}` ? null : `provider:${p.system}/${p.name}`)}
            saved={saved} />)}
        </tbody>
      </Table>
    </Card>}
    {list.data && <Promotions classes={list.data.classes} person={person} saved={saved} />}
  </>;
}

/** A quieter value for a noisy detector: the change, its replay on the closed cases before and after, and any real
 * case it would miss. Accepting tunes the detector (a new version in the settings history); setting it aside needs a
 * reason. */
function Proposal({ p, person, decided }: { p: TuningProposal; person: string; decided: () => void }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setError(null);
    setBusy(label);
    try { await fn(); decided(); } catch (e) { setError(String((e as Error).message ?? e)); } finally { setBusy(null); }
  };
  return <div className="setting-edit">
    <p><b>{p.title}</b></p>
    <p className="small">{pct(p.noise_rate)} noise on {p.cases} closed cases{p.class ? ` on ${p.class}` : ""}. Replayed
      over {p.windows} window{p.windows === 1 ? "" : "s"}: noisy cases raised {p.before.noise} to {p.after.noise}, real
      cases raised {p.before.real} to {p.after.real}.{" "}
      {p.missed.length ? <b>It would miss case{p.missed.length === 1 ? "" : "s"} {p.missed.join(", ")}.</b> : "It misses no real case."}</p>
    <div className="row small">
      <ConfirmAction label="Accept tuning" busy={!!busy}
        consequence={`Set ${p.param} to ${p.proposed} for ${p.detector}${p.class ? ` on ${p.class}` : ""}? It takes effect on its next run and is kept in the settings history.`}
        onConfirm={() => act("Accepting", () => api.acceptTuning(p.id, person))} />
      <Input aria-label={`Reason to set aside ${p.title}`} className="grow" placeholder="reason to leave it as it is"
        value={reason} onChange={(e) => setReason(e.target.value)} />
      <Button disabled={!!busy || reason.trim().length < 3}
        onClick={() => act("Setting aside", () => api.setTuningAside(p.id, person, reason))}>Set aside</Button>
    </div>
    <ActionStatus busy={busy} error={error} />
  </div>;
}

/** What the agentic watch proposed (P3.3) and the rules drafted from what people confirmed (P3.4): confirm or reject
 * each proposal; a pattern confirmed often enough can be drafted as a rule, and a person publishes or retires it. */
function Promotions({ classes, person, saved }: { classes: EquipmentClass[]; person: string; saved: () => void }) {
  const got = useAsync(() => api.promotions(), []);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState("");
  const reload = () => { got.reload(); saved(); };
  const act = async (label: string, fn: () => Promise<unknown>, after: string) => {
    setError(null);
    setDone("");
    setBusy(label);
    try {
      await fn();
      setDone(after);
      reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const label = (p: { class: string; role: string; direction: string }) =>
    `${classes.find((x) => x.code === p.class)?.name ?? p.class}: ${p.role.replace(/_/g, " ")} ${p.direction === "high" ? "rising" : "falling"}`;
  const d = got.data;
  return <Card title="Watch findings and promoted rules">
    <Status loading={got.loading && !d} error={got.error} />
    {d && <>
      <h3>Proposals{d.unreviewed ? ` (${d.unreviewed} to review)` : ""}</h3>
      {d.proposals.length === 0 ? <Empty>The watch has proposed nothing yet.</Empty> :
        <Table variant="settings">
          <thead><tr><th>When</th><th>Area</th><th>About</th><th>Why it is unusual</th><th>Verdict</th></tr></thead>
          <tbody>{d.proposals.map((p) => <ProposalLine key={p.id} p={p} busy={!!busy}
            judge={(real) => act(real ? "Confirming" : "Rejecting", () => api.judgeProposal(p.id, real, person),
              real ? "Confirmed. It counts towards its pattern's rule." : "Rejected.")} />)}</tbody>
        </Table>}
      <h3>Patterns</h3>
      {d.patterns.length === 0 ? <Empty>No confirmed proposal yet.</Empty> :
        <Table variant="settings">
          <thead><tr><th>Pattern</th><th>Confirmed</th><th>Smallest shift</th><th>Rule</th></tr></thead>
          <tbody>{d.patterns.map((p) => <PatternLine key={p.pattern} p={p} name={label(p)} busy={!!busy}
            draft={() => act("Drafting", () => api.draftRule(p.pattern, person),
              "Drafted. Review it below; it runs only once published.")} />)}</tbody>
        </Table>}
      <ActionStatus busy={busy} error={error}>{done}</ActionStatus>
      <h3>Rules</h3>
      {d.rules.length === 0 ? <Empty>No rule drafted yet.</Empty> :
        d.rules.map((r) => <RuleLine key={r.id} r={r} name={label(r.spec)} person={person} saved={reload} />)}
    </>}
  </Card>;
}

function ProposalLine({ p, busy, judge }: { p: WatchProposal; busy: boolean; judge: (real: boolean) => void }) {
  return <tr>
    <td>{when(p.start ?? p.since)}{p.end && <div className="muted small">to {when(p.end)}</div>}</td>
    <td>{p.area_name ?? "—"}</td>
    <td>{p.subject ?? "—"}{!p.cited && <div className="small"><Badge tone="warn">Cites what it wasn't shown</Badge></div>}</td>
    <td>{p.why}{p.ruled_out && <div className="muted small">Ruled out: {p.ruled_out}</div>}</td>
    <td>{p.verdict
      ? <span><Badge tone={p.verdict === "confirmed" ? "ok" : "muted"}>{p.verdict === "confirmed" ? "Confirmed" : "Rejected"}</Badge>
        <div className="muted small">{p.verdict_by}, {when(p.verdict_at)}</div></span>
      : <div className="row small">
        <Button size="sm" disabled={busy} onClick={() => judge(true)}>Confirm</Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => judge(false)}>Reject</Button>
      </div>}</td>
  </tr>;
}

function PatternLine({ p, name, busy, draft }: { p: WatchPattern; name: string; busy: boolean; draft: () => void }) {
  return <tr>
    <td>{name}</td>
    <td>{p.confirmed} of {p.needed} needed</td>
    <td>{p.smallest_z === null ? "—" : `${p.smallest_z.toFixed(1)} × the week's spread`}
      {p.shortest_minutes ? <div className="muted small">for {p.shortest_minutes} min at the shortest</div> : null}</td>
    <td>{p.rule ? <Badge tone={p.rule.status === "published" ? "ok" : undefined}>
      {p.rule.status === "published" ? "Published" : "Drafted"}</Badge>
      : <Button size="sm" disabled={busy || !p.ready}
        title={p.ready ? undefined : `${p.needed - p.confirmed} more confirmed before a rule is drafted`}
        onClick={draft}>Draft a rule</Button>}</td>
  </tr>;
}

function RuleLine({ r, name, person, saved }: { r: PromotedRule; name: string; person: string; saved: () => void }) {
  const c = useChange(saved);
  const s = r.score;
  const live = r.status !== "retired";
  return <div className="setting-param">
    <p><b>{name}</b>{" "}
      <Badge tone={r.status === "published" ? "ok" : r.status === "retired" ? "muted" : undefined}>
        {r.status === "published" ? "Published" : r.status === "retired" ? "Retired" : "Draft"}</Badge></p>
    <p className="small">Average over {r.spec.minutes} min moving {r.spec.direction === "high" ? "above" : "below"} the
      week before by {r.spec.z} × its spread · fitted on {r.examples.length} confirmed proposals</p>
    <p className="small">{s
      ? <>Replay: recall {pct(s.recall)} ({s.detected} of {s.events} events), precision {pct(s.precision)},
        {" "}{s.false_alarms_per_week ?? "—"} false alarms a week <span className="muted">({s.since.slice(0, 10)} to {s.until.slice(0, 10)})</span></>
      : <span className="muted">Not replayed yet</span>}
      <span className="muted"> · drafted by {r.drafted_by}, {when(r.drafted_at)}
        {r.published_by && ` · published by ${r.published_by}, ${when(r.published_at)}`}
        {r.retired_by && ` · retired by ${r.retired_by}, ${when(r.retired_at)}`}
        {r.reason && ` · "${r.reason}"`}</span></p>

    {live && <div className="row small">
      <Input aria-label={`Reason for the rule on ${name}`} className="grow" placeholder="reason for the change"
        value={c.reason} onChange={(e) => c.setReason(e.target.value)} />
      {r.status === "draft" && <Button variant="primary" disabled={!!c.busy}
        onClick={() => c.run("Publishing", (why) => api.publishRule(r.id, person, why),
          "Published. It runs with the other detectors from the next scan.")}>Publish</Button>}
      <Button variant={r.status === "published" ? "danger" : "secondary"} disabled={!!c.busy}
        onClick={() => c.run("Retiring", (why) => api.retireRule(r.id, person, why),
          r.status === "published" ? "Retired. It stops from the next scan; its detections stay." : "Retired.")}>Retire</Button>
    </div>}
    <ActionStatus busy={c.busy} error={c.error}>{c.done}</ActionStatus>
  </div>;
}

function ProviderLine({ p, classes, levels, person, open, toggle, saved }: {
  p: ProviderRow; classes: EquipmentClass[]; levels: Record<Trust, string>; person: string; open: boolean;
  toggle: () => void; saved: () => void;
}) {
  const c = useChange(saved);
  const [cls, setCls] = useState("");
  const [trust, setTrust] = useState<Trust>("route");
  const name = (code: string) => (code ? classes.find((x) => x.code === code)?.name ?? code : "All classes");
  const all = p.trust.find((t) => t.class === "");
  return <>
    <tr>
      <td>{p.label}<div className="muted small">{p.system}{p.enabled ? "" : " · off"}</div></td>
      <td><code>{p.type}</code></td>
      <td>{all ? levels[all.trust] : levels.route}
        {p.trust.filter((t) => t.class).map((t) => <div key={t.class} className="muted small">{name(t.class)}: {levels[t.trust]}</div>)}
      </td>
      <td><CloseoutRates c={p.closeouts} /></td>
      <td><Button size="sm" onClick={toggle}>{open ? "Close" : "Change"}</Button></td>
    </tr>
    {open && <tr className="setting-edit">
      <td colSpan={5}>
        {p.trust.map((t) => <p key={t.class} className="small"><b>{name(t.class)}</b>: {levels[t.trust]}, "{t.reason}"
          <span className="muted"> ({t.updated_by}, {when(t.updated_at)})</span></p>)}
        <div className="row small">
          <Field label="Equipment class" width="sm">
            <Select value={cls} onChange={(e) => setCls(e.target.value)}>
              <option value="">All classes</option>
              {classes.map((x) => <option key={x.code} value={x.code}>{x.name} ({x.units})</option>)}
            </Select>
          </Field>
          <Field label="Trust" width="sm">
            <Select value={trust} onChange={(e) => setTrust(e.target.value as Trust)}>
              {(Object.keys(levels) as Trust[]).map((k) => <option key={k} value={k}>{levels[k]}</option>)}
            </Select>
          </Field>
          <Input aria-label={`Reason to change trust in ${p.label}`} className="grow" placeholder="reason for the change"
            value={c.reason} onChange={(e) => c.setReason(e.target.value)} />
          <Button variant="primary" disabled={!!c.busy}
            onClick={() => c.run("Saving", (r) => api.setProviderTrust(p.system, p.name, trust, cls || null, person, r),
              "Saved. Applies to its next alerts; cases already open stay as they are.")}>Save</Button>
        </div>
        <ActionStatus busy={c.busy} error={c.error}>{c.done}</ActionStatus>
      </td>
    </tr>}
  </>;
}

function DetectorLine({ d, classes, project, person, open, toggle, saved }: {
  d: DetectorRow; classes: EquipmentClass[]; project: string | null; person: string; open: boolean;
  toggle: () => void; saved: () => void;
}) {
  return <>
    <tr>
      <td>{d.title}{d.version && d.version > 1 ? <span className="muted small"> v{d.version}</span> : null}
        <div className="muted small">{d.about}</div></td>
      <td>{d.kind_label}</td>
      <td>{d.cadence_label}</td>
      <td><State d={d} />{!d.applicable && d.note && <div className="muted small">{d.note}</div>}</td>
      <td><LastRun d={d} /></td>
      <td><Score d={d} /></td>
      <td>{d.closeouts && <DetectorRates c={d.closeouts} />}</td>
      <td><Button size="sm" onClick={toggle}>{open ? "Close" : "Change"}</Button></td>
    </tr>
    {open && <tr className="setting-edit">
      <td colSpan={8}>
        <ClassRates d={d} classes={classes} />
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
