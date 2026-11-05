// Typed client for the Fuzer API.

export interface Stats {
  pack: string;
  facility: string;
  entities: Record<string, number>;
  open_reviews: Record<string, number>;
  records: number;
  edges: number;
  rebuild: { running: boolean; pending: boolean; last: { finished?: string; error?: string } | null };
}

export interface SearchHit {
  fuzer_id: string;
  kind: string;
  label: string | null;
  description: string | null;
}

export interface Edge {
  direction: "in" | "out";
  rel: string;
  other: string;
  other_kind: string;
  other_label: string | null;
  other_description: string | null;
  valid_from: string | null;
  valid_to: string | null;
  sources: string[];
  confidence: number;
  props: Record<string, unknown> | null;
}

export interface SourceValue {
  source: string;
  source_key: string | null;
  value: unknown;
}

export interface EntityDetail {
  fuzer_id: string;
  kind: string;
  label: string | null;
  merged_into?: string;
  keys: { kind: string; key: string }[];
  golden: Record<string, { value: unknown; source: string; rule: string }>;
  values: Record<string, SourceValue[]>;
  records: { source: string; record_type: string; source_key: string; method: string; confidence: number }[];
  edges: Edge[];
  edge_counts: Record<string, number>;
}

export interface TraceRow {
  fuzer_id: string;
  depth: number;
  valid_from: string | null;
  valid_to: string | null;
  kind: string;
  label: string | null;
}

export interface MatchSide {
  ref: string;
  source: string;
  key: string | null;
  block: string | null;
  fields: Record<string, unknown>;
}

export interface MatchPayload {
  left: MatchSide;
  right: MatchSide;
  score: number;
  why: string;
}

export interface ConflictPayload {
  fuzer_id: string;
  field: string;
  chosen: unknown;
  values: SourceValue[];
}

export interface SchemaPayload {
  dataset: string;
  connector: string;
  path: string;
  old: [string, string][];
  new: [string, string][];
  added: string[];
  removed: string[];
  retyped: { column: string; was: string; now: string }[];
}

export interface ColumnProfile {
  name: string;
  filled: number;
  distinct: number;
  samples: string[];
}

export interface MappingPayload {
  dataset: string;
  connector: string;
  path: string;
  profile: ColumnProfile[];
  suggestion: {
    key: string | null;
    shape: string;
    kind: string | null;
    columns: Record<string, { meaning: string; by: "rules" | "claude"; why: string; ref?: string }>;
  };
  yaml: string;
}

export interface GeoFeature {
  type: "Feature";
  id: string;
  geometry: { type: string; coordinates: unknown };
  properties: { id: string; kind: string; label: string | null; [field: string]: unknown };
}

export interface GeoCollection {
  type: "FeatureCollection";
  features: GeoFeature[];
  truncated?: boolean;
  focus?: string | null;
}

export type MarkStatus = "linked" | "not_in_register" | "not_a_tag" | "line";

export interface DrawingMark {
  text: string;
  key?: string | null;
  status: MarkStatus;
  x?: number;
  y?: number;
  id?: string | null;
  confidence?: number;
}

export interface Drawing {
  sheet: string;
  format: string;
  title: string | null;
  read_by: string | null;
  image: string;
  width: number;
  height: number;
  marks: DrawingMark[];
  connections: { from: string; to: string; line: string | null; from_id: string | null; to_id: string | null }[];
  findings: { text: string; kind: string; issue: string }[];
}

export type ReviewKind = "match" | "conflict" | "schema" | "mapping" | "curve" | "check";

export interface CurveReading {
  curve: "pump" | "choke";
  read_from: string;
  notes: string;
  units: Record<string, string>;
  series: Record<string, [number, number][]>;
  ratings: Record<string, [number, number | null]>;
}

export interface CurvePayload {
  document: string | null;
  doc_number: string;
  title: string;
  manufacturer: string;
  model: string;
  reading: CurveReading;
}

export interface HeldLoadPayload {
  dataset: string; failed: { kind: string; value: number; detail?: { examples?: string[] } }[];
}

export type ReviewItem =
  | { id: number; kind: "match"; subject: string; status: string; label: null; payload: MatchPayload }
  | { id: number; kind: "schema"; subject: string; status: string; label: null; payload: SchemaPayload }
  | { id: number; kind: "mapping"; subject: string; status: string; label: null; payload: MappingPayload }
  | { id: number; kind: "curve"; subject: string; status: string; label: null; payload: CurvePayload }
  | { id: number; kind: "check"; subject: string; status: string; label: null; payload: HeldLoadPayload }
  | {
      id: number;
      kind: "conflict";
      subject: string;
      status: string;
      label: string | null;
      description: string | null;
      payload: ConflictPayload;
    };

export interface GraphNode {
  id: string;
  kind: string;
  label: string | null;
  description: string | null;
}

export interface GraphEdge {
  id: number;
  src: string;
  rel: string;
  dst: string;
  valid_from: string | null;
  valid_to: string | null;
  sources: string[];
}

export interface Subgraph {
  root?: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
}

export interface OntologyKind {
  kind: string;
  count: number;
  fields: { field: string; count: number }[];
  sources: { source: string; records: number }[];
  /** Whether the ontology declares this kind (#241); the rest only when it does. */
  declared?: boolean;
  label?: string;
  description?: string;
  group?: string | null;
  colour?: string | null;
  status?: OntologyStatus;
}

export interface OntologyRelation {
  src_kind: string;
  rel: string;
  dst_kind: string;
  n: number;
  dated: number;
  sources: string[];
  declared?: boolean;
  /** Its link type allows these two kinds. */
  allowed?: boolean;
  view?: string | null;
  label?: string;
}

