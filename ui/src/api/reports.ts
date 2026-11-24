// Report pages (docs/reports.md): definitions, and each panel read on its own through the bounded queue.
import type {
  CustomReport, PanelResult, ReportDefinition, ReportListing, ReportModel, ReportVersion, ReportView, WindowKey,
} from "../reports/spec";
import { request } from "./transport";

export interface ReportParams { scope?: string | null; window?: WindowKey | null; at?: string | null }
const params = (p: ReportParams) => ({ scope: p.scope || undefined, window: p.window || undefined, at: p.at || undefined });

export const reportsApi = {
  reports: () => request<ReportListing[]>("GET", "/reports"),
  report: (key: string, p: ReportParams) => request<ReportView>("GET", `/reports/${encodeURIComponent(key)}`, params(p)),
  reportPanel: (key: string, id: string, p: ReportParams) =>
    request<PanelResult>("GET", `/reports/${encodeURIComponent(key)}/panels/${encodeURIComponent(id)}`, params(p)),
  /** The measures, grains and row sets an editor can pick for a report of these scope kinds. */
  reportModel: (kinds: string[]) => request<ReportModel>("GET", "/model", { kinds: kinds.join(",") }),
  // custom reports (ops/custom_reports.py)
  copyReport: (key: string, title?: string) =>
    request<CustomReport>("POST", `/reports/${encodeURIComponent(key)}/copy`, undefined, { title }),
  blankReport: (title: string) => request<CustomReport>("POST", "/reports/custom", undefined, { title }),
  saveReport: (key: string, body: { definition?: ReportDefinition; text?: string; base: number }) =>
    request<CustomReport>("PUT", `/reports/${encodeURIComponent(key)}`, undefined, body),
  checkReport: (body: { definition?: ReportDefinition; text?: string }) =>
    request<{ definition: ReportDefinition; text: string }>("POST", "/reports/check", undefined, body),
  reportDraftPanel: (key: string, id: string, definition: ReportDefinition, p: ReportParams) =>
    request<PanelResult>("POST", `/reports/${encodeURIComponent(key)}/draft/panels/${encodeURIComponent(id)}`, undefined,
      { definition, ...params(p) }),
  shareReport: (key: string, shared: string) =>
    request<CustomReport>("POST", `/reports/${encodeURIComponent(key)}/share`, undefined, { shared }),
  reportVersions: (key: string) => request<ReportVersion[]>("GET", `/reports/${encodeURIComponent(key)}/versions`),
  restoreReport: (key: string, version: number) =>
    request<CustomReport>("POST", `/reports/${encodeURIComponent(key)}/versions/${version}/restore`),
  removeReport: (key: string) => request<{ ok: true }>("DELETE", `/reports/${encodeURIComponent(key)}`),
};
