import { Button, Input, Table, Card as UICard, Select , Badge as UIBadge} from "../../ui";
/** Systems of record and their connections (#225): edit a system, add, test, edit and delete connections, and
 * see what each connection holds. */
import { useState } from "react";
import { api, type Catalog, type CatalogConnection, type CatalogSystem, type Json } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { TestSteps, reviewer, useAction } from "./common";

const SYSTEM_FIELDS: [keyof CatalogSystem & string, string, string][] = [
  ["kind", "Kind", "erp, historian, planning, ..."], ["owner", "Owner", "who answers for it; alerts go to them"],
  ["steward", "Steward", "who looks after its data"], ["date_format", "Date format", "e.g. %d/%m/%Y; empty: ISO"],
  ["decimal", "Decimal mark", ", for a decimal comma"], ["timezone", "Timezone", "e.g. Europe/London"],
  ["encoding", "Encoding", "CSV text, e.g. latin-1"], ["rank", "Survivorship rank", "lower wins a field"],
  ["notes", "Notes", "reliability, known issues"],
];

export function Systems() {
  const cat = useAsync(() => api.catalog(), []);
  return (
    <>
      <p className="muted small">
        A system is where records come from (its name is what records say as their source). A connection is how Fuzer
        reaches it: a folder of exports (empty path: the project's data folder), PI Web API or ADX. Secrets are
        references such as <code>env:PI_PASSWORD</code>, never the value.
        {" "}A deliberate test contacts the service, may read sample rows and saves its result; it does not load data or call AI.
      </p>
      <NewSystem onDone={cat.reload} />
      <Status loading={cat.loading && !cat.data} error={cat.error} />
      {cat.data?.systems.map((s) => <SystemCard key={s.id} s={s} cat={cat.data!} reload={cat.reload} />)}
    </>
  );
}

export function NewSystem({ onDone }: { onDone: (name: string) => void }) {
  const [name, setName] = useState("");
  const [kind, setKind] = useState("");
  const [owner, setOwner] = useState("");
  const a = useAction();
  return (
    <div className="row">
      <Input placeholder="new system name, e.g. p6" value={name} onChange={(e) => setName(e.target.value.trim())} />
      <Input placeholder="kind" value={kind} onChange={(e) => setKind(e.target.value)} />
      <Input placeholder="owner" value={owner} onChange={(e) => setOwner(e.target.value)} />
      <Button variant="secondary" disabled={!name || !!a.busy}
        onClick={() => a.run("add", () => api.addSystem(name, { ...(kind ? { kind } : {}), ...(owner ? { owner } : {}) }, reviewer()),
          () => { onDone(name); setName(""); })}>Add system</Button>
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
      <Table variant="list" density="compact">
        <tbody>
          {SYSTEM_FIELDS.map(([k, label, hint]) => (
            <tr key={k}><th>{label}</th><td><Input placeholder={hint} value={vals[k]} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></td></tr>
          ))}
        </tbody>
      </Table>
      <Button variant="primary" disabled={!changed.length || !!a.busy}
        onClick={() => a.run("save", () => api.editSystem(s.name, Object.fromEntries(changed.map(([k]) =>
          [k, vals[k] === "" ? null : k === "rank" ? Number(vals[k]) : vals[k]])), reviewer()), reload)}>Save</Button>
      {a.error && <p className="error small">{a.error}</p>}
      <h3>Connections</h3>
      {s.connections.map((c) => <ConnectionRow key={c.id} c={c} system={s.name} reload={reload} />)}
      {adding ? (
        <ConnectionForm system={s.name} types={cat.types.connections} onDone={() => { setAdding(false); reload(); }} />
      ) : <Button variant="secondary" onClick={() => setAdding(true)}>+ Add a connection</Button>}
      {s.datasets.length === 0 && (
        <p className="small">
          <Button variant="link" disabled={!!a.busy} onClick={() => confirm(`Delete system ${s.name} and its connections?`) &&
            a.run("delsys", () => api.deleteSystem(s.name, reviewer()), reload)}>Delete this system</Button>
          <span className="muted"> (it has no datasets)</span>
        </p>
      )}
    </details>
  );
}

function ConnectionRow({ c, system, reload }: { c: CatalogConnection; system: string; reload: () => void }) {
  const a = useAction();
  const test = c.last_test;
  const [found, setFound] = useState<{ table: string; dataset: string | null }[] | null>(null);
  const [editing, setEditing] = useState(false);
  const [cfg, setCfg] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(c.config).map(([k, v]) => [k, v == null ? "" : String(v)])));
  return (
    <div className="small conn">
      <code>{c.name}</code> {c.type} <span className="muted">{c.display ?? JSON.stringify(c.config)}</span>{" "}
      {Object.entries(c.secrets).map(([k, v]) => (
        <UIBadge key={k} className={`badge ${v.resolves ? "ok" : ""}`} title={v.ref}>{k}: {v.resolves ? "resolves" : "missing"}</UIBadge>
      ))}{" "}
      {test && <UIBadge className={`badge ${test.ok ? "ok" : ""}`}>{test.ok ? "last test passed" : "last test failed"}</UIBadge>}{" "}
      {c.last_tested_at && <span className="muted">recorded {c.last_tested_at} </span>}
      <Button variant="link" disabled={!!a.busy} onClick={() => a.run("test", () => api.testConnection(c.id))}>
        {a.busy === "test" ? "testing…" : "test"}</Button>
      <Button variant="link" disabled={!!a.busy} onClick={() => a.run("tables", () => api.connectionTables(c.id), (r) => setFound(r.tables))}>
        what it holds</Button>
      <Button variant="link" onClick={() => setEditing(!editing)}>edit</Button>
      <Button variant="link" disabled={!!a.busy} onClick={() => confirm(`Delete connection ${c.name}?`) &&
        a.run("del", () => api.deleteConnection(c.id, reviewer()), reload)}>delete</Button>
      {editing && (
        <div className="row wrap">
          {Object.keys({ ...(c.type === "folder" ? { path: "" } : {}), ...c.config }).map((k) => (
            <label key={k} className="small">{k}{" "}
              <Input width={k === "path" ? "full" : "md"} value={cfg[k] ?? ""} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })}
                placeholder={k === "path" ? "absolute, or relative to the data folder or the repository (or one Delta / Parquet table folder)" : ""}
              />
            </label>
          ))}
          <Button variant="primary" disabled={!!a.busy}
            onClick={() => a.run("save", () => api.editConnection(c.id, {
              config: Object.fromEntries(Object.entries(cfg).filter(([, v]) => v !== "")) }, reviewer()),
              () => { setEditing(false); setFound(null); reload(); })}>Save</Button>
        </div>
      )}
      {test && !test.ok && <TestSteps steps={test.steps} />}
      {found && <TableList tables={found} system={system} connection={c.id} />}
      {a.error && <span className="error"> {a.error}</span>}
    </div>
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
          {t.dataset ? <a className="muted" href={href("/sources", { view: "dataset", name: t.dataset })}>loaded as {t.dataset}</a>
            : <a href={href("/sources", { view: "add", system, connection: String(connection), table: t.table })}>add it</a>}
        </li>
      ))}
    </ul>
  );
}