export type OntologyStatus = "core" | "custom" | "draft";

/** A declared entity kind (#241). */
export interface KindDef {
  name: string;
  label: string;
  description: string;
  group: string | null;
  colour: string | null;
  label_field: string | null;
  status: OntologyStatus;
  edited: boolean;
  updated_by: string;
  updated_at: string;
  count?: number;
}

/** A declared link type (#241); empty from / to kinds means any kind. */
export interface RelationDef {
  name: string;
  label: string;
  description: string;
  view: string | null;
  dated: boolean;
  from_kinds: string[];
  to_kinds: string[];
  status: OntologyStatus;
  edited: boolean;
  updated_by: string;
  updated_at: string;
  count?: number;
}

export interface OntologyDeclared {
  groups: string[];
  views: { id: string; label: string }[];
  kinds: KindDef[];
  relations: RelationDef[];
}

export interface Ontology {
  kinds: OntologyKind[];
  relations: OntologyRelation[];
  groups: string[];
  views: { id: string; label: string }[];
  model: { kinds: KindDef[]; relations: RelationDef[] };
  needs_definition: {
    kinds: string[];
    relations: string[];
    not_allowed: { src_kind: string; rel: string; dst_kind: string; n: number }[];
  };
}

export interface OntologyChange {
  id: number;
  at: string;
  by: string;
  object: "kind" | "relation";
  name: string;
  action: string;
  old: Record<string, unknown> | null;
  new: Record<string, unknown> | null;
  reason: string | null;
}

export type KindIn = Partial<Pick<KindDef, "name" | "label" | "description" | "group" | "colour" | "label_field" | "status">>;
export type RelationIn = Partial<Pick<RelationDef, "name" | "label" | "description" | "view" | "dated" | "from_kinds" | "to_kinds" | "status">>;

export interface FuzerEvent {
  id: string;
  type: string | null;
  summary: string | null;
  timestamp: string | null;
  details: Record<string, unknown>;
  involves: { id: string; label: string | null; kind: string; role: string | null }[];
  log: { id: string; label: string | null; text: string | null } | null;
}

export interface Maintenance {
  total: number;
  failure_modes: { code: string | null; name: string | null; count: number }[];
  recent: {
    id: string;
    label: string | null;
    created: string | null;
    type: string | null;
    failure_mode: string | null;
    failure_mode_name: string | null;
    failure_mechanism_name: string | null;
    text: string | null;
    work_orders: { id: string; label: string | null; activity: string | null; start: string | null; cost: string | null }[];
  }[];
}

export interface DowntimeRow {
  id: string;
  label: string | null;
  start: string | null;
  end: string | null;
  duration_h: number | null;
  reason: string | null;
  reason_category: string | null;
  lost_oil_sm3: number | null;
  comment: string | null;
  reason_check: string | null;
  affects: { id: string; label: string | null }[];
  caused_by: { id: string; label: string | null }[];
}

export interface Downtime {
  events: number;
  lost_oil_sm3: number;
  hours: number;
  recent: DowntimeRow[];
}

export interface RankRow {
  label: string | null;
  description: string | null;
  fuzer_id: string | null;
  events: number;
  lost_oil_sm3: number;
  hours: number;
}

export interface Incident {
  id: string;
  description: string;
  unit: string;
  start: string;
  end: string;
  signals: number;
  events: number;
  downtime_records: number;
  notifications: number;
  lost_oil_sm3: number;
  failure_modes?: string[];
  event_types?: string[];
}

export interface Upset {
  id: number;
  kind: string;
  status: string;
  start: string;
  end: string | null;
  detections: number;
  work_item_id: number | null;
  priority_usd: number | null;
  first_unit: string | null;
  first_detector: string | null;
  first_key: string | null;
  first_point: string | null;
  units: (string | null)[];
}

export interface TimelineItem {
  id: string;
  kind: string;
  label: string | null;
  at: string | null;
  text: string | null;
  detail: string | null;
  lost_oil_sm3: number | null;
}

export interface Passage {
  id: string;
  kind: string;
  score: number;
  document: string | null;
  document_label: string | null;
  title: string | null;
  doc_type: string | null;
  heading: string | null;
  page: number | null;
  snippet: string;
  why?: string;
}

export interface DocRef {
  id: string;
  label: string;
  title: string | null;
  doc_type: string | null;
  revision: string | null;
  why: string;
}

export interface Lesson {
  id: number;
  title: string;
  text: string;
  draft: string;
  failure_mode: string;
  status: string;
  stats: { incidents: number; fixed: number; fix_rate: number };
  evidence: { id: string; label: string; start: string; unit: string; recurred: boolean }[];
  decided_by: string | null;
}

export interface LeadIn {
  baseline_mean: number;
  mean: number;
  extreme: number;
  change_pct: number | null;
}

export interface MeasurementSeries {
  quantity: string;
  location: string | null;
  of: { id: string; label: string | null };
  unit: string | null;
  count: number;
  first: string;
  min: number;
  max: number;
  latest: { at: string; value: number; qualifier: string | null; method: string | null; record: string };
  per_year: number | null;
  values?: { at: string; value: number; qualifier: string | null; reported: string | null; method: string | null;
             record: string }[];
}

export interface Values {
  provider: string;
  centre: string;
  start: string;
  end: string;
  step_minutes: number;
  upsets: string[];
  series: import("./Trend").Series[];
  errors?: string[];
  error?: string;
}

