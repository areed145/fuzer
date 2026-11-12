import { request } from "./transport";

export interface SourceRow {
  dataset: string;
  connector: string;
  mapping: string | null;
  state: "new" | "unchanged" | "changed" | "held" | "unmapped";
  rows: number | null;
  loaded_at: string | null;
}

export interface SourcePreview {
  dataset: string;
  connector: string;
  path: string;
  schema: [string, string][] | null;
  drift: { added: string[]; removed: string[]; retyped: { column: string; was: string; now: string }[] } | null;
  held: boolean;
  sample: Record<string, unknown>[];
  mapping?: string;
  makes?: { record: string; entities: { kind: string; key: string | null; fields: string[] }[]; links: { rel: string; to: string }[] }[];
  suggested?: { yaml: string; key: string | null; columns: Record<string, string> };
}

export interface SourceSuggestion {
  yaml: string;
  key: string | null;
  columns: Record<string, { meaning: string; by: string }>;
  claude: { usd?: number } | null;
}

export type Json = Record<string, unknown>;

export interface SourceHealth {
  dataset: string; system: string; status: string; state: string; problems: string[]; owner: string | null;
  last_loaded: string | null; age_hours: number | null; expected_hours: number | null; rows: number | null;
  row_trend: number[]; failing_checks: { kind: string; column: string | null; value: number | null; blocking: boolean }[];
  profile_drift: { column: string; change: string }[]; coverage_pct: number | null; shared_pct: number | null;
  open_reviews: Record<string, number>;
}

export interface CatalogConnection {
  id: number; name: string; type: string; enabled: boolean; config: Json; display?: string;
  secrets: Record<string, { ref: string; resolves: boolean }>; last_tested_at: string | null; last_test: ConnectionTest | null;
}

export interface CatalogDatasetRow {
  name: string; record_type: string | null; reader: string | null; status: string; load_mode: string; rows: number | null;
  loaded_at: string | null; connection: string | null; health: SourceHealth | null; owner: string | null;
  schedule: string | null; last_run: string | null; present: boolean;
}

export interface CatalogSystem {
  id: number; name: string; kind: string | null; description: string | null; owner: string | null; steward: string | null;
  timezone: string | null; encoding: string | null; decimal: string | null; date_format: string | null; rank: number | null;
  enabled: boolean; notes: string | null; webhook_secret?: string | null; closeout?: string | null;
  closeout_role?: string | null; connections: CatalogConnection[];
  datasets: CatalogDatasetRow[];
}

/** The work a system brings (#465): exception types, rules over its loaded records, and its webhook. */
export interface SystemWorkflows {
  types: { name: string; label: string; role: string | null; process: string | null; audience: string[];
           close_case_on_clear: boolean; description: string | null; open: number }[];
  rules: { name: string; dataset: string; type: string; when: Json; clear_when?: Json; key?: string; refs?: unknown[];
           title?: string; priority?: number; value_usd?: number | string; enabled?: boolean; updated_by: string }[];
  webhook: { path: string; secret: string | null; ready: boolean };
}

export interface PackReport { [section: string]: string[] }

export interface Catalog {
  systems: CatalogSystem[];
  types: { connections: Record<string, { config: Json; secrets: string[] }>; load_modes: string[]; drift_policies: string[];
           statuses: string[] };
  check_kinds: Record<string, string>;
  data_dir: string;
  rebuild: RebuildStatus;
}

export interface RebuildStatus {
  built_at: string | null; needed: boolean; changes: { what: string; count: number; last: string }[];
  running?: { running: boolean; pending: boolean; last: Json | null };
}

export interface SuggestedCheck { kind: string; column: string | null; params: Json; blocking: boolean; why: string }

export interface ConnectionTest {
  ok: boolean; steps: { step: string; ok: boolean; ms: number; detail: unknown }[]; secrets?: Record<string, boolean>;
}

export interface MappingVersion {
  id: number; source: string; record_type: string; yaml: string; status: string; author: string | null; note: string | null;
  parent_id: number | null; created_at: string; approved_by: string | null; approved_at: string | null; current: boolean;
}

export interface SourceRun {
  id: number; trigger: string; status: string; started_at: string; finished_at: string | null; connector: string | null;
  rows_read: number | null; inserted: number | null; updated: number | null; unchanged: number | null; removed: number | null;
  error: string | null; detail: Json | null; seconds: number | null;
}

export interface SourceProfile {
  id: number; taken_at: string; rows: number; sampled: boolean;
  columns: Record<string, { type: string | null; fill_pct: number; distinct: number | string; top: [string, number][];
                             samples: string[] }>;
  key: { duplicates: number; empty: number; examples: string[] } | null;
  drift: { column: string; change: string; was?: unknown; now?: unknown; values?: string[] }[];
}

