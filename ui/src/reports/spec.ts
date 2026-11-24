// A report definition and its panel results (docs/reports.md): the contract between the report engine, the measures
// and the commentary. Types only. The server reads, checks and answers every definition; the page only lays it out.
import type { TrendSpec } from "../trends/spec";

export type WindowKey = "1d" | "7d" | "30d" | "90d" | "qtd" | "ytd";
export const WINDOWS: readonly WindowKey[] = ["1d", "7d", "30d", "90d", "qtd", "ytd"];

/** Quarters of the desktop width: 4 columns above 1100 px, 2 from 601 to 1100 px, 1 at 600 px and below. */
export type PanelSize = 1 | 2 | 3 | 4;

export type PanelType = "stat" | "trend" | "figure" | "table" | "cases" | "note" | "summary";

interface PanelBase {
  /** Unique in the report and kept across versions, so notes and links stay attached. */
  id: string;
  type: PanelType;
  size: PanelSize;
  /** The measure's label when left out. */
  title?: string;
  /** False leaves the panel out on phones (a wide table). */
  phone?: boolean;
}

export interface StatPanel extends PanelBase { type: "stat"; measure: string }
export interface TrendPanel extends PanelBase { type: "trend"; measures: string[] }
export interface FigurePanel extends PanelBase { type: "figure"; measure: string; grain: string; chart: "bar" | "pareto" | "share"; limit?: number }
/** Either a registered row set or a measure cut by one of its grains. */
export interface TablePanel extends PanelBase { type: "table"; rows?: string; measure?: string; grain?: string; limit?: number }
/** The Work list's own filters (ops/worklist.rows); the report's scope is added as `within`. */
export interface CasesFilter {
  state?: "open" | "closed" | "all"; kind?: string; stage?: string; holder?: string; queue?: string; source?: string;
  overdue?: boolean; q?: string; sort?: "value" | "due" | "opened" | "updated" | "title";
}
export interface CasesPanel extends PanelBase { type: "cases"; filter: CasesFilter; limit?: number }
/** Written commentary; `text` is fixed markdown kept in the definition (a section's heading line). */
export interface NotePanel extends PanelBase { type: "note"; text?: string }
export interface SummaryPanel extends PanelBase { type: "summary" }

export type Panel = StatPanel | TrendPanel | FigurePanel | TablePanel | CasesPanel | NotePanel | SummaryPanel;

/** A stack of panels beside the others in a row, one level deep. */
export interface PanelColumn { type: "column"; size: PanelSize; panels: Panel[] }

export interface ReportRow {
  /** r0, r1, ... when the definition leaves it out. */
  id: string;
  title?: string;
  /** Draw the row once for each entity of this kind beneath the scope. */
  repeat?: { kind: string; limit: number };
  panels: (Panel | PanelColumn)[];
}

export interface ReportDefinition {
  /** The definition format. */
  schema: 1;
  key: string;
  title: string;
  /** This definition's own version; bumps on every saved change. */
  version: number;
  /** Who reads it. */
  for: string[];
  scope: { kinds: string[] };
  window: { default: WindowKey; options: WindowKey[] };
  rows: ReportRow[];
  source: "platform" | "bundle" | "custom";
}

/** GET /reports */
export interface ReportListing {
  key: string;
  title: string;
  for: string[];
  scope: { kinds: string[] };
  window: { default: WindowKey };
  source: ReportDefinition["source"];
  /** Custom reports only (`source: "custom"`): owner, sharing, version and origin. */
  mine?: boolean;
  owner?: string;
  shared?: string;
  version?: number;
}

/** Where a custom report was copied from; `version` is that report's version now, `copied_version` when copied. */
export interface ReportOrigin { key: string; title: string | null; version: number | null; copied_version: number | null }

/** A custom report's own facts (ops/custom_reports.py). `shared`: "" private, "everyone" or "role:<workflow role>". */
export interface CustomReport extends ReportListing {
  source: "custom";
  version: number;
  owner: string;
  mine: boolean;
  shared: string;
  origin: ReportOrigin | null;
  updated_at: string;
  updated_by: string;
  /** May share beyond themselves (configure access). */
  can_share: boolean;
}

