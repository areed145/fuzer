import { Button, PageHeader, Table, Card as UICard, Input, Select, Badge as UIBadge, Disclosure, ErrorText, Text } from "../ui";
import { percent } from "../format";
import { useState } from "react";
import { api } from "../api";
import { FigureView } from "../Figure";
import { Status, href, notifyChanged, useAsync } from "../lib";
import { loadPerson } from "../work";
import { person as personName } from "../wording";

const KINDS: [string, string][] = [
  ["correlate", "What moved with a point, and first"], ["correlation_matrix", "What moves together"],
  ["modes", "Operating modes"], ["spe", "Multivariate anomaly"], ["entities", "Well peer groups"],
  ["events", "Recurring upset types"], ["cascades", "Recurring cascades"]];

export function StudiesPage({ query }: { query: URLSearchParams }) {
  const list = useAsync(() => api.studies(), []);
  const open = query.get("id");
  return (
    <section>
      <PageHeader help="analyses">Studies</PageHeader>
      <NewStudy onDone={list.reload} />
      <Status of={list} empty={list.data?.length === 0}>No studies yet.</Status>
      {list.data && list.data.length > 0 && (
        <Table variant="results">
          <thead><tr><th>Study</th><th>Kind</th><th>Status</th><th>Result</th><th>Run by</th></tr></thead>
          <tbody>
            {list.data.map((s) => (
              <tr key={s.id}>
                <td><a href={href("/studies", { id: String(s.id) })}>Study {s.id}</a></td>
                <td>{KINDS.find(([k]) => k === s.kind)?.[1] ?? s.kind}</td><td><UIBadge className="badge">{s.status}</UIBadge></td>
                <td className="small">{s.summary ?? s.error ?? ""}</td><td className="muted small">{personName(s.created_by)}</td>
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
    <UICard className="row small study-form">
      <label className="small">Study <Select value={kind} onChange={(e) => setKind(e.target.value)}>
        {KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </Select></label>
      {kind === "correlate" && <label className="small">Target PI point <Input value={target} onChange={(e) => setTarget(e.target.value)} /></label>}
      {kind !== "events" && kind !== "entities" && kind !== "cascades" && (
        <label className="small">Scope <Input placeholder="unit, area or facility" value={scope} onChange={(e) => setScope(e.target.value)} /></label>
      )}
      <label className="small">Since <Input type="datetime-local" value={since} onChange={(e) => setSince(e.target.value)} /></label>
      <label className="small">Until <Input type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} /></label>
      <Button variant="primary" onClick={run}>Run</Button>
      <ErrorText inline>{error}</ErrorText>
    </UICard>
  );
}

function StudyDetail({ id, reload }: { id: number; reload: () => void }) {
  const s = useAsync(() => api.study(id), [id]);
  const d = s.data;
  return (
    <UICard  as="section">
      <Status of={s} />
      {d && (
        <>
          <h2>study:{d.id} <Text small muted>{d.kind} · {d.status}</Text></h2>
          <Text as="p" small>{d.summary ?? d.error}</Text>
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
  if (!result.patterns?.length) return <Text as="p" small muted>No cascade repeated in this window.</Text>;
  return <>
    <Table density="compact">
      <thead><tr><th>First</th><th>Then</th><th>Times</th><th>Share followed</th><th>Typical lag</th><th>Links</th></tr></thead>
      <tbody>{result.patterns.map((p) => (
        <tr key={`${p.first}|${p.then}`}><td>{p.first}</td><td>{p.then}</td><td>{p.support} of {p.first_seen}</td>
          <td>{percent(p.confidence)}</td>
          <td>{p.median_lag_minutes} min <Text small muted>({p.lag_range_minutes[0]}–{p.lag_range_minutes[1]})</Text></td>
          <td>{p.hops ?? "—"}</td></tr>
      ))}</tbody>
    </Table>
    <Text as="p" small muted>{result.method}</Text>
  </>;
}
