/** Metrics (P7.1b, #714): every registered KPI, read from the registry the API, the daily report and agents read, so a
 * number here is the number everywhere. Each metric opens to its definition, formula and the records behind it, cut by
 * any grain it declares; every row drills to its items or cases. */
import { api, type MetricDeclaration } from "../api";
import { Badge, Card, Disclosure, Empty, PageHeader, Table, Tabs } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";

const show = (v: number | null, unit: string) =>
  v == null ? "–" : unit === "%" ? `${v}%` : `${v.toLocaleString()} ${unit}`;

const refHref = (ref: string) => ref.startsWith("case:") ? href(`/cases/${ref.slice(5)}`) : entityHref(ref);
const refText = (ref: string) => ref.startsWith("case:") ? `case ${ref.slice(5)}` : ref;

const BETTER: Record<MetricDeclaration["better"], string> = { higher: "higher is better", lower: "lower is better", none: "" };

export function MetricsPage({ metricKey, query }: { metricKey?: string; query: URLSearchParams }) {
  return metricKey ? <MetricDetail metricKey={metricKey} grain={query.get("grain")} /> : <MetricList />;
}

function MetricList() {
  const list = useAsync(() => api.metrics(), []);
  const groups = new Map<string, MetricDeclaration[]>();
  for (const m of list.data ?? []) groups.set(m.group, [...(groups.get(m.group) ?? []), m]);
  return (
    <section>
      <PageHeader subtitle="Each measure defined once; the same number on every page, the daily report and for agents."
        help="metrics">Metrics</PageHeader>
      <Status loading={list.loading && !list.data} error={list.error} onRetry={list.reload} />
      {list.data && !list.data.length && <Empty>No metrics are registered.</Empty>}
      {[...groups].map(([group, ms]) => (
        <Card key={group} title={group}>
          <Table density="compact" className="metric-table phone-rows">
            <thead><tr><th>Metric</th><th>Now</th><th>For</th><th>Cut by</th></tr></thead>
            <tbody>{ms.map((m) => <MetricRowNow key={m.key} m={m} />)}</tbody>
          </Table>
        </Card>
      ))}
    </section>
  );
}

function MetricRowNow({ m }: { m: MetricDeclaration }) {
  const v = useAsync(() => api.metric(m.key), [m.key]);
  return (
    <tr>
      <td><a href={href(`/metrics/${m.key}`)}>{m.label}</a><div className="small muted">{m.definition}</div></td>
      <td data-label="Now">{v.loading ? <span className="muted">…</span> : v.error ? <span className="muted">unavailable</span>
        : <strong title={v.data?.note ?? undefined}>{show(v.data?.value ?? null, m.unit)}</strong>}</td>
      <td data-label="For" className="small">{v.data?.as_of ? `${m.period}, ${v.data.as_of}` : m.period}</td>
      <td data-label="Cut by" className="small">{m.grains.length
        ? m.grains.map((g, i) => <span key={g}>{i ? ", " : ""}<a href={href(`/metrics/${m.key}`, { grain: g })}>{g}</a></span>)
        : <span className="muted">–</span>}</td>
    </tr>
  );
}

function MetricDetail({ metricKey, grain }: { metricKey: string; grain: string | null }) {
  const v = useAsync(() => api.metric(metricKey, grain), [metricKey, grain]);
  const m = v.data;
  return (
    <section>
      <PageHeader subtitle={m?.definition} actions={<a href={href("/metrics")}>All metrics</a>} help="metrics">
        {m?.label ?? metricKey}</PageHeader>
      <Status loading={v.loading && !m} error={v.error} onRetry={v.reload} />
      {m && <>
        <p>
          <strong>{show(m.value, m.unit)}</strong>{" "}
          <span className="muted small">{m.as_of ? `${m.period}, ${m.as_of}` : m.period}
            {BETTER[m.better] && ` · ${BETTER[m.better]}`}</span>
          {m.standard && <> <Badge>{m.standard}</Badge></>}
        </p>
        {m.note && <p className="small muted">{m.note}</p>}
        <dl className="data">
          <div><dt>Formula</dt><dd><code>{m.formula}</code></dd></div>
          <div><dt>Records</dt><dd>{m.records}</dd></div>
          <div><dt>Key</dt><dd><code>{m.key}</code></dd></div>
        </dl>
        {m.grains.length > 0 && <Tabs label="Cut by" items={[
          { key: "", label: "Headline", href: href(`/metrics/${m.key}`), current: !grain },
          ...m.grains.map((g) => ({ key: g, label: `By ${g}`, href: href(`/metrics/${m.key}`, { grain: g }), current: grain === g })),
        ]} />}
        {grain && (m.rows.length ? (
          <Table density="compact" className="metric-rows phone-rows">
            <thead><tr><th>{grain}</th><th>{m.label}</th><th>Records</th></tr></thead>
            <tbody>{m.rows.map((r) => (
              <tr key={r.key}>
                <td>{r.label}</td>
                <td data-label="Value">{show(r.value, m.unit)}</td>
                <td data-label="Records">{r.refs.length ? <Refs refs={r.refs} /> : <span className="muted">–</span>}</td>
              </tr>
            ))}</tbody>
          </Table>
        ) : <Empty>Nothing to show by {grain}.</Empty>)}
        {!grain && m.refs.length > 0 && <Refs refs={m.refs} label={`What it is made of (${m.refs.length})`} />}
      </>}
    </section>
  );
}

function Refs({ refs, label }: { refs: string[]; label?: string }) {
  return <Disclosure label={label ?? `${refs.length} ${refs.length === 1 ? "record" : "records"}`}>
    <ul className="plain small">{refs.slice(0, 200).map((r) => <li key={r}><a href={refHref(r)}>{refText(r)}</a></li>)}</ul>
    {refs.length > 200 && <p className="small muted">The first 200 of {refs.length}.</p>}
  </Disclosure>;
}
