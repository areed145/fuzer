import { useMemo, useState } from "react";
import { api, type Ontology as OntologyData } from "../api";
import { GraphCanvas, Legend, REL_VIEWS, kindColor } from "../graph";
import { SourceBadge, Status, href, useAsync } from "../lib";
import type { ElementDefinition } from "cytoscape";

const relId = (r: { src_kind: string; rel: string; dst_kind: string }) => `${r.src_kind}|${r.rel}|${r.dst_kind}`;

export function Ontology() {
  const data = useAsync(() => api.ontology(), []);
  const [view, setView] = useState<string>("all");
  const [selected, setSelected] = useState<string | null>(null);

  const elements = useMemo(() => (data.data ? ontologyElements(data.data, view) : []), [data.data, view]);

  if (!data.data) return <Status loading={data.loading} error={data.error} />;
  const o = data.data;

  return (
    <section>
      <div className="row spread">
        <div>
          <h1>Ontology</h1>
          <p className="muted">
            The data model as it exists in Fuzer right now: each circle is a kind of thing (sized by how many there
            are), each arrow a kind of link between them, with how many links exist. Click anything for details.
          </p>
        </div>
      </div>
      <div className="chips">
        {[{ id: "all", label: "All views" }, ...REL_VIEWS].map((v) => (
          <button key={v.id} className={view === v.id ? "chip on" : "chip"} onClick={() => setView(v.id)}>
            {v.label}
          </button>
        ))}
      </div>
      <div className="graph-layout">
        <GraphCanvas elements={elements} layout="force" selectedId={selected} onSelect={setSelected} height={620} />
        <aside className="card graph-panel">
          <OntologyPanel o={o} selected={selected} />
        </aside>
      </div>
      <Legend kinds={o.kinds.map((k) => k.kind)} />
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
          label: `${k.kind.replace("_", " ")}\n${k.count.toLocaleString()}`,
          color: kindColor(k.kind),
          size: 26 + 14 * Math.log10(k.count + 1),
        },
      })),
    ...relations.map((r) => ({
      classes: "labeled",
      data: {
        id: relId(r),
        source: r.src_kind,
        target: r.dst_kind,
        label: `${r.rel} · ${r.n.toLocaleString()}`,
        ended: false,
      },
    })),
  ];
}

function OntologyPanel({ o, selected }: { o: OntologyData; selected: string | null }) {
  const kind = o.kinds.find((k) => k.kind === selected);
  const rel = o.relations.find((r) => relId(r) === selected);

  if (kind) {
    const out = o.relations.filter((r) => r.src_kind === kind.kind);
    const inc = o.relations.filter((r) => r.dst_kind === kind.kind && r.src_kind !== kind.kind);
    return (
      <>
        <h2 className="row">
          <i className="dot" style={{ background: kindColor(kind.kind) }} /> {kind.kind.replace("_", " ")}
        </h2>
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
        <p className="muted small">Share of {kind.kind.replace("_", " ")} entities that have each field.</p>
        <ul className="coverage">
          {kind.fields.map((f) => {
            const pct = Math.round((100 * f.count) / kind.count);
            return (
              <li key={f.field}>
                <span>{f.field.replace("_", " ")}</span>
                <span className="bar">
                  <span style={{ width: `${pct}%` }} />
                </span>
                <span className="muted small">{pct}%</span>
              </li>
            );
          })}
        </ul>

        <h3>Links</h3>
        <ul className="plain">
          {out.map((r) => (
            <li key={relId(r)} className="small">
              → <b>{r.rel}</b> {r.dst_kind.replace("_", " ")} <span className="muted">{r.n.toLocaleString()}</span>
            </li>
          ))}
          {inc.map((r) => (
            <li key={relId(r)} className="small">
              ← {r.src_kind.replace("_", " ")} <b>{r.rel}</b> <span className="muted">{r.n.toLocaleString()}</span>
            </li>
          ))}
        </ul>
        <a href={href("/", { kind: kind.kind })}>Browse all {kind.kind.replace("_", " ")} →</a>
      </>
    );
  }

  if (rel) {
    return (
      <>
        <h2>{rel.rel}</h2>
        <p>
          {rel.src_kind.replace("_", " ")} → {rel.dst_kind.replace("_", " ")}
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
        Use the view buttons to see one part of the model at a time: physical structure, flow and allocation, data
        (PI time series), or documents and logs.
      </p>
      <p className="muted small">
        {o.kinds.reduce((a, k) => a + k.count, 0).toLocaleString()} entities ·{" "}
        {o.relations.reduce((a, r) => a + r.n, 0).toLocaleString()} links
      </p>
    </>
  );
}