export interface Brief {
  about: { id: string; label: string | null };
  suggested_checks: { step: string; from: string }[];
  lessons: { approved: { id: number; title: string }[]; drafts_not_yet_reviewed: { id: number; title: string }[] };
  spares: { fuzer_id: string; label: string; description: string; stock: number; bin: string }[];
  process_values: {
    provider: string;
    centre: string;
    note: string | null;
    errors?: string[];
    series: { id: string; point: string; description: string | null; uom: string | null; lead_in?: LeadIn }[];
  };
  measurements: MeasurementSeries[];
}

export interface BriefRatings {
  incidents: number;
  rated: number;
  ratings: number;
  useful_share: number | null;
  items: { id: string; description: string | null; start: string; ratings: number; useful: number;
           last_comment: string | null }[];
}

export interface FigureData {
  id: string;
  kind: string;
  title: string;
  spec: { data: any[]; layout: Record<string, any> };
  summary: Record<string, unknown>;
  sources: string[];
  by_source: "fuzer" | "assistant";
  created_at: string;
}

/** An image attached to a question: base64 data, plus a local URL for the preview. */
export interface Attachment {
  name: string;
  media_type: "image/png" | "image/jpeg" | "image/gif" | "image/webp";
  data: string;
  url: string;
}

export interface AssistantJob {
  id: string;
  conversation_id: string;
  question: string;
  status: "running" | "done" | "error";
  answer: string | null;
  error: string | null;
  seconds?: number;
  specialists?: string[];
  usage?: { model_calls: number; input: number; output: number; cache_read: number; cache_write: number; cost_usd: number;
            models: Record<string, { calls: number; cost_usd: number; unpriced?: boolean }> };
  steps: { agent: string; tool: string; args: Record<string, unknown> }[];
  model_calls: number;
  recalled?: number[];
}

export interface AssistantAnswer {
  id: string;
  question: string;
  answer: string | null;
  by_user: string;
  created_at: string;
  specialists: string[];
  usage: { cost_usd?: number };
  ratings: number;
  useful: number;
}

export interface AssistantConversation {
  id: string;
  title: string;
  by_user: string;
  created_at: string;
  updated_at: string;
  turns: number;
}

export interface SavedTurn {
  id: string;
  question: string;
  answer: string | null;
  error: string | null;
  specialists: string[];
  tools: { agent: string; tool: string; args?: Record<string, unknown> }[];
  usage: AssistantJob["usage"];
  seconds: number | null;
  images: string[];
  recalled: number[];
  created_at: string;
  ratings: number;
  useful: number;
}

export interface AssistantFact {
  id: number;
  text: string;
  subject: string;
  entity_ids: string[];
  entities: { id: string; label: string | null }[];
  valid_from: string | null;
  valid_to: string | null;
  learned_at: string;
  episode: string | null;
  episode_question: string | null;
  sources: string[];
  status: "current" | "ended";
  invalidated_by: number | null;
  by_user: string;
  edited_by: string | null;
  ended_by: string | null;
  ended_at: string | null;
}

export interface Decision {
  decision: "accept" | "reject" | "choose";
  value?: unknown;
  by: string;
}

// --- work engine (#97) ---

export interface WorkProblem {
  element: string | null;
  message: string;
}

export interface WorkDefinition {
  id: number;
  process_key: string;
  version: number;
  name: string | null;
  state: "draft" | "checked" | "published" | "superseded";
  problems: WorkProblem[];
  created_by: string;
  created_at: string;
  published_by: string | null;
  published_at: string | null;
  running: number;
}

export interface WorkChange {
  element: string;
  type: string;
  name: string | null;
  change: "added" | "removed" | "changed";
  fields?: { field: string; old: unknown; new: unknown }[];
}

export interface WorkCompare {
  id: number;
  version: number;
  against: { id: number; version: number; state: string } | null;
  changes: WorkChange[];
}

export interface WorkMonitorCell {
  name: string | null;
  waiting: number;
  overdue: number;
  oldest: string | null;
  done: number;
  median_seconds: number | null;
}

export interface WorkMonitor {
  process_key: string;
  definition_id: number | null;
  days: number;
  at: string;
  elements: Record<string, WorkMonitorCell>;
  processes: { definition_id: number; version: number; state: string; outcome: string | null; n: number }[];
}

export interface WorkCounts {
  total: number;
  overdue: number;
  due_soon: number;
}

/** A person role (#202): the processes with steps for it, who holds it and its unclaimed queue. */
export type WorkRoleSummary = { role: string; processes: string[]; people: string[]; waiting: number };

/** A row in the inbox: a process step (task_id set) or a work item without a process. */
export interface WorkRow {
  task_id: number | null;
  element: string | null;
  name: string | null;
  kind: string;
  role: string | null;
  assignee: string | null;
  requires_human: boolean;
  due_at: string | null;
  escalated_at: string | null;
  error: string | null;
  created_at: string;
  overdue: boolean | null;
  claimed_by: string | null;
  item_id: number;
  type: string;
  title: string;
  priority: number | null;
  subjects: string[];
  process_id: number | null;
  process_key: string | null;
  bulk: string[];
  covering_for?: string | null;
}

