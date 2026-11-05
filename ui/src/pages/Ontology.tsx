// The data model (#241): what the register holds, drawn as a graph, next to what the ontology declares, and
// where kinds and link types are defined. A kind or link a new dataset brings that nobody has described shows
// under "Needs definition".
import { useMemo, useState } from "react";
import {
  api, type KindDef, type KindIn, type Ontology as OntologyData, type OntologyKind, type RelationDef, type RelationIn,
} from "../api";
import { GraphCanvas, Legend, REL_VIEWS, kindColor } from "../graph";
import { Bar, Button, PageHeader } from "../ui";
import { SourceBadge, Status, href, useAsync } from "../lib";
import { reviewer, useAction, when } from "./sources/common";
import type { ElementDefinition } from "cytoscape";

const relId = (r: { src_kind: string; rel: string; dst_kind: string }) => `${r.src_kind}|${r.rel}|${r.dst_kind}`;
const words = (s: string) => s.replace(/_/g, " ");

type Editing =
  | { what: "kind"; def?: KindDef; draft?: KindIn }
  | { what: "relation"; def?: RelationDef; draft?: RelationIn };

export function Ontology({ query }: { query?: URLSearchParams }) {
  const data = useAsync(() => api.ontology(), []);
  const [view, setView] = useState<string>("all");
  const [tab, setTab] = useState<"graph" | "model">(query?.get("tab") === "model" ? "model" : "graph");
  const [selected, setSelected] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing | null>(null);

  const elements = useMemo(() => (data.data ? ontologyElements(data.data, view) : []), [data.data, view]);

  if (!data.data) return <Status loading={data.loading} error={data.error} />;
  const o = data.data;
  const kindDef = (name: string) => o.model.kinds.find((k) => k.name === name);
  const relDef = (name: string) => o.model.relations.find((r) => r.name === name);
  const edit = (e: Editing) => { setEditing(e); window.scrollTo({ top: 0, behavior: "smooth" }); };
  const done = () => { setEditing(null); data.reload(); };

  return (
    <section>
      <PageHeader
        subtitle={<>The data model: each kind of thing Fuzer holds and each kind of link between them, what they mean and
          what may link to what. The graph is the register as it is now (circles sized by how many there are);
          Kinds &amp; link types is the model itself, where you define them. A new dataset's kinds and links show
          under Needs definition until someone describes them.</>}
        actions={<>
          <Button onClick={() => edit({ what: "kind" })}>Add kind</Button>
          <Button onClick={() => edit({ what: "relation" })}>Add link type</Button>
        </>}
      >Ontology</PageHeader>

      {editing?.what === "kind" && <KindForm o={o} def={editing.def} draft={editing.draft} onDone={done} onCancel={() => setEditing(null)} />}
      {editing?.what === "relation" && <RelationForm o={o} def={editing.def} draft={editing.draft} onDone={done} onCancel={() => setEditing(null)} />}

      <NeedsDefinition o={o} edit={edit} relDef={relDef} />

      <div className="tabs small">
        <a className={tab === "graph" ? "on" : ""} onClick={() => setTab("graph")}>Graph</a>
        <a className={tab === "model" ? "on" : ""} onClick={() => setTab("model")}>Kinds &amp; link types</a>
      </div>

      {tab === "graph" ? (
        <>
          <div className="chips">
            {[{ id: "all", label: "All views" }, ...REL_VIEWS].map((v) => (
              <Button key={v.id} className={view === v.id ? "chip on" : "chip"} onClick={() => setView(v.id)}>
                {v.label}
              </Button>
            ))}
          </div>
          <div className="graph-layout">
            <GraphCanvas elements={elements} layout="force" selectedId={selected} onSelect={setSelected} height={620} />
            <aside className="card graph-panel">
              <OntologyPanel o={o} selected={selected} kindDef={kindDef} relDef={relDef} edit={edit} />
            </aside>
          </div>
          <Legend kinds={o.kinds.map((k) => k.kind)} />
        </>
      ) : (
        <ModelTables o={o} edit={edit} />
      )}
    </section>
  );
}

