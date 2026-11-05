/** One dataset's page (#225): what it is and how it's doing, its settings, mapping versions, runs, quality,
 * matching and history. */
import { useState } from "react";
import { api, type DatasetDetail, type ImpactResult, type Json, type MappingVersion, type SourceLoad } from "../../api";
import { Status, href, useAsync } from "../../lib";
import {
  DRIFT_HELP, Diff, HealthBadge, LOAD_HELP, Loaded, SCHEDULES, TestSteps, cell, reviewer, scheduleText, useAction, when,
} from "./common";
import { MappingEditor } from "./MappingEditor";

export const TABS = ["overview", "settings", "mapping", "runs", "quality", "matching", "history"] as const;
export type Tab = (typeof TABS)[number];
const TAB_LABEL: Record<Tab, string> = {
  overview: "Overview", settings: "Settings", mapping: "Mapping", runs: "Runs", quality: "Quality", matching: "Matching",
  history: "History",
};

export function DatasetPage({ name, tab }: { name: string; tab: Tab }) {
  const d = useAsync(() => api.dataset(name), [name]);
  if (!d.data) return <Status loading={d.loading} error={d.error} />;
  const x = d.data;
  const ds = x.dataset;
  return (
    <section>
      <p className="small"><a href={href("/sources")}>Sources</a> › {ds.source}</p>
      <div className="row spread">
        <h1><code>{name}</code> <HealthBadge h={x.health} /></h1>
        <span className="muted small">{ds.source} · {ds.record_type ?? ds.reader} · {ds.status}</span>
      </div>
      <LastLoad d={x} reload={d.reload} />
      <div className="tabs">
        {TABS.map((t) => (
          <a key={t} className={tab === t ? "on" : ""} href={href("/sources", { view: "dataset", name, tab: t })}>
            {TAB_LABEL[t]}{t === "quality" && x.suggested_checks.length ? <span className="muted"> · {x.suggested_checks.length} to add</span> : null}
          </a>
        ))}
      </div>
      {tab === "overview" && <Overview d={x} reload={d.reload} />}
      {tab === "settings" && <Settings d={x} reload={d.reload} />}
      {tab === "mapping" && <MappingTab d={x} reload={d.reload} />}
      {tab === "runs" && <Runs d={x} reload={d.reload} />}
      {tab === "quality" && <Quality d={x} reload={d.reload} />}
      {tab === "matching" && <Matching d={x} />}
      {tab === "history" && <History d={x} />}
    </section>
  );
}

