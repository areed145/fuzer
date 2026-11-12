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

/** Against the accounts (P7.5c, #372): a month's daily allocated volume against what production accounting closed. */
export interface AccountingMonth {
  period: string;
  product: "oil" | "gas";
  unit: string;
  accounting: { value: number | null; status: string; since: string; through: string; comment: string | null;
    record: string; source: string } | null;
  operational: { value: number | null; days: number; days_expected?: number; since: string | null;
    through: string | null; refs: string[]; source: string };
  difference: number | null;
  difference_pct: number | null;
  verdict: "agrees" | "differs" | "preliminary" | "incomplete" | "no_accounting" | "no_operational";
  why: string;
}

export interface UnitCostMonth {
  period: string;
  currency: string;
  cost: number;
  by_element: Record<string, number>;
  oil_sm3: number | null;
  oil_status: string | null;
  per_sm3: number | null;
  why_unknown: string | null;
  refs: string[];
}

export interface PlantAccounting {
  reconciliation: { since: string; until: string; tolerance_pct: number; months: AccountingMonth[];
    summary: Record<AccountingMonth["verdict"], number>; sources: { operational: string; accounting: string };
    note: string };
  unit_cost: { since: string; until: string; currencies: string[]; months: UnitCostMonth[];
    window: Record<string, { cost: number; oil_sm3: number; per_sm3: number }>;
    sources: { cost: string; volume: string }; note: string };
}

/** Is the backlog under control? (#348) */
export interface BacklogBurn {
  since: string; until: string; source: string; note: string;
  months: { month: string; since: string; until: string; created: number; closed: number; net: number;
    open_at_end: number; closed_without_finish: number; by_type: Record<string, { created: number; closed: number; open_at_end: number }> }[];
  summary: { created: number; closed: number; open_at_start: number; open_at_end: number | null;
    trend: "growing" | "steady" | "shrinking" };
  lag: { orders: number; with_notification: number; without_notification: number; ordered_before_notified: number;
    median_days: number | null; p90_days: number | null; max_days: number | null;
    longest: { order: string; label: string; notification: string; notified: string; ordered: string; lag_days: number;
      type: string | null; priority: string | null }[] };
}

/** Water breakthrough, gas and test age across the wells (#339). */
export interface WellScreen {
  wells_screened: number; source: string; note: string;
  thresholds: { water_cut_step_points: number; gor_rise: number; stale_days: number };
  patterns: { well_id: string; well: string; verdict: "water_cut_step" | "gor_climb"; tests: number;
    water_cut_step: { at: string; record: string; cum_oil_sm3: number; water_cut_before_pct: number;
      water_cut_after_pct: number; oil_before_sm3d: number | null; oil_after_sm3d: number | null } | null;
    gor_climb: { first_gor: number; latest_gor: number; ratio: number } | null }[];
  staleness: { as_of: string; max_days: number; note: string;
    summary: { current: number; stale: number; no_test_on_file: number; wells: number; stale_share_pct: number | null;
      median_age_days: number | null; oldest_days: number | null };
    wells: { id: string; well: string; tests: number; latest_test: string | null; age_days: number | null;
      status: "current" | "stale" | "no_test_on_file" }[] };
}

/** Allocated oil against the wells' tested offer, month by month (#338). */
export interface AllocationFactor {
  since: string; until: string; source?: string; note: string;
  verdict: "steady" | "low" | "high" | "drifting" | "no_tests" | "no_daily_report";
  flags: ("low" | "high" | "drifting")[];
  months: { month: string; days: number; days_left_out: number;
    left_out: { facility_or_tag_downtime: number; field_trip: number };
    allocated_sm3: number; offered_sm3: number; factor: number | null; factor_ignoring_downtime: number | null;
    wells_offering: number; well_days_shut_in: number; wells_without_a_live_test: number;
    median_test_age_days: number | null; max_test_age_days: number | null }[];
  summary: { months: number; factor: number | null; first_months_factor: number | null;
    latest_months_factor: number | null; drift: number | null; days_used: number; days_left_out: number;
    band: [number, number]; drift_threshold: number; drift_months: number; test_days: number };
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
  allocationFactor: () => request<AllocationFactor>("GET", "/plant/allocation-factor"),
  wellScreen: () => request<WellScreen>("GET", "/plant/well-screen"),
  backlogBurn: () => request<BacklogBurn>("GET", "/plant/backlog-burn"),
  plantAccounting: () => request<PlantAccounting>("GET", "/plant/accounting"),
  interventionGains: () => request<InterventionGains>("GET", "/plant/intervention-gains"),
  costOfUnreliability: () => request<CostOfUnreliability>("GET", "/plant/cost-of-unreliability"),
  forecastVsActual: () => request<ForecastVsActual>("GET", "/plant/forecast-vs-actual"),
  plantScorecard: (days: number, until?: string | null) =>
    request<PlantScorecard>("GET", `/plant/scorecard?days=${days}${until ? `&until=${encodeURIComponent(until)}` : ""}`),
  metrics: () => request<MetricDeclaration[]>("GET", "/metrics"),
  metric: (key: string, grain?: string | null) =>
    request<MetricValue>("GET", `/metrics/${encodeURIComponent(key)}${grain ? `?grain=${encodeURIComponent(grain)}` : ""}`),
};
