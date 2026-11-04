import { useState } from "react";
import {
  api, type CatalogSystem, type DatasetDetail, type ImpactResult, type Json, type SourceHealth, type SourceLoad,
  type SourcePreview,
} from "../api";
import { Status, href, notifyChanged, useAsync } from "../lib";

const reviewer = () => {
  try {
    return localStorage.getItem("fuzer.reviewer") || "anonymous";
  } catch {
    return "anonymous";
  }
};

const STATE: Record<string, string> = {
  ok: "ok", unchanged: "ok", new: "key", "not loaded": "key", changed: "updating", stale: "", held: "", failed: "",
  unmapped: "",
};
const TABS = ["connection", "mapping", "runs", "profile", "checks", "matching", "history"] as const;
type Tab = (typeof TABS)[number];

const cell = (v: unknown) =>
  v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v);
const when = (t: string | null | undefined) => t?.slice(0, 16).replace("T", " ") ?? "";

/** A button's async action with its busy and error state. */
function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async <T,>(name: string, fn: () => Promise<T>, after?: (r: T) => void) => {
    setBusy(name);
    setError(null);
    try {
      const r = await fn();
      after?.(r);
      notifyChanged();
      return r;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return { busy, error, run };
}

function HealthBadge({ h }: { h: SourceHealth | null | undefined }) {
  if (!h) return null;
  return (
    <span className={`badge ${STATE[h.state] ?? ""}`} title={h.problems.join(", ") || "healthy"}>
      {h.state}
    </span>
  );
}

export function SourcesPage({ query }: { query: URLSearchParams }) {
  const view = query.get("view") ?? "datasets";
  const open = query.get("name");
  return (
    <section>
      <h1>Sources</h1>
      <p className="muted">
        Every system of record, how Fuzer reaches it, and each dataset it delivers: where it is, how it loads, its
        mapping versions, runs, profile, checks and how well its records match. Changes are kept with who made them and
        why. A new table in the data folder can be mapped and loaded from <em>New tables</em>.
      </p>
      <div className="tabs">
        {[["datasets", "Datasets"], ["tables", "New tables"], ["rules", "Rules"]].map(([k, label]) => (
          <a key={k} className={view === k ? "on" : ""} href={href("/sources", { view: k })}>{label}</a>
        ))}
      </div>
      {view === "datasets" && <Datasets open={open} tab={(query.get("tab") as Tab) || "connection"} />}
      {view === "tables" && <NewTables open={open} />}
      {view === "rules" && <Rules />}
    </section>
  );
}

// ---- datasets by system -----------------------------------------------------------------------------

function Datasets({ open, tab }: { open: string | null; tab: Tab }) {
  const cat = useAsync(() => api.catalog(), []);
  return (
    <>
      <Status loading={cat.loading && !cat.data} error={cat.error} empty={cat.data?.systems.length === 0}>
        No sources yet.
      </Status>
      {open && <DatasetPanel key={open} name={open} tab={tab} onChanged={cat.reload} />}
      {cat.data && cat.data.systems.map((s) => <SystemCard key={s.id} s={s} open={open} />)}
      {cat.data && <AddSystem onDone={cat.reload} />}
    </>
  );
}

function SystemCard({ s, open }: { s: CatalogSystem; open: string | null }) {
  return (
    <div className="card">
      <h2>
        <code>{s.name}</code> <span className="muted small">{s.kind ?? ""}{s.owner ? ` · owner ${s.owner}` : ""}
          {s.rank !== null ? ` · rank ${s.rank}` : ""}{!s.enabled ? " · disabled" : ""}</span>
      </h2>
      {s.datasets.length > 0 ? (
        <table className="results">
          <thead>
            <tr><th>Dataset</th><th>Health</th><th>Status</th><th>Load</th><th>Connection</th><th>Rows</th><th>Loaded</th></tr>
          </thead>
          <tbody>
            {s.datasets.map((d) => (
              <tr key={d.name} className={open === d.name ? "selected" : ""}
                onClick={() => (location.hash = href("/sources", { view: "datasets", name: d.name }))}>
                <td><code>{d.name}</code> <span className="muted small">{d.record_type ?? d.reader}</span></td>
                <td><HealthBadge h={d.health} /></td>
                <td>{d.status}</td>
                <td className="muted small">{d.load_mode}</td>
                <td className="muted small">{d.connection}</td>
                <td>{d.rows?.toLocaleString() ?? ""}</td>
                <td className="muted small">{when(d.loaded_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <p className="muted small">No datasets.</p>}
    </div>
  );
}

function AddSystem({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("");
  const a = useAction();
  return (
    <details className="card">
      <summary>Add a system of record</summary>
      <div className="row">
        <input placeholder="name (what its records say as source)" value={name} onChange={(e) => setName(e.target.value)} />
        <input placeholder="kind (erp, historian, ...)" value={kind} onChange={(e) => setKind(e.target.value)} />
        <button className="primary" disabled={!name || !!a.busy}
          onClick={() => a.run("add", () => api.addSystem(name, kind ? { kind } : {}, reviewer()), () => { setName(""); onDone(); })}>
          Add
        </button>
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </details>
  );
}

function DatasetPanel({ name, tab, onChanged }: { name: string; tab: Tab; onChanged: () => void }) {
  const d = useAsync(() => api.dataset(name), [name]);
  const reload = () => { d.reload(); onChanged(); };
  if (!d.data) return <Status loading={d.loading} error={d.error} />;
  const x = d.data;
  return (
    <div className="card">
      <h2>
        <code>{name}</code> <span className="muted small">{x.dataset.source} · {x.dataset.record_type ?? x.dataset.reader}
          · {x.dataset.status}</span> <HealthBadge h={x.health} />{" "}
        <a className="small" href={href("/sources", { view: "datasets" })}>close</a>
      </h2>
      {x.health && x.health.problems.length > 0 && <p className="error small">{x.health.problems.join(", ")}</p>}
      <div className="tabs">
        {TABS.map((t) => (
          <a key={t} className={tab === t ? "on" : ""} href={href("/sources", { view: "datasets", name, tab: t })}>
            {t[0].toUpperCase() + t.slice(1)}
          </a>
        ))}
      </div>
      {tab === "connection" && <ConnectionTab d={x} reload={reload} />}
      {tab === "mapping" && <MappingTab d={x} reload={reload} />}
      {tab === "runs" && <RunsTab d={x} reload={reload} />}
      {tab === "profile" && <ProfileTab d={x} reload={reload} />}
      {tab === "checks" && <ChecksTab d={x} reload={reload} />}
      {tab === "matching" && <MatchingTab d={x} />}
      {tab === "history" && <HistoryTab d={x} />}
    </div>
  );
}

const FIELDS: [string, string][] = [
  ["load_mode", "Load mode"], ["drift_policy", "Schema drift"], ["schedule", "Schedule (cron)"],
  ["expected_hours", "Expected every (hours)"], ["owner", "Owner"], ["key", "Key columns"], ["location", "Location"],
  ["connector", "Connector"], ["watermark", "Watermark"], ["notes", "Notes"],
];

function ConnectionTab({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const ds = d.dataset as unknown as Record<string, unknown>;
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(FIELDS.map(([k]) => [k, Array.isArray(ds[k]) ? (ds[k] as string[]).join(", ") : ds[k] == null ? "" : String(ds[k])])));
  const [reason, setReason] = useState("");
  const [test, setTest] = useState<Awaited<ReturnType<typeof api.testConnection>> | null>(null);
  const [found, setFound] = useState<string[] | null>(null);
  const a = useAction();
  const save = () => {
    const values: Json = {};
    for (const [k] of FIELDS) {
      const before = Array.isArray(ds[k]) ? (ds[k] as string[]).join(", ") : ds[k] == null ? "" : String(ds[k]);
      if (vals[k] === before) continue;
      values[k] = vals[k] === "" ? null : k === "key" ? vals[k].split(",").map((s) => s.trim()).filter(Boolean)
        : k === "expected_hours" ? Number(vals[k]) : vals[k];
    }
    return a.run("save", () => api.editDataset(d.dataset.name, values, reviewer(), reason || undefined), reload);
  };
  const c = d.connection;
  return (
    <>
      <h3>Connection</h3>
      {c ? (
        <p className="small">
          <code>{c.name}</code> ({c.type}) on {c.system}{" "}
          <span className="muted">{JSON.stringify(c.config)}</span>{" "}
          {Object.entries(d.secrets).map(([k, ok]) => (
            <span key={k} className={`badge ${ok ? "ok" : ""}`} title="whether the secret's reference finds a value">
              {k}: {ok ? "resolves" : "missing"}
            </span>
          ))}
        </p>
      ) : <p className="small muted">No connection.</p>}
      {c && (
        <div className="row">
          <button className="secondary" disabled={!!a.busy} onClick={() => a.run("test", () => api.testConnection(c.id), setTest)}>
            {a.busy === "test" ? "Testing…" : "Test connection"}
          </button>
          <button className="secondary" disabled={!!a.busy} onClick={() => a.run("discover", () => api.discover(c.id), (r) => setFound(r.uncovered))}>
            Discover
          </button>
        </div>
      )}
      {test && (
        <ul className="small">
          {test.steps.map((s) => (
            <li key={s.step}>
              <span className={`badge ${s.ok ? "ok" : ""}`}>{s.ok ? "ok" : "failed"}</span> {s.step}{" "}
              <span className="muted">{s.ms} ms · {cell(s.detail)}</span>
            </li>
          ))}
        </ul>
      )}
      {found && (
        <p className="small">
          {found.length ? <>Not covered by any dataset: {found.map((n) => (
            <a key={n} href={href("/sources", { view: "tables", name: n })}><code>{n}</code> </a>))}</> : "Everything it holds is covered."}
        </p>
      )}
      <h3>How it loads</h3>
      <table className="list small">
        <tbody>
          {FIELDS.map(([k, label]) => (
            <tr key={k}>
              <th>{label}</th>
              <td>
                {k === "load_mode" || k === "drift_policy" ? (
                  <select value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })}>
                    {(k === "load_mode" ? ["snapshot", "incremental", "append"] : ["hold_breaking", "hold_any", "ignore"])
                      .map((o) => <option key={o}>{o}</option>)}
                  </select>
                ) : (
                  <input value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} />
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <input placeholder="why (kept in the history)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button className="primary" disabled={!!a.busy} onClick={save}>Save</button>
        {d.dataset.status === "active" && (
          <button className="secondary" disabled={!!a.busy}
            onClick={() => a.run("pause", () => api.datasetAction(d.dataset.name, "pause", reviewer(), reason || undefined), reload)}>
            Pause
          </button>
        )}
        {d.dataset.status === "paused" && (
          <button className="secondary" disabled={!!a.busy}
            onClick={() => a.run("resume", () => api.datasetAction(d.dataset.name, "resume", reviewer(), reason || undefined), reload)}>
            Resume
          </button>
        )}
      </div>
      {d.dataset.status !== "retired" && <RetireBox d={d} reload={reload} />}
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function RetireBox({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const p = d.retire_preview;
  return (
    <details>
      <summary className="small">Retire this dataset</summary>
      <p className="small">
        It stops loading for good. Keep its {p.records.toLocaleString()} records as history, or retract them: the next
        rebuild drops what only it said ({p.entities_only_here.toLocaleString()} entities hold nothing else).
      </p>
      <div className="row">
        <button className="secondary" disabled={!!a.busy}
          onClick={() => a.run("keep", () => api.retireDataset(d.dataset.name, false, reviewer()), reload)}>Retire, keep records</button>
        <button className="secondary" disabled={!!a.busy}
          onClick={() => confirm(`Remove ${p.records} records of ${d.dataset.name}?`) &&
            a.run("retract", () => api.retireDataset(d.dataset.name, true, reviewer()), reload)}>Retire and retract</button>
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </details>
  );
}

function MappingTab({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const current = d.mappings.find((m) => m.current) ?? d.mappings[0];
  const [text, setText] = useState(current?.yaml ?? "");
  const [note, setNote] = useState("");
  const [impact, setImpact] = useState<ImpactResult | null>(null);
  const a = useAction();
  if (!current) return <p className="muted small">No mapping.</p>;
  return (
    <>
      <h3>Versions</h3>
      <table className="list small">
        <thead><tr><th>#</th><th>Mapping</th><th>Status</th><th>By</th><th>Note</th><th>When</th><th /></tr></thead>
        <tbody>
          {d.mappings.map((m) => (
            <tr key={m.id}>
              <td>{m.id}</td>
              <td><code>{m.source}/{m.record_type}</code></td>
              <td>{m.current ? <span className="badge ok">in use</span> : m.status}</td>
              <td>{m.author ?? m.approved_by}</td>
              <td className="muted">{m.note}</td>
              <td className="muted">{when(m.created_at)}</td>
              <td>
                <button className="link" onClick={() => setText(m.yaml)}>open</button>{" "}
                {m.status === "draft" && (
                  <button className="link" disabled={!!a.busy} onClick={() => a.run("approve", () => api.approveMapping(m.id, reviewer()), reload)}>approve</button>
                )}{" "}
                {m.status === "approved" && !m.current && (
                  <button className="link" disabled={!!a.busy} onClick={() => a.run("rollback", () => api.rollbackMapping(m.id, reviewer()), reload)}>roll back to this</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Edit <span className="muted small">a new draft; preview what it would change, then approve it</span></h3>
      <textarea className="yaml mono" rows={Math.min(28, text.split("\n").length + 1)} value={text}
        onChange={(e) => { setText(e.target.value); setImpact(null); }} spellCheck={false} />
      <div className="row">
        <button className="secondary" disabled={!!a.busy} onClick={() => a.run("impact", () => api.impact({ yaml: text }), setImpact)}>
          {a.busy === "impact" ? "Comparing…" : "Preview impact"}
        </button>
        <input placeholder="note" value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="primary" disabled={!!a.busy || text === current.yaml}
          onClick={() => a.run("draft", () => api.proposeMapping(text, reviewer(), note || undefined), reload)}>Save as draft</button>
        <button className="secondary" disabled={!!a.busy} onClick={() => a.run("rematch", () => api.rematch())}
          title="rebuild the register with the mappings and rules in use">Rebuild</button>
      </div>
      {impact && <Impact r={impact} />}
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function Impact({ r }: { r: ImpactResult }) {
  const line = (label: string, xs: Json[]) =>
    xs.length > 0 && <li><span className="muted">{label}:</span> {xs.slice(0, 5).map((x) => cell(x)).join(" · ")}</li>;
  return (
    <div className="small">
      <p>
        Over {r.records.toLocaleString()} records{r.sampled ? " (a sample)" : ""}: entities +{r.entities.added} / −{r.entities.removed},
        links +{r.links.added} / −{r.links.removed}, {r.fields.changed} field values changed,{" "}
        <strong>{r.golden_flips.count} golden values would flip</strong>.
      </p>
      <ul>
        {line("entities", r.entities.examples)}
        {line("links", r.links.examples)}
        {line("fields", r.fields.examples)}
        {line("golden", r.golden_flips.examples)}
      </ul>
      {r.errors && <p className="error">{r.errors.length} records failed: {r.errors[0].error}</p>}
    </div>
  );
}

function RunsTab({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const [done, setDone] = useState<SourceLoad | null>(null);
  return (
    <>
      <div className="row">
        <button className="primary" disabled={!!a.busy} onClick={() => a.run("run", () => api.runDataset(d.dataset.name), (r) => { setDone(r); reload(); })}>
          {a.busy === "run" ? "Loading…" : "Load now"}
        </button>
        <button className="secondary" disabled={!!a.busy} onClick={() => a.run("full", () => api.runDataset(d.dataset.name, true), (r) => { setDone(r); reload(); })}>
          Full reload
        </button>
        {done && <Loaded r={done} />}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
      <table className="list small">
        <thead><tr><th>#</th><th>When</th><th>Trigger</th><th>Status</th><th>Read</th><th>New</th><th>Changed</th><th>Same</th><th>Removed</th><th>s</th><th /></tr></thead>
        <tbody>
          {d.runs.map((r) => (
            <tr key={r.id}>
              <td>{r.id}</td>
              <td className="muted">{when(r.finished_at ?? r.started_at)}</td>
              <td>{r.trigger}</td>
              <td><span className={`badge ${r.status === "done" ? "ok" : ""}`}>{r.status}</span></td>
              <td>{r.rows_read?.toLocaleString()}</td>
              <td>{r.inserted}</td><td>{r.updated}</td><td>{r.unchanged}</td><td>{r.removed}</td>
              <td className="muted">{r.seconds}</td>
              <td className="muted">{r.error ?? (r.detail ? cell(r.detail) : "")}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.runs.length === 0 && <p className="muted small">Not loaded yet.</p>}
    </>
  );
}

function ProfileTab({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const p = d.profiles[0];
  return (
    <>
      <div className="row">
        <button className="secondary" disabled={!!a.busy} onClick={() => a.run("profile", () => api.profileDataset(d.dataset.name), reload)}>
          {a.busy === "profile" ? "Profiling…" : "Profile now"}
        </button>
        {p && <span className="muted small">{p.rows.toLocaleString()} rows{p.sampled ? " (sampled)" : ""}, {when(p.taken_at)}
          {p.key && ` · key: ${p.key.duplicates} duplicated, ${p.key.empty} empty`}</span>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
      {p?.drift?.length ? (
        <p className="small">Since the profile before: {p.drift.map((x) => `${x.column} ${x.change}`).join("; ")}</p>
      ) : null}
      {p ? (
        <div className="scroll-x">
          <table className="list small">
            <thead><tr><th>Column</th><th>Type</th><th>Filled</th><th>Distinct</th><th>Top values</th><th>Samples</th></tr></thead>
            <tbody>
              {Object.entries(p.columns).map(([k, c]) => (
                <tr key={k}>
                  <td><code>{k}</code></td><td>{c.type}</td><td>{c.fill_pct}%</td><td>{c.distinct}</td>
                  <td className="muted">{c.top.map(([v, n]) => `${v} (${n})`).join(", ")}</td>
                  <td className="muted">{c.samples.join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="muted small">No profile yet: one is kept with every load.</p>}
    </>
  );
}

function ChecksTab({ d, reload }: { d: DatasetDetail; reload: () => void }) {
  const a = useAction();
  const [kind, setKind] = useState("not_null");
  const [column, setColumn] = useState("");
  const [params, setParams] = useState("{}");
  const [blocking, setBlocking] = useState(false);
  const add = () => {
    let p: Json;
    try {
      p = JSON.parse(params || "{}");
    } catch {
      return a.run("add", () => Promise.reject(new Error("params must be JSON, e.g. {\"max_pct\": 5}")));
    }
    return a.run("add", () => api.addCheck(d.dataset.name, { kind, column: column || null, params: p, blocking, by: reviewer() }), reload);
  };
  return (
    <>
      <table className="list small">
        <thead><tr><th>Check</th><th>Column</th><th>Params</th><th>Blocking</th><th>Last</th><th>Value</th><th /></tr></thead>
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
      {d.checks.length === 0 && <p className="muted small">No checks. Duplicate keys always hold a load.</p>}
      <h3>Add a check</h3>
      <div className="row">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {["key_unique", "not_null", "range", "accepted", "row_count", "freshness", "coverage"].map((k) => <option key={k}>{k}</option>)}
        </select>
        <input placeholder="column" value={column} onChange={(e) => setColumn(e.target.value)} />
        <input className="mono" placeholder='params, e.g. {"min": 0, "max": 100}' value={params} onChange={(e) => setParams(e.target.value)} />
        <label className="small"><input type="checkbox" checked={blocking} onChange={(e) => setBlocking(e.target.checked)} /> holds the load</label>
        <button className="primary" disabled={!!a.busy} onClick={add}>Add</button>
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function MatchingTab({ d }: { d: DatasetDetail }) {
  const s = d.matching[0]?.stats as Record<string, any> | undefined;  // eslint-disable-line @typescript-eslint/no-explicit-any
  if (!s) return <p className="muted small">No rebuild since this source loaded.</p>;
  const cov = s.coverage ?? {};
  return (
    <div className="small">
      <p>
        <code>{d.dataset.source}</code> after the rebuild of {when(d.matching[0].built_at)}: {s.records?.toLocaleString()} records
        in {s.entities?.toLocaleString()} entities; {s.shared_pct}% join an entity another source also has; wins {s.golden_wins ?? 0} golden
        fields; links resolve {cov.pct ?? "–"}% ({cov.resolved ?? 0} of {cov.total ?? 0}).
      </p>
      <p>
        How its records matched: {Object.entries(s.by_method ?? {}).map(([k, v]) => `${k} ${v}`).join(", ") || "–"}. Open reviews:{" "}
        {Object.entries(s.open_reviews ?? {}).map(([k, v]) => (
          <a key={k} href={href("/review", { kind: k })}>{k} {String(v)} </a>))}
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
      <p className="muted">Its survivorship rank and field rules are under <a href={href("/sources", { view: "rules" })}>Rules</a>.</p>
    </div>
  );
}

function HistoryTab({ d }: { d: DatasetDetail }) {
  return (
    <table className="list small">
      <thead><tr><th>When</th><th>Who</th><th>What</th><th>Was</th><th>Now</th><th>Why</th></tr></thead>
      <tbody>
        {d.history.map((h) => (
          <tr key={h.id}>
            <td className="muted">{when(h.at)}</td><td>{h.by}</td><td>{h.action}</td>
            <td className="muted">{cell(h.old)}</td><td>{cell(h.new)}</td><td className="muted">{h.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ---- rules ----------------------------------------------------------------------------------------

function Rules() {
  const r = useAsync(() => api.rules(), []);
  const a = useAction();
  const [field, setField] = useState("");
  const [prio, setPrio] = useState("review, sap");
  const [conflict, setConflict] = useState(false);
  if (!r.data) return <Status loading={r.loading} error={r.error} />;
  return (
    <>
      <div className="card">
        <h2>Default priority <span className="muted small">the systems' rank: lower wins; a reviewer's decision always comes first</span></h2>
        <table className="list small">
          <tbody>
            {r.data.ranks.map((s) => (
              <tr key={s.name}>
                <td><code>{s.name}</code></td>
                <td>
                  <input type="number" defaultValue={s.rank ?? ""} style={{ width: "6em" }}
                    onBlur={(e) => {
                      const v = e.target.value === "" ? null : Number(e.target.value);
                      if (v !== s.rank) a.run("rank", () => api.setRank(s.name, v, reviewer()), r.reload);
                    }} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card">
        <h2>Field and match rules</h2>
        <table className="list small">
          <thead><tr><th>Scope</th><th>Name</th><th>Rule</th><th>By</th><th /></tr></thead>
          <tbody>
            {r.data.rules.map((x) => (
              <tr key={x.id}>
                <td>{x.scope}</td><td><code>{x.name}</code></td><td className="mono">{JSON.stringify(x.config)}</td>
                <td className="muted">{x.updated_by}</td>
                <td><button className="link" onClick={() => a.run("del", () => api.deleteRule(x.scope, x.name, reviewer()), r.reload)}>remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3>Set a field rule</h3>
        <div className="row">
          <input placeholder="field (manufacturer)" value={field} onChange={(e) => setField(e.target.value)} />
          <input placeholder="priority: review, sap, maximo" value={prio} onChange={(e) => setPrio(e.target.value)} />
          <label className="small"><input type="checkbox" checked={conflict} onChange={(e) => setConflict(e.target.checked)} /> review conflicts</label>
          <button className="primary" disabled={!field || !!a.busy}
            onClick={() => a.run("set", () => api.setRule("field", field, {
              priority: prio.split(",").map((s) => s.trim()).filter(Boolean), review_on_conflict: conflict,
            }, reviewer()), r.reload)}>Save</button>
          <button className="secondary" disabled={!!a.busy} onClick={() => a.run("rematch", () => api.rematch())}>Rebuild</button>
        </div>
        {a.error && <p className="error small">{a.error}</p>}
      </div>
    </>
  );
}

// ---- tables no dataset covers yet -----------------------------------------------------------------

function NewTables({ open }: { open: string | null }) {
  const list = useAsync(() => api.sources(), []);
  const fresh = list.data?.sources.filter((s) => s.state === "unmapped") ?? [];
  return (
    <>
      <p className="muted small">
        Tables in the data folder{list.data ? ` (${list.data.data_dir})` : ""} that no dataset covers. Open one to see a
        sample and a suggested mapping; save it to add the dataset and load it.
      </p>
      <Status loading={list.loading && !list.data} error={list.error} empty={!!list.data && fresh.length === 0}>
        Every table in the data folder is a dataset.
      </Status>
      {fresh.length > 0 && (
        <table className="results">
          <thead><tr><th>Table</th><th>Connector</th></tr></thead>
          <tbody>
            {fresh.map((s) => (
              <tr key={s.dataset} onClick={() => (location.hash = href("/sources", { view: "tables", name: s.dataset }))}>
                <td><code>{s.dataset}</code></td><td>{s.connector}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <Preview key={open} name={open} />}
    </>
  );
}

function Preview({ name }: { name: string }) {
  const p = useAsync(() => api.source(name), [name]);
  if (!p.data) return <Status loading={p.loading} error={p.error} />;
  const d: SourcePreview = p.data;
  const cols = d.sample.length ? Object.keys(d.sample[0]) : (d.schema ?? []).map(([c]) => c);
  return (
    <div className="card">
      <h2><code>{d.dataset}</code> <span className="muted small">{d.connector} · {d.path}</span></h2>
      <h3>Sample</h3>
      <div className="scroll-x">
        <table className="list small">
          <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {d.sample.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{cell(r[c])}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
      {d.suggested ? <AddSource name={d.dataset} yaml={d.suggested.yaml} /> : (
        <p className="small">It has a dataset: <a href={href("/sources", { view: "datasets", name: d.dataset })}>open it</a>.</p>
      )}
    </div>
  );
}

function Loaded({ r }: { r: SourceLoad }) {
  if (r.held.length) return <p className="error small">Held: its columns changed or a check failed. See the Review page.</p>;
  if (r.unchanged.length) return <p className="small muted">Unchanged since the last load: nothing to do.</p>;
  return (
    <p className="small">
      Loaded {r.records.toLocaleString()} records ({r.inserted} new, {r.updated} changed, {r.removed} removed). They
      become entities on the next rebuild.
    </p>
  );
}

/** A table with no mapping: the draft from rules (or Claude), edited here, saved and loaded in one step. */
function AddSource({ name, yaml }: { name: string; yaml: string }) {
  const [text, setText] = useState(yaml);
  const [note, setNote] = useState<string | null>(null);
  const [done, setDone] = useState<SourceLoad | null>(null);
  const a = useAction();
  const ask = () => a.run("claude", () => api.suggestSource(name, true), (s) => {
    setText(s.yaml);
    const named = Object.values(s.columns).filter((c) => c.by === "claude").length;
    setNote(named ? `Claude named ${named} column${named > 1 ? "s" : ""} the rules couldn't.` : "Nothing left for Claude to name.");
  });
  return (
    <>
      <h3>Add this table <span className="muted small">a draft from rules: check what each column means</span></h3>
      <textarea className="yaml mono" rows={Math.min(24, text.split("\n").length + 1)} value={text}
        onChange={(e) => setText(e.target.value)} spellCheck={false} disabled={!!done} />
      <div className="row">
        <button className="primary" onClick={() => a.run("load", () => api.loadSource(name, text, reviewer()), setDone)}
          disabled={!!a.busy || !!done}>{a.busy === "load" ? "Loading…" : "Save mapping and load"}</button>
        <button className="secondary" onClick={ask} disabled={!!a.busy || !!done}
          title="Claude names the columns the rules could only call code or other (costs money; cached)">
          {a.busy === "claude" ? "Asking Claude…" : "Ask Claude"}
        </button>
        {text !== yaml && !done && <button className="secondary" onClick={() => setText(yaml)}>Undo edits</button>}
      </div>
      {note && <p className="small muted">{note}</p>}
      {a.error && <p className="error small">{a.error}</p>}
      {done && <><Loaded r={done} /> <a className="small" href={href("/sources", { view: "datasets", name })}>Open the dataset</a></>}
    </>
  );
}