/** Why the last load didn't go through, with a button to load again once it's fixed. */
function LastLoad({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const [done, setDone] = useState<SourceLoad | null>(null);
  const last = d.runs[0];
  const stuck = last && (last.status === "held" || last.status === "failed");
  if (!stuck && !done) return null;
  const checks = (last?.detail as { checks?: { kind: string; value: number; detail?: { examples?: string[] } }[] } | null)?.checks;
  return (
    <div className="notice small">
      {stuck && last.status === "failed" && <p>The last load failed: {last.error}</p>}
      {stuck && last.status === "held" && (
        <p>
          The last load ({when(last.finished_at)}) was held, so its records weren't changed:{" "}
          {checks ? checks.map((c) => c.kind === "duplicate keys"
            ? `the export repeats ${c.value} key${c.value === 1 ? "" : "s"} (${(c.detail?.examples ?? []).join(", ")})`
            : `the ${c.kind} check failed (${c.value})`).join("; ")
            : <>its columns changed (answer it on <a href={href("/review", { kind: "schema" })}>Review</a>)</>}.
          {" "}Fix it (the export, the key under Settings, or the check under Quality), then load again.
        </p>
      )}
      <div className="row">
        <button className="primary" disabled={!!a.busy}
          onClick={() => a.run("run", () => api.runDataset(d.dataset.name, true), (r) => { setDone(r); reload(); })}>
          {a.busy ? "Loading…" : "Load again"}</button>
        {done && <Loaded r={done} />}
      </div>
      {a.error && <p className="error">{a.error}</p>}
    </div>
  );
}

function Overview({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const [test, setTest] = useState<Awaited<ReturnType<typeof api.testConnection>> | null>(null);
  const [done, setDone] = useState<SourceLoad | null>(null);
  const ds = d.dataset, h = d.health, last = d.runs[0];
  const facts: [string, React.ReactNode][] = [
    ["System", <a href={href("/sources", { view: "systems" })}><code>{ds.source}</code></a>],
    ["Connection", <>{d.connection?.name} <span className="muted">({d.connection?.type}) {d.connection_display}</span></>],
    ["How it loads", <>{ds.load_mode} <span className="muted">{LOAD_HELP[ds.load_mode]}</span></>],
    ["Schedule", <>{scheduleText(ds.schedule)}{d.next_load && <span className="muted"> · next {when(d.next_load)}</span>}</>],
    ["Expected", ds.expected_hours ? `a delivery every ${ds.expected_hours} h` : <span className="muted">no expectation</span>],
    ["Owner", ds.owner ?? <span className="muted">none (alerts go to the system's owner, else data stewards)</span>],
    ["Records", h?.rows?.toLocaleString() ?? "–"],
    ["Last loaded", <>{when(h?.last_loaded) || "never"}{last && <span className="muted"> · last run {last.status} ({last.trigger})</span>}</>],
  ];
  return (
    <>
      <div className="row">
        <button className="primary" disabled={!!a.busy} onClick={() => a.run("run", () => api.runDataset(ds.name), (r) => { setDone(r); reload(); })}>
          {a.busy === "run" ? "Loading…" : "Load now"}</button>
        <button className="secondary" disabled={!!a.busy || !d.connection}
          onClick={() => a.run("test", () => api.testConnection(d.connection!.id), setTest)}>Test connection</button>
        <button className="secondary" disabled={!!a.busy} onClick={() => a.run("profile", () => api.profileDataset(ds.name), reload)}>
          Profile now</button>
        {done && <Loaded r={done} />}
        {test && <span className={`badge ${test.ok ? "ok" : ""}`}>{test.ok ? "connection ok" : "connection failed"}</span>}
      </div>
      {test && !test.ok && <TestSteps steps={test.steps} />}
      {a.error && <p className="error small">{a.error}</p>}
      <table className="kv">
        <tbody>{facts.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody>
      </table>
      {h && h.problems.length > 0 && <p className="error small">Needs a look: {h.problems.join(", ")}.</p>}
      {(d.work_items.length > 0 || d.reviews.length > 0) && (
        <>
          <h3>Open</h3>
          <ul className="small">
            {d.work_items.map((w) => (
              <li key={`w${w.id}`}><a href={href(`/cases/${w.id}`)}>work item {w.id}</a>: {w.title}{" "}
                <span className="muted">({w.assignee ?? w.role ?? "unassigned"}, {when(w.created_at)})</span></li>
            ))}
            {d.reviews.map((r) => (
              <li key={`r${r.id}`}><a href={href("/review", { kind: r.kind })}>review item {r.id}</a>:{" "}
                {r.kind === "check" ? "a held load" : "changed columns"} <span className="muted">({when(r.created_at)})</span></li>
            ))}
          </ul>
        </>
      )}
      {d.suggested_checks.length > 0 && (
        <p className="small">
          {d.suggested_checks.length} check{d.suggested_checks.length === 1 ? "" : "s"} suggested from its profile:{" "}
          <a href={href("/sources", { view: "dataset", name: ds.name, tab: "quality" })}>see Quality</a>.
        </p>
      )}
    </>
  );
}

function Settings({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const ds = d.dataset;
  const cat = useAsync(() => api.catalog(), []);
  const init = {
    load_mode: ds.load_mode, drift_policy: ds.drift_policy, schedule: ds.schedule ?? "", expected_hours: ds.expected_hours?.toString() ?? "",
    owner: ds.owner ?? "", notes: ds.notes ?? "", key: (ds.key ?? []).join(", "), location: ds.location ?? "",
    connector: ds.connector ?? "", watermark: ds.watermark ?? "", connection_id: ds.connection_id?.toString() ?? "",
  };
  const [v, setV] = useState(init);
  const [reason, setReason] = useState("");
  const a = useAction();
  const conns = cat.data?.systems.find((s) => s.name === ds.source)?.connections ?? [];
  const custom = !SCHEDULES.some(([c]) => c === v.schedule);
  const save = () => {
    const values: Json = {};
    for (const k of Object.keys(init) as (keyof typeof init)[]) {
      if (v[k] === init[k]) continue;
      values[k] = v[k] === "" ? null : k === "key" ? v[k].split(",").map((s) => s.trim()).filter(Boolean)
        : k === "expected_hours" || k === "connection_id" ? Number(v[k]) : v[k];
    }
    return a.run("save", () => api.editDataset(ds.name, values, reviewer(), reason || undefined), () => { setReason(""); reload(); });
  };
  const changed = (Object.keys(init) as (keyof typeof init)[]).some((k) => v[k] !== init[k]);
  const field = (k: keyof typeof init, placeholder = "") => (
    <input value={v[k]} placeholder={placeholder} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
  );
  return (
    <>
      <table className="kv">
        <tbody>
          <tr><th>How it loads</th><td>
            {Object.keys(LOAD_HELP).map((m) => (
              <label key={m} className="small choice"><input type="radio" checked={v.load_mode === m} onChange={() => setV({ ...v, load_mode: m })} />
                {" "}<strong>{m}</strong>: {LOAD_HELP[m]}</label>))}
          </td></tr>
          <tr><th>If its columns change</th><td>
            {Object.keys(DRIFT_HELP).map((m) => (
              <label key={m} className="small choice"><input type="radio" checked={v.drift_policy === m} onChange={() => setV({ ...v, drift_policy: m })} />
                {" "}{DRIFT_HELP[m]}</label>))}
          </td></tr>
          <tr><th>Schedule</th><td>
            <select value={custom ? "custom" : v.schedule} onChange={(e) => setV({ ...v, schedule: e.target.value === "custom" ? "0 0 * * *" : e.target.value })}>
              {SCHEDULES.map(([c, label]) => <option key={c} value={c}>{label}</option>)}
              <option value="custom">custom (cron)</option>
            </select>{" "}
            {custom && field("schedule", "cron: minute hour day month weekday")}
            {d.next_load && <span className="muted small"> next {when(d.next_load)}</span>}
          </td></tr>
          <tr><th>Expect a delivery every</th><td>{field("expected_hours", "hours (empty: no expectation)")}
            <span className="muted small"> stale after that; its owner is told</span></td></tr>
          <tr><th>Owner</th><td>{field("owner", "who answers for it")}</td></tr>
          <tr><th>Notes</th><td>{field("notes")}</td></tr>
        </tbody>
      </table>
      <details>
        <summary className="small">Advanced: connection, key, location, reader, watermark</summary>
        <table className="kv">
          <tbody>
            <tr><th>Connection</th><td>
              <select value={v.connection_id} onChange={(e) => setV({ ...v, connection_id: e.target.value })}>
                {conns.map((c) => <option key={c.id} value={c.id}>{c.name}: {c.display}</option>)}
              </select></td></tr>
            <tr><th>Key columns</th><td>{field("key", "comma separated")} <span className="muted small">the record's id; joined by &gt;</span></td></tr>
            <tr><th>Location</th><td>{field("location", `default: ${ds.name}`)} <span className="muted small">where it is on the connection</span></td></tr>
            <tr><th>Reader</th><td>{field("connector", "default: by the file (csv, parquet, delta, excel, json, geojson)")}</td></tr>
            <tr><th>Watermark</th><td>{field("watermark", "a column that only grows (append loads)")}</td></tr>
          </tbody>
        </table>
      </details>
      <div className="row">
        <input placeholder="why (kept in the history)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button className="primary" disabled={!changed || !!a.busy} onClick={save}>Save</button>
        {changed && <button className="secondary" onClick={() => setV(init)}>Undo</button>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
      <Lifecycle d={d} reload={reload} />
    </>
  );
}

function Lifecycle({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const ds = d.dataset, p = d.retire_preview;
  return (
    <div className="card danger">
      <h3>Stop or remove</h3>
      <div className="row">
        {ds.status === "active" && <button className="secondary" disabled={!!a.busy}
          onClick={() => a.run("pause", () => api.datasetAction(ds.name, "pause", reviewer()), reload)}>Pause loading</button>}
        {ds.status === "paused" && <button className="secondary" disabled={!!a.busy}
          onClick={() => a.run("resume", () => api.datasetAction(ds.name, "resume", reviewer()), reload)}>Resume loading</button>}
        <span className="muted small">Paused: its records stay, nothing loads.</span>
      </div>
      {ds.status !== "retired" && (
        <div className="row">
          <button className="secondary" disabled={!!a.busy}
            onClick={() => a.run("keep", () => api.retireDataset(ds.name, false, reviewer()), reload)}>Retire, keep records</button>
          <button className="secondary" disabled={!!a.busy}
            onClick={() => confirm(`Retire ${ds.name} and remove its ${p.records} records?`) &&
              a.run("retract", () => api.retireDataset(ds.name, true, reviewer()), reload)}>Retire and remove records</button>
          <span className="muted small">For a source you've stopped using: {p.records.toLocaleString()} records,
            {" "}{p.entities_only_here.toLocaleString()} entities only it holds.</span>
        </div>
      )}
      <div className="row">
        <button className="secondary" disabled={!!a.busy}
          onClick={() => confirm(`Delete ${ds.name}, its ${p.records} records, runs, checks and (unless shared) mapping?`) &&
            a.run("delete", () => api.deleteDataset(ds.name, reviewer()), () => { location.hash = href("/sources"); })}>
          Delete dataset</button>
        <span className="muted small">Undo adding it (a mistake): everything about it goes; the history keeps the change.</span>
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </div>
  );
}

function MappingTab({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const current = d.mappings.find((m) => m.current) ?? d.mappings[0];
  const [review, setReview] = useState<MappingVersion | null>(null);
  if (!current) return <p className="muted small">No mapping.</p>;
  const drafts = d.mappings.filter((m) => m.status === "draft");
  return (
    <>
      {drafts.length > 0 && !review && (
        <p className="notice small">{drafts.length} draft{drafts.length === 1 ? "" : "s"} waiting:{" "}
          {drafts.map((m) => <button key={m.id} className="link" onClick={() => setReview(m)}>review version {m.id}</button>)}</p>
      )}
      {review && <ReviewVersion v={review} current={current} reload={reload} close={() => setReview(null)} />}
      <h3>Edit <span className="muted small">changes become a draft; you see what it would do before approving it</span></h3>
      <MappingEditor key={current.id} yaml={current.yaml} dataset={d.dataset.name}
        onSave={(text, note) => api.proposeMapping(text, reviewer(), note).then((v) => { reload(); setReview({ ...v, current: false }); })} />
      <h3>Versions</h3>
      <table className="list small">
        <thead><tr><th>#</th><th>Mapping</th><th>Status</th><th>By</th><th>Note</th><th>When</th><th /></tr></thead>
        <tbody>
          {d.mappings.map((m) => (
            <tr key={m.id}>
              <td>{m.id}</td><td><code>{m.source}/{m.record_type}</code></td>
              <td>{m.current ? <span className="badge ok">in use</span> : m.status === "retired" ? <span className="muted">discarded</span> : m.status}</td>
              <td>{m.author ?? m.approved_by}</td><td className="muted">{m.note}</td><td className="muted">{when(m.created_at)}</td>
              <td>{!m.current && m.status !== "retired" && <button className="link" onClick={() => setReview(m)}>
                {m.status === "draft" ? "review" : "compare / roll back"}</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/** A version against the one in use: the change line by line, what it would do to the register, then approve
 * (a draft) or roll back to it (an older version). */
function ReviewVersion({ v, current, reload, close }: { v: MappingVersion; current: MappingVersion; reload: () => void;
                                                         close: () => void }) {
  const impact = useAsync<ImpactResult>(() => api.impact({ version_id: v.id }), [v.id]);
  const a = useAction();
  const draft = v.status === "draft";
  return (
    <div className="card inset">
      <h3>Version {v.id} against version {current.id} (in use) <button className="link small" onClick={close}>close</button></h3>
      <Diff from={current.yaml} to={v.yaml} />
      <h3>What it would do</h3>
      {impact.data ? <Impact r={impact.data} /> : <Status loading={impact.loading} error={impact.error} />}
      <div className="row">
        <button className="primary" disabled={!!a.busy || !impact.data}
          onClick={() => a.run("go", () => (draft ? api.approveMapping(v.id, reviewer()) : api.rollbackMapping(v.id, reviewer())),
            () => { close(); reload(); })}>
          {draft ? "Approve: use this version" : "Roll back to this version"}</button>
        {draft && <button className="secondary" disabled={!!a.busy}
          onClick={() => a.run("discard", () => api.discardMapping(v.id, reviewer()), () => { close(); reload(); })}>Discard draft</button>}
        <span className="muted small">It takes effect on the next load and rebuild.</span>
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </div>
  );
}

function Impact({ r }: { r: ImpactResult }) {
  const line = (label: string, xs: Json[]) =>
    xs.length > 0 && <li><span className="muted">{label}:</span> {xs.slice(0, 5).map((x) => cell(x)).join(" · ")}</li>;
  const none = !r.entities.added && !r.entities.removed && !r.links.added && !r.links.removed && !r.fields.changed;
  return (
    <div className="small">
      <p>
        Over its {r.records.toLocaleString()} stored records{r.sampled ? " (a sample)" : ""}:{" "}
        {none ? "nothing changes." : <>entities +{r.entities.added} / −{r.entities.removed}, links +{r.links.added} /
          −{r.links.removed}, {r.fields.changed} field values change, <strong>{r.golden_flips.count} chosen values would flip</strong>.</>}
      </p>
      <ul>{line("entities", r.entities.examples)}{line("links", r.links.examples)}{line("fields", r.fields.examples)}
        {line("chosen values", r.golden_flips.examples)}</ul>
      {r.errors && <p className="error">{r.errors.length} records failed: {r.errors[0].error}</p>}
    </div>
  );
}

function Runs({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const [done, setDone] = useState<SourceLoad | null>(null);
  return (
    <>
      <div className="row">
        <button className="primary" disabled={!!a.busy} onClick={() => a.run("run", () => api.runDataset(d.dataset.name), (r) => { setDone(r); reload(); })}>
          {a.busy === "run" ? "Loading…" : "Load now"}</button>
        <button className="secondary" disabled={!!a.busy} title="read everything again, even if unchanged"
          onClick={() => a.run("full", () => api.runDataset(d.dataset.name, true), (r) => { setDone(r); reload(); })}>Full reload</button>
        {done && <Loaded r={done} />}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
      <table className="list small">
        <thead><tr><th>#</th><th>When</th><th>Trigger</th><th>Status</th><th>Read</th><th>New</th><th>Changed</th><th>Same</th><th>Removed</th><th>s</th><th /></tr></thead>
        <tbody>
          {d.runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td><td className="muted">{when(r.finished_at ?? r.started_at)}</td><td>{r.trigger}</td>
              <td><span className={`badge ${r.status === "done" ? "ok" : ""}`}>{r.status}</span></td>
              <td>{r.rows_read?.toLocaleString()}</td><td>{r.inserted}</td><td>{r.updated}</td><td>{r.unchanged}</td><td>{r.removed}</td>
              <td className="muted">{r.seconds}</td><td className="muted">{r.error ?? (r.detail ? cell(r.detail) : "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.runs.length === 0 && <p className="muted small">Not loaded yet.</p>}
    </>
  );
}

function Quality({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const [kind, setKind] = useState("not_null");
  const [column, setColumn] = useState("");
  const [params, setParams] = useState("{}");
  const [blocking, setBlocking] = useState(false);
  const p = d.profiles[0];
  const add = () => {
    let parsed: Json;
    try {
      parsed = JSON.parse(params || "{}");
    } catch {
      return a.run("add", () => Promise.reject(new Error("params must be JSON, e.g. {\"max_pct\": 5}")));
    }
    return a.run("add", () => api.addCheck(d.dataset.name, { kind, column: column || null, params: parsed, blocking, by: reviewer() }), reload);
  };
  return (
    <>
      {d.suggested_checks.length > 0 && (
        <div className="card inset">
          <h3>Suggested from its profile</h3>
          <ul className="small plain">
            {d.suggested_checks.map((s, i) => (
              <li key={i}>
                <button className="secondary" disabled={!!a.busy}
                  onClick={() => a.run(`s${i}`, () => api.addCheck(d.dataset.name, { kind: s.kind, column: s.column, params: s.params,
                    blocking: s.blocking, by: reviewer() }), reload)}>Add</button>{" "}
                <strong>{s.kind}</strong>{s.column && <> on <code>{s.column}</code></>} {JSON.stringify(s.params) !== "{}" &&
                  <code className="muted">{JSON.stringify(s.params)}</code>}{s.blocking && " (holds the load)"}
                <span className="muted"> · {s.why}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <h3>Checks <span className="muted small">duplicate keys always hold a load</span></h3>
      {d.checks.length > 0 ? (
        <table className="list small">
          <thead><tr><th>Check</th><th>Column</th><th>Params</th><th>Holds the load</th><th>Last</th><th>Value</th><th /></tr></thead>
          <tbody>
            {d.checks.map((c) => (
              <tr key={c.id}>
                <td>{c.kind}</td><td><code>{c.column_name}</code></td><td className="muted">{JSON.stringify(c.params)}</td>
                <td>{c.blocking ? "yes" : ""}</td>
                <td>{c.last_ok === null ? <span className="muted">not run</span> :
                  <span className={`badge ${c.last_ok ? "ok" : ""}`}>{c.last_ok ? "pass" : "fail"}</span>}</td>
                <td>{c.last_value ?? ""}</td>
                <td><button className="link" onClick={() => a.run("rm", () => api.removeCheck(c.id, reviewer()), reload)}>remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted small">None yet.</p>}
      <details>
        <summary className="small">Add a check by hand</summary>
        <div className="row">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            {["not_null", "range", "accepted", "row_count", "freshness", "coverage", "key_unique"].map((k) => <option key={k}>{k}</option>)}
          </select>
          <input placeholder="column" value={column} onChange={(e) => setColumn(e.target.value)} />
          <input className="mono" placeholder='params, e.g. {"min": 0, "max": 100}' value={params} onChange={(e) => setParams(e.target.value)} />
          <label className="small"><input type="checkbox" checked={blocking} onChange={(e) => setBlocking(e.target.checked)} /> holds the load</label>
          <button className="primary" disabled={!!a.busy} onClick={add}>Add</button>
        </div>
      </details>
      {a.error && <p className="error small">{a.error}</p>}
      <h3>Profile {p && <span className="muted small">{p.rows.toLocaleString()} rows{p.sampled ? " (sampled)" : ""}, {when(p.taken_at)}
        {p.key && ` · key: ${p.key.duplicates} repeated, ${p.key.empty} empty`}</span>}</h3>
      {p?.drift?.length ? <p className="small">Since the profile before: {p.drift.map((x) => `${x.column} ${x.change}`).join("; ")}</p> : null}
      {p ? (
        <div className="scroll-x">
          <table className="list small">
            <thead><tr><th>Column</th><th>Type</th><th>Filled</th><th>Distinct</th><th>Top values</th><th>Samples</th></tr></thead>
            <tbody>
              {Object.entries(p.columns).map(([k, c]) => (
                <tr key={k}><td><code>{k}</code></td><td>{c.type}</td><td>{c.fill_pct}%</td><td>{c.distinct}</td>
                  <td className="muted">{c.top.map(([val, n]) => `${val} (${n})`).join(", ")}</td><td className="muted">{c.samples.join(", ")}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted small">No profile yet: one is kept with every load.</p>}
    </>
  );
}

function Matching({ d }: { d: DatasetDetail }) {
  const s = d.matching[0]?.stats as Record<string, any> | undefined;  // eslint-disable-line @typescript-eslint/no-explicit-any
  if (!s) return <p className="muted small">No rebuild since this source loaded.</p>;
  const cov = s.coverage ?? {};
  return (
    <div className="small">
      <p>
        <code>{d.dataset.source}</code> after the rebuild of {when(d.matching[0].built_at)}: {s.records?.toLocaleString()} records
        in {s.entities?.toLocaleString()} entities; {s.shared_pct}% join an entity another source also has; it supplies {s.golden_wins ?? 0} chosen
        values; its links resolve {cov.pct ?? "–"}% ({cov.resolved ?? 0} of {cov.total ?? 0}).
      </p>
      <p>
        How its records matched: {Object.entries(s.by_method ?? {}).map(([k, v]) => `${k} ${v}`).join(", ") || "–"}. Open reviews:{" "}
        {Object.entries(s.open_reviews ?? {}).map(([k, v]) => <a key={k} href={href("/review", { kind: k })}>{k} {String(v)} </a>)}
        {Object.keys(s.open_reviews ?? {}).length === 0 && "none"}
      </p>
      {cov.by_rel && (
        <table className="list">
          <thead><tr><th>Link</th><th>Resolved</th><th>Of</th><th>%</th></tr></thead>
          <tbody>
            {Object.entries(cov.by_rel as Record<string, [number, number]>).map(([rel, [ok, tot]]) => (
              <tr key={rel}><td>{rel}</td><td>{ok}</td><td>{tot}</td><td>{tot ? Math.round((100 * ok) / tot) : "–"}</td></tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">Which source wins a field: <a href={href("/sources", { view: "rules" })}>Rules</a>.</p>
    </div>
  );
}

function History({ d }: { d: DatasetDetail }) {
  return (
    <table className="list small">
      <thead><tr><th>When</th><th>Who</th><th>What</th><th>Was</th><th>Now</th><th>Why</th></tr></thead>
      <tbody>
        {d.history.map((h) => (
          <tr key={h.id}><td className="muted">{when(h.at)}</td><td>{h.by}</td><td>{h.action}</td>
            <td className="muted">{cell(h.old)}</td><td>{cell(h.new)}</td><td className="muted">{h.reason}</td></tr>
        ))}
      </tbody>
    </table>
  );
}