/** A new connection: its type's config fields and secret references, then a test. */
export function ConnectionForm({ system, types, onDone, initial }: { system: string; types: Catalog["types"]["connections"];
                                                      onDone: (c: Json) => void; initial?: { name: string; path: string } }) {
  const [type, setType] = useState("folder");
  const [name, setName] = useState(initial?.name ?? "");
  const [cfg, setCfg] = useState<Record<string, string>>(initial ? { path: initial.path } : {});
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
    <UICard className="inset">
      <div className="row">
        <Select value={type} onChange={(e) => { setType(e.target.value); setCfg({}); setSec({}); }}>
          {Object.keys(types).map((k) => <option key={k} value={k}>{k === "folder" ? "folder of exports / lake" : k}</option>)}
        </Select>
        <Input placeholder={`name (default ${type})`} value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <Table variant="list" density="compact">
        <tbody>
          {Object.entries(t.config).map(([k, dflt]) => (
            <tr key={k}>
              <th>{k}{dflt === null ? " *" : ""}</th>
              <td><Input value={cfg[k] ?? ""} onChange={(e) => setCfg({ ...cfg, [k]: e.target.value })}
                placeholder={k === "path" ? "the folder holding the tables: absolute, or relative to the data folder or the repository; empty: the data folder"
                  : dflt === null ? "required" : `default ${String(dflt) || "none"}`} /></td>
            </tr>
          ))}
          {t.secrets.map((k) => (
            <tr key={k}><th>{k} (secret)</th>
              <td><Input value={sec[k] ?? ""} onChange={(e) => setSec({ ...sec, [k]: e.target.value })} placeholder="env:VARIABLE_NAME" /></td></tr>
          ))}
        </tbody>
      </Table>
      <Button variant="primary" disabled={!!a.busy} onClick={save}>{a.busy ? "Adding…" : "Add connection"}</Button>
      {a.error && <p className="error small">{a.error}</p>}
    </UICard>
  );
}
