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

/** A well's latest test against its own decline forecast (P7.3b, #336). */
export interface WellForecast {
  id: string;
  well: string;
  verdict: "below_forecast" | "above_forecast" | "on_forecast" | "no_test_in_window" | "too_little_data";
  gap_sm3d?: number | null;
  below_since?: string | null;
  latest?: { at: string; actual_sm3d: number; forecast_sm3d: number; low_sm3d: number | null; high_sm3d: number | null };
  downtime: { id: string; start: string; reason: string | null; lost_oil_sm3: number | null }[];
}

export interface ForecastVsActual {
  since: string;
  until: string;
  source: string;
  unit: string;
  wells: WellForecast[];
  not_fitted: { id: string; well: string; tests_before: number; reason: string }[];
  field: { wells_compared: number; forecast_sm3d: number; actual_sm3d: number; gap_sm3d: number;
    below_forecast: string[]; above_forecast: string[] };
  note: string;
}

/** A unit's cost of unreliability (P7.3c, #352): lost oil at the pack's price beside corrective order cost, each in
 * its own currency; a total only when both share one. */
export interface UnreliabilityUnit {
  id: string;
  unit: string;
  description: string | null;
  lost_oil_sm3: number;
  lost_oil_value: number;
  order_cost: Record<string, number>;
  orders: number;
  orders_without_cost: number;
  total: number | null;
  total_currency: string | null;
  why_no_total: string | null;
}

export interface CostOfUnreliability {
  since: string;
  until: string;
  price: { oil_sm3: number; currency: string; source: string };
  order_currencies: string[];
  units: UnreliabilityUnit[];
  units_total: number;
  source: string;
  note: string;
}

/** One well intervention's result, net of decline (P7.5a, #340). */
export interface InterventionGain {
  id: string;
  well_id: string;
  well: string;
  start: string;
  reason: string | null;
  verdict: "gained" | "lost" | "no_clear_change" | "unknown";
  why?: string | null;
  tests_before: number;
  tests_after: number;
  oil_before_sm3d?: number;
  oil_after_sm3d?: number;
  oil_expected_sm3d?: number;
  oil_range_sm3d?: [number, number] | null;
  gain_sm3d?: number;
  water_cut_change_pct: number | null;
  gor_change: number | null;
}

export interface InterventionGains {
  since: string;
  until: string;
  source: string;
  interventions: InterventionGain[];
  summary: { interventions: number; said: number; unknown: number; gained: number; lost: number;
    no_clear_change: number; net_gain_sm3d: number | null };
  note: string;
}

/** Across assets (P7.5b): each granted project's scorecard; the projects left out are named (D6). */
export interface FleetScorecards {
  days: number;
  projects: { project: string; scorecard?: PlantScorecard; error?: string }[];
  left_out: string[];
  kpis: { key: string; label: string; unit: string; better: string }[];
  note: string;
}

export const metricsApi = {
  fleetScorecards: (days: number) => request<FleetScorecards>("GET", `/plant/scorecard/fleet?days=${days}`),
  interventionGains: () => request<InterventionGains>("GET", "/plant/intervention-gains"),
  costOfUnreliability: () => request<CostOfUnreliability>("GET", "/plant/cost-of-unreliability"),
  forecastVsActual: () => request<ForecastVsActual>("GET", "/plant/forecast-vs-actual"),
  plantScorecard: (days: number, until?: string | null) =>
    request<PlantScorecard>("GET", `/plant/scorecard?days=${days}${until ? `&until=${encodeURIComponent(until)}` : ""}`),
  metrics: () => request<MetricDeclaration[]>("GET", "/metrics"),
  metric: (key: string, grain?: string | null) =>
    request<MetricValue>("GET", `/metrics/${encodeURIComponent(key)}${grain ? `?grain=${encodeURIComponent(grain)}` : ""}`),
};
