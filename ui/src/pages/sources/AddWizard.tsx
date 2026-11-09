import { ActionStatus, ConfirmAction, Button, Input, Table, Card as UICard, Select , Badge as UIBadge} from "../../ui";
/** Add a source (#225): system -> connection -> table -> what its rows mean -> how it loads -> save and load. */
import { useState, type ReactNode } from "react";
import { api, type AddTableResult, type Catalog, type CatalogSystem, type SourceLoad, type TablePreview } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { LOAD_HELP, Loaded, TestSteps, cell, reviewer, useAction } from "./common";
import { MappingEditor } from "./MappingEditor";
import { ConnectionForm, NewSystem } from "./Systems";

function Step({ n, title, done, open, summary, onEdit, children }: {
  n: number; title: string; done: boolean; open: boolean; summary?: ReactNode; onEdit?: () => void;
  children?: ReactNode;
}) {
  return (
    <UICard className={` step${open ? " current" : ""}`}>
      <h2>
        <UIBadge className={`badge ${done ? "ok" : "key"}`}>{done ? "✓" : n}</UIBadge> {title}{" "}
        {done && !open && <span className="muted small">{summary}</span>}{" "}
        {done && !open && onEdit && <Button variant="link" className="small" onClick={onEdit}>change</Button>}
      </h2>
      {open && children}
    </UICard>
  );
}

export function AddWizard({ system, connection, table }: { system: string | null; connection: string | null; table: string | null }) {
  const cat = useAsync(() => api.catalog(), []);
  const [step, setStep] = useState(table ? 4 : connection ? 3 : system ? 2 : 1);
  const [sys, setSys] = useState<string | null>(system);
  const [conn, setConn] = useState<number | null>(connection ? Number(connection) : null);
  const [tbl, setTbl] = useState<string | null>(table);
  const [mapping, setMapping] = useState<{ yaml: string; dataset: string } | null>(null);
  const [result, setResult] = useState<AddTableResult | null>(null);
  const [suggest, setSuggest] = useState<{ name: string; path: string } | null>(null);
  if (!cat.data) return <Status loading={cat.loading} error={cat.error} />;
  const s = cat.data.systems.find((x) => x.name === sys);
  const c = s?.connections.find((x) => x.id === conn);
  return (
    <>
      <Step n={1} title="Which system is it from?" done={!!s} open={step === 1} summary={<code>{sys}</code>} onEdit={() => setStep(1)}>
        <PickSystem cat={cat.data} value={sys} onPick={(name) => { setSys(name); setConn(null); setTbl(null); cat.reload(); setStep(2); }} />
      </Step>
      <Step n={2} title="Where is the data?" done={!!c} open={step === 2 && !!s}
        summary={c && <><code>{c.name}</code> ({c.type}) {c.display}</>} onEdit={() => setStep(2)}>
        {s && <PickConnection key={suggest?.path ?? ""} s={s} types={cat.data.types.connections} dataDir={cat.data.data_dir}
          suggest={suggest} onPick={(id) => { setConn(id); setTbl(null); setSuggest(null); cat.reload(); setStep(3); }} />}
      </Step>
      <Step n={3} title="Which table?" done={!!tbl && step > 3} open={step === 3 && !!c} summary={<code>{tbl}</code>}
        onEdit={() => setStep(3)}>
        {c && <PickTable connection={c.id} folder={c.type === "folder"} onPick={(t) => { setTbl(t); setMapping(null); setStep(4); }}
          onOtherConnection={(sub) => { setSuggest(sub ?? null); setStep(2); }} />}
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
        <Select value={pick} onChange={(e) => setPick(e.target.value)}>
          <option value="">choose a system…</option>
          {cat.systems.map((s) => <option key={s.id} value={s.name}>{s.name}{s.kind ? ` (${s.kind})` : ""}</option>)}
        </Select>
        <Button variant="primary" disabled={!pick} onClick={() => onPick(pick)}>Continue</Button>
      </div>
      <p className="small">Or a new one:</p>
      <NewSystem onDone={onPick} />
    </>
  );
}

