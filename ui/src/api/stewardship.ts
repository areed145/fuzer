// Master data stewardship (#476): queues that keep the register matching the work, and the decisions made on it.
import { request } from "./transport";

export interface UnmatchedRef {
  ref: string; exceptions: number; sources: string[]; has_case: boolean;
  open: { id: number; title: string; type: string; item_id: number | null }[];
}
export interface UnresolvedValue { source: string; rel: string; value: string; rows: number; aliased: boolean; built_at: string }
export interface StewardDecision {
  kind: "alias" | "must_link" | "cannot_link"; id: number | string; what: string; fuzer_id: string | null;
  source: string | null; by: string | null; at: string; reason: string | null; reversed: boolean;
  reversed_by?: string | null; reversed_at?: string | null;
}

export const stewardshipApi = {
  unmatchedRefs: () => request<UnmatchedRef[]>("GET", "/stewardship/unmatched"),
  unresolvedValues: () => request<UnresolvedValue[]>("GET", "/stewardship/unresolved"),
  stewardDecisions: (q?: string, current = true) =>
    request<StewardDecision[]>("GET", "/stewardship/decisions", { q: q || undefined, current: current ? undefined : "false" }),
  addAlias: (value: string, fuzer_id: string, by: string, source?: string | null, reason?: string) =>
    request<Record<string, unknown>>("POST", "/stewardship/aliases", undefined, { value, fuzer_id, by, source: source ?? null, reason: reason ?? null }),
  retireAlias: (id: number, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/aliases/${id}/retire`, undefined, { by, reason: reason ?? null }),
  undoPin: (left_ref: string, right_ref: string, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", "/stewardship/pins/undo", undefined, { left_ref, right_ref, by, reason: reason ?? null }),
};
