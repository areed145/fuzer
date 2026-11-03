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

export type ReviewKind = "match" | "conflict" | "schema" | "mapping" | "curve";

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

export type ReviewItem =
  | { id: number; kind: "match"; subject: string; status: string; label: null; payload: MatchPayload }
  | { id: number; kind: "schema"; subject: string; status: string; label: null; payload: SchemaPayload }
  | { id: number; kind: "mapping"; subject: string; status: string; label: null; payload: MappingPayload }
  | { id: number; kind: "curve"; subject: string; status: string; label: null; payload: CurvePayload }
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
}

export interface OntologyRelation {
  src_kind: string;
  rel: string;
  dst_kind: string;
  n: number;
  dated: number;
  sources: string[];
}

export interface Ontology {
  kinds: OntologyKind[];
  relations: OntologyRelation[];
}

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
}

export interface WorkInbox {
  person: string;
  roles: string[];
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
  drafted_by: string;
  checks: { ok: boolean; uncited_lines: string[]; unknown_citations: string[]; unknown_numbers: string[] };
  changes: Record<string, { added: string[]; removed: string[] }> | null;
  usage: { cost_usd?: number } | null;
  seconds: number | null;
  item_id: number | null;
}

export interface ArchComponent {
  id: string; title: string; purpose: string; page: string; code: string; modules: number;
  uses: string[]; writes: string[];
}

export interface Architecture {
  components: ArchComponent[];
  rows: Record<string, number>;
  jobs: Record<string, string | null>;
  entities: { kind: string; n: number }[];
  sources: Record<string, number>;
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
  dailyReports: () => request<{ day: string; version: number; drafted_by: string; ok: boolean }[]>("GET", "/reports/daily"),
  dailyReport: (day: string, version?: number) => request<DailyReport>("GET", `/reports/daily/${day}`, { version }),
  studies: () => request<Study[]>("GET", "/studies"),
  study: (id: number) => request<Study>("GET", `/studies/${id}`),
  startStudy: (kind: string, params: Record<string, unknown>, by: string) =>
    request<Study>("POST", "/studies", undefined, { kind, params, by }),
  rerunStudy: (id: number, by: string) => request<Study>("POST", `/studies/${id}/rerun`, { by }),
  architecture: () => request<Architecture>("GET", "/architecture"),
  sources: () => request<{ data_dir: string; sources: SourceRow[] }>("GET", "/sources"),
  source: (name: string) => request<SourcePreview>("GET", `/sources/${encodeURIComponent(name)}`, { rows: 8 }),
  settings: (project?: string) =>
    request<{ project: string | null; projects: string[]; settings: SettingRow[] }>("GET", "/settings", { project }),
  setSetting: (key: string, value: unknown, scope: SettingScope, project: string | null, by: string, reason: string) =>
    request<unknown>("PUT", `/settings/${encodeURIComponent(key)}`, undefined, { value, scope, project, by, reason }),
  resetSetting: (key: string, scope: SettingScope, project: string | null, by: string, reason: string) =>
    request<unknown>("POST", `/settings/${encodeURIComponent(key)}/reset`, undefined, { scope, project, by, reason }),
  settingHistory: (key: string) => request<SettingChange[]>("GET", `/settings/${encodeURIComponent(key)}/history`),
  workPeople: () => request<{ name: string; roles: string[] }[]>("GET", "/work/people"),
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
  map: () => request<GeoCollection>("GET", "/map"),
  entityMap: (id: string) => request<GeoCollection>("GET", `/entities/${encodeURIComponent(id)}/map`),
  entityDrawings: (id: string) =>
    request<{ highlight: string[]; drawings: { id: string; label: string | null; title: string | null }[] }>(
      "GET", `/entities/${encodeURIComponent(id)}/drawings`),
  drawing: (id: string) => request<Drawing>("GET", `/documents/${encodeURIComponent(id)}/drawing`),
  documentsFor: (id: string) => request<DocRef[]>("GET", `/entities/${encodeURIComponent(id)}/documents`),
  ontology: () => request<Ontology>("GET", "/ontology"),
  neighborhood: (id: string, depth: number, rels?: string[], asOf?: string) =>
    request<Subgraph>("GET", `/graph/${encodeURIComponent(id)}`, { depth, rels: rels?.join(","), as_of: asOf }),
  network: (asOf?: string, rel: "flows_to" | "lift_gas_to" | "pipes_to" = "flows_to") =>
    request<Subgraph>("GET", "/network", { as_of: asOf, rel }),
  reviews: (kind: ReviewKind) => request<ReviewItem[]>("GET", "/review", { kind, limit: 500 }),
  decide: (id: number, d: Decision) => request<{ decided: number }>("POST", `/review/${id}`, undefined, d),
};
