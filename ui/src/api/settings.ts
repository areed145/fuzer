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

export const settingsApi = {
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
}

export interface EquipmentClass { code: string; name: string; units: number }

export const detectionApi = {
  detectors: () =>
    request<{ project: string | null; classes: EquipmentClass[]; detectors: DetectorRow[] }>("GET", "/settings/detectors"),
  switchDetector: (name: string, on: boolean, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/detectors/${encodeURIComponent(name)}/on`, undefined, { on, by, reason }),
  tuneDetector: (name: string, key: string, change: { value?: unknown; class?: string | null; reset?: boolean }, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/detectors/${encodeURIComponent(name)}/params/${encodeURIComponent(key)}`, undefined,
      { ...change, by, reason }),
};
