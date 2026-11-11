import { Button, PageHeader, Table, Card as UICard, Input, Select , Badge as UIBadge, Disclosure } from "../ui";
import { useState } from "react";
import { api } from "../api";
import { FigureView } from "../Figure";
import { Status, href, notifyChanged, useAsync } from "../lib";
import { loadPerson } from "../work";

const KINDS: [string, string][] = [
  ["correlate", "What moved with a point, and first"], ["correlation_matrix", "What moves together"],
  ["modes", "Operating modes"], ["spe", "Multivariate anomaly"], ["entities", "Well peer groups"],
  ["events", "Recurring upset types"], ["cascades", "Recurring cascades"]];

export function StudiesPage({ query }: { query: URLSearchParams }) {
  const list = useAsync(() => api.studies(), []);
  const open = query.get("id");
  return (
    <section>
      <PageHeader subtitle="Saved correlation and clustering runs." help="analyses">Studies</PageHeader>
      <NewStudy onDone={list.reload} />
      <Status loading={list.loading && !list.data} error={list.error} empty={list.data?.length === 0}>No studies yet.</Status>
      {list.data && list.data.length > 0 && (
        <Table variant="results">
          <tbody>
            {list.data.map((s) => (
              <tr key={s.id}>
                <td><a href={href("/studies", { id: String(s.id) })}>study:{s.id}</a></td>
                <td>{s.kind}</td><td><UIBadge className="badge">{s.status}</UIBadge></td>
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
      <Select value={kind} onChange={(e) => setKind(e.target.value)}>
        {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </Select>
      {kind === "correlate" && <Input placeholder="target PI point" value={target} onChange={(e) => setTarget(e.target.value)} />}
      {kind !== "events" && kind !== "entities" && kind !== "cascades" && (
        <Input placeholder="scope: unit, area or facility" value={scope} onChange={(e) => setScope(e.target.value)} />
      )}
      <Input placeholder="since (YYYY-MM-DDTHH:MM)" value={since} onChange={(e) => setSince(e.target.value)} />
      <Input placeholder="until" value={until} onChange={(e) => setUntil(e.target.value)} />
      <Button variant="primary" onClick={run}>Run</Button>
      {error && <span className="error">{error}</span>}
    </UICard>
  );
}

function StudyDetail({ id, reload }: { id: number; reload: () => void }) {
  const s = useAsync(() => api.study(id), [id]);
  const d = s.data;
  return (
    <UICard  as="section">
      <Status loading={s.loading && !d} error={s.error} />
      {d && (
        <>
          <h2>study:{d.id} <span className="muted small">{d.kind} · {d.status}</span></h2>
          <p className="small">{d.summary ?? d.error}</p>
          <pre className="small">{JSON.stringify(d.params, null, 1)}</pre>
          <Button onClick={async () => { await api.rerunStudy(d.id, loadPerson() || "anonymous"); reload(); }}>Run again</Button>
          {d.figure_id && <FigureView id={d.figure_id} />}
          {d.kind === "cascades" && d.result && <CascadeTable result={d.result as unknown as CascadeResult} />}
          {d.result && <Disclosure label={<>Results</>}><pre className="small">{JSON.stringify(d.result, null, 1).slice(0, 20000)}</pre></Disclosure>}
        </>
      )}
    </UICard>
  );
}

interface CascadeResult {
  patterns: { first: string; then: string; support: number; first_seen: number; confidence: number;
    median_lag_minutes: number; lag_range_minutes: [number, number]; hops: number | null }[];
  method: string;
}

/** Recurring cascades as the IOC reads them: this, then that, how often and how long after. */
function CascadeTable({ result }: { result: CascadeResult }) {
  if (!result.patterns?.length) return <p className="small muted">No cascade repeated in this window.</p>;
  return <>
    <Table density="compact">
      <thead><tr><th>First</th><th>Then</th><th>Times</th><th>Share followed</th><th>Typical lag</th><th>Links</th></tr></thead>
      <tbody>{result.patterns.map((p) => (
        <tr key={`${p.first}|${p.then}`}><td>{p.first}</td><td>{p.then}</td><td>{p.support} of {p.first_seen}</td>
          <td>{Math.round(p.confidence * 100)}%</td>
          <td>{p.median_lag_minutes} min <span className="muted small">({p.lag_range_minutes[0]}–{p.lag_range_minutes[1]})</span></td>
          <td>{p.hops ?? "—"}</td></tr>
      ))}</tbody>
    </Table>
    <p className="small muted">{result.method}</p>
  </>;
}
