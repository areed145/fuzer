import { request } from "./transport";

/** A registered metric's declaration (P7.1): the same one the API, the daily report and agents read. */
export interface MetricDeclaration {
  key: string;
  label: string;
  group: string;
  definition: string;
  formula: string;
  unit: string;
  standard: string | null;
  grains: string[];
  period: string;
  better: "higher" | "lower" | "none";
  records: string;
}

export interface MetricRow {
  key: string;
  label: string;
  value: number | null;
  refs: string[];
}

export interface MetricValue extends MetricDeclaration {
  value: number | null;
  rows: MetricRow[];
  refs: string[];
  as_of: string | null;
  note: string | null;
  grain: string | null;
}

/** One side of a scorecard KPI: a period's value and the records behind it (P7.3a, #353). */
export interface KpiPeriod {
  since: string;
  through: string;
  value: number | null;
  records: number;
  refs: string[];
  note: string | null;
}

export interface Kpi {
  key: string;
  label: string;
  unit: string;
  better: "higher" | "lower";
  source: string;
  definition: string;
  now: KpiPeriod;
  before: KpiPeriod;
  change: number | null;
  verdict: "better" | "worse" | "same" | "unknown";
}

export interface PlantScorecard {
  days: number;
  now: { since: string; through: string };
  before: { since: string; through: string };
  kpis: Kpi[];
  unknown: string[];
  note: string;
}

export const metricsApi = {
  plantScorecard: (days: number, until?: string | null) =>
    request<PlantScorecard>("GET", `/plant/scorecard?days=${days}${until ? `&until=${encodeURIComponent(until)}` : ""}`),
  metrics: () => request<MetricDeclaration[]>("GET", "/metrics"),
  metric: (key: string, grain?: string | null) =>
    request<MetricValue>("GET", `/metrics/${encodeURIComponent(key)}${grain ? `?grain=${encodeURIComponent(grain)}` : ""}`),
};
