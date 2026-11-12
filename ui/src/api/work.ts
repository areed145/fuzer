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

export type ReviewKind = "match" | "conflict" | "schema" | "mapping" | "curve" | "check" | "pid_connection" | "pid_mismatch";

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

/** An off-page line one drawing shows and the drawing it points to doesn't (#475). */
export interface PidConnectionPayload {
  sheet: string; other_sheet: string; direction: "to" | "from"; tag: string | null; other: string | null;
  line: string; from: string | null; to: string | null; question: string;
}

/** Where a drawing and the DCS, AF or SAP disagree (#475): an alarm and trip setting, or where an instrument sits. */
export interface PidMismatchPayload {
  check: "alarm" | "alarm_unset" | "placement"; sheet: string; document: string | null; tag: string;
  fuzer_id: string; point: string | null; detail: string; question: string; type?: string;
  drawn_on?: string; register?: { parent: string; sources: string[] }[];
  /** The disagreement in the one finding shape (P1.1); absent on questions raised before it. */
  finding?: Finding;
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
  | { id: number; kind: "pid_connection"; subject: string; status: string; label: null; payload: PidConnectionPayload }
  | { id: number; kind: "pid_mismatch"; subject: string; status: string; label: string | null; payload: PidMismatchPayload }
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
  /** Overarching (#497): the fields they cover ([] is every field) and the roles they hold there; null: this field's roster only. */
  covers?: string[] | null; cover_roles?: string[];
}

/** A person's Inbox in one field (project), for the cross-field view (#497). */
/** What changed on a person's work since they last looked at Home (P5.1). */
export interface WorkHome {
  person: string; seen_at: string | null;
  changes: { id: number; item_id: number; kind: string; actor: string; at: string; data: Record<string, unknown>;
    title: string; type: string; state: string; watching: boolean }[];
  watching: string[];
}

export interface FieldInbox { project: string; roles: string[]; mine: WorkRow[]; queue: WorkRow[] }

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

/** One row of the routing table (#509): a type of work, where it is declared, how it joins and what it starts. */
export interface Route {
  type: string; label: string; description: string; declared_in: "code" | "data" | "source pack";
  source_system: string | null; joins: boolean; join_hours: number | null; starts: "process" | "queue" | "dispatch";
  process: string | null; role: string | null; case_type: string; audience: string[]; close_case_on_clear: boolean;
  due: { by: "priority"; hours: Record<string, number>; setting: string | null; from: string; applies: string } | null;
}

export interface RouteStep extends StepRouting { holders?: string[] }

/** A dry run of an intake: what it would match, join or start, and who would hold it. Writes nothing. */
export interface RouteExplanation {
  type: string; route: Route; summary: string;
  outcome: "updates" | "joins" | "dispatched" | "process" | "queue" | "dispatch";
  matched: { id: string; label: string; kind: string }[]; unmatched: string[]; unmatched_note: string | null;
  updates: RouteCase | null; joins: RouteCase | null; due: string | null;
  process: { key: string; published: boolean; lane?: string; steps: RouteStep[] } | null;
  queue: { role: string | null; assignee: string | null; holders: string[] } | null;
}

