import { request, requestText } from "./transport";
import type { SourceValue } from "./explore";

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
  /** The project's thresholds (#410): scores from review_min go to review, from auto_accept match on their own. */
  review_min?: number;
  auto_accept?: number;
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

export interface Decision {
  decision: "accept" | "reject" | "choose";
  value?: unknown;
  by: string;
}

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

export type WorkRoleSummary = { role: string; processes: string[]; people: string[]; waiting: number };

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
  /** Why it was last handed on, e.g. "Back from the field: done: gauge reads 13 barg". */
  handed_reason?: string | null;
}

export interface WorkRoleRow {
  name: string; kind: "person" | "agent"; description: string | null; default_sla: string | null;
  escalate_to: string | null; assignment: "queue" | "round_robin" | "least_loaded"; updated_by: string | null;
  members: { name: string; away: boolean; away_until: string | null; delegate: string | null }[];
  processes: string[]; waiting: number; overdue: number; escalated: number; uncovered: boolean;
}

export interface WorkloadPerson {
  assigned_roles?: string[]; owner?: boolean;
  display_name?: string | null; account_enabled?: boolean | null;
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

export interface WorkDraft {
  id: string; title: string; role: string; summary: string; evidence: string[]; subjects: string[];
  item_id: number | null; item_state: string | null; confirmed_by: string | null; confirmed_at: string | null;
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
  /** An agent step no worker holds (#393). */
  unclaimed?: boolean;
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
  /** False when no agents provider is configured: no agent worker runs, so agent steps wait for a person. */
  agents_configured?: boolean;
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

/** An exception as the IOC sees it (fuzer.core.exceptions): one open condition from any source, matched to assets. */
export interface ExceptionRow {
  id: number; type: string; key: string; title: string; source: string | null; state: string;
  priority: number | null; value_usd: number | null; subjects: string[]; unmatched: string[];
  item_id: number | null; case_state: string | null; assignee: string | null; role: string | null;
  raised_count: number; first_seen: string; last_seen: string;
  planned?: { id: number; start_at: string; end_at: string; crew: string | null } | null;
  /** What the source sent with it, and what the field has reported back (`field`). */
  evidence?: { field?: { at: string; by: string; status: string; note: string | null }[]; [k: string]: unknown };
}

/** An exception type: declared by a module in code, or defined as data for another system's exceptions. */
export interface ExceptionKind {
  name: string; label: string; role: string | null; process: string | null; case_type: string | null;
  audience: string[]; close_case_on_clear: boolean; description: string; defined_in: "code" | "data"; open: number;
}

/** An item on the integrated plan, with its clashes (same asset, same person) and bundling chances. */
export interface PlanItem {
  id: number; kind: string; title: string; source: string | null; source_key: string | null; subjects: string[];
  area_id: string | null; area_label: string | null; start_at: string; end_at: string; status: string;
  crew: string | null; crew_hours: number | null; needs_shutdown: boolean; deferment_usd: number | null;
  exception_id: number | null; notes: string | null; assets: { fuzer_id: string; label: string | null }[];
  clashes: { kind: "same_asset" | "crew" | "bundle"; with: number; title: string; say: string }[];
  /** Spare parts for its assets recorded with zero stock, for active work starting within 30 days. */
  parts_short?: { id: string; label: string | null; description: string | null; mpn: string | null; bin: string | null; for: string }[];
}

/** A turnaround's scope read against the plan (GET /turnarounds). */
export interface TurnaroundScope {
  revision: string | null; revisions: string[]; as_of: string; planned_start: string | null;
  scoped_work: { id: string; order: string | null; tag: string | null; tag_id: string | null; priority: string | null;
    text: string | null; planned_start: string | null; status: string | null }[];
  shutdown_plans_not_in_scope: { id: string; plan: string | null; task: string | null; tag: string | null; next_due: string }[];
  bad_actors_not_in_scope: { id: string; tag: string | null; failures: number; lost_oil_sm3: number | null }[];
  spares_out_of_stock: { for: string | null; material: string | null; id: string; description: string | null }[];
  window: { start: string; end: string } | null;
  on_plan: PlanItem[]; ride_along: PlanItem[]; shutdown_outside: PlanItem[];
}

/** A job a field operator was sent to: the exception with where to go, what was seen and what they reported. */
export interface FieldJob extends ExceptionRow {
  label: string; due: string | null; task_id: number | null; task_name: string | null;
  assets: { fuzer_id: string; label: string | null; kind: string | null; lat: number | null; lon: number | null }[];
  seen: [string, string][];
  history: { id: number; title: string; state: string; first_seen: string }[];
  field: { at: string; by: string; status: string; note: string | null }[];
  /** Who sent it to them, when, and what they asked for. */
  sent: { by: string | null; at: string | null; reason: string | null } | null;
  /** The open step is in one of their roles (a Field check), so they can open it; another role's step handed to
   * them (an engineer's Triage) is reported back on from My jobs instead. */
  own_step: boolean;
  /** Waiting unclaimed in one of their roles' queues: theirs to take, not yet sent to them. */
  queued: boolean;
}

export const workApi = {
  board: (f: BoardFilter, page = 1) =>
    request<BoardPage>("GET", "/board", { ...f, page, per_page: 50, facets: "true" }),
  boardHistory: (key: string) => request<{ rank: number; value_usd: number | null; at: string }[]>("GET", "/board/history", { key }),
  boardPin: (key: string, by: string, reason: string, rank?: number, value_usd?: number) =>
    request<unknown>("POST", "/board/pins", undefined, { key, by, reason, rank: rank ?? null, value_usd: value_usd ?? null }),
  boardProject: (p: { title: string; by: string; owner?: string; due?: string; benefit_usd?: number; scope?: string }) =>
    request<unknown>("POST", "/board/projects", undefined, p),
  workPeople: () => request<{ name: string; roles: string[]; assigned_roles?: string[]; away_until?: string | null; delegate?: string | null }[]>("GET", "/work/people"),
  setWorkPerson: (name: string, roles: string[], by: string) =>
    request<{ name: string; roles: string[] }>("PUT", `/work/people/${encodeURIComponent(name)}`, undefined, { roles, by }),
  removeWorkPerson: (name: string, by: string) =>
    request<{ name: string; released_tasks: number[]; released_items: number[] }>(
      "DELETE", `/work/people/${encodeURIComponent(name)}`, { by }),
  workRoleSummary: () => request<WorkRoleSummary[]>("GET", "/work/roles/summary"),
  inbox: (person: string) => request<WorkInbox>("GET", "/work/inbox", { person }),
  exceptions: (f: { state?: string; type?: string; subject?: string; audience?: string } = {}) =>
    request<ExceptionRow[]>("GET", "/exceptions", f),
  fieldJobs: (person: string) => request<{ person: string; jobs: FieldJob[]; coming_up: PlanItem[] }>("GET", "/exceptions/field", { person }),
  fieldReport: (id: number, by: string, status: string, note?: string) =>
    request<FieldJob>("POST", `/exceptions/${id}/report`, undefined, { by, status, note: note ?? null }),
  plan: (f: { start?: string; end?: string; area?: string; status?: string; subject?: string } = {}) => request<PlanItem[]>("GET", "/plan", f),
  planChange: (id: number, by: string, change: Partial<{ start: string; end: string; status: string; crew: string }>) =>
    request<PlanItem>("PATCH", `/plan/${id}`, undefined, { by, ...change }),
  planSync: () => request<{ orders: number; added: number; updated: number; closed: number }>("POST", "/plan/sync"),
  turnaround: (revision?: string) => request<TurnaroundScope>("GET", "/turnarounds", { revision }),
  turnaroundBringIn: (itemId: number, by: string, revision: string | null) =>
    request<PlanItem>("POST", "/turnarounds/bring-in", undefined, { by, item_id: itemId, revision }),
  planException: (id: number, by: string, start: string, end: string, crew?: string) =>
    request<PlanItem>("POST", `/exceptions/${id}/plan`, undefined, { by, start, end, crew: crew ?? null }),
  exceptionKinds: () => request<ExceptionKind[]>("GET", "/exceptions/types"),
  /** The reference forms another system's refs are matched by, each with an example from this register. */
  exceptionRefs: () => request<{ form: string; example: string; entities: number }[]>("GET", "/exceptions/refs"),
  defineExceptionKind: (name: string, k: { by: string; label: string; role?: string | null; process?: string | null;
    audience: string[]; description?: string | null }) => request<unknown>("PUT", `/exceptions/types/${encodeURIComponent(name)}`, undefined, k),
  removeExceptionKind: (name: string) => request<unknown>("DELETE", `/exceptions/types/${encodeURIComponent(name)}`),
  dispatch: (id: number, by: string, to: string, reason?: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/dispatch`, undefined, { by, to, reason: reason ?? null }),
  workTask: (id: number) => request<WorkTaskView>("GET", `/work/tasks/${id}`),
  workItem: (id: number) => request<WorkItem>("GET", `/work/items/${id}`),
  workDefinitionXml: (id: number) => requestText(`/work/definitions/${id}/bpmn`),
  workDefinitions: (processKey?: string) => request<WorkDefinition[]>("GET", "/work/definitions", { process_key: processKey }),
  generateWorkflow: (id: number, description: string, by: string) =>
    request<{ xml: string; problems: WorkProblem[]; steps: number; connections: number }>(
      "POST", `/work/definitions/${id}/generate`, undefined, { description, by }),
  workRoles: () => request<{ person: string[]; agent: string[] }>("GET", "/work/roles"),
  createWorkItem: (title: string, role: string, by: string) =>
    request<{ id: number }>("POST", "/work/items", undefined, { type: "task", title, role, by }),
  workDraft: (id: string) => request<WorkDraft>("GET", `/work/drafts/${encodeURIComponent(id)}`),
  confirmWorkDraft: (id: string, by: string) =>
    request<{ item_id: number; created: boolean }>("POST", `/work/drafts/${encodeURIComponent(id)}/confirm`, undefined, { by }),
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
  decideLesson: (id: number, decision: "approve" | "reject" | "edit", by: string, text?: string) =>
    request<{ id: number; status: string }>("POST", `/lessons/${id}`, undefined, { decision, by, text }),
  reviews: (kind: ReviewKind) => request<ReviewItem[]>("GET", "/review", { kind, limit: 500 }),
  decide: (id: number, d: Decision) => request<{ decided: number }>("POST", `/review/${id}`, undefined, d),
};
