import { Button, Input, Table, Card as UICard, Select, Badge as UIBadge, Disclosure, Empty } from "../../ui";
/** Systems of record and their connections (#225): edit a system, add, test, edit and delete connections, and
 * see what each connection holds. */
import { useState, type ReactNode } from "react";
import { api, type Catalog, type CatalogConnection, type CatalogSystem, type Json, type PackReport } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { TestSteps, reviewer, useAction } from "./common";

const SYSTEM_FIELDS: [keyof CatalogSystem & string, string, string][] = [
  ["kind", "Kind", "erp, historian, planning, ..."], ["owner", "Owner", "who answers for it; alerts go to them"],
  ["steward", "Steward", "who looks after its data"], ["date_format", "Date format", "e.g. %d/%m/%Y; empty: ISO"],
  ["decimal", "Decimal mark", ", for a decimal comma"], ["timezone", "Timezone", "e.g. Europe/London"],
  ["encoding", "Encoding", "CSV text, e.g. latin-1"], ["rank", "Survivorship rank", "lower wins a field"],
  ["notes", "Notes", "reliability, known issues"],
  ["webhook_secret", "Webhook secret", "env:NAME holding the shared secret its webhook checks"],
  ["closeout", "Close-out", "none, draft or send: how work the field finishes goes back to it (SAP: draft)"],
  ["closeout_role", "Close-out queue", "who approves drafted close-outs (SAP: maintenance_planner)"],
];

const download = (name: string, text: string, type: string) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
};

