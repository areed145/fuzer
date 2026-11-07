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
