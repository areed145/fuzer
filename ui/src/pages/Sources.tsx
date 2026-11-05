import { useState, type ReactNode } from "react";
import {
  api, type AddTableResult, type Catalog, type CatalogConnection, type CatalogSystem, type DatasetDetail,
  type ImpactResult, type Json, type SourceHealth, type SourceLoad, type TablePreview,
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
  if (view === "add") {
    return (
      <section>
        <h1>Add a source</h1>
        <p className="muted">
          Five steps: the system the data comes from, where it is, which table, what its rows mean, and how it should
          load. Nothing is loaded until the last step. <a href={href("/sources")}>Back to sources</a>
        </p>
        <AddWizard system={query.get("system")} connection={query.get("connection")} table={query.get("table")} />
      </section>
    );
  }
  return (
    <section>
      <div className="row spread">
        <h1>Sources</h1>
        <a className="button primary" href={href("/sources", { view: "add" })}>+ Add a source</a>
      </div>
      <p className="muted">
        Every system of record, how Fuzer reaches it, and each dataset it delivers. Open a dataset for its connection,
        mapping versions, runs, profile, checks, how well it matches and its history. Every change is kept with who
        made it and why.
      </p>
      <div className="tabs">
        {[["datasets", "Datasets"], ["systems", "Systems & connections"], ["rules", "Rules"]].map(([k, label]) => (
          <a key={k} className={view === k ? "on" : ""} href={href("/sources", { view: k })}>{label}</a>
        ))}
      </div>
      {view === "datasets" && <Datasets open={query.get("name")} tab={(query.get("tab") as Tab) || "connection"} />}
      {view === "systems" && <Systems />}
      {view === "rules" && <Rules />}
    </section>
  );
}

// ---- datasets -----------------------------------------------------------------------------------------

