// A report's panels (docs/reports.md): one component per type, each reading its own result from the server, so a slow
// panel costs only itself. The server words and computes everything; a panel only lays it out. The commentary's
// note and summary panels plug in to PANELS.
import type { ComponentType, ReactNode } from "react";
import { api, type WorkListRow } from "../api";
import { Sparkline, onTarget, show } from "../analytics/Metric";
import { PlotlyChart } from "../analytics/PlotlyChart";
import { count, quantity, whenDay } from "../format";
import { usePerson } from "../identityStore";
import { Status, useAsync } from "../lib";
import { BoardCard } from "../pages/WorkList";
import { TrendChart, isCompact } from "../trends/TrendChart";
import { Card, Empty, ErrorText, Table, Text } from "../ui";
import { NarrativePanel } from "./panels/NarrativePanel";
import { NotePanel } from "./panels/NotePanel";
import { SummaryPanel } from "./panels/SummaryPanel";
import { ViewPanel } from "./panels/ViewPanel";
import type {
  CasesResult, FigureResult, PanelProps, PanelResult, PanelType, StatResult, TableResult, TrendResult,
} from "./spec";

/** A link the server gives as an app path ("/list?stage=dispatch") as the page's own link. */
export const appLink = (path: string) => `#${path}`;

/** One panel's result for the report's scope, window and day. */
export function usePanel<T extends PanelResult>({ report, panel, scope, window, at, draft }: PanelProps) {
  const key = report.definition.key;
  // a panel added in the editor is read from the definition being edited, again whenever that panel changes
  const changed = draft ? JSON.stringify(panel) : "";
  return useAsync(() => (draft ? api.reportDraftPanel(key, panel.id, report.definition, { scope, window, at })
    : api.reportPanel(key, panel.id, { scope, window, at })) as Promise<T>, [key, panel.id, scope, window, at, changed]);
}

/** The frame every panel shares: its title (opening what it is made of), its body, and the one line that says what the
 * figure leaves out or why it is empty. */
export function PanelFrame({ title, detail, result, loading, children, className = "" }: {
  title: ReactNode; detail?: ReactNode; result?: PanelResult; loading: ReturnType<typeof usePanel>; children?: ReactNode;
  className?: string;
}) {
  const link = result?.href ? appLink(result.href) : null;
  return <Card as="section" className={`report-panel ${className}`.trim()} title={link ? <a href={link}>{title}</a> : title}
    detail={detail}>
    <Status of={loading} />
    {result?.error ? <ErrorText>{result.error}</ErrorText> : result && children}
    {result?.note && !result.error && <Text as="div" small muted>{result.note}</Text>}
  </Card>;
}

function StatPanel(props: PanelProps) {
  const r = usePanel<StatResult>(props);
  const d = r.data;
  const good = d ? onTarget(d, d.value) : null;
  return <PanelFrame title={props.panel.title ?? d?.title ?? ""} result={d} loading={r} className="report-stat">
    {d && <>
      <strong className={`report-stat-value${good === false ? " report-off-target" : ""}`}>{d.unknown ? "Unknown" : show(d.value, d.unit)}</strong>
      {!d.unknown && d.parts && <Text as="div" small>
        {show(d.parts.value, d.parts.unit)} of the {show(d.parts.of, d.parts.unit)} {d.parts.of_what}</Text>}
      <Text as="div" small muted>
        {[d.target != null && `target ${d.better === "lower" ? "≤" : "≥"} ${show(d.target, d.unit)}`,
          d.previous_unknown ? "no history the period before"
            : d.previous != null && `${show(d.previous, d.unit)} the period before`].filter(Boolean).join(" · ")}
      </Text>
      {d.history.length > 1 && <Sparkline points={d.history} target={d.target} label={d.title} />}
      {/* where the figure comes from and the day it is for, from the measure's declaration */}
      <Text as="div" small muted>{[d.source, d.as_of && whenDay(d.as_of)].filter(Boolean).join(" · ")}</Text>
    </>}
  </PanelFrame>;
}