function ontologyElements(o: OntologyData, view: string): ElementDefinition[] {
  const rels = view === "all" ? null : new Set(REL_VIEWS.find((v) => v.id === view)?.rels);
  const relations = o.relations.filter((r) => !rels || rels.has(r.rel));
  const used = new Set(relations.flatMap((r) => [r.src_kind, r.dst_kind]));
  return [
    ...o.kinds
      .filter((k) => !rels || used.has(k.kind))
      .map((k) => ({
        data: {
          id: k.kind,
          label: `${k.label ?? words(k.kind)}${k.declared === false ? " ?" : ""}\n${k.count.toLocaleString()}`,
          color: k.declared === false ? "#8d98a7" : kindColor(k.kind),
          size: 26 + 14 * Math.log10(k.count + 1),
        },
      })),
    ...relations.map((r) => ({
      classes: "labeled",
      data: {
        id: relId(r),
        source: r.src_kind,
        target: r.dst_kind,
        label: `${r.rel}${r.allowed === false ? " ?" : ""} · ${r.n.toLocaleString()}`,
        ended: false,
      },
    })),
  ];
}

function StatusBadge({ status }: { status?: string }) {
  if (!status) return <span className="badge">not defined</span>;
  return <span className={`badge${status === "core" ? "" : status === "custom" ? " ok" : " key"}`}
    title={status === "core" ? "comes with Fuzer" : status === "custom" ? "added for this asset" : "made with a mapping, not described yet"}>
    {status}</span>;
}

