import type { AuthSession } from "../authState";
import { request } from "./transport";

export interface AccessAccount {
  id: string; actor: string; display_name: string; enabled: boolean; owner: boolean; roles: string[];
  tenant_id?: string; object_id?: string;
  /** Areas of each project's register the account is narrowed to act on; a project not listed: all of it. */
  areas?: Record<string, string[]>;
}

export interface AccessRole { name: string; description: string; permissions: string[]; }

export interface AccessAdministration {
  accounts: AccessAccount[]; roles: AccessRole[]; permissions: Record<string, string>;
}

export interface DemoConfig {
  enabled: boolean;
}

export const identityApi = {
  demoConfig: () => request<DemoConfig>("GET", "/demo/config"),
  authSession: () => request<AuthSession>("GET", "/auth/me"),
  signOut: () => request<{ ok: boolean }>("POST", "/auth/logout"),
  bootstrapOwner: (token: string) => request<{ ok: boolean }>("POST", "/auth/bootstrap", undefined, { token }),
  accessAdministration: () => request<AccessAdministration>("GET", "/auth/access"),
  updateAccount: (id: string, update: { enabled: boolean; owner: boolean; roles: string[] }) =>
    request<{ ok: boolean }>("PUT", `/auth/accounts/${encodeURIComponent(id)}`, undefined, update),
  setAreas: (id: string, project: string, areas: string[]) =>
    request<{ ok: boolean }>("PUT", `/auth/accounts/${encodeURIComponent(id)}/projects/${encodeURIComponent(project)}/areas`,
      undefined, { areas }),
  setAccessRole: (name: string, update: { description: string; permissions: string[] }) =>
    request<{ ok: boolean }>("PUT", `/auth/roles/${encodeURIComponent(name)}`, undefined, update),
};
