import { Component, type ReactNode } from "react";
import { api, type LiveFigureData, type LiveFigureTable } from "../api";
import { Status, useAsync } from "../lib";
import { Disclosure, Table, ErrorText, Text } from "../ui";
import { PlotlyChart } from "./PlotlyChart";
import "./analytics.css";

/** One chart failing to render costs that chart, not the page holding it. */
class FigureBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    return this.state.failed ? <ErrorText>This chart could not be drawn from the data returned.</ErrorText> : this.props.children;
  }
}

type Frame = (body: ReactNode) => ReactNode;

/** Any live figure, whole. The server words and builds it; nothing here knows which chart it is. `hideEmpty` leaves
 * out a figure with nothing to plot (a facility with no production reports, #1074), frame and all, where a page
 * summarizes rather than reports; `frame` wraps the figure in its card, so the card goes with it. */
export function LiveFigure({ kind, params, hideEmpty, frame }: { kind: string; params?: Record<string, string>;
                                                                hideEmpty?: boolean; frame?: Frame }) {
  return <Figure kind={kind} params={params} hideEmpty={hideEmpty} frame={frame} />;
}

function Figure({ kind, params, hideEmpty, frame = (body) => body }: { kind: string; params?: Record<string, string>;
                                                                      hideEmpty?: boolean; frame?: Frame }) {
  const key = JSON.stringify(params ?? {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const result = useAsync(() => api.liveFigure(kind, params), [kind, key]);
  const d = result.data;
  const s = d?.summary;
  if (hideEmpty && Array.isArray(d?.spec?.data) && d.spec.data.length === 0) return null;
  // The boundary sits inside the frame, so a chart that can't be drawn fails inside its own card.
  return frame(<FigureBoundary>
    <Status of={result} />
    {d && s && <Body d={d} />}
  </FigureBoundary>);
}

/** The figure itself: its own component, so a malformed one throws inside the boundary. */
function Body({ d }: { d: LiveFigureData }) {
  const s = d.summary;
  return <>
      {s.headline && <p role="status"><strong>{s.headline}</strong></p>}
      {s.stats.length > 0 && <dl className="analytics-summary">
        {s.stats.map((stat) => <div key={stat.label}><dt>{stat.label}</dt><dd>{stat.value}{stat.unit && <> <small>{stat.unit}</small></>}</dd></div>)}
      </dl>}
      {d.spec.data.length > 0 && <PlotlyChart spec={d.spec} label={[d.title, s.headline, s.context].filter(Boolean).join(". ")} />}
      {s.tables.map((table) => <FigureTable key={table.title} table={table} />)}
      {s.warnings.length > 0 && <div className="analytics-warnings" role="status"><strong>Data notes</strong><ul>{s.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}
      <Disclosure className="analytics-about" label="Notes and source">
        <Text as="p" small muted>{s.context}</Text>
        {s.notes.length > 0 && <ul className="small muted analytics-notes">{s.notes.map((note) => <li key={note}>{note}</li>)}</ul>}
        <Text as="p" small muted>Source: {s.source}</Text>
      </Disclosure>
  </>;
}

function FigureTable({ table }: { table: LiveFigureTable }) {
  return <Disclosure className="analytics-details" label={table.title}>
    {table.note && <Text as="p" small muted>{table.note}</Text>}
    <div className="analytics-table"><Table variant="list" density="compact">
      <thead><tr>{table.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
      <tbody>{table.rows.map((row, i) => <tr key={i}>{row.map((cell, c) => <td key={c}>{cell}</td>)}</tr>)}</tbody>
    </Table></div>
  </Disclosure>;
}
