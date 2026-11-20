/** Metrics (P7.1b, #714): every registered KPI, read from the registry the API, the daily report and agents read, so a
 * number here is the number everywhere. Each metric opens to its definition, formula and the records behind it, cut by
 * any grain it declares; every row drills to its items or cases. */
import { api, type MetricDeclaration } from "../api";
import { Sparkline, onTarget, show } from "../analytics/Metric";
import { Badge, Card, Disclosure, Empty, PageHeader, Table, Tabs, Text } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";


const refHref = (ref: string) => ref.startsWith("case:") ? href(`/cases/${ref.slice(5)}`) : entityHref(ref);
const refText = (ref: string) => ref.startsWith("case:") ? `case ${ref.slice(5)}` : ref;

const BETTER: Record<MetricDeclaration["better"], string> = { higher: "higher is better", lower: "lower is better", none: "" };

const targetText = (m: MetricDeclaration) =>
  m.target == null || m.better === "none" ? "" : `target ${m.better === "higher" ? "≥" : "≤"} ${show(m.target, m.unit)}`;

export function MetricsPage({ metricKey, query }: { metricKey?: string; query: URLSearchParams }) {
  return metricKey ? <MetricDetail metricKey={metricKey} grain={query.get("grain")} /> : <MetricList />;
}

function MetricList() {
  const list = useAsync(() => api.metrics(), []);
  const groups = new Map<string, MetricDeclaration[]>();
  for (const m of list.data ?? []) groups.set(m.group, [...(groups.get(m.group) ?? []), m]);
  return (
    <section>
      <PageHeader
        actions={<a href={href("/scorecard")}>How the plant is running</a>}>Metrics</PageHeader>
      <Status of={list} />
      {list.data && !list.data.length && <Empty>No metrics are registered.</Empty>}
      {[...groups].map(([group, ms]) => (
        // Work's measures open the queue view a manager wants, not only from All work (#1078).
        <Card key={group} title={group} actions={group === "Work" ? <a href={href("/performance")}>How the work is running</a> : undefined}>
          {/* One set of column widths on every group's table, so "Now" sits in one place down the page (#1078). */}
          <Table density="compact" className="metric-table phone-rows">
            <colgroup><col /><col className="metric-now" /><col className="metric-trend" /><col className="metric-for" /><col className="metric-cut" /></colgroup>
            <thead><tr><th>Metric</th><th>Now</th><th>Trend</th><th>For</th><th>Cut by</th></tr></thead>
            <tbody>{ms.map((m) => <MetricRowNow key={m.key} m={m} />)}</tbody>
          </Table>
        </Card>
      ))}
    </section>
  );
}

function MetricRowNow({ m }: { m: MetricDeclaration }) {
  const v = useAsync(() => api.metric(m.key, null, true), [m.key]);
  const good = onTarget(m, v.data?.value);
  const target = targetText(m);
  return (
    <tr>
      <td><a href={href(`/metrics/${m.key}`)}>{m.label}</a><Text as="div" small muted>{m.definition}</Text></td>
      {/* The value says whether it meets its target, in colour and in words, never by colour alone (#1078). */}
      <td data-label="Now">{v.loading ? <Text small muted>loading</Text> : v.error ? <Text muted>unavailable</Text>
        : v.data?.value == null ? <Text small muted title={v.data?.note ?? undefined}>no reading{v.data?.note ? `: ${v.data.note}` : ""}</Text>
        : <><strong className={good === null ? undefined : good ? "metric-good" : "metric-bad"} title={v.data?.note ?? undefined}>
            {show(v.data.value, m.unit)}</strong>
          {target && <div className={`small ${good === false ? "metric-bad" : "muted"}`}>{good === false ? "off " : ""}{target}</div>}</>}</td>
      <td data-label="Trend">{v.data ? <Sparkline points={v.data.history ?? []} target={m.target} label={m.label} /> : null}</td>
      <td data-label="For" className="small">{v.data?.as_of ? `${m.period}, ${v.data.as_of}` : m.period}</td>
      <td data-label="Cut by" className="small">{m.grains.length
        ? m.grains.map((g, i) => <span key={g}>{i ? ", " : ""}<a href={href(`/metrics/${m.key}`, { grain: g })}>{g}</a></span>)
        : <Text muted>–</Text>}</td>
    </tr>
  );
}

function MetricDetail({ metricKey, grain }: { metricKey: string; grain: string | null }) {
  const v = useAsync(() => api.metric(metricKey, grain), [metricKey, grain]);
  const m = v.data;
  return (
    <section>
      <PageHeader detail={m?.definition} actions={<a href={href("/metrics")}>All metrics</a>}>
        {m?.label ?? metricKey}</PageHeader>
      <Status of={v} />
      {m && <>
        <p>
          <strong>{show(m.value, m.unit)}</strong>{" "}
          <Text small muted>{m.as_of ? `${m.period}, ${m.as_of}` : m.period}
            {BETTER[m.better] && ` · ${BETTER[m.better]}`}</Text>
          {m.standard && <> <Badge>{m.standard}</Badge></>}
        </p>
        {m.note && <Text as="p" small muted>{m.note}</Text>}
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
                <td>{r.refs.length ? <Refs refs={r.refs} /> : <Text muted>–</Text>}</td>
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
    {refs.length > 200 && <Text as="p" small muted>The first 200 of {refs.length}.</Text>}
  </Disclosure>;
}
