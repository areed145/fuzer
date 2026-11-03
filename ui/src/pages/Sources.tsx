import { api, type SourcePreview } from "../api";
import { Status, href, useAsync } from "../lib";

const STATE: Record<string, string> = {
  unchanged: "ok", new: "key", changed: "updating", held: "", unmapped: "",
};

const cell = (v: unknown) =>
  v === null || v === undefined ? "" : typeof v === "object" ? JSON.stringify(v).slice(0, 80) : String(v);

export function SourcesPage({ query }: { query: URLSearchParams }) {
  const list = useAsync(() => api.sources(), []);
  const open = query.get("name");
  return (
    <section>
      <h1>Sources</h1>
      <p className="muted">
        The tables in the data folder{list.data ? ` (${list.data.data_dir})` : ""}, with the connector that reads each,
        its mapping and its state since the last load. Open one to see a sample and what its rows would make, before
        anything loads. Held tables changed their columns and wait for review.
      </p>
      <Status loading={list.loading && !list.data} error={list.error} empty={list.data?.sources.length === 0}>
        No tables in the data folder.
      </Status>
      {list.data && list.data.sources.length > 0 && (
        <table className="results">
          <thead>
            <tr><th>Table</th><th>Connector</th><th>Mapping</th><th>State</th><th>Rows</th><th>Loaded</th></tr>
          </thead>
          <tbody>
            {list.data.sources.map((s) => (
              <tr key={s.dataset} onClick={() => (location.hash = href("/sources", { name: s.dataset }))}>
                <td><code>{s.dataset}</code></td>
                <td>{s.connector}</td>
                <td className="muted">{s.mapping ?? "none"}</td>
                <td><span className={`badge ${STATE[s.state] ?? ""}`}>{s.state}</span></td>
                <td>{s.rows?.toLocaleString() ?? ""}</td>
                <td className="muted small">{s.loaded_at?.slice(0, 16).replace("T", " ") ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {open && <Preview key={open} name={open} />}
    </section>
  );
}

function Preview({ name }: { name: string }) {
  const p = useAsync(() => api.source(name), [name]);
  if (!p.data) return <Status loading={p.loading} error={p.error} />;
  const d: SourcePreview = p.data;
  const cols = d.sample.length ? Object.keys(d.sample[0]) : (d.schema ?? []).map(([c]) => c);
  const drift = d.drift && (d.drift.added.length || d.drift.removed.length || d.drift.retyped.length);
  return (
    <div className="card">
      <h2><code>{d.dataset}</code> <span className="muted small">{d.connector} · {d.path}</span></h2>
      {d.held && <p className="error small">Held: its columns changed since the last load and wait for review.</p>}
      {drift ? (
        <p className="small">
          Since the last load: {d.drift!.added.length > 0 && <>added {d.drift!.added.join(", ")}. </>}
          {d.drift!.removed.length > 0 && <>removed {d.drift!.removed.join(", ")}. </>}
          {d.drift!.retyped.map((r) => `${r.column} ${r.was} → ${r.now}`).join(", ")}
        </p>
      ) : null}
      <h3>Sample</h3>
      <div className="scroll-x">
        <table className="list small">
          <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {d.sample.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{cell(r[c])}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
      {d.makes && (
        <>
          <h3>What the rows make <span className="muted small">mapping {d.mapping}</span></h3>
          <ul className="small">
            {d.makes.map((m) => (
              <li key={m.record}>
                <code>{m.record}</code>:{" "}
                {m.entities.map((e) => `${e.kind} ${e.key ?? "(no key)"}`).join(", ") || "no entity"}
                {m.links.length > 0 && <span className="muted"> · {m.links.map((l) => `${l.rel} → ${l.to}`).join(", ")}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      {d.suggested && (
        <>
          <h3>Suggested mapping <span className="muted small">from rules; review it with fuzer suggest-mappings</span></h3>
          <pre className="small">{d.suggested.yaml}</pre>
        </>
      )}
    </div>
  );
}
