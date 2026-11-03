import { useState } from "react";
import { api, type SourceLoad, type SourcePreview } from "../api";
import { Status, href, notifyChanged, useAsync } from "../lib";

const reviewer = () => {
  try {
    return localStorage.getItem("fuzer.reviewer") || "anonymous";
  } catch {
    return "anonymous";
  }
};

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
        anything loads; for a new table, check the suggested mapping, save it and load it here. Held tables changed
        their columns and wait for review.
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
      {d.suggested ? (
        <AddSource name={d.dataset} yaml={d.suggested.yaml} />
      ) : d.held ? (
        <p className="small"><a href={href("/review", { kind: "schema" })}>Review the new columns</a> to load it.</p>
      ) : (
        <LoadNow name={d.dataset} />
      )}
    </div>
  );
}

function Loaded({ r }: { r: SourceLoad }) {
  if (r.held.length) return <p className="error small">Held: its columns changed. Review them on the Review page.</p>;
  if (r.unchanged.length) return <p className="small muted">Unchanged since the last load: nothing to do.</p>;
  return (
    <p className="small">
      Loaded {r.records.toLocaleString()} records ({r.inserted} new, {r.updated} changed, {r.removed} removed). They
      become entities on the next rebuild.
    </p>
  );
}

function LoadNow({ name }: { name: string }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<SourceLoad | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = async () => {
    setBusy(true);
    setError(null);
    try {
      setDone(await api.loadSource(name, null, reviewer()));
      notifyChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="row">
      <button className="primary" onClick={load} disabled={busy}>{busy ? "Loading…" : "Load now"}</button>
      {done && <Loaded r={done} />}
      {error && <p className="error small">{error}</p>}
    </div>
  );
}

/** A table with no mapping: the draft from rules (or Claude), edited here, saved and loaded in one step. */
function AddSource({ name, yaml }: { name: string; yaml: string }) {
  const [text, setText] = useState(yaml);
  const [busy, setBusy] = useState<"claude" | "load" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<SourceLoad | null>(null);
  const ask = async () => {
    setBusy("claude");
    setError(null);
    try {
      const s = await api.suggestSource(name, true);
      setText(s.yaml);
      const named = Object.values(s.columns).filter((c) => c.by === "claude").length;
      setNote(named ? `Claude named ${named} column${named > 1 ? "s" : ""} the rules couldn't.` : "Nothing left for Claude to name.");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  const save = async () => {
    setBusy("load");
    setError(null);
    try {
      setDone(await api.loadSource(name, text, reviewer()));
      notifyChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };
  return (
    <>
      <h3>Add this table <span className="muted small">a draft from rules: check what each column means</span></h3>
      <textarea className="yaml mono" rows={Math.min(24, text.split("\n").length + 1)} value={text}
        onChange={(e) => setText(e.target.value)} spellCheck={false} disabled={!!done} />
      <div className="row">
        <button className="primary" onClick={save} disabled={!!busy || !!done}>{busy === "load" ? "Loading…" : "Save mapping and load"}</button>
        <button className="secondary" onClick={ask} disabled={!!busy || !!done}
          title="Claude names the columns the rules could only call code or other (costs money; cached)">
          {busy === "claude" ? "Asking Claude…" : "Ask Claude"}
        </button>
        {text !== yaml && !done && <button className="secondary" onClick={() => setText(yaml)}>Undo edits</button>}
      </div>
      {note && <p className="small muted">{note}</p>}
      {error && <p className="error small">{error}</p>}
      {done && <Loaded r={done} />}
    </>
  );
}