export interface SourceCheck {
  id: number; kind: string; column_name: string | null; params: Json; blocking: boolean; enabled: boolean; note: string | null;
  last_ok: boolean | null; last_value: number | null; last_detail: Json | null; last_at: string | null;
}

export interface AuditRow {
  id: number; at: string; by: string; object: string; name: string | null; action: string; old: Json | null; new: Json | null;
  reason: string | null;
}

export interface DatasetDetail {
  dataset: Json & { name: string; source: string; status: string; load_mode: string; drift_policy: string; duplicates: string;
                    schedule: string | null; expected_hours: number | null; owner: string | null; key: string[] | null;
                    location: string | null; connector: string | null; watermark: string | null; reader: string | null;
                    notes: string | null; record_type: string | null; connection_id: number | null };
  connection: (CatalogConnection & { system: string }) | null;
  secrets: Record<string, boolean>;
  mappings: MappingVersion[];
  runs: SourceRun[];
  profiles: SourceProfile[];
  checks: SourceCheck[];
  matching: { id: number; built_at: string; source: string; stats: Json }[];
  health: SourceHealth | null;
  history: AuditRow[];
  retire_preview: { records: number; entities_only_here: number; record_types?: string[] };
  connection_display: string | null;
  next_load: string | null;
  suggested_checks: SuggestedCheck[];
  scorecard?: { measures: { key: string; label: string; now: number | null; best: number | null; dropped: boolean; trend: (number | null)[] }[];
    drop: number; window_days: number; at: string | null };
  work_items: { id: number; title: string; state: string; assignee: string | null; role: string | null; created_at: string }[];
  reviews: { id: number; kind: string; created_at: string }[];
}

export interface ImpactResult {
  mapping: string; records: number; sampled: boolean; current_version: number | null;
  entities: { added: number; removed: number; examples: Json[] };
  links: { added: number; removed: number; examples: Json[] };
  fields: { changed: number; examples: Json[] };
  golden_flips: { count: number; examples: Json[] };
  errors?: { record: string; error: string }[];
}

export interface RulesList {
  ranks: { name: string; rank: number | null; enabled: boolean }[];
  rules: { id: number; scope: string; name: string; config: Json; updated_by: string; updated_at: string }[];
  calibration?: MatchCalibration[];
}

export interface MatchThresholds { auto_accept: number; review_min: number }

/** What a source's match scores were worth when last measured (P2.2). */
export interface MatchCalibration {
  kind: string; source: string; labels: string; measured_at: string; measured_by: string | null;
  pairs: number; same: number; ambiguous: number; target: number;
  recommended: MatchThresholds | null; in_use: MatchThresholds | null;
  at_auto_accept: { threshold: number; precision: number | null; lower: number | null; pairs: number; recall: number | null } | null;
}

export interface WhyValue {
  entity: string; field: string; value: unknown; source: string; rule: string; priority: string[] | null;
  review_on_conflict: boolean;
  assertions: { source: string; record: string; value: unknown; wins: boolean; record_last_changed: string | null;
                mapping_version: { id: number; author: string | null; approved_at: string | null } | null;
                run: { id: number; dataset: string; trigger: string; finished_at: string } | null }[];
}

export interface ConnectionTables {
  connection: string; system: string; type: string; tables: { table: string; dataset: string | null }[];
  /** Subfolders of a folder connection that hold tables of their own, each needing its own connection (#412). */
  subfolders?: { name: string; path: string; tables: number }[];
}

export interface TablePreview {
  connection: string; system: string; table: string; connector: string | null; path: string;
  schema: [string, string][] | null; sample: Record<string, unknown>[]; covered_by: string | null;
  date_hint: { date_format: string; example: string; certain: boolean } | null;
  suggested: { yaml: string; key: string | null; dataset: string; record_type: string;
               columns: Record<string, { meaning: string; by: string }> };
  claude: Json | null;
}

export interface AddTableResult {
  dataset: string; mapping_version: number;
  load?: { loaded: string[]; held: string[]; records: number; inserted: number; updated: number; removed: number };
  run?: SourceRun | null; health?: SourceHealth | null;
}

export interface MapperColumn {
  name: string; field: { name: string; as: string | null } | null;
  link: { rel: string; target: string; keep: Json } | null;
}

export interface MapperForm {
  source: string; record_type: string; shape: string; dataset: string | null; key: string[]; kind: string | null;
  entity_key: { column: string; as: string | null } | null; columns: MapperColumn[]; kept: string[];
  choices: { targets: Record<string, string>; field_as: Record<string, string>; key_as: Record<string, string> };
  profile?: Record<string, { samples?: string[]; type?: string; fill_pct?: number }>;
  /** What it makes that the ontology doesn't declare or allow (#241). */
  warnings?: string[];
}