export interface RouteCase {
  exception_id: number; type: string; title: string; source: string | null;
  case: { id: number; title: string; state: string; assignee: string | null; role: string | null } | null;
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

/** Something a person was told (P5.2, #604): work sent, handed back, escalated or assigned to them, or coming due. */
export interface WorkNotification {
  id: number;
  kind: "dispatched" | "handed_back" | "escalated" | "assigned" | "due_soon";
  item_id: number | null;
  token_id: number | null;
  text: string;
  link: string;
  channel: string;
  delivery: string | null;
  created_at: string;
  read_at: string | null;
}

/** How a person is told (P5.2b, #606): on My work only, or also by email, Teams or Slack, with quiet hours. */
export interface NotifyPreference {
  channel: "in_app" | "email" | "teams" | "slack";
  address: string | null;
  quiet_from: string | null;
  quiet_to: string | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

/** A gate's decision on a case (P5.10): whether an automatic or agent decision went ahead or stopped for a person. */
export interface GateDecision {
  id: number;
  kind: string;
  actor: string;
  confidence: number | null;
  threshold: number | null;
  criticality: string | null;
  could_not_check: string[];
  fired: boolean;
  reason: string;
  at: string;
}

/** A change proposed to a source system (P5.7): it is written only after a named approver approves it. */
export interface Writeback {
  id: number;
  system: string;
  kind: string;
  record: string | null;
  action: string;
  payload: Record<string, unknown>;
  state: "drafted" | "awaiting_approval" | "approved" | "sent" | "confirmed" | "failed" | "rejected";
  drafted_by: string;
  drafted_at: string;
  approver_role: string | null;
  item_id: number | null;
  source_ref: string | null;
  approved_by: string | null;
  approved_at: string | null;
  due_at: string | null;
  sent_at: string | null;
  confirmed_at: string | null;
  error: string | null;
  reason: string | null;
}

export interface WritebackDetail extends Writeback {
  label: string;
  approvers: string[] | null;
  cadence: string | null;
  adapter: string;
  history: { id: number; state: string; by: string; note: string | null; at: string }[];
}

/** A reading the field took on site (P5.3b): a number with its unit, and what it was of. */
export interface FieldReading { value: number; unit: string; what?: string | null }

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
  /** When an unclaimed agent step goes to its fallback role's person queue (work.agent_fallback_minutes). */
  fallback_at?: string | null;
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
  /** What agents have spent on the case and its cap (USD; cap 0 means none), P4.4. */
  agent_spend?: { usd: number; cap_usd: number };
  /** Every exception the case works, each with its finding. */
  sources?: CaseSource[];
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
  /** Every exception the case works: more than one when several systems reported the same event (#464). */
  sources?: CaseSource[];
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
/** A shift handover (#467). */
export interface Handover {
  id: number; shift_start: string; shift_end: string; state: "draft" | "handed_over" | "acknowledged";
  facts: { sections: Record<string, { text: string; cites: string[] }[]>; more?: Record<string, number> };
  item_id: number | null; drafted_at: string; notes: string | null; notes_by: string | null; notes_at: string | null;
  acknowledged_by: string | null; acknowledged_at: string | null; acknowledged_note: string | null;
}
export interface HandoverRow {
  id: number; shift_start: string; shift_end: string; state: string; notes_by: string | null;
  acknowledged_by: string | null; acknowledged_at: string | null; item_id: number | null; lines: number | null;
}

/** An update drafted for the system that raised work the field finished (#466). */
export interface Closeout {
  id: number; exception_id: number; system: string; record: string; action: "complete" | "follow_up";
  draft: { text: string; status?: string | null; short_text?: string; [k: string]: unknown };
  state: "draft" | "approved" | "sent" | "discarded"; item_id: number | null; created_by: string; created_at: string;
  decided_by: string | null; decided_at: string | null; applied_at: string | null;
}

export interface ExceptionRow {
  id: number; type: string; key: string; title: string; source: string | null; state: string;
  priority: number | null; value_usd: number | null; subjects: string[]; unmatched: string[];
  item_id: number | null; case_state: string | null; assignee: string | null; role: string | null;
  raised_count: number; reports?: number; first_seen: string; last_seen: string;
  planned?: { id: number; start_at: string; end_at: string; crew: string | null } | null;
  /** What the source sent with it, and what the field has reported back (`field`). */
  evidence?: { field?: { at: string; by: string; status: string; note: string | null }[]; [k: string]: unknown };
  /** Other systems' exceptions about the same event, which joined this one's case (#464). */
  joined?: JoinedException[];
}

/** An exception that joined another's case: the same event reported by another source. */
export interface JoinedException {
  id: number; type: string; key: string; title: string; source: string | null; state: string;
  value_usd: number | null; subjects: string[]; first_seen: string; label: string;
}

/** One labelled value of a finding: text, a number with its unit, yes or no, a moment, or an asset by its name. */
/** Open questions that share an answer (P2.3c), with the answers they can take. */
export interface ReviewGroup {
  kind: string; key: string; label: string; count: number; items: number[];
  options: { decision: "accept" | "reject" | "choose"; source?: string; value?: unknown; label: string }[];
  standing: { decision: string; source: string | null; value: unknown; set_by: string } | null;
}

export interface FindingItem {
  label: string; value: string | number | boolean; unit?: string;
  kind: "text" | "number" | "flag" | "time" | "asset" | "page";
  /** An asset's tag and description, or a page link's words. */
  text?: string | null;
}

/** The one record shape for anything detected (P1.1), as every reader gets it. */
export interface Finding {
  detector: string; origin: "deterministic" | "agentic" | "external"; origin_label: string;
  method: string | null; standard: string | null; start: string | null; end: string | null;
  confidence: number | null; items: FindingItem[]; checks: string[]; subjects: string[]; value_usd: number | null;
}

/** One exception a case works, with its finding: a case's and a task page's sources. */
export interface CaseSource {
  id: number; type: string; key: string; title: string; source: string | null; state: string;
  value_usd: number | null; first_seen: string; last_seen: string; joined_to: number | null; subjects: string[];
  label: string; finding: Finding;
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
  finding: Finding;
  history: { id: number; title: string; state: string; first_seen: string }[];
  field: { at: string; by: string; status: string; note: string | null; reading?: FieldReading; photos?: number[] }[];
  /** Who sent it to them, when, and what they asked for. */
  sent: { by: string | null; at: string | null; reason: string | null } | null;
  /** The open step is in one of their roles (a Field check), so they can open it; another role's step handed to
   * them (an engineer's Triage) is reported back on from My jobs instead. */
  own_step: boolean;
  /** Waiting unclaimed in one of their roles' queues: theirs to take, not yet sent to them. */
  queued: boolean;
  /** Each system's report of this event, when more than one reported it (#464). */
  sources: CaseSource[];
  /** Checks to make before the work, such as a compressor trip's restart checklist. */
  checklist?: string[];
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
  notifications: (person: string) =>
    request<{ notifications: WorkNotification[]; unread: number }>("GET", "/work/notifications", { person }),
  notifyPreference: (person: string) => request<NotifyPreference>("GET", `/work/people/${encodeURIComponent(person)}/notify`),
  setNotifyPreference: (person: string, p: NotifyPreference, by: string) =>
    request<NotifyPreference>("PUT", `/work/people/${encodeURIComponent(person)}/notify`, undefined, { ...p, by }),
  markNotificationsRead: (person: string, ids?: number[]) =>
    request<{ read: number }>("POST", "/work/notifications/read", undefined, { person, ids: ids ?? null }),
  exceptions: (f: { state?: string; type?: string; subject?: string; audience?: string } = {}) =>
    request<ExceptionRow[]>("GET", "/exceptions", f),
  handovers: () => request<HandoverRow[]>("GET", "/handovers"),
  handover: (id: number) => request<Handover>("GET", `/handovers/${id}`),
  draftHandover: (by: string) => request<Handover | null>("POST", "/handovers/draft", undefined, { by, refresh: true }),
  handoverNotes: (id: number, by: string, notes: string) =>
    request<Handover>("POST", `/handovers/${id}/notes`, undefined, { by, notes }),
  acknowledgeHandover: (id: number, by: string, note?: string) =>
    request<Handover>("POST", `/handovers/${id}/acknowledge`, undefined, { by, note: note ?? null }),
  resolveRef: (id: number, by: string, ref: string, fuzer_id: string, remember: boolean, reason?: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/resolve`, undefined, { by, ref, fuzer_id, remember, reason: reason ?? null }),
  writebacks: (f: { system?: string; state?: string; item_id?: number } = {}) => request<Writeback[]>("GET", "/writebacks", f),
  writeback: (id: number) => request<WritebackDetail>("GET", `/writebacks/${id}`),
  approveWriteback: (id: number, by: string, payload?: Record<string, unknown>) =>
    request<Writeback>("POST", `/writebacks/${id}/approve`, undefined, { by, payload: payload ?? null }),
  rejectWriteback: (id: number, by: string, reason: string) =>
    request<Writeback>("POST", `/writebacks/${id}/reject`, undefined, { by, reason }),
  confirmWriteback: (id: number, by: string, note?: string) =>
    request<Writeback>("POST", `/writebacks/${id}/confirm`, undefined, { by, note: note ?? null }),
  failWriteback: (id: number, by: string, reason: string) =>
    request<Writeback>("POST", `/writebacks/${id}/fail`, undefined, { by, reason }),
  closeout: (id: number) => request<Closeout>("GET", `/closeouts/${id}`),
  approveCloseout: (id: number, by: string, text?: string, short_text?: string) =>
    request<Closeout>("POST", `/closeouts/${id}/approve`, undefined, { by, text: text ?? null, short_text: short_text ?? null }),
  discardCloseout: (id: number, by: string, reason?: string) =>
    request<Closeout>("POST", `/closeouts/${id}/discard`, undefined, { by, reason: reason ?? null }),
  fieldJobs: (person: string) => request<{ person: string; jobs: FieldJob[]; coming_up: PlanItem[] }>("GET", "/exceptions/field", { person }),
  fieldReport: (id: number, by: string, status: string, note?: string,
    extra: { reading?: FieldReading; photos?: string[]; client_id?: string; at?: string } = {}) =>
    request<FieldJob>("POST", `/exceptions/${id}/report`, undefined, { by, status, note: note ?? null,
      reading: extra.reading ?? null, photos: extra.photos ?? [], client_id: extra.client_id ?? null, at: extra.at ?? null }),
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
  splitException: (id: number, by: string, reason?: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/split`, undefined, { by, reason: reason ?? null }),
  dispatch: (id: number, by: string, to: string, reason?: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/dispatch`, undefined, { by, to, reason: reason ?? null }),
  workTask: (id: number) => request<WorkTaskView>("GET", `/work/tasks/${id}`),
  workItem: (id: number) => request<WorkItem>("GET", `/work/items/${id}`),
  workItemGates: (id: number) => request<GateDecision[]>("GET", `/work/items/${id}/gates`),
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
  workHome: (person: string, mark = false) => request<WorkHome>("GET", "/work/home", { person, mark: mark ? "true" : undefined }),
  watching: (person: string) => request<string[]>("GET", "/work/watch", { person }),
  watch: (person: string, ref: string, on: boolean) =>
    request<{ watching: boolean }>("POST", "/work/watch", undefined, { person, ref, on }),
  inboxFields: (person: string) => request<FieldInbox[]>("GET", "/work/inbox/fields", { person }),
  setCover: (name: string, roles: string[], projects: string[], by: string) =>
    request("PUT", `/work/people/${encodeURIComponent(name)}/cover`, undefined, { roles, projects, by }),
  removeCover: (name: string, by: string) =>
    request("DELETE", `/work/people/${encodeURIComponent(name)}/cover`, { by }),
  workRouting: (definitionId: number) => request<StepRouting[]>("GET", `/work/definitions/${definitionId}/routing`),
  routes: () => request<Route[]>("GET", "/routing"),
  explainRoute: (type: string, o: { refs?: string[]; key?: string; priority?: string; to?: string } = {}) => {
    const qs = new URLSearchParams();
    for (const r of o.refs ?? []) if (r.trim()) qs.append("refs", r.trim());
    for (const k of ["key", "priority", "to"] as const) if (o[k]?.trim()) qs.set(k, o[k]!.trim());
    return request<RouteExplanation>("GET", `/routing/${encodeURIComponent(type)}/explain${qs.size ? `?${qs}` : ""}`);
  },
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
  reviewGroups: (kind: ReviewKind) => request<ReviewGroup[]>("GET", "/review/groups", { kind }),
  decideGroup: (body: { key: string; decision: Decision["decision"]; source?: string; value?: unknown; standing: boolean; by: string }) =>
    request<{ decided: number[]; skipped: { id: number; why: string }[] }>("POST", "/review/groups", undefined, body),
  withdrawGroup: (key: string, by: string) => request<{ withdrawn: string }>("DELETE", "/review/groups", { key, by }),
};