// ---- workflows: roles, routing, cases, process statistics
export interface WorkRoleRow {
  name: string; kind: "person" | "agent"; description: string | null; default_sla: string | null;
  escalate_to: string | null; assignment: "queue" | "round_robin" | "least_loaded"; updated_by: string | null;
  members: { name: string; away: boolean; away_until: string | null; delegate: string | null }[];
  processes: string[]; waiting: number; overdue: number; escalated: number; uncovered: boolean;
}
export interface WorkloadPerson {
  name: string; roles: string[]; assigned: number; overdue: number; away: boolean; away_until: string | null;
  delegate: string | null; note: string | null;
}
export interface Workload {
  people: WorkloadPerson[];
  roles: Record<string, { waiting: number; overdue: number; escalated: number }>;
  agents: { role: string; waiting: number; failing: number }[];
  agent_failures: { at: string; actor: string; item_id: number | null; data: Record<string, unknown>; title: string | null }[];
}
export interface ProcessStats {
  process_key: string; name: string | null; version: number; running: number; finished: number; median_h: number | null;
  p90_h: number | null; waiting: number; overdue: number; steps: number; breached: number; sla_breach_pct: number | null;
}
export interface StepRouting {
  element: string; step: string; kind: "person" | "agent"; role: string | null; sla: string | null;
  sla_from: "step" | "role" | null; escalation: string[]; escalation_from: "step" | "role" | null;
  assignment: string | null; form: string | null; requires_human: boolean; fallback_role: string | null;
}
export interface ProcessInstance {
  id: number; item_id: number; title: string; priority: number | null; state: string; outcome: string | null;
  started_at: string; ended_at: string | null; started_by: string; version: number; age_h: number;
  open_steps: { task_id: number; step: string; kind: string; role: string | null; assignee: string | null;
                due_at: string | null; overdue: boolean | null; escalated: boolean }[];
}
export interface CaseRow {
  id: number; type: string; title: string; subjects: string[]; state: string; outcome: string | null; step: string | null;
  priority: number | null; assignee: string | null; role: string | null; due: string | null; created_by: string;
  source: string | null; created_at: string; updated_at: string; closed_at: string | null; overdue: boolean | null;
  process_key: string | null; escalated: boolean;
}

export interface WorkInbox {
  person: string;
  roles: string[];
  covering?: string[];
  mine: WorkRow[];
  queue: WorkRow[];
  counts: { mine: WorkCounts; queue: WorkCounts };
}

export interface WorkEvent {
  id: number;
  kind: string;
  actor: string;
  element: string | null;
  data: Record<string, any>;
  at: string;
  process_id?: number | null;
  token_id?: number | null;
}

export interface WorkStep {
  id: number;
  process_id: number;
  element: string;
  name: string | null;
  kind: string;
  state: string;
  flow: string | null;
  role: string | null;
  assignee: string | null;
  requires_human: boolean;
  due_at: string | null;
  escalated_at: string | null;
  attempts: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  finished_by: string | null;
}

export interface WorkProcess {
  id: number;
  process_key: string;
  version: number;
  definition_id: number;
  parent_token: number | null;
  correlation_key: string | null;
  state: string;
  outcome: string | null;
  data: Record<string, unknown>;
  started_by: string;
  started_at: string;
  ended_at: string | null;
  path: WorkStep[];
  open: WorkStep[];
}

export interface WorkItem {
  id: number;
  type: string;
  title: string;
  subjects: string[];
  state: string;
  outcome: string | null;
  step: string | null;
  priority: number | null;
  assignee: string | null;
  role: string | null;
  due: string | null;
  created_by: string;
  source: string | null;
  created_at: string;
  closed_at: string | null;
  processes: WorkProcess[];
  events: WorkEvent[];
}

export interface BulkAction {
  label: string;
  data: Record<string, unknown>;
  reason_key?: string;
}

export interface WorkTaskView {
  task: Omit<WorkStep, "id" | "flow" | "process_id"> & {
    task_id: number;
    claimed_by: string | null;
    lease_until: string | null;
    overdue: boolean | null;
    process_id: number;
    item_id: number;
    process_key: string;
    definition_id: number;
    version: number;
  };
  item: Omit<WorkItem, "processes" | "events">;
  data: Record<string, unknown>;
  form: { key: string; version: number | null; schema: Record<string, unknown> | null } | null;
  bulk: BulkAction[];
  evidence: {
    agents: WorkEvent[];
    messages: WorkEvent[];
    comments: WorkEvent[];
    subjects: { fuzer_id: string; kind: string | null; label: string | null }[];
  };
}

type Params = Record<string, string | number | undefined | null>;

export interface BoardCard {
  key: string;
  kind: string;
  title: string;
  text: string | null;
  unit_id: string | null;
  unit: string | null;
  area_id: string | null;
  area: string | null;
  owner: string;
  records: string[];
  value_usd: number | null;
  effective_usd: number | null;
  reason: string | null;
  breakdown: {
    components?: Record<string, { value_usd: number | null; status: string; [k: string]: unknown }>;
    urgency?: { factor: number; [k: string]: unknown };
    effort?: { value_usd: number | null; [k: string]: unknown };
    unknown?: string[];
    value_per_effort?: number | null;
    [k: string]: unknown;
  };
  rank: number;
  prev_rank: number | null;
  moved: string | null;
  pinned: boolean;
  pin_reason: string | null;
  pinned_by: string | null;
  item_id: number | null;
  first_seen: string;
}

export interface BoardPage {
  total: number;
  page: number;
  per_page: number;
  cards: BoardCard[];
  facets?: { kind: Record<string, number>; owner: Record<string, number>; area: { id: string | null; label: string | null; n: number }[] };
}

export interface BoardFilter {
  kind?: string;
  area?: string;
  owner?: string;
  text?: string;
  since?: string;
}