export interface SourceLoad {
  paused?: string[];
  loaded: string[];
  unchanged: string[];
  held: string[];
  records: number;
  inserted: number;
  updated: number;
  removed: number;
}

/** A link a system states, as the last rebuild resolved it (P2.1c). */
export interface SystemLink {
  src: string; rel: string; dst: string; src_label: string; dst_label: string; dst_kind: string;
  confidence: number; with_others: boolean; basis: string;
}
/** How a system's records contextualized themselves on the last rebuild (P2.1). */
export interface SystemContext {
  source: string; records: number; built_at: string | null; expects_match: string[]; needs_steward: number;
  share: number | null; target: number; decided: { key: number; score: number; pinned: number; new: number };
  links: { pct: number | null; resolved: number | null; total: number | null; by_rel: Record<string, [number, number]>;
    listed: SystemLink[]; unlinked: (Omit<SystemLink, "confidence" | "basis" | "dst_kind" | "with_others"> & { decided_by: string; decided_at: string })[] };
  steward: { to_confirm: number; disagree: number; unresolved: number; unmatched: number };
  automatic: { ref: string; source_key: string; fuzer_id: string; label: string; kind: string; method: "key" | "score";
    confidence: number; basis: string; with: string[] }[];
  unmatched: { ref: string; source_key: string; fuzer_id: string; label: string; kind: string }[];
}