export function Systems() {
  const cat = useAsync(() => api.catalog(), []);
  return (
    <>
      <NewSystem onDone={cat.reload} />
      <ImportPack onDone={cat.reload} />
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

/** Bring in a source pack (#227, #458, #465): a system with its connections, datasets, mappings, checks, and the
 * exception types and rules it brings. A folder elsewhere in this environment is given before anything is written. */
export function ImportPack({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [folders, setFolders] = useState<Record<string, string>>({});
  const [report, setReport] = useState<PackReport | null>(null);
  const a = useAction();
  const conns = [...text.matchAll(/- name: (\S+)\n\s+type: folder/g)].map((m) => m[1]);
  if (!open) return <p className="small"><Button variant="link" onClick={() => setOpen(true)}>Import a source pack…</Button></p>;
  return (
    <UICard title="Import a source pack">
      <p className="small">Importing a pack is safe to repeat. A mapping that differs from an approved one becomes a draft.</p>
      <input type="file" accept=".yaml,.yml" onChange={async (e) => { const f = e.target.files?.[0]; if (f) setText(await f.text()); }} />
      <textarea className="mono" rows={8} style={{ width: "100%" }} placeholder="fuzer_source_pack: 1 …" value={text}
        onChange={(e) => setText(e.target.value)} />
      {conns.map((c) => (
        <div key={c} className="row small">
          <span>Folder for connection <code>{c}</code> here</span>
          <Input placeholder="as in the pack" value={folders[c] ?? ""} onChange={(e) => setFolders({ ...folders, [c]: e.target.value })} />
        </div>
      ))}
      <div className="row">
        <Button variant="primary" disabled={!text.trim() || !!a.busy}
          onClick={() => a.run("import", () => api.importPack(text, Object.fromEntries(Object.entries(folders)
            .filter(([, v]) => v.trim()).map(([k, v]) => [k, { path: v.trim() }])), reviewer()), (r) => { setReport(r); onDone(); })}>
          {a.busy ? "Importing…" : "Import"}</Button>
        <Button variant="link" onClick={() => { setOpen(false); setReport(null); }}>Close</Button>
      </div>
      {a.error && <p className="error small">{a.error}</p>}
      {report && <PackInstalled report={report} />}
    </UICard>
  );
}

/** What installing a pack did (P1.5): each thing it added, under the page that owns it, linked there. */
export function PackInstalled({ report }: { report: PackReport }) {
  const list = (k: string) => report[k] ?? [];
  const links = (items: string[], to: (x: string) => string) =>
    items.map((x, i) => <span key={x}>{i > 0 && ", "}<a href={to(x)}>{x}</a></span>);
  const rows: [string, string, ReactNode][] = [];
  const add = (page: string, what: string, items: string[], to?: (x: string) => string) => {
    if (items.length) rows.push([page, what, to ? links(items, to) : items.join(", ")]);
  };
  add("Sources", "System", list("system").map((x) => x.replace(/^system /, "")), () => href("/sources", { view: "systems" }));
  add("Sources", "Connections", list("connections"));
  add("Sources", "Datasets", list("datasets"), (d) => href("/sources", { view: "dataset", name: d }));
  add("Sources", "Mappings", list("mappings"));
  add("Sources", "Drafts to review", list("drafts"));
  add("Sources", "Checks", list("checks"));
  add("Routing", "Types of work", list("exception_types"), (t) => href("/routing", { type: t }));
  add("Routing", "Rules", list("exception_rules"));
  add("Detection", "Switched on", list("detectors"), () => href("/settings", { view: "detection" }));
  const unchanged = list("unchanged");
  return <div className="pack-installed" role="status">
    <p className="small">{rows.length ? "Installed." : "Nothing changed: everything in the pack was already here."}</p>
    {["Sources", "Routing", "Detection"].map((page) => {
      const mine = rows.filter((r) => r[0] === page);
      return mine.length > 0 && <div key={page}>
        <h4>On {page}</h4>
        <Table variant="list" density="compact">
          <tbody>{mine.map(([, what, items]) => <tr key={what}><th scope="row">{what}</th><td>{items}</td></tr>)}</tbody>
        </Table>
      </div>;
    })}
    {unchanged.length > 0 && <Disclosure label={`${unchanged.length} already in place`}>
      <p className="small">{unchanged.join(", ")}</p></Disclosure>}
  </div>;
}

/** The work a system brings (#465): its exception types with how many are open, the rules that raise them from its
 * loaded records, and its webhook. */
function SystemWork({ system }: { system: string }) {
  const w = useAsync(() => api.systemWorkflows(system), [system]);
  const a = useAction();
  if (!w.data) return <Status loading={w.loading} error={w.error} />;
  const { types, rules, webhook } = w.data;
  return (
    <>
      <h3>Work it brings</h3>
      {!types.length && !rules.length && <Empty className="small">No exception types or rules yet.</Empty>}
      {types.length > 0 && (
        <Table density="compact">
          <thead><tr><th>Exception type</th><th>Goes to</th><th>Seen by</th><th>Open</th></tr></thead>
          <tbody>{types.map((t) => (
            <tr key={t.name}><td title={t.description ?? ""}>{t.label} <code className="muted">{t.name}</code></td>
              <td>{t.process ? `process ${t.process}` : t.role?.replace("role:", "") ?? "the board"}</td>
              <td>{t.audience.join(", ")}</td>
              <td>{t.open ? <a href={href(`/exceptions?type=${encodeURIComponent(t.name)}`)}>{t.open}</a> : 0}</td></tr>
          ))}</tbody>
        </Table>
      )}
      {rules.length > 0 && (
        <Table density="compact">
          <thead><tr><th>Rule</th><th>Over</th><th>Raises when</th><th>Type</th><th /></tr></thead>
          <tbody>{rules.map((r) => (
            <tr key={r.name}><td><code>{r.name}</code>{r.enabled === false && <span className="muted"> (off)</span>}</td>
              <td>{r.dataset}</td>
              <td className="mono small">{whenText(r.when)}{r.clear_when ? ` · clears when ${whenText(r.clear_when)}` : ""}</td>
              <td>{r.type}</td>
              <td><Button variant="link" disabled={!!a.busy} onClick={() => confirm(`Remove rule ${r.name}? Exceptions it raised stay until cleared.`)
                && a.run(`del-${r.name}`, () => api.deleteExceptionRule(system, r.name, reviewer()), w.reload)}>remove</Button></td></tr>
          ))}</tbody>
        </Table>
      )}
      <p className="small">Webhook <code>POST {webhook.path}</code> with header <code>X-Fuzer-Secret</code>:{" "}
        {webhook.secret ? <UIBadge tone={webhook.ready ? "ok" : "warn"} title={webhook.secret}>
          {webhook.ready ? "secret resolves" : `${webhook.secret} is not set`}</UIBadge>
          : <span className="muted">off (set a webhook secret above)</span>}</p>
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

const whenText = (when: Json) => Object.entries(when).map(([col, cond]) =>
  Object.entries(cond as Json).map(([op, v]) => `${col} ${OP_TEXT[op] ?? op} ${Array.isArray(v) ? v.join("|") : String(v)}`).join(", ")).join(" and ");
const OP_TEXT: Record<string, string> = { eq: "=", ne: "≠", lt: "<", le: "≤", gt: ">", ge: "≥", in: "in", not_in: "not in" };

function SystemCard({ s, cat, reload }: { s: CatalogSystem; cat: Catalog; reload: () => void }) {
  const [vals, setVals] = useState<Record<string, string>>(() =>
    Object.fromEntries(SYSTEM_FIELDS.map(([k]) => [k, s[k] == null ? "" : String(s[k])])));
  const [adding, setAdding] = useState(false);
  const a = useAction();
  const changed = SYSTEM_FIELDS.filter(([k]) => vals[k] !== (s[k] == null ? "" : String(s[k])));
  return (
    <Disclosure className="card" label={<><code>{s.name}</code> <span className="muted small">{s.kind ?? ""} · {s.datasets.length} datasets ·{" "}
          {s.connections.length} connection{s.connections.length === 1 ? "" : "s"}{s.owner ? ` · owner ${s.owner}` : ""}</span></>}>
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
      <Button variant="secondary" disabled={!!a.busy} onClick={() => a.run("pack", () => api.exportPack(s.name),
        (r) => download(`${s.name}.source-pack.yaml`, r.yaml, "application/yaml"))}>Download source pack</Button>
      {a.error && <p className="error small">{a.error}</p>}
      <SystemWork system={s.name} />
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
    </Disclosure>
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
  if (!tables.length) return <Empty className="small">It holds no tables Fuzer can read.</Empty>;
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