export interface DailyReport {
  day: string;
  version: number;
  versions: number[];
  markdown: string;
  facts: { sections: Record<string, { text: string; cites: string[] }[]> };
  drafted_by: string;
  checks: { ok: boolean; uncited_lines: string[]; unknown_citations: string[]; unknown_numbers: string[] };
  changes: Record<string, { added: string[]; removed: string[] }> | null;
  usage: { cost_usd?: number } | null;
  seconds: number | null;
  item_id: number | null;
  /** Where the day's review stands (#242), with the open step to act on. */
  review: DailyReview | null;
  /** The EC DAY001 record behind it. */
  production_day: string | null;
}

export type DailyStatus = "drafting" | "awaiting review" | "sent back" | "published" | "needs a fix" | "no review" | string;

export interface DailyReview {
  item_id: number;
  status: DailyStatus;
  outcome: string | null;
  closed_at: string | null;
  comments: string | null;
  step: { id: number; element: string; name: string | null; kind: string; assignee: string | null; role: string | null;
    due_at: string | null; error: string | null } | null;
}


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

// ---- the source catalog (#225)
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
  enabled: boolean; notes: string | null; connections: CatalogConnection[]; datasets: CatalogDatasetRow[];
}
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
  dataset: Json & { name: string; source: string; status: string; load_mode: string; drift_policy: string;
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
  loaded: string[];
  unchanged: string[];
  held: string[];
  records: number;
  inserted: number;
  updated: number;
  removed: number;
}

export interface SettingRow {
  key: string;
  group: string;
  type: "int" | "float" | "bool" | "str" | "choice" | "secret";
  description: string;
  default: unknown;
  global: unknown;
  project: unknown;
  value: unknown;
  from: "default" | "global" | "project";
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

export interface Study {
  id: number;
  kind: string;
  params: Record<string, unknown>;
  status: string;
  summary: string | null;
  figure_id: string | null;
  error: string | null;
  created_by: string;
  created_at: string;
  result?: Record<string, unknown> | null;
}

// --- Jobs (Dagster) #208 ---
export type HealthLevel = "green" | "amber" | "red" | "unknown";

export interface JobsHealth {
  level: HealthLevel;
  available: boolean;
  failed_runs_24h: number;
  failing_checks: number;
  scheduler_running: boolean;
  summary: string;
}

export interface JobTick {
  status: string;
  time: string | null;
  skip_reason: string | null;
  run_keys: string[];
  error: string | null;
}

export interface JobTrigger {
  kind: "schedule" | "sensor";
  name: string;
  status: "on" | "off";
  cron?: string;
  timezone?: string | null;
  next_tick?: string | null;
  description?: string | null;
  job?: string | null;
  last_tick: JobTick | null;
}

export interface JobRunSummary {
  run_id: string;
  job: string;
  status: string;
  start: string | null;
  end: string | null;
  seconds: number | null;
  trigger: string;
}

export interface JobCheck {
  name: string;
  asset: string;
  description: string | null;
  level: "pass" | "warn" | "fail" | "running" | "not_run";
  metadata: Record<string, unknown>;
  note: string | null;
  time: string | null;
  run_id: string | null;
}

export interface JobsOverview {
  available: boolean;
  dagster: { url: string; reachable: boolean };
  scheduler: { running: boolean; last_heartbeat: string | null };
  health: JobsHealth;
  jobs: { name: string; description: string | null; triggers: JobTrigger[]; last_run: JobRunSummary | null; recent_runs: JobRunSummary[] }[];
  sensors: JobTrigger[];
  checks: JobCheck[];
  claude: {
    schedule: "on" | "off" | "none";
    usd_30d: number;
    limits: Record<string, number | boolean>;
    runs: (JobRunSummary & { model: string; input_tokens: number; output_tokens: number; usd: number })[];
  } | null;
}

export interface JobRunDetail extends JobRunSummary {
  tags: Record<string, string>;
  assets: { asset: string; time: string | null; metadata: Record<string, unknown> }[];
  errors: { step: string | null; kind: string; message: string; error_class: string | null; stack: string | null }[];
  log_tail: { time: string | null; level: string; step: string | null; message: string }[];
}

async function request<T>(method: string, path: string, params?: Params, body?: unknown): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const url = qs.size ? `${path}?${qs}` : path;
  const res = await fetch(url, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw Object.assign(new Error(detail?.detail ?? `${res.status} ${res.statusText}`), { body: detail });
  }
  return res.json();
}

async function requestText(path: string): Promise<string> {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.text();
}

