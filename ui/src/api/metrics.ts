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

export const metricsApi = {
  metrics: () => request<MetricDeclaration[]>("GET", "/metrics"),
  metric: (key: string, grain?: string | null) =>
    request<MetricValue>("GET", `/metrics/${encodeURIComponent(key)}${grain ? `?grain=${encodeURIComponent(grain)}` : ""}`),
};