/** GET /reports/{key}/versions */
export interface ReportVersion { version: number; title: string; by: string; note: string | null; at: string; panels: number; current: boolean }

/** GET /model: what an editor can put on a report, filtered to its scope kinds. */
export interface ReportModel {
  kinds: string[];
  types: PanelType[];
  charts: FigurePanel["chart"][];
  case_filters: (keyof CasesFilter)[];
  measures: { key: string; label: string; group: string; unit: string; definition: string; grains: string[];
    /** False: it answers for the whole register only, not a scope. */ scoped: boolean; windowed: boolean }[];
  row_sets: { key: string; label: string; definition: string; scoped: boolean }[];
}

export interface ScopeRef { id: string | null; label: string; kind: string | null }

/** A named window resolved to dates by the server (core/windows.py). */
export interface ResolvedWindow {
  key: WindowKey;
  /** The day the window ends. */
  at: string;
  start: string;
  end: string;
  days: number;
  label: string;
  previous: { start: string; end: string };
  /** The day was named in the link; without it each measure reads its own latest day. */
  asked?: boolean;
}

/** GET /reports/{key}?scope&window&at */
export interface ReportView {
  definition: ReportDefinition;
  scope: ScopeRef;
  window: ResolvedWindow;
  /** The entities each repeat row draws for, by row id. */
  repeats: Record<string, ScopeRef[]>;
  /** The entities the report can be scoped to, the one shown first. */
  scopes: ScopeRef[];
  /** A custom report's owner, sharing and origin; null for a standard report. */
  custom?: CustomReport | null;
}

/** What every panel result carries, then its type's own fields. */
export interface PanelResultBase {
  id: string;
  type: PanelType;
  title: string;
  scope: ScopeRef;
  window: ResolvedWindow;
  /** The day or period the value is for. */
  as_of: string | null;
  /** Why it is empty, or what it leaves out. */
  note: string | null;
  /** False when the data is for the whole register, not the scope asked for. */
  scoped: boolean;
  /** What it is made of: entity ids or "case:<id>". */
  refs: string[];
  /** Where it drills: the metric, the Work list with the same filter, the row set's page. */
  href: string | null;
  /** Only when it could not be computed. */
  error?: string;
}

export interface StatResult extends PanelResultBase {
  type: "stat";
  measure: string;
  value: number | null;
  unit: string;
  target: number | null;
  better: "higher" | "lower" | "none";
  /** The value for the window before, when the measure can say it. */
  previous: number | null;
  history: { at: string; value: number }[];
}

export interface TrendResult extends PanelResultBase { type: "trend"; spec: TrendSpec }

export interface GrainRow { key: string; label: string; value: number; refs: string[]; href: string | null }

export interface FigureResult extends PanelResultBase {
  type: "figure";
  /** A Plotly figure built on the server. */
  figure: { data: unknown[]; layout: Record<string, unknown> };
  rows: GrainRow[];
  unit: string;
}

export interface TableColumn { key: string; label: string; unit?: string; align?: "start" | "end" }
export interface TableRow { key: string; cells: Record<string, string | number | null>; href: string | null }
export interface TableResult extends PanelResultBase { type: "table"; columns: TableColumn[]; rows: TableRow[]; total: number }

/** Rows exactly as the Work list (GET /cases, ops/worklist.rows) returns them; `href` opens that list. */
export interface CasesResult extends PanelResultBase {
  type: "cases";
  cases: Record<string, unknown>[];
  total: number;
  overdue: number;
  /** The names of the places and entities the rows name. */
  labels: Record<string, string>;
}

/** The commentary's panels add their own fields. */
export interface NoteResult extends PanelResultBase { type: "note"; [field: string]: unknown }
export interface SummaryResult extends PanelResultBase { type: "summary"; [field: string]: unknown }

export type PanelResult = StatResult | TrendResult | FigureResult | TableResult | CasesResult | NoteResult | SummaryResult;

/** What every panel component is handed (ui/src/reports/panels.tsx maps a type to its component). */
export interface PanelProps {
  report: ReportView;
  panel: Panel;
  scope: string | null;
  window: WindowKey;
  /** Null when the link names no day: each measure reads its own latest. */
  at: string | null;
  /** A panel added in the editor and not saved yet: read from `report.definition`, the definition being edited. */
  draft?: boolean;
}
