import { Component, type ReactNode } from "react";
import { api, type LiveFigureTable } from "../api";
import { Status, useAsync } from "../lib";
import { Disclosure, Table } from "../ui";
import { PlotlyChart } from "./PlotlyChart";
import "./analytics.css";

/** One chart failing to render costs that chart, not the page holding it. */
class FigureBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <p className="error" role="alert">This chart could not be drawn from the data returned.</p> : this.props.children;
  }
}

/** Any live figure, whole. The server words and builds it; nothing here knows which chart it is. */
export function LiveFigure({ kind }: { kind: string }) {
  return <FigureBoundary><Figure kind={kind} /></FigureBoundary>;
}

function Figure({ kind }: { kind: string }) {
  const result = useAsync(() => api.liveFigure(kind), [kind]);
  const d = result.data;
  const s = d?.summary;
  return <>
    <Status loading={result.loading && !d} error={result.error} onRetry={result.reload} />
    {d && s && <>
      <p className="small muted">{s.context}</p>
      {s.headline && <p role="status"><strong>{s.headline}</strong></p>}
      {s.stats.length > 0 && <dl className="analytics-summary">
        {s.stats.map((stat) => <div key={stat.label}><dt>{stat.label}</dt><dd>{stat.value}{stat.unit && <> <small>{stat.unit}</small></>}</dd></div>)}
      </dl>}
      {d.spec.data.length > 0 && <PlotlyChart spec={d.spec} label={[d.title, s.headline, s.context].filter(Boolean).join(". ")} />}
      {s.notes.length > 0 && <ul className="small muted analytics-notes">{s.notes.map((note) => <li key={note}>{note}</li>)}</ul>}
      {s.warnings.length > 0 && <div className="analytics-warnings" role="status"><strong>Data notes</strong><ul>{s.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}
      {s.tables.map((table) => <FigureTable key={table.title} table={table} />)}
      <p className="small muted">Source: {s.source}</p>
    </>}
  </>;
}

function FigureTable({ table }: { table: LiveFigureTable }) {
  return <Disclosure className="analytics-details" label={table.title}>
    {table.note && <p className="small muted">{table.note}</p>}
    <div className="analytics-table"><Table variant="list" density="compact">
      <thead><tr>{table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
      <tbody>{table.rows.map((row, i) => <tr key={i}>{row.map((cell, c) => <td key={c}>{cell}</td>)}</tr>)}</tbody>
    </Table></div>
  </Disclosure>;
}