export const api = {
  stats: () => request<Stats>("GET", "/stats"),
  jobs: () => request<JobsOverview>("GET", "/jobs"),
  jobsHealth: () => request<JobsHealth>("GET", "/jobs/health"),
  jobRun: (id: string) => request<JobRunDetail>("GET", `/jobs/runs/${encodeURIComponent(id)}`),
  search: (q: string, kind?: string) => request<SearchHit[]>("GET", "/entities", { q, kind, limit: 200 }),
  entity: (id: string, asOf?: string) =>
    request<EntityDetail>("GET", `/entities/${encodeURIComponent(id)}`, { as_of: asOf }),
  trace: (id: string, direction: "up" | "down", asOf?: string) =>
    request<TraceRow[]>("GET", `/entities/${encodeURIComponent(id)}/trace`, { direction, as_of: asOf }),
  events: (id: string) => request<FuzerEvent[]>("GET", `/entities/${encodeURIComponent(id)}/events`, { limit: 200 }),
  maintenance: (id: string) =>
    request<Maintenance>("GET", `/entities/${encodeURIComponent(id)}/maintenance`, { limit: 25 }),
  downtime: (id?: string, since?: string) =>
    request<Downtime>("GET", id ? `/entities/${encodeURIComponent(id)}/downtime` : "/downtime", { since, limit: 25 }),
  downtimeRanking: (by: "tag" | "class" | "reason", since?: string) =>
    request<RankRow[]>("GET", "/downtime/ranking", { by, since, limit: 15 }),
  reasonChecks: () =>
    request<{ fuzer_id: string; label: string; issue: string; start: string }[]>("GET", "/downtime/reason-checks"),
  incidents: (id?: string) =>
    request<{ total: number; items: Incident[] }>(
      "GET", id ? `/entities/${encodeURIComponent(id)}/incidents` : "/incidents", { limit: 100 }),
  upsets: (status?: string, kind?: string, limit = 50) =>
    request<Upset[]>("GET", "/upsets", { status, kind, limit }),
  incidentTimeline: (id: string) => request<TimelineItem[]>("GET", `/incidents/${encodeURIComponent(id)}/timeline`),
  similarIncidents: (id: string) =>
    request<(Incident & { score: number; same_unit: boolean })[]>("GET", `/incidents/${encodeURIComponent(id)}/similar`),
  knowledgeSearch: (q: string, ref?: string) => request<Passage[]>("GET", "/knowledge/search", { q, ref, limit: 8 }),
  incidentReading: (id: string) =>
    request<{ id: string; document_label: string | null; heading: string | null; page: number | null;
              failure_modes: string[] }[]>("GET", `/incidents/${encodeURIComponent(id)}/reading`),
  history: (id: string) =>
    request<{ record: string; at: string; change: string; fields: Record<string, { from: unknown; to: unknown }> }[]>(
      "GET", `/entities/${encodeURIComponent(id)}/history`, { limit: 20 }),
  board: (f: BoardFilter, page = 1) =>
    request<BoardPage>("GET", "/board", { ...f, page, per_page: 50, facets: "true" }),
  boardHistory: (key: string) => request<{ rank: number; value_usd: number | null; at: string }[]>("GET", "/board/history", { key }),
  boardPin: (key: string, by: string, reason: string, rank?: number, value_usd?: number) =>
    request<unknown>("POST", "/board/pins", undefined, { key, by, reason, rank: rank ?? null, value_usd: value_usd ?? null }),
  boardProject: (p: { title: string; by: string; owner?: string; due?: string; benefit_usd?: number; scope?: string }) =>
    request<unknown>("POST", "/board/projects", undefined, p),
  dailyReports: () => request<{ day: string; version: number; drafted_by: string; ok: boolean; item_id: number | null;
    status: DailyStatus }[]>("GET", "/reports/daily"),
  dailyReport: (day: string, version?: number) => request<DailyReport>("GET", `/reports/daily/${day}`, { version }),
  studies: () => request<Study[]>("GET", "/studies"),
  study: (id: number) => request<Study>("GET", `/studies/${id}`),
  startStudy: (kind: string, params: Record<string, unknown>, by: string) =>
    request<Study>("POST", "/studies", undefined, { kind, params, by }),
  rerunStudy: (id: number, by: string) => request<Study>("POST", `/studies/${id}/rerun`, { by }),
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
  why: (id: string, field: string) => request<WhyValue>("GET", `/sources/why/${encodeURIComponent(id)}/${encodeURIComponent(field)}`),
  settings: (project?: string) =>
    request<{ project: string | null; projects: string[]; settings: SettingRow[] }>("GET", "/settings", { project }),
  setSetting: (key: string, value: unknown, scope: SettingScope, project: string | null, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/${encodeURIComponent(key)}`, undefined, { value, scope, project, by, reason }),
  resetSetting: (key: string, scope: SettingScope, project: string | null, by: string, reason: string) =>
    request<unknown>("POST", `/settings/${encodeURIComponent(key)}/reset`, undefined, { scope, project, by, reason }),
  settingHistory: (key: string) => request<SettingChange[]>("GET", `/settings/${encodeURIComponent(key)}/history`),
  workPeople: () => request<{ name: string; roles: string[]; away_until?: string | null; delegate?: string | null }[]>("GET", "/work/people"),
  setWorkPerson: (name: string, roles: string[], by: string) =>
    request<{ name: string; roles: string[] }>("PUT", `/work/people/${encodeURIComponent(name)}`, undefined, { roles, by }),
  removeWorkPerson: (name: string, by: string) =>
    request<{ name: string; released_tasks: number[]; released_items: number[] }>(
      "DELETE", `/work/people/${encodeURIComponent(name)}`, { by }),
  workRoleSummary: () => request<WorkRoleSummary[]>("GET", "/work/roles/summary"),
  inbox: (person: string) => request<WorkInbox>("GET", "/work/inbox", { person }),
  workTask: (id: number) => request<WorkTaskView>("GET", `/work/tasks/${id}`),
  workItem: (id: number) => request<WorkItem>("GET", `/work/items/${id}`),
  workDefinitionXml: (id: number) => requestText(`/work/definitions/${id}/bpmn`),
  workDefinitions: (processKey?: string) => request<WorkDefinition[]>("GET", "/work/definitions", { process_key: processKey }),
  workRoles: () => request<{ person: string[]; agent: string[] }>("GET", "/work/roles"),
  importDefinition: (xml: string, by: string) =>
    request<{ id: number; process_key: string; version: number }[]>("POST", "/work/definitions", undefined, { xml, by }),
  draftDefinition: (processKey: string, by: string, fromId?: number) =>
    request<{ id: number; version: number; state: string }>(
      "POST", "/work/definitions/draft", undefined, { process_key: processKey, by, from_id: fromId }),
  saveDefinition: (id: number, xml: string, by: string) =>
    request<{ id: number; state: string }>("PUT", `/work/definitions/${id}`, undefined, { xml, by }),
  checkDefinition: (id: number, by: string) =>
    request<{ id: number; state: string; problems: WorkProblem[] }>("POST", `/work/definitions/${id}/check`, undefined, { by }),
  publishDefinition: (id: number, by: string) =>
    request<{ id: number; state: string; version: number }>("POST", `/work/definitions/${id}/publish`, undefined, { by }),
  compareDefinition: (id: number, against?: number) =>
    request<WorkCompare>("GET", `/work/definitions/${id}/compare`, { against }),
  workMonitor: (processKey: string, definitionId?: number, days = 30) =>
    request<WorkMonitor>("GET", `/work/monitor/${encodeURIComponent(processKey)}`, { definition_id: definitionId, days }),
  workForms: () => request<{ key: string; version: number; created_by: string; created_at: string }[]>("GET", "/work/forms"),
  workForm: (key: string) =>
    request<{ key: string; version: number; schema: Record<string, unknown> }>("GET", `/work/forms/${encodeURIComponent(key)}`),
  saveForm: (schema: Record<string, unknown>, by: string) =>
    request<{ key: string; version: number }>("POST", "/work/forms", { by }, schema),
  rolesCatalog: () => request<WorkRoleRow[]>("GET", "/work/roles/catalog"),
  setRole: (name: string, values: Partial<Pick<WorkRoleRow, "kind" | "description" | "default_sla" | "escalate_to" | "assignment">>,
            by: string) => request<WorkRoleRow>("PUT", `/work/roles/${encodeURIComponent(name)}`, undefined, { ...values, by }),
  deleteRole: (name: string, by: string) => request<unknown>("DELETE", `/work/roles/${encodeURIComponent(name)}`, { by }),
  setAway: (name: string, by: string, until: string | null, delegate?: string | null, note?: string | null) =>
    request<unknown>("PUT", `/work/people/${encodeURIComponent(name)}/away`, undefined, { by, until, delegate, note }),
  workload: () => request<Workload>("GET", "/work/workload"),
  workRouting: (definitionId: number) => request<StepRouting[]>("GET", `/work/definitions/${definitionId}/routing`),
  workStats: (days = 30) => request<ProcessStats[]>("GET", "/work/stats", { days }),
  workInstances: (processKey: string, state = "running") =>
    request<ProcessInstance[]>("GET", "/work/instances", { process_key: processKey, state }),
  cases: (f: { state?: string; type?: string; assignee?: string; role?: string; overdue?: boolean; q?: string; limit?: number }) =>
    request<{ items: CaseRow[]; total: number; overdue: number; due_soon: number }>("GET", "/work/items", {
      state: f.state, type: f.type, assignee: f.assignee, role: f.role, overdue: f.overdue === undefined ? undefined : String(f.overdue),
      q: f.q, limit: f.limit ?? 200 }),
  setCase: (id: number, by: string, values: { priority?: number; due?: string; reason?: string }) =>
    request<unknown>("POST", `/work/items/${id}/set`, undefined, { by, ...values }),
  reassignCase: (id: number, by: string, to: string, reason?: string) =>
    request<unknown>("POST", `/work/items/${id}/reassign`, undefined, { by, to, reason }),
  transitionCase: (id: number, by: string, state: string, reason?: string) =>
    request<unknown>("POST", `/work/items/${id}/transition`, undefined, { by, state, reason }),
  cancelProcess: (id: number, by: string, reason?: string) =>
    request<unknown>("POST", `/work/processes/${id}/cancel`, undefined, { by, state: "cancelled", reason }),
  claim: (id: number, by: string) => request<unknown>("POST", `/work/tasks/${id}/claim`, undefined, { by }),
  release: (id: number, by: string) => request<unknown>("POST", `/work/tasks/${id}/release`, undefined, { by }),
  complete: (id: number, by: string, data: Record<string, unknown>) =>
    request<{ process_state: string }>("POST", `/work/tasks/${id}/complete`, undefined, { by, data }),
  handOff: (id: number, by: string, to: string, reason?: string) =>
    request<unknown>("POST", `/work/tasks/${id}/hand-off`, undefined, { by, to, reason }),
  bulkComplete: (taskIds: number[], by: string, action: string, reason?: string) =>
    request<{ done: number[]; refused: { task_id: number; error: string }[] }>(
      "POST", "/work/tasks/bulk-complete", undefined, { by, task_ids: taskIds, action, reason }),
  comment: (itemId: number, by: string, text: string) =>
    request<unknown>("POST", `/work/items/${itemId}/comment`, undefined, { by, text }),
  lessons: (status: string) => request<Lesson[]>("GET", "/lessons", { status }),
  decideLesson: (id: number, decision: "approve" | "reject" | "edit", by: string, text?: string) =>
    request<{ id: number; status: string }>("POST", `/lessons/${id}`, undefined, { decision, by, text }),
  spares: (id: string) =>
    request<{ fuzer_id: string; label: string; description: string; mpn: string; stock: number; bin: string;
              used_by: number; documents: { id: string; label: string }[] }[]>(
      "GET", `/entities/${encodeURIComponent(id)}/spares`),
  values: (id: string, at?: string, hoursBefore?: number, hoursAfter?: number, direct?: boolean) =>
    request<Values>("GET", `/entities/${encodeURIComponent(id)}/values`,
                    { at, hours_before: hoursBefore, hours_after: hoursAfter, direct: direct ? "true" : undefined }),
  measurements: (id: string, quantity?: string) =>
    request<{ id: string; series: MeasurementSeries[]; truncated: boolean }>(
      "GET", `/entities/${encodeURIComponent(id)}/measurements`, { quantity }),
  brief: (id: string) => request<Brief>("GET", `/entities/${encodeURIComponent(id)}/brief`),
  feedback: (target: string, useful: boolean, by: string, comment?: string) =>
    request<{ id: number }>("POST", "/feedback", undefined, { target, useful, by, comment }),
  briefRatings: () => request<BriefRatings>("GET", "/feedback/briefs"),
  ask: (question: string, conversationId: string | undefined, by: string, images: Attachment[] = []) =>
    request<{ job_id: string; conversation_id: string }>("POST", "/assistant/ask", undefined, {
      question, conversation_id: conversationId, by,
      images: images.map(({ media_type, data }) => ({ media_type, data })),
    }),
  figure: (id: string) => request<FigureData>("GET", `/figures/${encodeURIComponent(id)}`),
  assistantJob: (id: string) => request<AssistantJob>("GET", `/assistant/jobs/${encodeURIComponent(id)}`),
  assistantAnswers: () => request<AssistantAnswer[]>("GET", "/assistant/answers", { limit: 20 }),
  conversations: (by?: string) => request<AssistantConversation[]>("GET", "/assistant/conversations", { by, limit: 15 }),
  conversation: (id: string) =>
    request<AssistantConversation & { turns: SavedTurn[] }>("GET", `/assistant/conversations/${encodeURIComponent(id)}`),
  facts: (ref: string) => request<AssistantFact[]>("GET", "/assistant/facts", { ref }),
  editFact: (id: number, by: string, edit: { text?: string; valid_from?: string; valid_to?: string;
                                               clear?: ("valid_from" | "valid_to")[] }) =>
    request<AssistantFact>("PATCH", `/assistant/facts/${id}`, undefined, { ...edit, by }),
  endFact: (id: number, by: string, validTo?: string) =>
    request<AssistantFact>("POST", `/assistant/facts/${id}/end`, undefined, { by, valid_to: validTo }),
  deleteFact: (id: number) => request<{ deleted: number }>("DELETE", `/assistant/facts/${id}`),
  remember: (answerId: string, statement: string, by: string) =>
    request<{ facts: number[]; ended: number[]; items: AssistantFact[] }>(
      "POST", `/assistant/answers/${encodeURIComponent(answerId)}/remember`, undefined, { statement, by }),
  deleteConversation: (id: string) =>
    request<{ deleted: string }>("DELETE", `/assistant/conversations/${encodeURIComponent(id)}`),
  map: () => request<GeoCollection>("GET", "/map"),
  entityMap: (id: string) => request<GeoCollection>("GET", `/entities/${encodeURIComponent(id)}/map`),
  entityDrawings: (id: string) =>
    request<{ highlight: string[]; drawings: { id: string; label: string | null; title: string | null }[] }>(
      "GET", `/entities/${encodeURIComponent(id)}/drawings`),
  drawing: (id: string) => request<Drawing>("GET", `/documents/${encodeURIComponent(id)}/drawing`),
  documentsFor: (id: string) => request<DocRef[]>("GET", `/entities/${encodeURIComponent(id)}/documents`),
  ontology: () => request<Ontology>("GET", "/ontology"),
  ontologyDeclared: () => request<OntologyDeclared>("GET", "/ontology/declared"),
  ontologyHistory: (object?: "kind" | "relation", name?: string) =>
    request<OntologyChange[]>("GET", "/ontology/history", { object, name }),
  kindAdd: (k: KindIn, by: string, reason?: string) =>
    request<KindDef>("POST", "/ontology/kinds", undefined, { ...k, by, reason }),
  kindUpdate: (name: string, k: KindIn, by: string, reason?: string) =>
    request<KindDef>("PUT", `/ontology/kinds/${encodeURIComponent(name)}`, undefined, { ...k, by, reason }),
  kindDelete: (name: string, by: string, reason?: string) =>
    request<KindDef>("DELETE", `/ontology/kinds/${encodeURIComponent(name)}`, { by, reason }),
  relationAdd: (r: RelationIn, by: string, reason?: string) =>
    request<RelationDef>("POST", "/ontology/relations", undefined, { ...r, by, reason }),
  relationUpdate: (name: string, r: RelationIn, by: string, reason?: string) =>
    request<RelationDef>("PUT", `/ontology/relations/${encodeURIComponent(name)}`, undefined, { ...r, by, reason }),
  relationDelete: (name: string, by: string, reason?: string) =>
    request<RelationDef>("DELETE", `/ontology/relations/${encodeURIComponent(name)}`, { by, reason }),
  neighborhood: (id: string, depth: number, rels?: string[], asOf?: string) =>
    request<Subgraph>("GET", `/graph/${encodeURIComponent(id)}`, { depth, rels: rels?.join(","), as_of: asOf }),
  network: (asOf?: string, rel: "flows_to" | "lift_gas_to" | "pipes_to" = "flows_to") =>
    request<Subgraph>("GET", "/network", { as_of: asOf, rel }),
  reviews: (kind: ReviewKind) => request<ReviewItem[]>("GET", "/review", { kind, limit: 500 }),
  decide: (id: number, d: Decision) => request<{ decided: number }>("POST", `/review/${id}`, undefined, d),
};
