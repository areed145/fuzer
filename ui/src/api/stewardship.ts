// Master data stewardship (#476): queues that keep the register matching the work, and the decisions made on it.
import { request } from "./transport";

export interface UnmatchedRef {
  ref: string; exceptions: number; sources: string[]; has_case: boolean;
  open: { id: number; title: string; type: string; item_id: number | null }[];
}
export interface UnresolvedValue { source: string; rel: string; value: string; rows: number; aliased: boolean; built_at: string }
/** A rule the register learned from stewards' answers (P6.5): a source's values that wrap a key the same way. */
export interface LearnedShape { source: string; kind: string; prefix: string; suffix: string; rule: string }
export interface LearnedProposal extends LearnedShape {
  taught: number; examples: { id: number; value: string; kind: string; key: string }[]; would_answer: number; rows: number;
  values: { value: string; rows: number; rel: string; fuzer_id: string; key: string }[];
}
export interface LearnedRule extends LearnedShape {
  id: number; status: "accepted" | "rejected" | "withdrawn"; decided_by: string; decided_at: string; reason: string | null;
  answers: number;
}
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

/** Asset context completeness (P2.5): what is known about an asset, what is missing and what each gap holds back. */
export interface ContextGap {
  check: string; label: string; missing: string; prevents: string; usual: string; detectors: { name: string; title: string }[];
}
export interface AssetContext {
  fuzer_id: string; label: string; description: string | null; class_name: string; area: string | null;
  criticality: string | null; score: number | null; blocked: string[]; gaps: ContextGap[]; of: string | null;
  known: { check: string; label: string; has: boolean; expected: boolean }[]; item_id: number | null;
  suggestions?: PlacementSuggestion[];
}
/** A ranked proposal for what an asset's gaps lack (P2.1d): an area, a drawing or a document, with its evidence. */
export interface PlacementSuggestion {
  target: string; target_label: string; rel: "part_of" | "shown_on" | "applies_to"; confidence: number;
  kinds: string[]; basis: string[];
}
export interface ContextRollup { name: string; assets: number; blocked: number; score: number | null; gaps: { check: string; label: string; assets: number }[] }
export interface ContextTop {
  fuzer_id: string; label: string; description: string | null; class_name: string; area: string | null;
  criticality: string | null; score: number | null; missing: string[]; blocked: string[]; value: number; item_id: number | null;
}
export interface ContextSummary { areas: ContextRollup[]; classes: ContextRollup[]; top: ContextTop[]; checks: { check: string; label: string }[] }

export interface Proposal {
  id: number; kind: "unresolved" | "match" | "conflict" | "spelling"; subject: string; context: Record<string, unknown>;
  options: { id: string; text: string; fuzer_id?: string }[];
  answer: { choice: string; confidence: number; reason: string; evidence: string[] } | null;
  pick: string | null; status: string; model: string | null; decided_by: string | null; decided_at: string | null;
}
export interface Proposals { proposals: Proposal[]; bulk_accept_at: number; configured: boolean }

export const stewardshipApi = {
  unmatchedRefs: () => request<UnmatchedRef[]>("GET", "/stewardship/unmatched"),
  unresolvedValues: () => request<UnresolvedValue[]>("GET", "/stewardship/unresolved"),
  stewardDecisions: (q?: string, current = true) =>
    request<StewardDecision[]>("GET", "/stewardship/decisions", { q: q || undefined, current: current ? undefined : "false" }),
  addAlias: (value: string, fuzer_id: string, by: string, source?: string | null, reason?: string) =>
    request<Record<string, unknown>>("POST", "/stewardship/aliases", undefined, { value, fuzer_id, by, source: source ?? null, reason: reason ?? null }),
  learnedRules: () => request<{ proposals: LearnedProposal[]; rules: LearnedRule[] }>("GET", "/stewardship/learned"),
  decideLearned: (accept: boolean, r: LearnedShape, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/learned/${accept ? "accept" : "reject"}`, undefined,
      { source: r.source, kind: r.kind, prefix: r.prefix, suffix: r.suffix, by, reason: reason ?? null }),
  withdrawLearned: (id: number, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/learned/${id}/withdraw`, undefined, { by, reason: reason ?? null }),
  retireAlias: (id: number, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/aliases/${id}/retire`, undefined, { by, reason: reason ?? null }),
  undoPin: (left_ref: string, right_ref: string, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", "/stewardship/pins/undo", undefined, { left_ref, right_ref, by, reason: reason ?? null }),
  undoDecision: (batch: string, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", "/stewardship/pins/undo", undefined, { batch, by, reason: reason ?? null }),
  completenessGaps: () => request<CompletenessGap[]>("GET", "/stewardship/completeness"),
  assetContext: (id: string) => request<AssetContext | null>("GET", `/entities/${encodeURIComponent(id)}/context`),
  answerPlacement: (id: string, rel: string, target: string, accept: boolean, by: string) =>
    request<Record<string, unknown>>("POST", `/entities/${encodeURIComponent(id)}/placement`, undefined, { rel, target, accept, by }),
  contextSummary: () => request<ContextSummary>("GET", "/stewardship/context"),
  spellingGroups: () => request<SpellingGroup[]>("GET", "/stewardship/consistency"),
  conflictGaps: () => request<ConflictGap[]>("GET", "/stewardship/conflicts"),
  setValues: (field: string, value: string, fuzer_ids: string[], by: string, reason?: string) =>
    request<{ batch: string; changed: number }>("POST", "/stewardship/fixes", undefined, { field, value, fuzer_ids, by, reason: reason ?? null }),
  undoFix: (batch: string, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/fixes/${encodeURIComponent(batch)}/undo`, undefined, { by, reason: reason ?? null }),
  proposals: (status = "open") => request<Proposals>("GET", "/stewardship/proposals", { status }),
  runProposals: (limit = 60) =>
    request<Record<string, unknown>>("POST", "/stewardship/proposals/run", undefined, { limit }),
  acceptProposal: (id: number, by: string, choice?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/proposals/${id}/accept`, undefined, { by, choice: choice ?? null }),
  rejectProposal: (id: number, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/stewardship/proposals/${id}/reject`, undefined, { by, reason: reason ?? null }),
  acceptConfident: (by: string) =>
    request<{ accepted: number }>("POST", "/stewardship/proposals/accept-confident", undefined, { by }),
  mergePreview: (a: string, b: string) => request<MergePreview>("GET", "/stewardship/merge/preview", { a, b }),
  mergeEntities: (a: string, b: string, by: string, reason?: string) =>
    request<CurationDone>("POST", "/stewardship/merge", undefined, { a, b, by, reason: reason ?? null }),
  splitPreview: (fuzer_id: string, ref: string) => request<SplitPreview>("GET", "/stewardship/split/preview", { fuzer_id, ref }),
  splitRecord: (fuzer_id: string, ref: string, by: string, reason?: string) =>
    request<CurationDone>("POST", "/stewardship/split", undefined, { fuzer_id, ref, by, reason: reason ?? null }),
};