function PickConnection({ s, types, dataDir, onPick, suggest }: { s: CatalogSystem; types: Catalog["types"]["connections"];
                                                         dataDir: string; onPick: (id: number) => void;
                                                         suggest?: { name: string; path: string } | null }) {
  const hasDataFolder = s.connections.some((c) => c.type === "folder" && !(c.config as { path?: string }).path);
  const [adding, setAdding] = useState(!!suggest);
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
          <Button variant="secondary" onClick={() => onPick(c.id)}>Use <code>{c.name}</code></Button>
          <span className="small">{c.type} <span className="muted">{c.display}</span></span>
          <Button variant="link" className="small" disabled={!!a.busy}
            onClick={() => a.run(`t${c.id}`, () => api.testConnection(c.id), (r) => setTested({ ...tested, [c.id]: r }))}>test</Button>
          {tested[c.id] && <UIBadge className={`badge ${tested[c.id].ok ? "ok" : ""}`}>{tested[c.id].ok ? "ok" : "failed"}</UIBadge>}
          {tested[c.id] && !tested[c.id].ok && <TestSteps steps={tested[c.id].steps} />}
        </div>
      ))}
      {!hasDataFolder && (
        <div className="row">
          <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("files", () => api.addConnection(s.name,
            { name: "files", type: "folder", config: {}, secrets: {}, by: reviewer() }), (c) => onPick(Number(c.id)))}>
            Use the project data folder</Button>
          <span className="small muted">{dataDir}</span>
        </div>
      )}
      {adding ? (
        <ConnectionForm system={s.name} types={types} initial={suggest ?? undefined} onDone={(c) => onPick(Number(c.id))} />
      ) : <Button variant="secondary" onClick={() => setAdding(true)}>+ A new connection</Button>}
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function PickTable({ connection, folder, onPick, onOtherConnection }: {
  connection: number; folder: boolean; onPick: (t: string) => void;
  onOtherConnection: (sub?: { name: string; path: string }) => void;
}) {
  const t = useAsync(() => api.connectionTables(connection), [connection]);
  const test = useAsync(() => api.testConnection(connection), [connection]);
  if (!t.data) return <Status loading={t.loading} error={t.error} />;
  const fresh = t.data.tables.filter((x) => !x.dataset);
  return (
    <>
      {test.data && !test.data.ok && <><p className="error small">The connection test failed:</p><TestSteps steps={test.data.steps} /></>}
      {fresh.length === 0 && (
        // Not a dead end (#412): new tables elsewhere need a connection that points there.
        <div className="small">
          <p className="muted">Everything this connection holds is already a dataset.
            {folder && " A folder connection reads only the tables directly in its folder, not in its subfolders."}</p>
          <Button variant="secondary" size="sm" onClick={() => onOtherConnection()}>
            {folder ? "Add a connection for another folder" : "Choose or add another connection"}</Button>
        </div>
      )}
      {(t.data.subfolders ?? []).length > 0 && (
        <div className="small">
          <p className="muted">Subfolders with tables of their own, each read through its own connection:</p>
          <div className="row wrap">
            {t.data.subfolders!.map((f) => (
              <Button key={f.path} variant="secondary" size="sm" onClick={() => onOtherConnection({ name: f.name, path: f.path })}>
                Add a connection for <code>{f.name}/</code> ({f.tables} {f.tables === 1 ? "table" : "tables"})</Button>
            ))}
          </div>
        </div>
      )}
      <div className="row wrap">
        {fresh.map((x) => <Button key={x.table} variant="secondary" onClick={() => onPick(x.table)}><code>{x.table}</code></Button>)}
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
  const yaml = text ?? initial?.yaml ?? d.suggested.yaml;
  const cols = d.sample.length ? Object.keys(d.sample[0]) : (d.schema ?? []).map(([x]) => x);
  const datasetOf = (y: string) => /^dataset:\s*(\S+)/m.exec(y)?.[1] ?? d.suggested.dataset;
  return (
    <>
      <p className="small muted">{d.connector} · {d.path}</p>
      {d.date_hint && (
        <p className="small notice">
          Its dates look like <code>{d.date_hint.example}</code>. Set <code>{d.system}</code>'s date format to{" "}
          <code>{d.date_hint.date_format}</code> so they are read as dates?{" "}
          <Button variant="secondary" disabled={!!a.busy}
            onClick={() => a.run("fmt", () => api.editSystem(d.system, { date_format: d.date_hint!.date_format }, reviewer(),
              "dates in the export's local format"), () => p.reload())}>Set it</Button>
        </p>
      )}
      <h3>Sample</h3>
      <div className="scroll-x">
        <Table variant="list" density="compact">
          <thead><tr>{cols.map((x) => <th key={x}>{x}{d.suggested.columns[x]?.meaning && (
            // the detected meaning under the column name, not run into it (#413)
            <span className="sample-meaning muted small" title="detected meaning">{d.suggested.columns[x]?.meaning}</span>)}</th>)}</tr></thead>
          <tbody>{d.sample.map((r, i) => <tr key={i}>{cols.map((x) => <td key={x}>{cell(r[x])}</td>)}</tr>)}</tbody>
        </Table>
      </div>
      <h3>Mapping <span className="muted small">suggested from the columns{d.suggested.key ? `; key ${d.suggested.key}` : "; no key found: name one"}</span></h3>
      <div className="row">
        <Input placeholder={`dataset name (${d.suggested.dataset})`} value={dataset} onChange={(e) => setDataset(e.target.value)} />
        <Input placeholder={`record type (${d.suggested.record_type})`} value={rtype} onChange={(e) => setRtype(e.target.value)} />
        <Button variant="secondary" onClick={() => { setText(null); setOpts({ ...opts, dataset: dataset || undefined, record_type: rtype || undefined }); }}>
          Suggest again</Button>
        <Button variant="secondary" title="Claude names the columns rules could only call code or other (costs money; cached)"
          onClick={() => { setText(null); setOpts({ ...opts, claude: true }); }}>Ask Claude</Button>
      </div>
      <MappingEditor key={d.suggested.yaml} yaml={initial?.yaml ?? d.suggested.yaml} columns={cols} samples={d.sample}
        onChange={setText} />
      <div className="row">
        <Button variant="primary" onClick={() => onDone({ yaml, dataset: datasetOf(yaml) })}>Continue</Button>
        {text !== null && <span className="muted small">edited</span>}
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
      <Table variant="list" density="compact">
        <tbody>
          <tr><th>Load mode</th><td>
            <Select value={mode} onChange={(e) => setMode(e.target.value)}>
              {Object.keys(LOAD_HELP).map((m) => <option key={m}>{m}</option>)}
            </Select> <span className="muted">{LOAD_HELP[mode]}</span></td></tr>
          <tr><th>Schedule</th><td>
            <Select value={schedule} onChange={(e) => setSchedule(e.target.value)}>
              <option value="">with the daily refresh (02:00) and when its file changes</option>
              <option value="0 * * * *">hourly</option>
              <option value="0 6 * * *">daily at 06:00</option>
              <option value="0 6 * * 1">weekly, Monday 06:00</option>
            </Select></td></tr>
          <tr><th>Expect a delivery every</th><td>
            <Input width="md" value={hours} onChange={(e) => setHours(e.target.value)} placeholder="hours (empty: no expectation)" />
            <span className="muted"> stale after that, and its owner is told</span></td></tr>
          <tr><th>Owner</th><td><Input value={who} onChange={(e) => setWho(e.target.value)} placeholder="who answers for it" /></td></tr>
        </tbody>
      </Table>
      <ConfirmAction label="Save and load" busy={!!a.busy}
        consequence={`Save this dataset and mapping, then load shared records in ${mode} mode? ${LOAD_HELP[mode]}. Check the mapping and key before confirming. No AI call is required.`}
        onConfirm={() => a.run("add", () => api.addTable(connection, table, {
          yaml, by: reviewer(), values: { load_mode: mode, schedule: schedule || null, owner: who || null,
                                          expected_hours: hours ? Number(hours) : null } }), onDone)} />
      <ActionStatus busy={a.busy ? "Saving mapping and loading shared records; wait for the result…" : null} error={a.error} />
    </>
  );
}

function AddResult({ r }: { r: AddTableResult }) {
  const a = useAction();
  const [buildRequested, setBuildRequested] = useState(false);
  const held = (r.load?.held.length ?? 0) > 0;
  const checks = (r.run?.detail as { checks?: { kind: string; value: number; detail?: { examples?: string[] } }[] } | null)?.checks;
  const open = (tab: string) => href("/sources", { view: "dataset", name: r.dataset, tab });
  return (
    <UICard className={` ${held ? "warn" : ""}`}>
      <h2>{held ? "Saved, but the first load is held" : "Added and loaded"}</h2>
      {!held && r.load && (
        <div>
          <code>{r.dataset}</code>: {r.load.records.toLocaleString()} records ({r.load.inserted} new). They become entities on
          the next rebuild.{" "}
          <ConfirmAction label="Rebuild now" busy={!!a.busy}
            consequence="Rebuild shared facility entities and links from the loaded records? This may take time and change connected evidence."
            onConfirm={() => a.run("rebuild", () => api.rematch(), () => setBuildRequested(true))} />
        </div>
      )}
      {held && (
        <>
          <p>The dataset <code>{r.dataset}</code> and its mapping are saved; nothing was loaded because:</p>
          <ul>
            {(checks ?? []).map((c, i) => (
              <li key={i}>
                {c.kind === "duplicate keys"
                  ? <>the export repeats {c.value} key{c.value === 1 ? "" : "s"} ({(c.detail?.examples ?? []).join(", ")}). Fix the
                    export (or pick another key under the dataset's Settings), then load again.</>
                  : <>the {c.kind} check failed ({c.value}). See the dataset's Quality tab.</>}
              </li>
            ))}
            {!checks && <li>its columns changed: answer the schema item on the <a href={href("/review", { kind: "schema" })}>Review</a> page.</li>}
          </ul>
        </>
      )}
      {held && <LoadAgain name={r.dataset} />}
      <p className="small">
        <a href={open("overview")}>Open the dataset</a> · <a href={open("runs")}>Runs</a> · <a href={open("quality")}>Profile
        and suggested checks</a> · <a href={href("/sources", { view: "add" })}>Add another source</a>
        {" · "}<a href={href("/jobs")}>Scheduler execution details</a>
      </p>
      <ActionStatus busy={a.busy ? "Requesting shared facility rebuild…" : null} error={a.error}>
        {buildRequested && <p>Rebuild requested, not yet confirmed complete. Follow the application's Updating/health
          indicator and <a href={href("/jobs")}>Jobs diagnostics</a>; then inspect the dataset's linked evidence.</p>}
      </ActionStatus>
    </UICard>
  );
}

function LoadAgain({ name }: { name: string }) {
  const a = useAction();
  const [done, setDone] = useState<SourceLoad | null>(null);
  return (
    <div className="row">
      <ConfirmAction label="Load again" busy={!!a.busy}
        consequence={`Retry ${name} as a full shared load? Fix the source/check issue first; this may replace or retract records.`}
        onConfirm={() => a.run("run", () => api.runDataset(name, true), setDone)} />
      <span className="small muted">after fixing it</span>
      {done && !a.busy && !a.error && <Loaded r={done} />}
      <ActionStatus busy={a.busy ? "Retrying source load…" : null} error={a.error} />
    </div>
  );
}
