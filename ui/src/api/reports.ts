// Report pages (docs/reports.md): definitions, and each panel read on its own through the bounded queue.
import type { PanelResult, ReportListing, ReportView, WindowKey } from "../reports/spec";
import { request } from "./transport";

export interface ReportParams { scope?: string | null; window?: WindowKey | null; at?: string | null }
const params = (p: ReportParams) => ({ scope: p.scope || undefined, window: p.window || undefined, at: p.at || undefined });

export const reportsApi = {
  reports: () => request<ReportListing[]>("GET", "/reports"),
  report: (key: string, p: ReportParams) => request<ReportView>("GET", `/reports/${encodeURIComponent(key)}`, params(p)),
  reportPanel: (key: string, id: string, p: ReportParams) =>
    request<PanelResult>("GET", `/reports/${encodeURIComponent(key)}/panels/${encodeURIComponent(id)}`, params(p)),
};
