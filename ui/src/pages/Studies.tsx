import { Button, PageHeader, Table, Card as UICard } from "../ui";
import { useState } from "react";
import { api } from "../api";
import { FigureView } from "../Figure";
import { Status, href, notifyChanged, useAsync } from "../lib";
import { loadPerson } from "../work";

const KINDS: [string, string][] = [
  ["correlate", "What moved with a point, and first"], ["correlation_matrix", "What moves together"],
  ["modes", "Operating modes"], ["spe", "Multivariate anomaly"], ["entities", "Well peer groups"],
  ["events", "Recurring upset types"]];

export function StudiesPage({ query }: { query: URLSearchParams }) {
  const list = useAsync(() => api.studies(), []);
  const open = query.get("id");
  return (
    <section>
      <PageHeader>Studies</PageHeader>
      <p className="muted">
        Correlation and clustering runs kept with their parameters and results, to re-run, cite (study:&lt;id&gt;) or attach
        to a work item. Big ones run in the background.
      </p>
      <NewStudy onDone={list.reload} />
      <Status loading={list.loading && !list.data} error={list.error} empty={list.data?.length === 0}>No studies yet.</Status>
      {list.data && list.data.length > 0 && (
        <Table variant="results">
          <tbody>
            {list.data.map((s) => (
              <tr key={s.id}>
                <td><a href={href("/studies", { id: String(s.id) })}>study:{s.id}</a></td>
                <td>{s.kind}</td><td><span className="badge">{s.status}</span></td>
                <td className="small">{s.summary ?? s.error ?? ""}</td><td className="muted small">{s.created_by}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {open && <StudyDetail key={open} id={Number(open)} reload={list.reload} />}
    </section>
  );
}

function NewStudy({ onDone }: { onDone: () => void }) {
  const [kind, setKind] = useState("correlate");
  const [target, setTarget] = useState("");
  const [scope, setScope] = useState("facility");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [error, setError] = useState<string | null>(null);
  const run = async () => {
    setError(null);
    const params: Record<string, unknown> = { scope, since, until };
    if (kind === "correlate") params.target = target;
    if (kind === "spe") {
      const d = (s: string, days: number) => new Date(new Date(s).getTime() - days * 864e5).toISOString().slice(0, 16);
      params.train_since = d(since, 7);
      params.train_until = since;
    }
    try {
      await api.startStudy(kind, params, loadPerson() || "anonymous");
      notifyChanged();
      onDone();
    } catch (e) {
      setError(String(e));
    }
  };
  return (
    <UICard className="row small">
      <select value={kind} onChange={(e) => setKind(e.target.value)}>
        {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      {kind === "correlate" && <input placeholder="target PI point" value={target} onChange={(e) => setTarget(e.target.value)} />}
      {kind !== "events" && kind !== "entities" && (
        <input placeholder="scope: unit, area or facility" value={scope} onChange={(e) => setScope(e.target.value)} />
      )}
      <input placeholder="since (YYYY-MM-DDTHH:MM)" value={since} onChange={(e) => setSince(e.target.value)} />
      <input placeholder="until" value={until} onChange={(e) => setUntil(e.target.value)} />
      <Button variant="primary" onClick={run}>Run</Button>
      {error && <span className="error">{error}</span>}
    </UICard>
  );
}

function StudyDetail({ id, reload }: { id: number; reload: () => void }) {
  const s = useAsync(() => api.study(id), [id]);
  const d = s.data;
  return (
    <section className="card">
      <Status loading={s.loading && !d} error={s.error} />
      {d && (
        <>
          <h2>study:{d.id} <span className="muted small">{d.kind} · {d.status}</span></h2>
          <p className="small">{d.summary ?? d.error}</p>
          <pre className="small">{JSON.stringify(d.params, null, 1)}</pre>
          <Button onClick={async () => { await api.rerunStudy(d.id, loadPerson() || "anonymous"); reload(); }}>Run again</Button>
          {d.figure_id && <FigureView id={d.figure_id} />}
          {d.result && <details><summary>Results</summary><pre className="small">{JSON.stringify(d.result, null, 1).slice(0, 20000)}</pre></details>}
        </>
      )}
    </section>
  );
}
