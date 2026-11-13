import type { AuthSession } from "../authState";
import { request } from "./transport";

export interface AccessAccount {
  id: string; actor: string; display_name: string; enabled: boolean; owner: boolean; roles: string[];
  tenant_id?: string; object_id?: string;
  /** Areas of each project's register the account is narrowed to act on; a project not listed: all of it. */
  areas?: Record<string, string[]>;
  /** Groups the account is in, per project; a group sees the document classes restricted to it. */
  groups?: Record<string, string[]>;
}

/** Document classes restricted to a group in one project, and every class the register holds. */
export interface DocumentGroups {
  restricted: Record<string, string>;
  classes: { doc_type: string; documents: number }[];
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
  documentGroups: (project: string) =>
    request<DocumentGroups>("GET", `/auth/projects/${encodeURIComponent(project)}/document-groups`),
  setDocumentGroups: (project: string, restricted: Record<string, string>) =>
    request<{ ok: boolean }>("PUT", `/auth/projects/${encodeURIComponent(project)}/document-groups`, undefined, { restricted }),
  setGroups: (id: string, project: string, groups: string[]) =>
    request<{ ok: boolean }>("PUT", `/auth/accounts/${encodeURIComponent(id)}/projects/${encodeURIComponent(project)}/groups`,
      undefined, { groups }),
  setAccessRole: (name: string, update: { description: string; permissions: string[] }) =>
    request<{ ok: boolean }>("PUT", `/auth/roles/${encodeURIComponent(name)}`, undefined, update),
};