function NeedsDefinition({ o, edit, relDef }: {
  o: OntologyData; edit: (e: Editing) => void; relDef: (n: string) => RelationDef | undefined;
}) {
  const n = o.needs_definition;
  const drafts = o.model.kinds.filter((k) => k.status === "draft");
  if (!n.kinds.length && !n.relations.length && !n.not_allowed.length && !drafts.length) return null;
  const count = (k: string) => o.kinds.find((x) => x.kind === k)?.count ?? 0;
  const ends = (rel: string) => {
    const rs = o.relations.filter((r) => r.rel === rel);
    return { from_kinds: [...new Set(rs.map((r) => r.src_kind))], to_kinds: [...new Set(rs.map((r) => r.dst_kind))] };
  };
  return (
    <section className="card">
      <h2>Needs definition</h2>
      <p className="muted small">
        What the register holds that the model doesn't describe yet, usually from a newly added dataset. Define each
        so people and Claude know what it means, or change the mapping that makes it (Sources).
      </p>
      <ul className="plain">
        {n.kinds.map((k) => (
          <li key={k} className="row small">
            <span>Kind <b className="mono">{k}</b> <span className="muted">{count(k).toLocaleString()} entities</span></span>
            <Button onClick={() => edit({ what: "kind", draft: { name: k } })}>Define</Button>
          </li>
        ))}
        {drafts.map((k) => (
          <li key={k.name} className="row small">
            <span>Draft kind <b className="mono">{k.name}</b> <span className="muted">made with a mapping; no description yet</span></span>
            <Button onClick={() => edit({ what: "kind", def: k })}>Describe</Button>
          </li>
        ))}
        {n.relations.map((r) => (
          <li key={r} className="row small">
            <span>Link type <b className="mono">{r}</b> <span className="muted">
              {o.relations.filter((x) => x.rel === r).reduce((a, x) => a + x.n, 0).toLocaleString()} links</span></span>
            <Button onClick={() => edit({ what: "relation", draft: { name: r, ...ends(r) } })}>Define</Button>
          </li>
        ))}
        {n.not_allowed.map((x) => {
          const d = relDef(x.rel);
          return (
            <li key={relId(x)} className="row small">
              <span><b className="mono">{x.rel}</b> from {words(x.src_kind)} to {words(x.dst_kind)}{" "}
                <span className="muted">{x.n.toLocaleString()} links its link type doesn't allow</span></span>
              {d && <Button onClick={() => edit({
                what: "relation", def: d, draft: {
                  from_kinds: d.from_kinds.length && !d.from_kinds.includes(x.src_kind) ? [...d.from_kinds, x.src_kind] : d.from_kinds,
                  to_kinds: d.to_kinds.length && !d.to_kinds.includes(x.dst_kind) ? [...d.to_kinds, x.dst_kind] : d.to_kinds,
                },
              })}>Allow</Button>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function OntologyPanel({ o, selected, kindDef, relDef, edit }: {
  o: OntologyData; selected: string | null; kindDef: (n: string) => KindDef | undefined;
  relDef: (n: string) => RelationDef | undefined; edit: (e: Editing) => void;
}) {
  const kind = o.kinds.find((k) => k.kind === selected);
  const rel = o.relations.find((r) => relId(r) === selected);

  if (kind) {
    const def = kindDef(kind.kind);
    const out = o.relations.filter((r) => r.src_kind === kind.kind);
    const inc = o.relations.filter((r) => r.dst_kind === kind.kind && r.src_kind !== kind.kind);
    return (
      <>
        <h2 className="row">
          <i className="dot" style={{ background: kindColor(kind.kind) }} /> {kind.label ?? words(kind.kind)}
        </h2>
        <p className="row small">
          <span className="mono muted">{kind.kind}</span> <StatusBadge status={def?.status} />
          {def?.group && <span className="muted">{def.group}</span>}
        </p>
        <p className="small">{def?.description || <span className="muted">No description yet.</span>}</p>
        <p className="row">
          <Button onClick={() => edit(def ? { what: "kind", def } : { what: "kind", draft: { name: kind.kind } })}>
            {def ? "Edit" : "Define"}</Button>
        </p>
        <p className="muted small">{kind.count.toLocaleString()} entities</p>

        <h3>Fed by</h3>
        <div className="row">
          {kind.sources.map((s) => (
            <span key={s.source} className="row small">
              <SourceBadge source={s.source} /> {s.records.toLocaleString()}
            </span>
          ))}
        </div>

        <h3>Fields</h3>
        <p className="muted small">Share of {words(kind.kind)} entities that have each field.</p>
        <Coverage kind={kind} />

        <h3>Links</h3>
        <ul className="plain">
          {out.map((r) => (
            <li key={relId(r)} className="small">
              → <b>{r.rel}</b> {words(r.dst_kind)} <span className="muted">{r.n.toLocaleString()}</span>
            </li>
          ))}
          {inc.map((r) => (
            <li key={relId(r)} className="small">
              ← {words(r.src_kind)} <b>{r.rel}</b> <span className="muted">{r.n.toLocaleString()}</span>
            </li>
          ))}
        </ul>
        <a href={href("/", { kind: kind.kind })}>Browse all {words(kind.kind)} →</a>
      </>
    );
  }

  if (rel) {
    const def = relDef(rel.rel);
    return (
      <>
        <h2>{rel.rel}</h2>
        <p className="row small"><StatusBadge status={def?.status} />{def?.dated && <span className="muted">dated</span>}</p>
        <p className="small">{def?.description || <span className="muted">No description yet.</span>}</p>
        <p>
          {words(rel.src_kind)} → {words(rel.dst_kind)}
          {rel.allowed === false && def && <span className="muted small"> (not among the kinds this link type allows)</span>}
        </p>
        {def && (def.from_kinds.length > 0 || def.to_kinds.length > 0) && (
          <p className="muted small">
            Allowed: {def.from_kinds.map(words).join(", ") || "any kind"} → {def.to_kinds.map(words).join(", ") || "any kind"}
          </p>
        )}
        <p className="row">
          <Button onClick={() => edit(def ? { what: "relation", def } : {
            what: "relation", draft: { name: rel.rel, from_kinds: [rel.src_kind], to_kinds: [rel.dst_kind] },
          })}>{def ? "Edit link type" : "Define"}</Button>
        </p>
        <p className="muted small">
          {rel.n.toLocaleString()} links{rel.dated > 0 && `, ${rel.dated.toLocaleString()} with start/end dates`}
        </p>
        <h3>From</h3>
        <div className="row">
          {rel.sources.map((s) => (
            <SourceBadge key={s} source={s} />
          ))}
        </div>
      </>
    );
  }

  return (
    <>
      <h2>How to read this</h2>
      <p className="muted small">
        Every record from every system is merged into one entity per real-world thing. A <b>tag</b> is a position in
        the plant; <b>equipment</b> is the physical item installed there (pumps get swapped, tags stay).
      </p>
      <p className="muted small">
        Click a kind or a link for what it means and to edit it. A grey circle or a link marked ? isn't defined in
        the model yet. The view buttons show one part of the model at a time.
      </p>
      <p className="muted small">
        {o.kinds.reduce((a, k) => a + k.count, 0).toLocaleString()} entities ·{" "}
        {o.relations.reduce((a, r) => a + r.n, 0).toLocaleString()} links
      </p>
    </>
  );
}

function Coverage({ kind }: { kind: OntologyKind }) {
  return (
    <ul className="coverage">
      {kind.fields.map((f) => {
        const pct = Math.round((100 * f.count) / kind.count);
        return (
          <li key={f.field}>
            <span>{words(f.field)}</span>
            <Bar className="bar" value={pct} />
            <span className="muted small">{pct}%</span>
          </li>
        );
      })}
    </ul>
  );
}

function ModelTables({ o, edit }: { o: OntologyData; edit: (e: Editing) => void }) {
  const groups = [...o.groups, ""].filter((g) => o.model.kinds.some((k) => (k.group ?? "") === g));
  const viewLabel = (id: string | null) => o.views.find((v) => v.id === id)?.label ?? (id ? words(id) : "No view");
  const views = [...new Set(o.model.relations.map((r) => r.view ?? ""))]
    .sort((a, b) => o.views.findIndex((v) => v.id === a) - o.views.findIndex((v) => v.id === b));
  return (
    <>
      <section className="card">
        <h2>Kinds</h2>
        <table className="results">
          <thead><tr><th>Kind</th><th>Description</th><th>Entities</th><th>Status</th><th /></tr></thead>
          {groups.map((g) => (
            <tbody key={g || "none"}>
              <tr><th colSpan={5} className="small muted">{g || "No group"}</th></tr>
              {o.model.kinds.filter((k) => (k.group ?? "") === g).map((k) => (
                <tr key={k.name}>
                  <td><span className="row"><i className="dot" style={{ background: kindColor(k.name) }} />
                    <span>{k.label}<br /><span className="mono muted small">{k.name}</span></span></span></td>
                  <td className="small">{k.description || <span className="muted">no description</span>}</td>
                  <td className="small">{(k.count ?? 0).toLocaleString()}</td>
                  <td><StatusBadge status={k.status} /></td>
                  <td><Button onClick={() => edit({ what: "kind", def: k })}>Edit</Button></td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </section>
      <section className="card">
        <h2>Link types</h2>
        <table className="results">
          <thead><tr><th>Link type</th><th>From → to</th><th>Description</th><th>Links</th><th>Status</th><th /></tr></thead>
          {views.map((v) => (
            <tbody key={v || "none"}>
              <tr><th colSpan={6} className="small muted">{viewLabel(v || null)}</th></tr>
              {o.model.relations.filter((r) => (r.view ?? "") === v).map((r) => (
                <tr key={r.name}>
                  <td><span className="mono">{r.name}</span>{r.dated && <span className="muted small"> dated</span>}</td>
                  <td className="small">{r.from_kinds.map(words).join(", ") || "any"} → {r.to_kinds.map(words).join(", ") || "any"}</td>
                  <td className="small">{r.description || <span className="muted">no description</span>}</td>
                  <td className="small">{(r.count ?? 0).toLocaleString()}</td>
                  <td><StatusBadge status={r.status} /></td>
                  <td><Button onClick={() => edit({ what: "relation", def: r })}>Edit</Button></td>
                </tr>
              ))}
            </tbody>
          ))}
        </table>
      </section>
    </>
  );
}

function KindPicker({ o, value, onChange }: { o: OntologyData; value: string[]; onChange: (v: string[]) => void }) {
  const on = new Set(value);
  return (
    <div className="chips">
      {o.model.kinds.map((k) => (
        <Button key={k.name} type="button" className={on.has(k.name) ? "chip on" : "chip"}
          onClick={() => onChange(on.has(k.name) ? value.filter((x) => x !== k.name) : [...value, k.name])}>
          {k.label}
        </Button>
      ))}
      {value.length === 0 && <span className="muted small">none picked: any kind</span>}
    </div>
  );
}

function History({ object, name }: { object: "kind" | "relation"; name: string }) {
  const h = useAsync(() => api.ontologyHistory(object, name), [object, name]);
  if (!h.data?.length) return null;
  return (
    <details className="small">
      <summary>History ({h.data.length})</summary>
      <ul className="plain">
        {h.data.map((c) => (
          <li key={c.id}>
            {when(c.at)} <b>{c.by}</b> {c.action}
            {c.new && c.action !== "delete" && <span className="muted"> {Object.keys(c.new).join(", ")}</span>}
            {c.reason && <> · {c.reason}</>}
          </li>
        ))}
      </ul>
    </details>
  );
}

function KindForm({ o, def, draft, onDone, onCancel }: {
  o: OntologyData; def?: KindDef; draft?: KindIn; onDone: () => void; onCancel: () => void;
}) {
  const [k, setK] = useState<KindIn>({
    name: def?.name ?? draft?.name ?? "", label: def?.label ?? (draft?.name ? capital(words(draft.name)) : ""),
    description: def?.description ?? "", group: def?.group ?? null, colour: def?.colour ?? null,
    label_field: def?.label_field ?? null, status: def?.status === "draft" ? "custom" : def?.status ?? "custom", ...draft,
  });
  const [reason, setReason] = useState("");
  const a = useAction();
  const core = def?.status === "core";
  const set = (patch: KindIn) => setK({ ...k, ...patch });
  const save = () => a.run("save", () => def
    ? api.kindUpdate(def.name, changed(def, k), reviewer(), reason || undefined)
    : api.kindAdd(k, reviewer(), reason || undefined), onDone);
  return (
    <section className="card">
      <h2>{def ? `Edit kind: ${def.label}` : "Add a kind"}</h2>
      <table className="kv small">
        <tbody>
          <tr><th>Name</th><td>
            <input className="mono" value={k.name ?? ""} disabled={core} placeholder="activity"
              onChange={(e) => set({ name: e.target.value })} />
            <span className="muted"> {core ? "core kinds keep their name" : def
              ? "renaming changes the register and the mappings that make it" : "lower case, words joined with _"}</span>
          </td></tr>
          <tr><th>Label</th><td><input value={k.label ?? ""} onChange={(e) => set({ label: e.target.value })} /></td></tr>
          <tr><th>Description</th><td>
            <textarea rows={3} value={k.description ?? ""} placeholder="What one of these is, in the plant's words"
              onChange={(e) => set({ description: e.target.value })} />
          </td></tr>
          <tr><th>Group</th><td>
            <input list="ontology-groups" value={k.group ?? ""} onChange={(e) => set({ group: e.target.value || null })} />
            <datalist id="ontology-groups">{o.groups.map((g) => <option key={g} value={g} />)}</datalist>
          </td></tr>
          <tr><th>Colour</th><td className="row">
            <input type="color" value={k.colour ?? kindColor(k.name ?? "")} onChange={(e) => set({ colour: e.target.value })} />
            {k.colour && <Button onClick={() => set({ colour: null })}>Default</Button>}
          </td></tr>
          <tr><th>Name field</th><td>
            <input className="mono" value={k.label_field ?? ""} placeholder="the key"
              onChange={(e) => set({ label_field: e.target.value || null })} />
            <span className="muted"> the field shown as its name, when it isn't the key</span>
          </td></tr>
          {!core && <tr><th>Status</th><td>
            <select value={k.status ?? "custom"} onChange={(e) => set({ status: e.target.value as KindIn["status"] })}>
              <option value="custom">custom: described and in use</option>
              <option value="draft">draft: not described yet</option>
            </select>
          </td></tr>}
          <tr><th>Why</th><td><input value={reason} placeholder="what changed and why (kept in the history)"
            onChange={(e) => setReason(e.target.value)} /></td></tr>
        </tbody>
      </table>
      <div className="row">
        <Button variant="primary" disabled={!!a.busy || !k.name || !k.label} onClick={save}>{def ? "Save" : "Add kind"}</Button>
        <Button onClick={onCancel}>Cancel</Button>
        {def && !core && <Button disabled={!!a.busy}
          onClick={() => confirm(`Delete kind ${def.name}?`) && a.run("delete", () => api.kindDelete(def.name, reviewer(), reason || undefined), onDone)}>
          Delete</Button>}
        {def && <span className="muted small">last changed by {def.updated_by} {when(def.updated_at)}</span>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
      {def && <History object="kind" name={def.name} />}
    </section>
  );
}

function RelationForm({ o, def, draft, onDone, onCancel }: {
  o: OntologyData; def?: RelationDef; draft?: RelationIn; onDone: () => void; onCancel: () => void;
}) {
  const [r, setR] = useState<RelationIn>({
    name: def?.name ?? "", label: def?.label ?? "", description: def?.description ?? "", view: def?.view ?? null,
    dated: def?.dated ?? false, from_kinds: def?.from_kinds ?? [], to_kinds: def?.to_kinds ?? [],
    status: def?.status ?? "custom", ...draft,
  });
  const [reason, setReason] = useState("");
  const a = useAction();
  const core = def?.status === "core";
  const set = (patch: RelationIn) => setR({ ...r, ...patch });
  const save = () => a.run("save", () => def
    ? api.relationUpdate(def.name, changed(def, r, ["name"]), reviewer(), reason || undefined)
    : api.relationAdd({ ...r, label: r.label || words(r.name ?? "") }, reviewer(), reason || undefined), onDone);
  return (
    <section className="card">
      <h2>{def ? `Edit link type: ${def.name}` : "Add a link type"}</h2>
      <table className="kv small">
        <tbody>
          <tr><th>Name</th><td>
            <input className="mono" value={r.name ?? ""} disabled={!!def} placeholder="executes"
              onChange={(e) => set({ name: e.target.value })} />
            {!def && <span className="muted"> a verb, lower case, words joined with _</span>}
          </td></tr>
          <tr><th>Label</th><td><input value={r.label ?? ""} placeholder={words(r.name ?? "")}
            onChange={(e) => set({ label: e.target.value })} /></td></tr>
          <tr><th>Description</th><td>
            <textarea rows={2} value={r.description ?? ""} placeholder="What it means that one links to the other"
              onChange={(e) => set({ description: e.target.value })} />
          </td></tr>
          <tr><th>From</th><td><KindPicker o={o} value={r.from_kinds ?? []} onChange={(v) => set({ from_kinds: v })} /></td></tr>
          <tr><th>To</th><td><KindPicker o={o} value={r.to_kinds ?? []} onChange={(v) => set({ to_kinds: v })} /></td></tr>
          <tr><th>View</th><td>
            <select value={r.view ?? ""} onChange={(e) => set({ view: e.target.value || null })}>
              <option value="">none</option>
              {o.views.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}
            </select>
          </td></tr>
          <tr><th>Dated</th><td><label className="row">
            <input type="checkbox" checked={!!r.dated} onChange={(e) => set({ dated: e.target.checked })} />
            <span className="muted">links hold for a period (from / to dates)</span></label></td></tr>
          <tr><th>Why</th><td><input value={reason} placeholder="what changed and why (kept in the history)"
            onChange={(e) => setReason(e.target.value)} /></td></tr>
        </tbody>
      </table>
      <div className="row">
        <Button variant="primary" disabled={!!a.busy || !r.name} onClick={save}>{def ? "Save" : "Add link type"}</Button>
        <Button onClick={onCancel}>Cancel</Button>
        {def && !core && <Button disabled={!!a.busy}
          onClick={() => confirm(`Delete link type ${def.name}?`) && a.run("delete", () => api.relationDelete(def.name, reviewer(), reason || undefined), onDone)}>
          Delete</Button>}
        {def && <span className="muted small">last changed by {def.updated_by} {when(def.updated_at)}</span>}
      </div>
      {a.error && <p className="error small">{a.error}</p>}
      {def && <History object="relation" name={def.name} />}
    </section>
  );
}

const capital = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Only the fields that differ from what's stored (a rename only when the name changed). */
function changed<T extends object>(def: object, next: T, skip: string[] = []): Partial<T> {
  const d = def as Record<string, unknown>;
  return Object.fromEntries(Object.entries(next).filter(([k, v]) =>
    !skip.includes(k) && JSON.stringify(d[k] ?? null) !== JSON.stringify(v ?? null))) as Partial<T>;
}