export const sourcesApi = {
  sources: () => request<{ data_dir: string; sources: SourceRow[] }>("GET", "/sources"),
  source: (name: string) => request<SourcePreview>("GET", `/sources/${encodeURIComponent(name)}`, { rows: 8 }),
  suggestSource: (name: string, claude: boolean) =>
    request<SourceSuggestion>("POST", `/sources/${encodeURIComponent(name)}/suggest`, undefined, { claude }),
  loadSource: (name: string, yaml: string | null, by: string) =>
    request<SourceLoad>("POST", `/sources/${encodeURIComponent(name)}/load`, undefined, { yaml, by }),
  catalog: () => request<Catalog>("GET", "/sources/catalog"),
  dataset: (name: string) => request<DatasetDetail>("GET", `/sources/datasets/${encodeURIComponent(name)}`),
  editDataset: (name: string, values: Json, by: string, reason?: string) =>
    request<Json>("PATCH", `/sources/datasets/${encodeURIComponent(name)}`, undefined, { values, by, reason }),
  datasetAction: (name: string, action: "pause" | "resume", by: string, reason?: string) =>
    request<Json>("POST", `/sources/datasets/${encodeURIComponent(name)}/${action}`, undefined, { by, reason }),
  retireDataset: (name: string, retract: boolean, by: string, reason?: string) =>
    request<Json>("POST", `/sources/datasets/${encodeURIComponent(name)}/retire`, undefined, { retract, by, reason }),
  runDataset: (name: string, full = false) =>
    request<SourceLoad & { paused: string[]; profile_drift?: Json }>("POST", `/sources/datasets/${encodeURIComponent(name)}/run`, { full: full ? "true" : undefined }),
  profileDataset: (name: string) => request<Json>("POST", `/sources/datasets/${encodeURIComponent(name)}/profile`),
  editSystem: (name: string, values: Json, by: string, reason?: string) =>
    request<Json>("PATCH", `/sources/systems/${encodeURIComponent(name)}`, undefined, { values, by, reason }),
  addSystem: (name: string, values: Json, by: string) =>
    request<Json>("POST", "/sources/systems", undefined, { name, values, by }),
  addDataset: (name: string, system: string, values: Json, by: string) =>
    request<Json>("POST", "/sources/datasets", undefined, { name, system, values, by }),
  addConnection: (system: string, body: { name: string; type: string; config: Json; secrets: Record<string, string>; by: string }) =>
    request<Json>("POST", `/sources/systems/${encodeURIComponent(system)}/connections`, undefined, body),
  editConnection: (id: number, values: Json, by: string) =>
    request<Json>("PATCH", `/sources/connections/${id}`, undefined, { values, by }),
  testConnection: (id: number) => request<ConnectionTest>("POST", `/sources/connections/${id}/test`),
  discover: (id: number) => request<{ found: string[]; uncovered: string[] }>("GET", `/sources/connections/${id}/discover`),
  proposeMapping: (yaml: string, by: string, note?: string) =>
    request<MappingVersion>("POST", "/sources/mappings", undefined, { yaml, by, note }),
  approveMapping: (id: number, by: string) => request<Json>("POST", `/sources/mappings/${id}/approve`, undefined, { by }),
  discardMapping: (id: number, by: string) => request<Json>("POST", `/sources/mappings/${id}/discard`, undefined, { by }),
  rollbackMapping: (id: number, by: string, reason?: string) =>
    request<Json>("POST", `/sources/mappings/${id}/rollback`, undefined, { by, reason }),
  impact: (body: { yaml?: string; version_id?: number; limit?: number }) => request<ImpactResult>("POST", "/sources/impact", undefined, body),
  addCheck: (name: string, body: { kind: string; column?: string | null; params: Json; blocking: boolean; by: string }) =>
    request<Json>("POST", `/sources/datasets/${encodeURIComponent(name)}/checks`, undefined, body),
  removeCheck: (id: number, by: string) => request<Json>("DELETE", `/sources/checks/${id}`, { by }),
  rules: () => request<RulesList>("GET", "/sources/rules"),
  setRule: (scope: string, name: string, config: Json, by: string) =>
    request<Json>("PUT", `/sources/rules/${scope}/${encodeURIComponent(name)}`, undefined, { config, by }),
  deleteRule: (scope: string, name: string, by: string) =>
    request<Json>("DELETE", `/sources/rules/${scope}/${encodeURIComponent(name)}`, { by }),
  setRank: (system: string, rank: number | null, by: string) =>
    request<Json>("PUT", `/sources/systems/${encodeURIComponent(system)}/rank`, undefined, { rank, by }),
  rematch: () => request<Json>("POST", "/sources/rematch"),
  calibrate: (by: string) => request<Json>("POST", "/sources/calibration", undefined, { by }),
  adoptCalibration: (by: string) => request<Json>("POST", "/sources/calibration/adopt", undefined, { by }),
  mapperForm: (yaml: string, opts: { columns?: string[]; dataset?: string } = {}) =>
    request<MapperForm>("POST", "/sources/mapper/form", undefined, { yaml, ...opts }),
  mapperYaml: (form: MapperForm, yaml: string) => request<{ yaml: string; warnings: string[] }>("POST", "/sources/mapper/yaml", undefined, { form, yaml }),
  sourceStatus: () => request<RebuildStatus>("GET", "/sources/status"),
  deleteDataset: (name: string, by: string, reason?: string) =>
    request<Json>("DELETE", `/sources/datasets/${encodeURIComponent(name)}`, { by, reason }),
  deleteConnection: (id: number, by: string) => request<Json>("DELETE", `/sources/connections/${id}`, { by }),
  deleteSystem: (name: string, by: string) => request<Json>("DELETE", `/sources/systems/${encodeURIComponent(name)}`, { by }),
  connectionTables: (id: number) => request<ConnectionTables>("GET", `/sources/connections/${id}/tables`),
  tablePreview: (id: number, table: string, opts: { claude?: boolean; dataset?: string; record_type?: string } = {}) =>
    request<TablePreview>("GET", `/sources/connections/${id}/tables/${encodeURIComponent(table)}`,
      { claude: opts.claude ? "true" : undefined, dataset: opts.dataset, record_type: opts.record_type }),
  addTable: (id: number, table: string, body: { yaml: string; values: Json; by: string; load?: boolean }) =>
    request<AddTableResult>("POST", `/sources/connections/${id}/tables/${encodeURIComponent(table)}`, undefined, body),
  sourceHealth: () => request<SourceHealth[]>("GET", "/sources/health"),
  exportPack: (system: string) => request<{ system: string; yaml: string }>("GET", `/sources/systems/${encodeURIComponent(system)}/pack`),
  importPack: (yaml: string, config: Record<string, Json>, by: string, reason?: string) =>
    request<PackReport>("POST", "/sources/packs", undefined, { yaml, config, by, reason }),
  systemContext: (system: string) => request<SystemContext>("GET", `/sources/systems/${encodeURIComponent(system)}/context`),
  systemRecordsAreNew: (system: string, by: string, reason?: string) =>
    request<{ accepted: number }>("POST", `/sources/systems/${encodeURIComponent(system)}/context/new`, undefined, { by, reason: reason ?? null }),
  systemUnlink: (system: string, l: { src: string; rel: string; dst: string }, by: string, reason?: string) =>
    request<Record<string, unknown>>("POST", `/sources/systems/${encodeURIComponent(system)}/links/unlink`, undefined, { ...l, by, reason: reason ?? null }),
  systemRelink: (system: string, l: { src: string; rel: string; dst: string }, by: string) =>
    request<Record<string, unknown>>("POST", `/sources/systems/${encodeURIComponent(system)}/links/relink`, undefined, { ...l, by }),
  systemWorkflows: (system: string) => request<SystemWorkflows>("GET", `/sources/systems/${encodeURIComponent(system)}/workflows`),
  deleteExceptionRule: (system: string, rule: string, by: string) =>
    request<Json>("DELETE", `/sources/systems/${encodeURIComponent(system)}/rules/${encodeURIComponent(rule)}`, { by }),
  why: (id: string, field: string) => request<WhyValue>("GET", `/sources/why/${encodeURIComponent(id)}/${encodeURIComponent(field)}`),
};