function TrendPanel(props: PanelProps) {
  const r = usePanel<TrendResult>(props);
  const spec = r.data?.spec;
  const on = new Set((spec?.series ?? []).map((s) => s.key));
  return <PanelFrame title={props.panel.title ?? r.data?.title ?? ""} result={r.data} loading={r} className="report-chart">
    {spec && (spec.series.length
      ? <TrendChart spec={spec} timeline={null} on={on} compact={isCompact()}
        label={`${spec.title} from ${whenDay(spec.time.start)} to ${whenDay(spec.time.end)}`} />
      : <Empty className="report-empty">No values in this window.</Empty>)}
  </PanelFrame>;
}

function FigurePanel(props: PanelProps) {
  const r = usePanel<FigureResult>(props);
  const d = r.data;
  return <PanelFrame title={props.panel.title ?? d?.title ?? ""} result={d} loading={r} className="report-chart">
    {d && (d.rows.length
      ? <PlotlyChart spec={d.figure as never} label={`${d.title}: ${d.rows.map((x) => `${x.label} ${quantity(x.value, d.unit)}`).join(", ")}`} />
      : <Empty className="report-empty">Nothing to show for this scope and window.</Empty>)}
  </PanelFrame>;
}

function TablePanel(props: PanelProps) {
  const r = usePanel<TableResult>(props);
  const d = r.data;
  // the column heading names the measure, so a cell is the number alone
  const cell = (v: string | number | null) => (v == null ? "–" : typeof v === "number" ? quantity(v) : v);
  return <PanelFrame title={props.panel.title ?? d?.title ?? ""} result={d} loading={r}>
    {d && (d.rows.length ? <Table density="compact" aria-label={props.panel.title ?? d.title}>
      <thead><tr>{d.columns.map((c) => <th key={c.key} className={c.align === "end" ? "num" : undefined}>{c.label}</th>)}</tr></thead>
      <tbody>{d.rows.map((row) => <tr key={row.key}>{d.columns.map((c, i) => {
        const v = cell(row.cells[c.key]);
        return <td key={c.key} className={c.align === "end" ? "num" : undefined}>
          {i === 0 && row.href ? <a href={appLink(row.href)}>{v}</a> : v}</td>;
      })}</tr>)}</tbody>
    </Table> : !d.unknown && <Empty>Nothing to show for this scope and window.</Empty>)}
    {d && d.total > d.rows.length && <Text as="div" small muted>{d.rows.length} of {count(d.total)}</Text>}
  </PanelFrame>;
}

function CasesPanel(props: PanelProps) {
  const r = usePanel<CasesResult>(props);
  const [person] = usePerson();
  const d = r.data;
  const rows = (d?.cases ?? []) as unknown as WorkListRow[];
  const list = d?.href ? appLink(d.href) : null;
  // the same people and queues the Work list offers, so Send to works from here
  const people = useAsync(() => api.workPeople(), []);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const queues = (roles.data ?? []).filter((x) => x.kind === "person").map((x) => x.name);
  // the count is for the report's scope, not the whole of Work: said with it so it isn't read as Work's number
  return <PanelFrame title={props.panel.title ?? d?.title ?? ""} result={d} loading={r}
    detail={d && d.total ? `${count(d.total)}${d.scope.id ? ` at ${d.scope.label}` : ""}` : undefined}
    className="report-cases">
    {d && (rows.length
      ? <div className="report-case-cards">{rows.map((row) => <BoardCard key={row.uid} r={row} person={person || null}
        people={people.data ?? []} queues={queues} onChange={r.reload} />)}</div>
      : <Empty>None.</Empty>)}
    {d && list && d.total > rows.length && <a href={list}>All {count(d.total)} in Work</a>}
  </PanelFrame>;
}

/** Each panel type's component. */
export const PANELS: Record<PanelType, ComponentType<PanelProps>> = {
  stat: StatPanel,
  trend: TrendPanel,
  figure: FigurePanel,
  table: TablePanel,
  cases: CasesPanel,
  note: NotePanel,
  summary: SummaryPanel,
  narrative: NarrativePanel,
  view: ViewPanel,
};
