import { request } from "./transport";

export interface SettingRow {
  key: string;
  group: string;
  type: "int" | "float" | "bool" | "str" | "choice" | "secret";
  description: string;
  default: unknown;
  global: unknown;
  project: unknown;
  value: unknown;
  from: "default" | "global" | "project" | "environment";
  environment?: string | null;
  updated_by: string | null;
  updated_at: string | null;
  min?: number;
  max?: number;
  choices?: string[];
  restart: boolean;
  scopes: string[];
  resolves?: boolean;
}

export interface SettingChange {
  scope: string;
  old: unknown;
  new: unknown;
  by: string;
  reason: string | null;
  at: string;
}

export type SettingScope = "global" | "project";

/** The gate policy (P5.10): fixed gates, never configurable, and each confidence gate's thresholds (null: always a
 * person) by asset criticality. */
export interface GatePolicy {
  fixed: { kind: string; label: string }[];
  safety_case_types: string[];
  confidence: { kind: string; label: string; default: number | null; by_criticality: Record<string, number | null>;
    updated_by: string | null; updated_at: string | null }[];
}

/** The onboarding journey (P8.4): each step a new asset goes through, ready, blocked or to do, and what each gap
 * prevents. */
export interface OnboardingStep {
  key: string;
  title: string;
  status: "ready" | "blocked" | "to do";
  summary: string;
  gaps: { what: string; prevents: string; link: string | null }[];
  link: string | null;
  command?: string;
  never_loaded?: string[];
}

export interface Onboarding {
  project: string | null;
  steps: OnboardingStep[];
  ready: number;
  blocked: number;
  to_do: number;
}

/** Conformance (P8.2): each asset against its operating model, and its work in practice. */
export interface StandingDeviation {
  part: string;
  key: string;
  change: "changed" | "added" | "missing";
  reason: string | null;
  owner: string | null;
  review_by: string | null;
  overdue: boolean;
  named: boolean;
}

export interface Standing {
  project: string;
  error?: string;
  note?: string;
  model?: string | null;
  version?: number;
  current?: boolean | null;
  applied?: boolean;
  deviations?: StandingDeviation[];
  unnamed?: number;
  overdue?: number;
  verdict?: "standard" | "justified" | "drifted" | "no_model";
  practice?: {
    days: number;
    sla: { on_time: number; late: number; open_past_due: number; due: number; met_pct: number | null };
    overrides: { sent_not_ready: number; started_without_permit: number; total: number };
    skipped: number;
    rounds: { rounds: number; done: number; partly_done: number; missed: number; done_pct: number | null };
  };
}

export interface Conformance {
  days: number;
  projects: Standing[];
  left_out: string[];
  current: { model: string; version: number };
}

export const settingsApi = {
  onboarding: () => request<Onboarding>("GET", "/onboarding"),
  conformance: (days = 28) => request<Conformance>("GET", `/standards/conformance?days=${days}`),
  gatePolicy: () => request<GatePolicy>("GET", "/settings/gates"),
  setGate: (kind: string, by: string, def: number | null, byCriticality: Record<string, number | null>) =>
    request<GatePolicy["confidence"][number]>("PUT", `/settings/gates/${kind}`, undefined,
      { by, default: def, by_criticality: byCriticality }),
  settings: (project?: string) =>
    request<{ project: string | null; projects: string[]; settings: SettingRow[];
      deployment?: { mode: string; configured: boolean; bootstrap_present: boolean } }>("GET", "/settings", { project }),
  setSetting: (key: string, value: unknown, scope: SettingScope, project: string | null, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/${encodeURIComponent(key)}`, undefined, { value, scope, project, by, reason }),
  resetSetting: (key: string, scope: SettingScope, project: string | null, by: string, reason: string) =>
    request<unknown>("POST", `/settings/${encodeURIComponent(key)}/reset`, undefined, { scope, project, by, reason }),
  settingHistory: (key: string) => request<SettingChange[]>("GET", `/settings/${encodeURIComponent(key)}/history`),
};

export interface DetectorParam {
  key: string;
  description: string;
  type: SettingRow["type"];
  value: unknown;
  default: unknown;
  from: SettingRow["from"];
  updated_by: string | null;
  updated_at: string | null;
  min?: number;
  max?: number;
  by_class: boolean;
  classes: { qualifier: string; value: unknown; updated_by: string; updated_at: string }[];
}

export interface DetectorRow {
  name: string;
  title: string;
  kind_label: string;
  cadence_label: string;
  raises: string[];
  about: string;
  on: boolean;
  switched: { value: unknown; updated_by: string; updated_at: string } | null;
  applicable: boolean;
  note: string | null;
  last_run: { status: "ran" | "not_applicable" | "failed" | "off"; note: string | null; found: number | null; at: string } | null;
  score: { recall: number | null; precision: number | null; false_alarms_per_week: number | null; since: string; until: string } | null;
  eval: string | null;
  settings: DetectorParam[];
  closeouts: Closeouts;
}

/** How the cases a detector or provider raised were closed out (P3.8b). */
export interface Closeouts {
  cases: number; closed: number; real: number; false_alarm: number;
  confirmed_rate: number | null; false_alarm_rate: number | null;
}

export type Trust = "route" | "triage" | "mute";

export interface ProviderRow {
  system: string; name: string; label: string; type: string; enabled: boolean;
  trust: { class: string; trust: Trust; reason: string; updated_by: string; updated_at: string }[];
  closeouts: Closeouts;
}

export interface EquipmentClass { code: string; name: string; units: number }

export const detectionApi = {
  detectors: () =>
    request<{ project: string | null; classes: EquipmentClass[]; detectors: DetectorRow[]; providers: ProviderRow[];
      trust_levels: Record<Trust, string> }>("GET", "/settings/detectors"),
  setProviderTrust: (system: string, name: string, trust: Trust, cls: string | null, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/providers/${encodeURIComponent(system)}/${encodeURIComponent(name)}/trust`, undefined,
      { trust, class: cls, by, reason }),
  switchDetector: (name: string, on: boolean, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/detectors/${encodeURIComponent(name)}/on`, undefined, { on, by, reason }),
  tuneDetector: (name: string, key: string, change: { value?: unknown; class?: string | null; reset?: boolean }, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/detectors/${encodeURIComponent(name)}/params/${encodeURIComponent(key)}`, undefined,
      { ...change, by, reason }),
};
