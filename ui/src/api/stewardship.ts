// Master data stewardship (#476): queues that keep the register matching the work, and the decisions made on it.
import { request } from "./transport";

export interface UnmatchedRef {
  ref: string; exceptions: number; sources: string[]; has_case: boolean;
  open: { id: number; title: string; type: string; item_id: number | null }[];
}
export interface UnresolvedValue { source: string; rel: string; value: string; rows: number; aliased: boolean; built_at: string }
export interface StewardDecision {
  kind: "alias" | "must_link" | "cannot_link" | "merge" | "split" | "fix"; id: number | string; what: string; fuzer_id: string | null;
  source: string | null; by: string | null; at: string; reason: string | null; reversed: boolean;
  reversed_by?: string | null; reversed_at?: string | null;
}

interface CurationEntity {
  fuzer_id: string; kind: string; label: string | null;
  records: { source: string; record_type: string; source_key: string; method: string; ref: string }[];
  keys: { kind: string; key: string }[];
}
export interface MergePreview {
  a: CurationEntity; b: CurationEntity; differ: { field: string; a: unknown; b: unknown }[];
  keys: { kind: string; key: string }[]; records: number; replaces: string[]; note: string;
}
export interface SplitPreview {
  entity: CurationEntity; ref: string; record: { source: string; record_type: string; source_key: string };
  stays: string[]; brings: { field: string; value: unknown }[]; note: string;
}
export interface CurationDone { batch: string; decision: "merge" | "split"; rebuild: unknown }

export interface CompletenessGap {
  kind: string; field: string; have: number; total: number; missing: number; share: number;
  examples?: { fuzer_id: string; label: string | null }[];
}
export interface SpellingGroup {
  field: string; usual: string; entities: number; spellings: { value: string; entities: number; fuzer_ids: string[] }[];
}
export interface ConflictGap {
  id: number; fuzer_id: string; field: string; chosen: unknown; label: string | null; created_at: string;
  values: { source: string; source_key: string; value: unknown }[];
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
  undoDecision: (batch: string, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", "/stewardship/pins/undo", undefined, { batch, by, reason: reason ?? null }),
  completenessGaps: () => request<CompletenessGap[]>("GET", "/stewardship/completeness"),
  spellingGroups: () => request<SpellingGroup[]>("GET", "/stewardship/consistency"),
  conflictGaps: () => request<ConflictGap[]>("GET", "/stewardship/conflicts"),
  setValues: (field: string, value: string, fuzer_ids: string[], by: string, reason?: string) =>
    request<{ batch: string; changed: number }>("POST", "/stewardship/fixes", undefined, { field, value, fuzer_ids, by, reason: reason ?? null }),
  undoFix: (batch: string, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/fixes/${encodeURIComponent(batch)}/undo`, undefined, { by, reason: reason ?? null }),
  mergePreview: (a: string, b: string) => request<MergePreview>("GET", "/stewardship/merge/preview", { a, b }),
  mergeEntities: (a: string, b: string, by: string, reason?: string) =>
    request<CurationDone>("POST", "/stewardship/merge", undefined, { a, b, by, reason: reason ?? null }),
  splitPreview: (fuzer_id: string, ref: string) => request<SplitPreview>("GET", "/stewardship/split/preview", { fuzer_id, ref }),
  splitRecord: (fuzer_id: string, ref: string, by: string, reason?: string) =>
    request<CurationDone>("POST", "/stewardship/split", undefined, { fuzer_id, ref, by, reason: reason ?? null }),
};