function Datasets({ open, tab }: { open: string | null; tab: Tab }) {
  const cat = useAsync(() => api.catalog(), []);
  const [filter, setFilter] = useState("");
  const [problems, setProblems] = useState(false);
  const rows = (cat.data?.systems ?? []).flatMap((s) => s.datasets.map((d) => ({ s, d })));
  const count = (st: string) => rows.filter(({ d }) => d.health?.state === st).length;
  const shown = rows.filter(({ s, d }) =>
    (!filter || `${s.name} ${d.name} ${d.record_type ?? ""}`.toLowerCase().includes(filter.toLowerCase())) &&
    (!problems || (d.health?.problems.length ?? 0) > 0));
  return (
    <>
      <Status loading={cat.loading && !cat.data} error={cat.error} empty={!!cat.data && rows.length === 0}>
        No datasets yet: <a href={href("/sources", { view: "add" })}>add a source</a>.
      </Status>
      {open && <DatasetPanel key={open} name={open} tab={tab} onChanged={cat.reload} />}
      {rows.length > 0 && (
        <>
          <div className="row">
            <span className="small">
              {rows.length} datasets in {cat.data!.systems.length} systems:{" "}
              {["ok", "not loaded", "stale", "held", "failed", "checks failing", "profile drift"].map((st) =>
                count(st) ? <span key={st} className={`badge ${STATE[st] ?? ""}`}>{count(st)} {st}</span> : null)}
            </span>
            <input placeholder="filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
            <label className="small"><input type="checkbox" checked={problems} onChange={(e) => setProblems(e.target.checked)} /> problems only</label>
          </div>
          <table className="results">
            <thead>
              <tr><th>System</th><th>Dataset</th><th>Health</th><th>Status</th><th>Load</th><th>Connection</th><th>Rows</th><th>Loaded</th></tr>
            </thead>
            <tbody>
              {shown.map(({ s, d }) => (
                <tr key={d.name} className={open === d.name ? "selected" : ""}
                  onClick={() => (location.hash = href("/sources", { view: "datasets", name: d.name }))}>
                  <td><code>{s.name}</code></td>
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
        </>
      )}
    </>
  );
}

// ---- systems and connections --------------------------------------------------------------------------

const SYSTEM_FIELDS: [keyof CatalogSystem & string, string, string][] = [
  ["kind", "Kind", "erp, historian, planning, ..."], ["owner", "Owner", "who answers for it; alerts go to them"],
  ["steward", "Steward", "who looks after its data"], ["date_format", "Date format", "e.g. %d/%m/%Y; empty: ISO"],
  ["decimal", "Decimal mark", ", for a decimal comma"], ["timezone", "Timezone", "e.g. Europe/London"],
  ["encoding", "Encoding", "CSV text, e.g. latin-1"], ["rank", "Survivorship rank", "lower wins a field"],
  ["notes", "Notes", "reliability, known issues"],
];

function Systems() {
  const cat = useAsync(() => api.catalog(), []);
  return (
    <>
      <p className="muted small">
        A system is where records come from (its name is what records say as their source). A connection is how Fuzer
        reaches it: a folder of exports (empty path: the project's data folder), PI Web API or ADX. Secrets are
        references such as <code>env:PI_PASSWORD</code>, never the value.
      </p>
      <NewSystem onDone={cat.reload} />
      <Status loading={cat.loading && !cat.data} error={cat.error} />
      {cat.data?.systems.map((s) => <SystemCard key={s.id} s={s} cat={cat.data!} reload={cat.reload} />)}
    </>
  );
}

function NewSystem({ onDone }: { onDone: (name: string) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("");
  const [owner, setOwner] = useState("");
  const a = useAction();
  return (
    <div className="row">
      <input placeholder="new system name, e.g. p6" value={name} onChange={(e) => setName(e.target.value.trim())} />
      <input placeholder="kind" value={kind} onChange={(e) => setKind(e.target.value)} />
      <input placeholder="owner" value={owner} onChange={(e) => setOwner(e.target.value)} />
      <button className="secondary" disabled={!name || !!a.busy}
        onClick={() => a.run("add", () => api.addSystem(name, { ...(kind ? { kind } : {}), ...(owner ? { owner } : {}) }, reviewer()),
          () => { onDone(name); setName(""); })}>Add system</button>
      {a.error && <span className="error small">{a.error}</span>}
    </div>
  );
}

function SystemCard({ s, cat, reload }: { s: CatalogSystem; cat: Catalog; reload: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(SYSTEM_FIELDS.map(([k]) => [k, s[k] == null ? "" : String(s[k])])));
  const [adding, setAdding] = useState(false);
  const a = useAction();
  const changed = SYSTEM_FIELDS.filter(([k]) => vals[k] !== (s[k] == null ? "" : String(s[k])));
  return (
    <details className="card">
      <summary>
        <code>{s.name}</code> <span className="muted small">{s.kind ?? ""} · {s.datasets.length} datasets ·{" "}
          {s.connections.length} connection{s.connections.length === 1 ? "" : "s"}{s.owner ? ` · owner ${s.owner}` : ""}</span>
      </summary>
      <table className="list small">
        <tbody>
          {SYSTEM_FIELDS.map(([k, label, hint]) => (
            <tr key={k}><th>{label}</th><td><input placeholder={hint} value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></td></tr>
          ))}
        </tbody>
      </table>
      <button className="primary" disabled={!changed.length || !!a.busy}
        onClick={() => a.run("save", () => api.editSystem(s.name, Object.fromEntries(changed.map(([k]) =>
          [k, vals[k] === "" ? null : k === "rank" ? Number(vals[k]) : vals[k]])), reviewer()), reload)}>Save</button>
      {a.error && <p className="error small">{a.error}</p>}
      <h3>Connections</h3>
      {s.connections.map((c) => <ConnectionRow key={c.id} c={c} system={s.name} />)}
      {adding ? (
        <ConnectionForm system={s.name} types={cat.types.connections} onDone={() => { setAdding(false); reload(); }} />
      ) : <button className="secondary" onClick={() => setAdding(true)}>+ Add a connection</button>}
    </details>
  );
}

function ConnectionRow({ c, system }: { c: CatalogConnection; system: string }) {
  const a = useAction();
  const [test, setTest] = useState(c.last_test);
  const [found, setFound] = useState<{ table: string; dataset: string | null }[] | null>(null);
  return (
    <div className="small conn">
      <code>{c.name}</code> {c.type} <span className="muted">{JSON.stringify(c.config)}</span>{" "}
      {Object.entries(c.secrets).map(([k, v]) => (
        <span key={k} className={`badge ${v.resolves ? "ok" : ""}`} title={v.ref}>{k}: {v.resolves ? "resolves" : "missing"}</span>
      ))}{" "}
      {test && <span className={`badge ${test.ok ? "ok" : ""}`}>{test.ok ? "test ok" : "test failed"}</span>}{" "}
      <button className="link" disabled={!!a.busy} onClick={() => a.run("test", () => api.testConnection(c.id), setTest)}>
        {a.busy === "test" ? "testing…" : "test"}</button>
      <button className="link" disabled={!!a.busy} onClick={() => a.run("tables", () => api.connectionTables(c.id), (r) => setFound(r.tables))}>
        what it holds</button>
      {test && !test.ok && <TestSteps steps={test.steps} />}
      {found && <TableList tables={found} system={system} connection={c.id} />}
      {a.error && <span className="error"> {a.error}</span>}
    </div>
  );
}

function TestSteps({ steps }: { steps: { step: string; ok: boolean; ms: number; detail: unknown }[] }) {
  return (
    <ul className="small">
      {steps.map((s) => (
        <li key={s.step}><span className={`badge ${s.ok ? "ok" : ""}`}>{s.ok ? "ok" : "failed"}</span> {s.step}{" "}
          <span className="muted">{s.ms} ms · {cell(s.detail)}</span></li>
      ))}
    </ul>
  );
}

function TableList({ tables, system, connection }: { tables: { table: string; dataset: string | null }[]; system: string;
                                                      connection: number }) {
  if (!tables.length) return <p className="muted small">It holds no tables Fuzer can read.</p>;
  return (
    <ul className="small">
      {tables.map((t) => (
        <li key={t.table}>
          <code>{t.table}</code>{" "}
          {t.dataset ? <a className="muted" href={href("/sources", { view: "datasets", name: t.dataset })}>loaded as {t.dataset}</a>
            : <a href={href("/sources", { view: "add", system, connection: String(connection), table: t.table })}>add it</a>}
        </li>
      ))}
    </ul>
  );
}

/** A new connection: its type's config fields and secret references, then a test. */
function ConnectionForm({ system, types, onDone }: { system: string; types: Catalog["types"]["connections"];
                                                      onDone: (c: Json) => void }) {
  const [type, setType] = useState("folder");
  const [name, setName] = useState("");
  const [cfg, setCfg] = useState<Record<string, string>>({});
  const [sec, setSec] = useState<Record<string, string>>({});
  const a = useAction();
  const t = types[type] ?? { config: {}, secrets: [] };
  const save = () => {
    const config: Json = {};
    for (const [k, dflt] of Object.entries(t.config)) {
      const v = cfg[k];
      if (v === undefined || v === "") continue;
      config[k] = typeof dflt === "boolean" ? v === "true" : v;
    }
    const secrets = Object.fromEntries(Object.entries(sec).filter(([, v]) => v));
    return a.run("save", () => api.addConnection(system, { name: name || type, type, config, secrets, by: reviewer() }), onDone);
  };
  return (
    <div className="card inset">
      <div className="row">
        <select value={type} onChange={(e) => { setType(e.target.value); setCfg({}); setSec({}); }}>
          {Object.keys(types).map((k) => <option key={k} value={k}>{k === "folder" ? "folder of exports / lake" : k}</option>)}
        </select>
        <input placeholder={`name (default ${type})`} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <table className="list small">
        <tbody>
          {Object.entries(t.config).map(([k, dflt]) => (
            <tr key={k}>
              <th>{k}{dflt === null ? " *" : ""}</th>
              <td><input value={cfg[k] ?? ""} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })}
                placeholder={k === "path" ? "absolute, or relative to the project's data folder; empty: the data folder itself"
                  : dflt === null ? "required" : `default ${String(dflt) || "none"}`} /></td>
            </tr>
          ))}
          {t.secrets.map((k) => (
            <tr key={k}><th>{k} (secret)</th>
              <td><input value={sec[k] ?? ""} onChange={(e) => setSec({ ...sec, [k]: e.target.value })} placeholder="env:VARIABLE_NAME" /></td></tr>
          ))}
        </tbody>
      </table>
      <button className="primary" disabled={!!a.busy} onClick={save}>{a.busy ? "Adding…" : "Add connection"}</button>
      {a.error && <p className="error small">{a.error}</p>}
    </div>
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
          {found.length ? <>Not a dataset yet: {found.map((n) => (
            <a key={n} href={href("/sources", { view: "add", system: d.dataset.source, connection: String(c!.id), table: n })}>
              <code>{n}</code> </a>))}</> : "Everything it holds is a dataset."}
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

// ---- add a source ------------------------------------------------------------------------------------

const LOAD_HELP: Record<string, string> = {
  snapshot: "each delivery is the whole table: records no longer in it are removed",
  incremental: "deliveries add and change records; nothing is removed",
  append: "only rows at or after the last watermark value are read (lake tables)",
};

function Step({ n, title, done, open, summary, onEdit, children }: {
  n: number; title: string; done: boolean; open: boolean; summary?: ReactNode; onEdit?: () => void;
  children?: ReactNode;
}) {
  return (
    <div className={`card step${open ? " current" : ""}`}>
      <h2>
        <span className={`badge ${done ? "ok" : "key"}`}>{done ? "✓" : n}</span> {title}{" "}
        {done && !open && <span className="muted small">{summary}</span>}{" "}
        {done && !open && onEdit && <button className="link small" onClick={onEdit}>change</button>}
      </h2>
      {open && children}
    </div>
  );
}

function AddWizard({ system, connection, table }: { system: string | null; connection: string | null; table: string | null }) {
  const cat = useAsync(() => api.catalog(), []);
  const [step, setStep] = useState(table ? 4 : connection ? 3 : system ? 2 : 1);
  const [sys, setSys] = useState<string | null>(system);
  const [conn, setConn] = useState<number | null>(connection ? Number(connection) : null);
  const [tbl, setTbl] = useState<string | null>(table);
  const [mapping, setMapping] = useState<{ yaml: string; dataset: string } | null>(null);
  const [result, setResult] = useState<AddTableResult | null>(null);
  if (!cat.data) return <Status loading={cat.loading} error={cat.error} />;
  const s = cat.data.systems.find((x) => x.name === sys);
  const c = s?.connections.find((x) => x.id === conn);
  return (
    <>
      <Step n={1} title="Which system is it from?" done={!!s} open={step === 1} summary={<code>{sys}</code>} onEdit={() => setStep(1)}>
        <PickSystem cat={cat.data} value={sys} onPick={(name) => { setSys(name); setConn(null); setTbl(null); cat.reload(); setStep(2); }} />
      </Step>
      <Step n={2} title="Where is the data?" done={!!c} open={step === 2 && !!s}
        summary={c && <><code>{c.name}</code> ({c.type}) {JSON.stringify(c.config)}</>} onEdit={() => setStep(2)}>
        {s && <PickConnection s={s} types={cat.data.types.connections}
          onPick={(id) => { setConn(id); setTbl(null); cat.reload(); setStep(3); }} />}
      </Step>
      <Step n={3} title="Which table?" done={!!tbl && step > 3} open={step === 3 && !!c} summary={<code>{tbl}</code>}
        onEdit={() => setStep(3)}>
        {c && <PickTable connection={c.id} onPick={(t) => { setTbl(t); setMapping(null); setStep(4); }} />}
      </Step>
      <Step n={4} title="What do its rows mean?" done={!!mapping && step > 4} open={step === 4 && !!tbl && conn !== null}
        summary={mapping && <>dataset <code>{mapping.dataset}</code></>} onEdit={() => setStep(4)}>
        {tbl && conn !== null && <MapTable connection={conn} table={tbl} initial={mapping}
          onDone={(m) => { setMapping(m); setStep(5); }} />}
      </Step>
      <Step n={5} title="How should it load?" done={!!result} open={step === 5 && !!mapping} summary="">
        {mapping && conn !== null && tbl && <LoadSettings connection={conn} table={tbl} yaml={mapping.yaml} owner={s?.owner ?? ""}
          onDone={(r) => { setResult(r); setStep(6); }} />}
      </Step>
      {result && <AddResult r={result} />}
    </>
  );
}

function PickSystem({ cat, value, onPick }: { cat: Catalog; value: string | null; onPick: (name: string) => void }) {
  const [pick, setPick] = useState(value ?? "");
  return (
    <>
      <p className="muted small">The system of record the data comes from. Its name is what the records will say as their source.</p>
      <div className="row">
        <select value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">choose a system…</option>
          {cat.systems.map((s) => <option key={s.id} value={s.name}>{s.name}{s.kind ? ` (${s.kind})` : ""}</option>)}
        </select>
        <button className="primary" disabled={!pick} onClick={() => onPick(pick)}>Continue</button>
      </div>
      <p className="small">Or a new one:</p>
      <NewSystem onDone={onPick} />
    </>
  );
}

function PickConnection({ s, types, onPick }: { s: CatalogSystem; types: Catalog["types"]["connections"]; onPick: (id: number) => void }) {
  const [adding, setAdding] = useState(false);
  const a = useAction();
  const [tested, setTested] = useState<Record<number, Awaited<ReturnType<typeof api.testConnection>>>>({});
  return (
    <>
      <p className="muted small">
        How Fuzer reaches <code>{s.name}</code>. A folder connection with no path reads the project's data folder;
        give it a path for anywhere else (a lake, an export drop).
      </p>
      {s.connections.map((c) => (
        <div key={c.id} className="row">
          <button className="secondary" onClick={() => onPick(c.id)}>Use <code>{c.name}</code></button>
          <span className="small">{c.type} <span className="muted">{JSON.stringify(c.config)}</span></span>
          <button className="link small" disabled={!!a.busy}
            onClick={() => a.run(`t${c.id}`, () => api.testConnection(c.id), (r) => setTested({ ...tested, [c.id]: r }))}>test</button>
          {tested[c.id] && <span className={`badge ${tested[c.id].ok ? "ok" : ""}`}>{tested[c.id].ok ? "ok" : "failed"}</span>}
          {tested[c.id] && !tested[c.id].ok && <TestSteps steps={tested[c.id].steps} />}
        </div>
      ))}
      {adding ? (
        <ConnectionForm system={s.name} types={types} onDone={(c) => onPick(Number(c.id))} />
      ) : <button className="secondary" onClick={() => setAdding(true)}>+ A new connection</button>}
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function PickTable({ connection, onPick }: { connection: number; onPick: (t: string) => void }) {
  const t = useAsync(() => api.connectionTables(connection), [connection]);
  const test = useAsync(() => api.testConnection(connection), [connection]);
  if (!t.data) return <Status loading={t.loading} error={t.error} />;
  const fresh = t.data.tables.filter((x) => !x.dataset);
  return (
    <>
      {test.data && !test.data.ok && <><p className="error small">The connection test failed:</p><TestSteps steps={test.data.steps} /></>}
      {fresh.length === 0 && <p className="muted small">Everything this connection holds is already a dataset.</p>}
      <div className="row wrap">
        {fresh.map((x) => <button key={x.table} className="secondary" onClick={() => onPick(x.table)}><code>{x.table}</code></button>)}
      </div>
      {t.data.tables.length > fresh.length && (
        <p className="muted small">Already loaded: {t.data.tables.filter((x) => x.dataset).map((x) => x.table).join(", ")}</p>
      )}
    </>
  );
}

function MapTable({ connection, table, initial, onDone }: { connection: number; table: string;
  initial: { yaml: string; dataset: string } | null; onDone: (m: { yaml: string; dataset: string }) => void }) {
  const [opts, setOpts] = useState<{ claude?: boolean; dataset?: string; record_type?: string }>({});
  const p = useAsync(() => api.tablePreview(connection, table, opts), [connection, table, JSON.stringify(opts)]);
  const [text, setText] = useState<string | null>(initial?.yaml ?? null);
  const [dataset, setDataset] = useState("");
  const [rtype, setRtype] = useState("");
  const a = useAction();
  if (!p.data) return <Status loading={p.loading} error={p.error} />;
  const d: TablePreview = p.data;
  const yaml = text ?? d.suggested.yaml;
  const cols = d.sample.length ? Object.keys(d.sample[0]) : (d.schema ?? []).map(([x]) => x);
  const datasetOf = (y: string) => /^dataset:\s*(\S+)/m.exec(y)?.[1] ?? d.suggested.dataset;
  return (
    <>
      <p className="small muted">{d.connector} · {d.path}</p>
      {d.date_hint && (
        <p className="small notice">
          Its dates look like <code>{d.date_hint.example}</code>. Set <code>{d.system}</code>'s date format to{" "}
          <code>{d.date_hint.date_format}</code> so they are read as dates?{" "}
          <button className="secondary" disabled={!!a.busy}
            onClick={() => a.run("fmt", () => api.editSystem(d.system, { date_format: d.date_hint!.date_format }, reviewer(),
              "dates in the export's local format"), () => p.reload())}>Set it</button>
        </p>
      )}
      <h3>Sample</h3>
      <div className="scroll-x">
        <table className="list small">
          <thead><tr>{cols.map((x) => <th key={x}>{x} <span className="muted">{d.suggested.columns[x]?.meaning}</span></th>)}</tr></thead>
          <tbody>{d.sample.map((r, i) => <tr key={i}>{cols.map((x) => <td key={x}>{cell(r[x])}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <h3>Mapping <span className="muted small">suggested from the columns{d.suggested.key ? `; key ${d.suggested.key}` : "; no key found: name one"}</span></h3>
      <div className="row">
        <input placeholder={`dataset name (${d.suggested.dataset})`} value={dataset} onChange={(e) => setDataset(e.target.value)} />
        <input placeholder={`record type (${d.suggested.record_type})`} value={rtype} onChange={(e) => setRtype(e.target.value)} />
        <button className="secondary" onClick={() => { setText(null); setOpts({ ...opts, dataset: dataset || undefined, record_type: rtype || undefined }); }}>
          Suggest again</button>
        <button className="secondary" title="Claude names the columns rules could only call code or other (costs money; cached)"
          onClick={() => { setText(null); setOpts({ ...opts, claude: true }); }}>Ask Claude</button>
      </div>
      <textarea className="yaml mono" rows={Math.min(26, yaml.split("\n").length + 1)} value={yaml}
        onChange={(e) => setText(e.target.value)} spellCheck={false} />
      <div className="row">
        <button className="primary" onClick={() => onDone({ yaml, dataset: datasetOf(yaml) })}>Continue</button>
        {text !== null && <button className="secondary" onClick={() => setText(null)}>Undo edits</button>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function LoadSettings({ connection, table, yaml, owner, onDone }: { connection: number; table: string; yaml: string;
  owner: string; onDone: (r: AddTableResult) => void }) {
  const [mode, setMode] = useState("snapshot");
  const [schedule, setSchedule] = useState("");
  const [hours, setHours] = useState("");
  const [who, setWho] = useState(owner);
  const a = useAction();
  return (
    <>
      <table className="list small">
        <tbody>
          <tr><th>Load mode</th><td>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {Object.keys(LOAD_HELP).map((m) => <option key={m}>{m}</option>)}
            </select> <span className="muted">{LOAD_HELP[mode]}</span></td></tr>
          <tr><th>Schedule</th><td>
            <select value={schedule} onChange={(e) => setSchedule(e.target.value)}>
              <option value="">with the daily refresh (02:00) and when its file changes</option>
              <option value="0 * * * *">hourly</option>
              <option value="0 6 * * *">daily at 06:00</option>
              <option value="0 6 * * 1">weekly, Monday 06:00</option>
            </select></td></tr>
          <tr><th>Expect a delivery every</th><td>
            <input value={hours} onChange={(e) => setHours(e.target.value)} placeholder="hours (empty: no expectation)" style={{ width: "14em" }} />
            <span className="muted"> stale after that, and its owner is told</span></td></tr>
          <tr><th>Owner</th><td><input value={who} onChange={(e) => setWho(e.target.value)} placeholder="who answers for it" /></td></tr>
        </tbody>
      </table>
      <button className="primary" disabled={!!a.busy}
        onClick={() => a.run("add", () => api.addTable(connection, table, {
          yaml, by: reviewer(), values: { load_mode: mode, schedule: schedule || null, owner: who || null,
                                          expected_hours: hours ? Number(hours) : null } }), onDone)}>
        {a.busy ? "Saving and loading…" : "Save and load"}
      </button>
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function AddResult({ r }: { r: AddTableResult }) {
  const a = useAction();
  const held = (r.load?.held.length ?? 0) > 0;
  const checks = (r.run?.detail as { checks?: { kind: string; value: number; detail?: { examples?: string[] } }[] } | null)?.checks;
  const open = (tab: string) => href("/sources", { view: "datasets", name: r.dataset, tab });
  return (
    <div className={`card ${held ? "warn" : ""}`}>
      <h2>{held ? "Saved, but the first load is held" : "Added and loaded"}</h2>
      {!held && r.load && (
        <p>
          <code>{r.dataset}</code>: {r.load.records.toLocaleString()} records ({r.load.inserted} new). They become entities on
          the next rebuild.{" "}
          <button className="primary" disabled={!!a.busy} onClick={() => a.run("rebuild", () => api.rematch())}>
            {a.busy ? "Rebuilding…" : "Rebuild now"}</button>
        </p>
      )}
      {held && (
        <>
          <p>The dataset <code>{r.dataset}</code> and its mapping are saved; nothing was loaded because:</p>
          <ul>
            {(checks ?? []).map((c, i) => (
              <li key={i}>
                {c.kind === "duplicate keys"
                  ? <>the export repeats {c.value} key{c.value === 1 ? "" : "s"} ({(c.detail?.examples ?? []).join(", ")}). Fix the
                    export, or pick another key on the dataset's Connection tab, then load again from Runs.</>
                  : <>the {c.kind} check failed ({c.value}). See its Checks tab.</>}
              </li>
            ))}
            {!checks && <li>its columns changed: answer the schema item on the <a href={href("/review", { kind: "schema" })}>Review</a> page.</li>}
          </ul>
        </>
      )}
      <p className="small">
        <a href={open("connection")}>Open the dataset</a> · <a href={open("runs")}>Runs</a> · <a href={open("profile")}>Profile</a>
        {" "}· <a href={open("checks")}>Add checks</a> · <a href={href("/sources", { view: "add" })}>Add another source</a>
      </p>
      {a.error && <p className="error small">{a.error}</p>}
    </div>
  );
}

function Loaded({ r }: { r: SourceLoad }) {
  if (r.held.length) return <p className="error small">Held: its columns changed or a check failed. See the Runs and Review pages.</p>;
  if (r.unchanged.length) return <p className="small muted">Unchanged since the last load: nothing to do.</p>;
  return (
    <p className="small">
      Loaded {r.records.toLocaleString()} records ({r.inserted} new, {r.updated} changed, {r.removed} removed). They
      become entities on the next rebuild.
    </p>
  );
}
