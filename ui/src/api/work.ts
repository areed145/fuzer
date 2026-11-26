import { request, requestText } from "./transport";

import type { SourceValue } from "./explore";
import type { DowntimeRow, Maintenance } from "./investigate";

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

/** A correction that came back to the steward (P2.4): rejected by the source's owner, or checked in the field. */
export interface Returned { kind: string; system: string; why: string }

export interface ConflictPayload {
  fuzer_id: string;
  field: string;
  chosen: unknown;
  values: SourceValue[];
  returned?: Returned;
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

export type ReviewKind = "match" | "conflict" | "schema" | "mapping" | "curve" | "check" | "pid_connection" | "pid_mismatch" | "role" | "message";

/** A message to the IOC as its case and its question show it (P2.8): the channel in words, who sent it and what it said. */
export interface MessageSaid {
  id: string; channel: string; channel_label: string; sender: string; subject: string | null; text: string; received_at: string;
}

/** A message that named no entity for certain (P2.8): why the reader stopped and the entities it might be about. */
export interface MessagePayload {
  message: MessageSaid; why: string; question: string;
  /** the entities it might be about that work can be about, each with its kind, description and place (P5.26f) */
  candidates: { fuzer_id: string; kind: string; label: string | null; description: string | null; parent_label: string | null; at?: string | null }[];
  /** the kinds a message's case can be about: the register search for another is limited to them */
  kinds?: string[];
}

/** A note sent from the phone, read as any message to the IOC is: what became of it, in one line (`said`). */
export interface MessageNote {
  id: string; outcome: "case" | "steward" | "handled"; said: string; exception_id: number | null; review_item_id: number | null;
  /** The case it opened, linked from the confirmation. */ item_id: number | null;
}

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
  check: "alarm" | "alarm_unset" | "placement" | "twin" | "flow"; sheet: string; document: string | null; tag: string;
  fuzer_id: string; point: string | null; detail: string; question: string; type?: string;
  drawn_on?: string; register?: { parent: string; sources: string[] }[];
  /** A flow link (check "flow"): the flow network and the drawn piping disagree on whether `from` reaches `to`. */
  direction?: "flow_only" | "drawn_only"; from?: string; to?: string; to_id?: string | null;
  drawn_path?: string[][]; sheets?: string[]; systems?: string[];
  /** The disagreement in the one finding shape (P1.1); absent on questions raised before it. */
  finding?: Finding;
  returned?: Returned;
}

/** A measurement role the proposer wasn't sure of (P1.4b): what the point says about itself and the role it proposed. */
export interface RolePayload {
  point_id: string; point: string | null; description: string | null; uom: string | null; letters: string | null;
  asset: string | null; asset_label: string | null; proposed: string | null; proposed_label: string | null;
  confidence: number; basis: string[]; choices: { key: string; label: string }[];
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
  | { id: number; kind: "role"; subject: string; status: string; label: string | null; payload: RolePayload }
  | { id: number; kind: "message"; subject: string; status: string; label: string | null; payload: MessagePayload }
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
  decision: "accept" | "reject" | "choose" | "field";
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
  /** Why it was last handed on, e.g. "Back from the field: done: gauge reads 190 psig". */
  handed_reason?: string | null;
  /** Its case's checklist progress; null when the case has none. */
  checklist?: ChecklistProgress | null;
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
/** What changed on a person's work lately (P5.1): the last day, or back to their last look at Home. */
export interface WorkHome {
  person: string; seen_at: string | null;
  /** Where its changes start: a day ago, or the last look when that was longer ago (#1358). */
  since: string;
  changes: { id: number; item_id: number; kind: string; actor: string; at: string; data: Record<string, unknown>;
    title: string; type: string; state: string; subjects: string[]; watching: boolean }[];
  watching: string[];
}

export interface SavedWorkView {
  id: number; role: string; name: string; list: string; filters: Record<string, string>; created_by: string; created_at: string;
}

export interface CloseoutVocabulary {
  dispositions: Record<string, string>; outcomes: Record<string, string>; failure_modes: Record<string, string>;
  /** Set by a steward's answer (Matched, No work needed), never picked on the form. */
  answered?: string[];
  mechanisms: Record<string, string>; causes: Record<string, string>; activities: Record<string, string>;
}

export interface CaseCloseout {
  item_id: number; disposition: string; failure_mode: string | null; mechanism: string | null; cause: string | null;
  activity: string | null; outcome: string | null; note: string | null; duplicate_of: number | null; by: string; at: string;
  recheck_at?: string | null; held?: boolean | null; held_note?: string | null; held_by?: string | null;
}

export interface CloseoutSuggestion {
  from: "words" | "agent"; disposition?: string; failure_mode?: string; mechanism?: string; cause?: string;
  activity?: string; outcome?: string;
}

export interface CloseoutInput {
  by: string; disposition: string; failure_mode?: string; mechanism?: string; cause?: string; activity?: string;
  outcome?: string; note?: string; duplicate_of?: number; suggested?: CloseoutSuggestion;
  /** Contained or not fixed: who the follow-up goes to, a person or role:<role> (#1385). */
  follow_up_to?: string;
}

export interface PerfCell {
  arrivals: { n: number; ids: number[] }; closed: { n: number; ids: number[] }; backlog: { n: number; ids: number[] };
  sla_misses: { n: number; ids: number[] }; escalations: { n: number; ids: number[] }; hand_backs: { n: number; ids: number[] };
  repeats: { n: number; ids: number[] }; triage_h: number | null; dispatch_h: number | null; close_h: number | null;
}

export interface PerfRow {
  queue?: string; type?: string; weeks: PerfCell[]; keeping_up: "keeping_up" | "holding" | "falling_behind"; backlog_avg4: number;
  /** The last four weeks' mean arrivals and closed, from the server like backlog_avg4. */
  arrivals_avg4: number; closed_avg4: number;
}

export interface WorkPerformance {
  weeks: { start: string; end: string }[]; queues: PerfRow[]; types: PerfRow[];
  compliance: { as_of: string; summary: Record<string, { plans: number; overdue: number; on_time_pct: number }> } | null;
  /** Operator rounds per shift, newest first (P5.4c). */
  rounds?: { shift_start: string; shift_end: string; rounds: number; done: number; partly_done: number; missed: number;
    open: number; not_taken: number; found: number }[];
}

/** One of an event's options (P5.20): when it would be done, the production deferred until then, the risk of waiting,
 * and whether it is ready; an option that can't be taken says why. */
export interface CaseOption {
  key: "dispatch" | "window" | "shelve" | "escalate" | "watch"; label: string; detail?: string; available: boolean;
  unavailable?: string; hours?: number | null; by?: string | null;
  deferred?: { oil_bbl: number; gas_mscf: number; usd: number } | null;
  risk?: { level: "Low" | "Medium" | "High"; why: string };
  ready?: boolean; blockers?: string[]; warnings?: string[]; clashes?: string[]; basis?: string;
  /** Readiness that can't be said, with why ("Parts readiness unknown: no stock source."): never read as ready. */
  unknown?: string[];
  plan_item?: number; start?: string; crew?: string | null; until?: string; to?: string;
  /** A ride along: who takes it on the visit (the operator queue when null). */
  operator?: string | null;
  /** A ride along on a work order nobody is named on ("SAP order 4000123"): the planner adds it to the order. */
  order?: string | null;
}

export interface OptionDecision { id: number; chosen: CaseOption["key"]; recommended: CaseOption["key"]; accepted: boolean;
  reason: string | null; by: string; at: string }

/** An event's options side by side, one recommended with its reason (P5.20). */
export interface CaseOptions {
  applies: boolean; why_not: string | null; exception_id: number | null; item_id?: number | null; type?: string;
  stage?: string; at?: string;
  /** A case a field operator already has on the same asset: said and linked, never a clash (#1401). */
  field_case?: { exception: number; item_id: number; title: string } | null;
  loss?: { oil_bbld: number; gas_mscfd: number; usd_per_day: number; wells: number; basis: string } | null;
  if_it_trips?: { oil_bbld: number; gas_mscfd: number; usd_per_day: number; wells: number; basis: string } | null;
  criticality?: string | null; prices?: { oil_bbl: number; gas_mscf: number; source: string };
  options: CaseOption[]; recommended?: CaseOption["key"] | null; reason?: string; unknown?: string[];
  decided?: OptionDecision[] | null;
}

export interface OptionAcceptance {
  days: number; decided: number; accepted: number; rate: number | null;
  types: { type: string; label: string; decided: number; accepted: number; rate: number | null; chosen: Record<string, number> }[];
}

export interface Readiness {
  ready: boolean; blockers: string[];
  /** For a send to someone: whether the checks hold it at all (a send to the planner never is, #1391). */
  holds?: boolean;
  /** What whoever sends the job should know that never holds it: an open MOC on the equipment, a permit to confirm. */
  warnings?: string[];
  /** Each check that can't be said, with why ("Parts readiness unknown: no stock source."). */
  unknown?: string[];
  checks: { check: string; label: string; ready: boolean; blockers: string[]; checked: boolean; note: string | null;
    unknown?: string;
    parts?: { material: string; label: string; description: string | null; needed: number; stock: number | null;
      short: number | null;
      orders: { po: string; quantity: number; due: string }[] }[];
    centers?: { id: string; label: string; description: string | null; crew: number | null; busy: number;
      full: boolean; jobs: { id: number; title: string; assignee: string | null }[] }[];
    needed?: boolean;
    /** Needs a permit and no permit records are loaded (#742): confirm one is in hand, never a block. */
    confirm?: boolean;
    permits?: { id: string; label: string; type: string | null; state: string | null; valid_to: string | null;
      location_label: string | null; valid: boolean; why_not: string | null }[] }[];
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

/** How much of a case's checklist is ticked off (done or not needed). */
export interface ChecklistProgress { done: number; total: number }

/** One item on a case's checklist: what the case asks for, ticked off as it is done (not a workflow step). */
export interface CheckItem {
  id: number; position: number; text: string; subjects: string[];
  state: "open" | "done" | "skipped"; note: string | null; done_by: string | null; done_at: string | null;
  /** The case it was sent as, part of this one: the item is done when that case is done. */
  child: { id: number; title: string; state: string; closed_at: string | null; assignee: string | null; role: string | null;
    /** Where it is and when it is due (#26), while it is open. */
    step?: string | null; due?: string | null } | null;
}

export interface WorkDraft {
  id: string; title: string; role: string; summary: string; evidence: string[]; subjects: string[];
  steps: { text: string; subjects: string[] }[];
  item_id: number | null; item_state: string | null; confirmed_by: string | null; confirmed_at: string | null;
}

export interface CaseRow {
  id: number; type: string; title: string; subjects: string[]; state: string; outcome: string | null; step: string | null;
  priority: number | null; assignee: string | null; role: string | null; due: string | null; created_by: string;
  source: string | null; created_at: string; updated_at: string; closed_at: string | null; overdue: boolean | null;
  process_key: string | null; escalated: boolean; checklist: ChecklistProgress | null;
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

/** What sounded and what was written around a case's event (P3.5a): alarms on its assets' points and the operator log
 * entries that mention them, in the event's window. */
export interface CaseAround {
  window: { start: string; end: string } | null;
  units: { id: string; label: string | null }[];
  alarms: { point_id: string; point: string; type: string; level: string | null; limit: number; peak: number;
    start: string; end: string | null }[];
  log: { id: string; at: string; author: string | null; text: string | null }[];
  basis: string;
}

/** A safeguard bypass in place on a case's system, and an open change record about it (P3.9b, GET
 * /work/items/{id}/safeguards): the banner at the top of the evidence. */
export interface CaseBypass {
  id: string; label: string; safeguard: string; function: string | null; action: string | null; state: string | null;
  bypassed_from: string | null; expires: string | null; reason: string | null; authorised_by: string | null;
  moc: string | null; protects: string[]; protects_labels: string[]; acts_on: string[]; acts_on_labels: string[];
  overdue: boolean; overdue_hours: number | null; say: string;
}

export interface CaseChange {
  id: string; label: string; title: string | null; change_type: string | null; state: string | null;
  opened: string | null; closes: string | null; about: string[]; about_labels: string[];
}

export interface CaseSafeguards {
  bypasses: CaseBypass[];
  changes: CaseChange[];
  basis: string;
}

/** What suppression held back (P3.5b): the windows running now and every finding counted under one. */
export interface SuppressedView {
  findings: number;
  raised: number;
  windows: { kind: string; id: number; reason: string; start_at: string; end_at: string; created_by: string;
    raised: number; findings: number }[];
  items: { id: number; type: string; key: string; title: string; source: string | null; subjects: string[];
    value_usd: number | null; window_kind: string; window_id: number; reason: string; raised: number;
    first_seen: string; last_seen: string }[];
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

/** The headline work figures (GET /work/counts), counted over the List's own rows: `open` is the List unfiltered,
 * `overdue` its Overdue toggle, `stages` its Stage picker and the Board's columns, `waiting_on_you` its Waiting on me
 * preset. The Work badge, Home, Needs you and the handover read these, so a number always equals the List it opens. */
export type EventStage = "dispatch" | "planned" | "field" | "back" | "desk" | "closed";
export interface WorkFigures {
  open: number;
  overdue: number;
  stages: Record<Exclude<EventStage, "closed">, number>;
  /** `my_work`: what waits on you less what waits for dispatch, the count Home's My work and the My work page show. */
  waiting_on_you: { total: number; overdue: number; to_dispatch: number; my_work: number; assigned: number; unclaimed: number } | null;
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
  /** How many entries in a row read the same as this one, the latest of them (#1372); absent for one. */
  count?: number;
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
  /** The owning specialist's brief (P4.3): whether one is expected, when it is due, the brief or why there is none,
   * and the person's verdict on its proposal. */
  brief?: CaseBriefView;
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
  /** What the case asks for, ticked off as it is done. */
  checklist?: CheckItem[];
  /** The case this one is part of: an item of its checklist sent here. */
  parent?: { id: number; title: string; state: string; closed_at: string | null } | null;
}

/** A case as an action left it (P5.29d): GET /work/items/{id} without what the action doesn't change (its findings,
 * brief and spend), so the page shows the new holder, steps and subjects with the action's confirmation. */
export type CaseNow = Omit<WorkItem, "agents_configured" | "agent_spend" | "brief" | "sources">;

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
  id: number; shift_start: string; shift_end: string; state: "draft" | "acknowledged"; status: HandoverStatus;
  /** `unknown`: sections that can't be said, and why ("no bypass register loaded"): not sections with nothing. */
  /** A line's `at` is its time, written `{at}` in its text; `links` is the List filtered to a section's rows (P5.26i). */
  facts: { sections: Record<string, { text: string; cites: string[]; at?: string }[]>; more?: Record<string, number>;
    unknown?: Record<string, string>; links?: Record<string, string> };
  item_id: number | null; drafted_at: string; notes: string | null; notes_by: string | null; notes_at: string | null;
  acknowledged_by: string | null; acknowledged_at: string | null; acknowledged_note: string | null;
  /** Acknowledged, or a later shift's handover replaced it: kept as it was, with no notes or acknowledgement (E12). */
  read_only?: boolean;
}
/** Where a handover stands, in the page's words (fuzer.ops.handover.status). */
export type HandoverStatus = "in progress" | "acknowledged" | "not acknowledged";
export interface HandoverRow {
  id: number; shift_start: string; shift_end: string; state: string; status: HandoverStatus; notes_by: string | null;
  acknowledged_by: string | null; acknowledged_at: string | null; item_id: number | null; lines: number | null;
}

/** An update drafted for the system that raised work the field finished (#466). */
export interface Closeout {
  id: number; exception_id: number; system: string; record: string; action: "complete" | "follow_up";
  draft: { text: string; status?: string | null; short_text?: string; [k: string]: unknown };
  state: "draft" | "approved" | "sent" | "discarded"; item_id: number | null; created_by: string; created_at: string;
  decided_by: string | null; decided_at: string | null; applied_at: string | null;
  /** What approving writes, in plain words (#1145). */
  change?: string;
}

export interface ExceptionRow {
  id: number; type: string; key: string; title: string; source: string | null; state: string;
  priority: number | null; value_usd: number | null; subjects: string[]; unmatched: string[];
  item_id: number | null; case_state: string | null; assignee: string | null; role: string | null;
  raised_count: number; reports?: number; first_seen: string; last_seen: string;
  /** Its case's last hand-on was the field's report sending it back to the IOC to close out. */
  back_from_field?: boolean;
  /** Where it stands, decided once on the server (ops/exceptions.py stage): All work's views filter and count by it. */
  stage?: EventStage;
  /** When it was closed or cleared, and when anything about it last changed. */
  closed_at?: string | null; updated_at?: string;
  planned?: { id: number; start_at: string; end_at: string; crew: string | null } | null;
  /** Set aside by a person until then, with why (P3.5c). */
  shelved_until?: string | null; shelved_by?: string | null; shelved_reason?: string | null;
  /** What the source sent with it, and what the field has reported back (`field`). */
  evidence?: { field?: { at: string; by: string; status: string; note: string | null; reading?: FieldReading }[]; [k: string]: unknown };
  /** Other systems' exceptions about the same event, which joined this one's case (#464). */
  joined?: JoinedException[];
}

/** One row of all the work (GET /cases): an event (with its case, if any) or a case no event raised, in one shape.
 * A case-only row has no exception `id`, `exception_id` or `key`. */
export interface WorkListRow extends Omit<ExceptionRow, "id" | "key"> {
  uid: string; id: number | null; exception_id: number | null; key: string | null;
  kind: string; kind_label: string; stage: EventStage; holder: string; queue: string;
  due_at: string | null; overdue: boolean; opened: string; updated: string;
  /** the facility, area and asset (equipment, well or pipeline) it is about, by id, or `none` */
  facility: string; area: string; asset: string;
  /** A step waiting unclaimed in a queue of a role the person looking acts in (asked with `person`): theirs to take. */
  take?: number | null;
}
/** The filters of all the work, each a comma list where it can be one; a preset is only a set of these. */
export interface WorkListFilters {
  state?: string; kind?: string; stage?: string; holder?: string; queue?: string; waiting_on?: string; person?: string;
  overdue?: string; subject?: string; at?: string; source?: string; facility?: string; area?: string; asset?: string;
  within?: string; q?: string; sort?: string;
}
export interface WorkListResult {
  rows: WorkListRow[]; total: number;
  facets: { kind: Record<string, number>; stage: Record<string, number>; holder: Record<string, number>;
            queue: Record<string, number>; source: Record<string, number>; facility: Record<string, number>;
            area: Record<string, number>; asset: Record<string, number>; overdue: number };
  /** the names of the facilities, areas and assets the facets count */
  labels?: Record<string, string>;
}

/** Where attention is needed (Home's map): open exceptions gathered at the located places they are about. */
export type AttentionBand = "high" | "medium" | "low";
export interface AttentionCrew { who: string | null; status: "on site" | "planned"; at: string | null }
export interface AttentionItem {
  id: number; title: string; type: string; label: string; source: string | null; priority: number | null;
  band: AttentionBand; value_usd: number | null; assignee: string | null; role: string | null; item_id: number | null;
  due: string | null; overdue: boolean; first_seen: string; crew: AttentionCrew | null;
}
export interface AttentionPlace {
  id: string; places: string[]; label: string; name: string | null; kind: string; lon: number; lat: number; band: AttentionBand;
  open: number; counts: Record<AttentionBand, number>; overdue: number; unassigned: number;
  top: AttentionItem[]; exceptions: number[]; crews: AttentionCrew[];
}
export interface LocatedPlace { id: string; label: string; name: string | null; kind: string; lon: number; lat: number }
export interface Attention { places: AttentionPlace[]; open: number; unplaced: number; data_work: number; truncated: boolean }

/** An exception that joined another's case: the same event reported by another source. */
export interface JoinedException {
  id: number; type: string; key: string; title: string; source: string | null; state: string;
  value_usd: number | null; subjects: string[]; first_seen: string; label: string;
  /** The message it came from, when a message to the IOC raised it (P2.8). */
  message?: MessageSaid | null;
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
  kind: "text" | "number" | "flag" | "time" | "asset" | "page" | "link";
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
  /** Set aside by a person until then (P3.5c): the case says so in place of Open (#1400). */
  shelved_until?: string | null;
}

/** An exception type: declared by a module in code, or defined as data for another system's exceptions. */
export interface ExceptionKind {
  name: string; label: string; role: string | null; process: string | null; case_type: string | null;
  audience: string[]; close_case_on_clear: boolean; description: string; defined_in: "code" | "data"; open: number;
}

/** An item on the integrated plan, with its clashes (same asset, same person) and bundling chances. */
/** A run of an operator round this shift (P5.4). */
export interface RoundRun {
  id: number; round: string; area: string | null; shift_start: string; shift_end: string; role: string;
  points: number; taken: number; found?: number; state: "open" | "done" | "partly_done" | "missed";
}
export interface RoundPoint {
  id: string; point_id: string; asset: string | null; asset_label: string | null; round: string; area: string | null;
  what: string; kind: "reading" | "check"; unit: string | null; low: number | null; high: number | null;
  taken: { value: number | null; passed: boolean | null; note: string | null; by: string; at: string; exception_id: number | null } | null;
}
export interface RoundDetail extends Omit<RoundRun, "points"> { points: RoundPoint[] }

/** Production at risk from planned shutdowns over a horizon (P5.9a), per window of overlapping outages. */
export interface AtRisk {
  start: string; end: string; days: number; basis: string;
  windows: {
    start: string; end: string; assumptions: string[]; untested: string[];
    items: { id: number; title: string; kind: string; hours: number; assumed: string | null; start: string; end: string;
      units: { id: string; label: string }[]; wells: number }[];
    wells: { id: string; label: string; hours: number; rates: Record<string, { value: number; unit: string | null; at: string }> }[];
    at_risk: { quantity: string; value: number; unit: string | null }[];
  }[];
}

/** Outages on the plan and the customers they hit (P5.22): one set read by asset, by person and by customer. */
export interface OutageHit { contract: string; customer: string | null; service: string | null; point: string; start: string;
  end: string; hours: number; volume_bbl: number | null; usd: number | null }
export interface Outage {
  key: string; title: string; project: string | null; item_ids: number[]; start: string; end: string; hours: number;
  units: { id: string; label: string }[]; areas: { id: string; label: string }[]; crews: { code: string; name: string }[];
  people: string[]; over_capacity: { key: string; resource: string; name: string; peak: number; capacity: number; from: string; to: string }[];
  breaks: { rule: number; rule_say: string; say: string }[]; hits: OutageHit[]; volume_bbl: number | null; usd: number | null;
}
export interface Outages {
  start: string; end: string; days: number; contracts: number; basis: string; outages: Outage[];
  by_asset: { id: string; label: string; kind: string | null; outages: string[] }[];
  by_person: { who: string; name: string; crew: boolean; outages: string[]; over_capacity: Outage["over_capacity"] }[];
  by_customer: { customer: string; customer_type: string | null; contracts: string[]; outages: string[]; times: number;
    flagged: boolean; volume_bbl: number | null; usd: number | null }[];
}

/** A schedule on the integrated plan (P5.18): a turnaround, project or campaign with activities, links and resources. */
export interface ScheduleSummary {
  code: string; title: string; kind: string; start_at: string; finish_by: string | null; hours_per_day: number;
  days_per_week: number; activities: number; first_start: string | null; last_finish: string | null;
}
export interface ScheduleLinkRef { code: string; type: "FS" | "SS" | "FF" | "SF"; lag_hours: number }
export interface ScheduleRow {
  id: number; code: string | null; wbs: string | null; name: string; status: string; duration_hours: number | null;
  percent_complete: number | null; work_order: string | null; group?: string | null; not_before?: string | null; adds_boed?: number | null; assets: string[]; area: string | null;
  needs: Record<string, number>; predecessors: ScheduleLinkRef[];
  early_start: string; early_finish: string; late_start: string; late_finish: string; float_hours: number; critical: boolean;
  start: string; finish: string; held_back_hours: number; held_back_by: { resource: string; activities: string[] } | null;
}
export interface ScheduleOverload {
  resource: string; name: string | null; from: string; to: string; need: number; capacity: number; unit: string | null;
  activities: string[]; other_work: boolean;
}
export interface ScheduleDay { day: string; schedule: number; other: number; capacity: number | null }
export interface ScheduleAnalysis {
  project: { code: string; title: string; kind: string; start_at: string; finish_by: string | null; hours_per_day: number; days_per_week: number };
  activities: ScheduleRow[]; critical_path: string[]; unconstrained_finish: string; levelled_finish: string;
  levelled_days_longer: number; overruns_window_by_days: number; levelled_chain: { code: string; waited_for?: string }[];
  overloads_if_unlevelled: ScheduleOverload[]; overloads_levelled: ScheduleOverload[];
  resources: PlanResource[]; profiles: { levelled: Record<string, ScheduleDay[]>; unlevelled: Record<string, ScheduleDay[]> };
  /** Every other active job in the schedule's span, a row per resource it needs (resource null when it needs none). */
  other_work: { id: number; title: string; resource: string | null; quantity: number | null; start: string; end: string; project: string | null;
    order?: string | null; status?: string }[];
}
export interface ResourceMove {
  item_id: number; title: string; source: string; code: string | null; order: string | null; slip_hours: number;
  float_hours: number | null; finish_later_days: number | null; new_start: string; new_end: string;
}
/** A capacity window that lowered a resource while it was over: a weather window, leave, a vessel in dock. */
export interface CapacityWindow { id: number; start: string; end: string; capacity: number; reason: string | null }
export interface ResourceOverload {
  /** Names the overload (resource@start) for a proposal that clears it. */
  key: string; from: string; to: string; peak: number; capacity: number; over_by: number; limited_by: CapacityWindow[];
  /** A crew change names the job it travels for: moving that job moves the trip. */
  jobs: { item_id: number | null; title: string; source: string; code: string | null; quantity: number;
    travels_for?: { item_id: number; code: string | null; title: string } }[];
  moves: ResourceMove[];
}
/** The constraint check (GET /plan/constraints): every overload on every shared resource, first to last. */
export interface PlanConstraints {
  start: string; end: string; days: number; basis: string;
  overloads: (ResourceOverload & { resource: string; name: string; kind: string; unit: string | null })[];
}
/** One job a plan change moves (P5.21): its dates before and after and the constraint behind the move. */
export interface PlanMove {
  item_id: number; title: string; code: string | null; project: string | null; kind: string; crew: string | null;
  case: number | null; order: string | null; released: boolean;
  before: { start: string; end: string }; after: { start: string; end: string }; hours: number; because: string;
}
export interface PlanKpis { finish: string | null; deferred_oil: number; deferred_unit: string; overloads: number }
/** A plan change (GET /plan/changes/{id}): what broke, the constraint it puts on the work, and the plan levelled
 * again around it, with what the planner typed. */
export interface PlanNotice {
  n: number; audience: "crew" | "field" | "downstream" | "customer"; to: string; name: string; jobs: number[]; text: string;
  sent_by: string | null; sent_at: string | null;
}
export interface PlanChange {
  exception_id: number; key: string; say: string; constraint: string; start: string; until: string;
  cause_case: number | null; open: boolean; forms: string[]; constraints: { text: string; form: string }[];
  /** Once a maintenance planner approves it (P5.21c): who and when, the SAP schedule changes queued, the notices. */
  state: "open" | "approved" | "reverted"; approved_by: string | null; approved_at: string | null;
  /** What was done to it (P5.23c): when, by whom (Fuzer or a person), what; once approved, what it changed, before
   * and after; and who reverted it. */
  history: { at: string; by: string; did: string }[]; changed: PlanChanged[];
  reverted_by: string | null; reverted_at: string | null;
  writebacks: { id: number; record: string | null; state: string; item_id: number | null; approver_role: string }[];
  notices: PlanNotice[];
  /** The plan change's own case; and on an auto-assign one (P5.23b), the planner's picks by job id. */
  case: number | null; picks: Record<string, string | number | null>;
  proposal: {
    moves: PlanMove[]; schedules: string[]; basis: string;
    pull_forward: { item_id: number; title: string; start: string; end: string; order: string | null; because: string }[];
    /** Absent on an auto-assign plan change, which moves nothing. */
    kpis?: { before: PlanKpis; after: PlanKpis; start: string; days: number };
    /** Auto-assign (P5.23b): the crew proposed for each field job with none, why, and anything a pick breaks. */
    assign?: PlanAssign[];
    /** An outage's windows (P5.22c): three proposed, the one recommended and why, the one picked, similar past work. */
    windows?: OutageWindow[]; recommended?: number; picked?: number; recommend?: string;
    past?: { key: string; start: string; end: string; hours: number; comment: string | null }[];
    /** What its jobs work on, each with its notifications and work orders and its downtime (P5.22d). */
    maintenance?: { id: string; label: string; kind: string; notifications: Maintenance["recent"]; downtime: DowntimeRow[] }[];
  } | null;
}
export interface OutageWindow {
  n: number; start: string; end: string; hours: number; usd: number; volume_bbl: number; reasons: string[];
  alongside: string[]; hits: OutageHit[]; customers: { customer: string; times: number; flagged: boolean }[];
}
export type PlanChanged =
  | { what: "job"; id: number; title: string; code: string | null; case: number | null; fields: Record<string, [unknown, unknown]> }
  | { what: "booking"; id: number; resource: string; name: string; title: string | null; before: number | null; after: number | null }
  | { what: "capacity"; id: number; resource: string; name: string | null; title: string | null; start: string; end: string;
      before: null; after: number }
  | { what: "schedule"; id: string; title: string; before: string; after: string };
export interface PlanAssign {
  item_id: number; title: string; kind: string; start: string; end: string; case: number | null; order: string | null;
  crew: string | null; name: string | null; people: number; because: string; breaks: string[]; picked: boolean;
}
export interface ResourceLoad {
  code: string; name: string; kind: string; unit: string | null; capacity: number; work_center: string | null; sources: string[];
  counts: string[]; routine: string | null; routine_load: RoutineLoad | null;
  days: { day: string; capacity: number; demand: number; by_source: Record<string, number> }[]; overloads: ResourceOverload[];
  capacity_windows: CapacityWindow[];
}
/** The people a resource's routine duties hold whatever else is planned (operators on their rounds and checks). */
export interface RoutineLoad { quantity: number; source: string; basis: string }
/** One job on a day of the logistics plan: who it needs, where, and when. */
export interface LogisticsJob { item_id: number; title: string; code: string | null; schedule: string | null; order: string | null;
  assets: string[]; area: string | null; start: string; end: string; quantity: number }
export interface LogisticsMaterial { material: string | null; material_id: string; description: string | null; quantity: number;
  order: string | null; item_id: number; title: string; code: string | null; schedule: string | null;
  verdict: "in_stock" | "on_order" | "late" | "not_on_order" | "not_on_order_too_late"; due: string | null;
  days_late: number | null; short: number | null }
export interface LogisticsDay {
  day: string;
  crews: { resource: string; name: string; unit: string | null; capacity: number; kind: string; jobs: LogisticsJob[] }[];
  equipment: { resource: string; name: string; unit: string | null; capacity: number; kind: string; jobs: LogisticsJob[] }[];
  travel: { crew: string; name: string; people: number; way: "out" | "back"; schedule: string; by: string | null }[];
  /** Visiting crews aboard with no job that day: they keep their beds until they fly back. */
  aboard?: { crew: string; name: string; people: number; schedule: string }[];
  /** Parts reserved on the work orders whose first job starts that day, with what covers each. */
  materials?: LogisticsMaterial[];
  limits: { resource: string; name: string; kind: string; unit: string | null; need: number; capacity: number; over: boolean }[];
}
export interface PlanResource { code: string; name: string; kind: string; unit: string | null; capacity: number; work_center?: string | null;
  counts?: string[]; routine?: RoutineLoad | string | null }

export interface PlanItem {
  id: number; kind: string; title: string; source: string | null; source_key: string | null; subjects: string[];
  area_id: string | null; area_label: string | null; start_at: string; end_at: string; status: string;
  crew: string | null; crew_hours: number | null; needs_shutdown: boolean; deferment_usd: number | null;
  exception_id: number | null; notes: string | null;
  /** Why a plan change last moved it (P5.21c): "20-K-2200B tripped". */
  moved_because?: string | null;
  /** Its case, once it has one; and whether the job is done in the field or at a desk. */
  item_id?: number | null; place?: "field" | "desk";
  /** Released to the field (step 5 of the work model): when, by whom, and why if it went though not ready. */
  released_at?: string | null; released_by?: string | null; release_reason?: string | null;
  /** Where the job stands on the field job's flow: planned, scheduled, released, in progress, done or cancelled. */
  stage?: "planned" | "scheduled" | "released" | "in_progress" | "done" | "cancelled";
  assets: { fuzer_id: string; label: string | null }[];
  clashes: { kind: "same_asset" | "crew" | "bundle" | "simops"; with: number; title: string; say: string }[];
  /** Parts on its own work order that stock does not cover, for active work starting within 30 days (the Spares
   * check's parts, never the equipment's BOM). */
  parts_short?: { id: string; label: string | null; description: string | null }[];
  /** The shared-resource overloads it is in (asked for with `over_capacity`): as a job using the resource, or as the
   * job a crew flies for; `limited_by` is the reason of any capacity window behind it (weather, leave). */
  over_capacity?: { key: string; resource: string; name: string; kind: string; unit: string | null; from: string; to: string;
    peak: number; capacity: number; over_by: number; limited_by: string[] }[];
  /** The planner's rules it breaks (asked for with `rules`): the rule said back and how this job breaks it. */
  breaks?: { rule: number; rule_say: string; say: string }[];
}

/** A rule a planner typed in words on Plan (P5.23a), as read and said back, on or off, with the jobs breaking it. */
export interface PlanRule {
  id: number; text: string; form: "deadline" | "duration" | "gap" | "overlap" | "sequence"; say: string; on: boolean;
  created_by: string; created_at: string;
  breaks: { item_id: number; title: string; code: string | null; start: string; end: string; say: string }[];
}

/** What a job needs before it is released: each prerequisite satisfied or not, with what holds it. */
export interface PlanPrerequisites {
  item_id: number; ready: boolean; released_at: string | null; released_by: string | null;
  prerequisites: { kind: string; label: string; satisfied: boolean; detail: string[] }[];
}

/** A turnaround's scope read against the plan (GET /turnarounds). */
export interface TurnaroundScope {
  revision: string | null; revisions: string[]; as_of: string; planned_start: string | null;
  scoped_work: { id: string; order: string | null; tag: string | null; tag_id: string | null; priority: string | null;
    text: string | null; planned_start: string | null; status: string | null }[];
  shutdown_plans_not_in_scope: { id: string; plan: string | null; task: string | null; tag: string | null; next_due: string }[];
  bad_actors_not_in_scope: { id: string; tag: string | null; failures: number; lost_oil_bbl: number | null }[];
  spares_out_of_stock: { for: string | null; material: string | null; id: string; description: string | null }[];
  window: { start: string; end: string } | null;
  on_plan: PlanItem[]; ride_along: PlanItem[]; shutdown_outside: PlanItem[];
  /** The planning system's schedule for the revision, when one names it (its S-curve is a live figure). */
  schedule?: { project: string; as_of: string; late: string[]; slipped: string[] } | null;
}

/** A job a field operator was sent to: the exception with where to go, what was seen and what they reported. */
export interface FieldJob extends ExceptionRow {
  label: string; due: string | null; task_id: number | null; task_name: string | null;
  assets: { fuzer_id: string; label: string | null; kind: string | null; lat: number | null; lon: number | null;
    /** The located place it is on (a platform's name), and whether that stands in water: no road directions there. */
    site?: string | null; offshore?: boolean }[];
  finding: Finding;
  history: { id: number; title: string; state: string; first_seen: string }[];
  field: { at: string; by: string; status: string; note: string | null; reading?: FieldReading; photos?: number[] }[];
  /** Who sent it to them, when, and what they asked for. */
  sent: { by: string | null; at: string | null; reason: string | null } | null;
  /** The open step is in one of their roles (a Field check), so they can open it; another role's step handed to
   * them (an engineer's Triage) is reported back on from My jobs instead. */
  own_step: boolean;
  /** What to know before starting that never holds the job: an open MOC on the equipment. */
  warnings?: string[];
  /** Waiting unclaimed in one of their roles' queues: theirs to take, not yet sent to them. */
  queued: boolean;
  /** Why a plan change last moved this planned job (P5.21c): "20-K-2200B tripped". */
  moved_because?: string | null;
  /** Each system's report of this event, when more than one reported it (#464). */
  sources: CaseSource[];
  /** Checks to make before the work, such as a compressor trip's restart checklist. */
  checklist?: string[];
  /** The permit to work it runs under (P5.11a): null when no permit records are loaded. */
  permit?: FieldPermit | null;
  /** A handover at shift change still waiting to be accepted (P5.11c). */
  handover?: FieldHandover | null;
}

export interface FieldHandover {
  from: string; to: string; at: string; done: string; left: string; unsafe: string | null;
  permit: FieldPermit["permit"]; mine_to_accept: boolean; waiting_on: string | null;
}

export interface FieldPermit {
  needed: boolean; blocked: boolean; blocker: string | null;
  /** No permit records are loaded, so none could be checked (#742): the phone asks for one in hand. */
  unchecked?: boolean; note?: string | null;
  permit: { label: string; type: string | null; state: string | null; valid_from: string | null; valid_to: string | null;
    work: string | null; location_label: string | null; valid: boolean; why_not: string | null;
    isolations: { isolation: string; point: string | null; method: string | null; state: string | null }[] } | null;
}

/** One opportunity with its working (GET /opportunities/{key}): the value its scan stored, which the board, the case
 * and the daily report all read, and how it is made. */
export interface OpportunityMove {
  from: string | null; from_id: string | null; to: string | null; to_id: string | null;
  gas: number | null; gas_unit: string | null; gain: number | null; gain_unit: string | null;
  from_marginal: number | null; to_marginal: number | null;
}
export interface OpportunityView {
  key: string; kind: string; title: string; target: string | null; gain_bbld: number | null; value_usd: number | null;
  status: string; item_id: number | null; reason: string | null; scanned_at: string; decided_by: string | null;
  decided_at: string | null; detail: Record<string, unknown>;
  worth: { usd: number; days: number; gain: number; gain_unit: string; oil_bbl: number; price_bbl: number; currency: string } | null;
  moves: OpportunityMove[];
  evidence: { records: string[]; first: string | null; last: string | null };
}

/** The most rows the server lists of exceptions in one request (`/exceptions` caps `limit` at 1000). */
export const EXCEPTIONS_LIMIT = 1000;

export const workApi = {
  board: (f: BoardFilter, page = 1) =>
    request<BoardPage>("GET", "/board", { ...f, page, per_page: 50, facets: "true" }),
  boardHistory: (key: string) => request<{ rank: number; value_usd: number | null; at: string }[]>("GET", "/board/history", { key }),
  boardPin: (key: string, by: string, reason: string, rank?: number, value_usd?: number) =>
    request<unknown>("POST", "/board/pins", undefined, { key, by, reason, rank: rank ?? null, value_usd: value_usd ?? null }),
  boardProject: (p: { title: string; by: string; owner?: string; due?: string; benefit_usd?: number; scope?: string }) =>
    request<unknown>("POST", "/board/projects", undefined, p),
  workNames: () => request<Record<string, string>>("GET", "/work/names"),
  /** Each kind of work's one label (#1358), read once by the app shell into `workKind`. */
  workKinds: () => request<Record<string, string>>("GET", "/work/kinds"),
  workPeople: () => request<{ name: string; roles: string[]; assigned_roles?: string[]; away_until?: string | null; delegate?: string | null; display_name?: string | null; owner?: boolean;
    /** Open cases and waiting steps they hold now. */ holding?: number }[]>("GET", "/work/people"),
  /** The demo account picks the human roles it acts as in this project; none is every role. */
  actAs: (roles: string[]) => request<{ name: string; roles: string[]; picked: string[] }>("PUT", "/work/me/roles", undefined, { roles }),
  setWorkPerson: (name: string, roles: string[], by: string) =>
    request<{ name: string; roles: string[] }>("PUT", `/work/people/${encodeURIComponent(name)}`, undefined, { roles, by }),
  removeWorkPerson: (name: string, by: string) =>
    request<{ name: string; released_tasks: number[]; released_items: number[] }>(
      "DELETE", `/work/people/${encodeURIComponent(name)}`, { by }),
  workRoleSummary: () => request<WorkRoleSummary[]>("GET", "/work/roles/summary"),
  inbox: (person: string) => request<WorkInbox>("GET", "/work/inbox", { person }),
  workCounts: (person: string) => request<WorkFigures>("GET", "/work/counts", { person }),
  notifications: (person: string) =>
    request<{ notifications: WorkNotification[]; unread: number }>("GET", "/work/notifications", { person }),
  notifyPreference: (person: string) => request<NotifyPreference>("GET", `/work/people/${encodeURIComponent(person)}/notify`),
  setNotifyPreference: (person: string, p: NotifyPreference, by: string) =>
    request<NotifyPreference>("PUT", `/work/people/${encodeURIComponent(person)}/notify`, undefined, { ...p, by }),
  markNotificationsRead: (person: string, ids?: number[]) =>
    request<{ read: number }>("POST", "/work/notifications/read", undefined, { person, ids: ids ?? null }),
  // Every open row, not the server's default page of 200: All work's counts and search read this list, so
  // a cap here showed "200 open" and hid the rest. EXCEPTIONS_LIMIT is the server's own ceiling.
  exceptions: (f: { state?: string; type?: string; subject?: string; audience?: string; closed_since?: string; at?: string } = {}) =>
    request<ExceptionRow[]>("GET", "/exceptions", { limit: String(EXCEPTIONS_LIMIT), ...f }),
  attention: () => request<Attention>("GET", "/attention"),
  /** Where each entity sits on Home's map, by the place its chain leads to; unlocated ids are left out. */
  attentionPlaces: (ids: string[]) => request<Record<string, LocatedPlace>>("GET", "/attention/places", { ids: ids.join(",") }),
  opportunity: (key: string) => request<OpportunityView>("GET", `/opportunities/${encodeURIComponent(key)}`),
  decideOpportunity: (key: string, decision: "accepted" | "rejected" | "project", by: string, reason?: string) =>
    request<unknown>("POST", `/opportunities/${encodeURIComponent(key)}/decision`, undefined,
      { decision, by, reason: reason ?? null }),
  exceptionsSuppressed: () => request<SuppressedView>("GET", "/exceptions/suppressed"),
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
  sendBackCloseout: (id: number, by: string, reason: string) =>
    request<Closeout>("POST", `/closeouts/${id}/send-back`, undefined, { by, reason }),
  discardCloseout: (id: number, by: string, reason?: string) =>
    request<Closeout>("POST", `/closeouts/${id}/discard`, undefined, { by, reason: reason ?? null }),
  fieldJobs: (person: string) => request<{ person: string; jobs: FieldJob[]; coming_up: PlanItem[] }>("GET", "/exceptions/field", { person }),
  /** The same jobs without what is looked up per job, for a page that only counts them. */
  fieldJobsBrief: (person: string) => request<{ person: string; jobs: Pick<FieldJob, "item_id" | "queued">[]; coming_up: PlanItem[] }>("GET", "/exceptions/field", { person, brief: "true" }),
  fieldReport: (id: number, by: string, status: string, note?: string,
    extra: { reading?: FieldReading; photos?: string[]; client_id?: string; at?: string; override?: string;
      permit_in_hand?: boolean; project?: string } = {}) =>
    request<FieldJob>("POST", `/exceptions/${id}/report`, undefined, { by, status, note: note ?? null,
      reading: extra.reading ?? null, photos: extra.photos ?? [], client_id: extra.client_id ?? null, at: extra.at ?? null,
      override: extra.override ?? null, permit_in_hand: extra.permit_in_hand ?? null }, extra.project),
  /** A note from the phone to the IOC (P2.8): a case about what it names, a question for the steward, or nothing. */
  sendNote: (by: string, text: string) => request<MessageNote>("POST", "/messages", undefined, { by, text }),
  fieldHandover: (id: number, h: { by: string; to: string; done: string; left: string; unsafe?: string }) =>
    request<FieldJob>("POST", `/exceptions/${id}/handover`, undefined, { ...h, unsafe: h.unsafe ?? null }),
  acceptHandover: (id: number, by: string, client_id?: string, project?: string) =>
    request<FieldJob>("POST", `/exceptions/${id}/handover/accept`, undefined, { by, client_id: client_id ?? null }, project),
  rounds: () => request<RoundRun[]>("GET", "/rounds"),
  round: (id: number) => request<RoundDetail>("GET", `/rounds/${id}`),
  takeRound: (id: number, t: { point: string; by: string; value?: number; passed?: boolean; note?: string; client_id?: string;
    at?: string }, project?: string) =>
    request<RoundPoint & { exception: { id: number; raised: boolean } | null }>("POST", `/rounds/${id}/readings`, undefined, t, project),
  planAtRisk: (start?: string, days = 90) => request<AtRisk>("GET", "/plan/at-risk", { start, days }),
  planOutages: (start?: string, days = 90) => request<Outages>("GET", "/plan/outages", { start, days }),
  /** `active` keeps the work still to do: proposed, scheduled or in progress. */
  plan: (f: { start?: string; end?: string; area?: string; status?: string; subject?: string; within?: string; active?: boolean;
              overCapacity?: boolean; rules?: boolean } = {}) => {
    const { overCapacity, rules, ...rest } = f;
    return request<PlanItem[]>("GET", "/plan", { ...rest, active: f.active ? "true" : undefined, over_capacity: overCapacity ? "true" : undefined,
                                                 rules: rules ? "true" : undefined });
  },
  planChange: (id: number, by: string, change: Partial<{ start: string; end: string; status: string; crew: string; place: "field" | "desk" }>,
    project?: string) =>
    request<PlanItem>("PATCH", `/plan/${id}`, undefined, { by, ...change }, project),
  /** Book a job on a crew (or other resource) for its dates; refused with 409 over capacity unless a reason is given. */
  planBook: (id: number, by: string, b: { resource: string; quantity?: number; person?: string; reason?: string }) =>
    request<PlanItem>("POST", `/plan/${id}/book`, undefined, { by, ...b }),
  planPrerequisites: (id: number) => request<PlanPrerequisites>("GET", `/plan/${id}/prerequisites`),
  planRelease: (id: number, by: string, reason?: string) =>
    request<PlanItem>("POST", `/plan/${id}/release`, undefined, { by, reason }),
  planReschedule: (id: number, by: string, start: string, end: string, reason: string) =>
    request<PlanItem>("POST", `/plan/${id}/reschedule`, undefined, { by, start, end, reason }),
  planSync: () => request<{ orders: number; added: number; updated: number; closed: number }>("POST", "/plan/sync"),
  schedules: () => request<ScheduleSummary[]>("GET", "/plan/schedules"),
  containers: (status: string | null = "open") =>
    request<WorkContainer[]>("GET", "/containers", status === null ? undefined : { status }),
  container: (id: number) => request<WorkContainerDetail>("GET", `/containers/${id}`),
  containerCreate: (c: { by: string; kind: string; title: string; parent?: number | null; owner?: string | null;
    start_at?: string | null; finish_by?: string | null }) => request<WorkContainer>("POST", "/containers", undefined, c),
  containerPlan: (id: number) => request<PlanAgainstActual>("GET", `/containers/${id}/plan`),
  planLineAdd: (id: number, line: PlanLineNew) => request<PlanLine>("POST", `/containers/${id}/plan-lines`, undefined, line),
  planLineUpdate: (id: number, by: string, fields: Partial<PlanLineNew> & { status?: "open" | "dropped" }) =>
    request<PlanLine>("PUT", `/plan-lines/${id}`, undefined, { by, fields }),
  planLineMatch: (id: number, by: string, item_id: number) =>
    request<unknown>("POST", `/plan-lines/${id}/cases`, undefined, { by, item_id }),
  planLineUnmatch: (item_id: number, by: string) => request<unknown>("DELETE", `/plan-lines/cases/${item_id}`, { by }),
  containerPlanMatch: (id: number, by: string) => request<{ matched: number }>("POST", `/containers/${id}/plan/match`, { by }),
  statusMap: (system?: string) => request<StatusMapRow[]>("GET", "/status-map", system ? { system } : undefined),
  statusMapSet: (row: { by: string; system: string; object_type: string; status: string; state: string;
    user_status?: string; move?: string | null; note?: string | null }) =>
    request<StatusMapRow>("PUT", "/status-map", undefined, row),
  schedule: (code: string) => request<ScheduleAnalysis>("GET", `/plan/schedules/${encodeURIComponent(code)}`),
  scheduleApply: (code: string, by: string) =>
    request<{ project: string; finish: string; unconstrained_finish: string }>("POST", `/plan/schedules/${encodeURIComponent(code)}/apply`, undefined, { by }),
  scheduleAdd: (code: string, a: { by: string; code: string; name: string; duration_hours: number; after?: string[] }) =>
    request<unknown>("POST", `/plan/schedules/${encodeURIComponent(code)}/activities`, undefined, a),
  scheduleChange: (id: number, by: string, change: Partial<{ name: string; duration_hours: number; percent_complete: number; status: string; not_before: string; clear_not_before: boolean; adds_boed: number }>) =>
    request<unknown>("PATCH", `/plan/schedules/activities/${id}`, undefined, { by, ...change }),
  scheduleRemove: (id: number, by: string) => request<unknown>("DELETE", `/plan/schedules/activities/${id}`, { by }),
  scheduleDemand: (id: number, by: string, resource: string, quantity: number | null) =>
    request<unknown>("PUT", `/plan/schedules/activities/${id}/demand`, undefined, { by, resource, quantity }),
  scheduleLink: (by: string, pred: number, succ: number, type: ScheduleLinkRef["type"], lag_hours: number) =>
    request<unknown>("POST", "/plan/schedules/links", undefined, { by, pred, succ, type, lag_hours }),
  scheduleUnlink: (by: string, pred: number, succ: number) => request<unknown>("DELETE", "/plan/schedules/links", { by, pred, succ }),
  planRules: () => request<PlanRule[]>("GET", "/plan/rules"),
  planRuleAdd: (by: string, text: string) => request<PlanRule[]>("POST", "/plan/rules", undefined, { by, text }),
  planRuleSwitch: (id: number, by: string, on: boolean) => request<PlanRule[]>("PATCH", `/plan/rules/${id}`, undefined, { by, on }),
  planRuleRemove: (id: number, by: string) => request<PlanRule[]>("DELETE", `/plan/rules/${id}`, { by }),
  planChangeOf: (id: number) => request<PlanChange>("GET", `/plan/changes/${id}`),
  planConstraint: (id: number, by: string, text: string) =>
    request<PlanChange>("POST", `/plan/changes/${id}/constraints`, undefined, { by, text }),
  planConstraintRemove: (id: number, by: string, n: number) =>
    request<PlanChange>("DELETE", `/plan/changes/${id}/constraints/${n}`, { by }),
  planAutoAssign: (by: string, start: string, end: string) =>
    request<PlanChange>("POST", "/plan/auto-assign", undefined, { by, start, end }),
  planAssignPick: (id: number, by: string, item: number, crew: string | null) =>
    request<PlanChange>("PUT", `/plan/changes/${id}/assign/${item}`, undefined, { by, crew }),
  planOutageWindows: (key: string, by: string) =>
    request<PlanChange>("POST", `/plan/outages/${encodeURIComponent(key)}/windows`, undefined, { by }),
  planWindowPick: (id: number, by: string, n: number, reason?: string | null) =>
    request<PlanChange>("PUT", `/plan/changes/${id}/window`, undefined, { by, n, reason: reason ?? null }),
  planChangeApprove: (id: number, by: string) => request<PlanChange>("POST", `/plan/changes/${id}/approve`, undefined, { by }),
  planChangeRevert: (id: number, by: string) => request<PlanChange>("POST", `/plan/changes/${id}/revert`, undefined, { by }),
  planNoticeSend: (id: number, n: number, by: string, text: string) =>
    request<PlanChange>("POST", `/plan/changes/${id}/notices/${n}/send`, undefined, { by, text }),
  resourceLoad: (f: { resource?: string; start?: string; days?: number } = {}) =>
    request<{ start: string; end: string; days: number; resources: ResourceLoad[] }>("GET", "/plan/resources/load", f),
  planLogistics: (f: { start?: string; days?: number } = {}) =>
    request<{ start: string; end: string; days: number; by_day: LogisticsDay[] }>("GET", "/plan/logistics", f),
  capacityWindow: (code: string, w: { by: string; start: string; end: string; capacity: number; reason?: string }) =>
    request<unknown>("POST", `/plan/resources/${encodeURIComponent(code)}/capacity`, undefined, w),
  capacityWindowRemove: (id: number, by: string) => request<unknown>("DELETE", `/plan/resources/capacity/${id}`, { by }),
  planResourceChange: (code: string, by: string, change: Partial<{ name: string; kind: string; capacity: number; unit: string; work_center: string; counts: string[]; routine: string }>) =>
    request<PlanResource>("PUT", `/plan/resources/${encodeURIComponent(code)}`, undefined, { by, ...change }),
  planResources: () => request<PlanResource[]>("GET", "/plan/resources"),
  turnaround: (revision?: string) => request<TurnaroundScope>("GET", "/turnarounds", { revision }),
  turnaroundBringIn: (itemId: number, by: string, revision: string | null) =>
    request<PlanItem>("POST", "/turnarounds/bring-in", undefined, { by, item_id: itemId, revision }),
  planException: (id: number, by: string, start: string, end: string, crew?: string) =>
    request<PlanItem>("POST", `/exceptions/${id}/plan`, undefined, { by, start, end, crew: crew ?? null }),
  shelveException: (id: number, by: string, reason: string, until: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/shelve`, undefined, { by, reason, until }),
  unshelveException: (id: number, by: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/unshelve`, undefined, { by }),
  exceptionKinds: () => request<ExceptionKind[]>("GET", "/exceptions/types"),
  workList: (f: WorkListFilters) => {
    const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v) as [string, string][]);
    return request<WorkListResult>("GET", `/cases${qs.size ? `?${qs}` : ""}`);
  },
  /** The reference forms another system's refs are matched by, each with an example from this register. */
  exceptionRefs: () => request<{ form: string; example: string; entities: number }[]>("GET", "/exceptions/refs"),
  defineExceptionKind: (name: string, k: { by: string; label: string; role?: string | null; process?: string | null;
    audience: string[]; description?: string | null }) => request<unknown>("PUT", `/exceptions/types/${encodeURIComponent(name)}`, undefined, k),
  removeExceptionKind: (name: string) => request<unknown>("DELETE", `/exceptions/types/${encodeURIComponent(name)}`),
  splitException: (id: number, by: string, reason?: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/split`, undefined, { by, reason: reason ?? null }),
  dispatch: (id: number, by: string, to: string, reason?: string, override?: string, due?: string) =>
    request<ExceptionRow>("POST", `/exceptions/${id}/dispatch`, undefined, { by, to, reason: reason ?? null, override: override ?? null,
      ...(due ? { due } : {}) }),
  exceptionReadiness: (id: number, to: string) => request<Readiness>("GET", `/exceptions/${id}/readiness`, { to }),
  exceptionOptions: (id: number) => request<CaseOptions>("GET", `/exceptions/${id}/options`),
  caseOptions: (itemId: number) => request<CaseOptions>("GET", `/work/items/${itemId}/options`),
  chooseOption: (id: number, by: string, option: CaseOption["key"], c: { reason?: string; to?: string; ask?: string;
    due?: string; priority?: number; override?: string } = {}) =>
    request<{ choice: OptionDecision; event: { id: number; item_id: number | null; stage: string; state: string };
      item: CaseNow | null }>("POST", `/exceptions/${id}/options/choose`, undefined, { by, option, ...c }),
  /** Add an event to another's case: Options' "Add to that job" on a case the field already has (tour F, F6). */
  joinException: (id: number, into: number, by: string) =>
    request<{ event: { id: number; item_id: number; joined_to: number; state: string }; item: CaseNow | null }>(
      "POST", `/exceptions/${id}/join`, undefined, { by, into }),
  optionAcceptance: (days = 90) => request<OptionAcceptance>("GET", "/options/acceptance", { days: String(days) }),
  workTask: (id: number) => request<WorkTaskView>("GET", `/work/tasks/${id}`),
  // A case goes ahead of the reads waiting (P5.29d): after an action every view on the page reads again, the case's
  // own (its parent's effect runs after its children's) asked last, so it waited behind the rest.
  workItem: (id: number) => request<WorkItem>("GET", `/work/items/${id}`, undefined, undefined, undefined, true),
  workItemGates: (id: number) => request<GateDecision[]>("GET", `/work/items/${id}/gates`),
  workItemAround: (id: number) => request<CaseAround>("GET", `/work/items/${id}/around`),
  workItemSafeguards: (id: number) => request<CaseSafeguards>("GET", `/work/items/${id}/safeguards`),
  workDefinitionXml: (id: number) => requestText(`/work/definitions/${id}/bpmn`),
  workDefinitions: (processKey?: string) => request<WorkDefinition[]>("GET", "/work/definitions", { process_key: processKey }),
  generateWorkflow: (id: number, description: string, by: string) =>
    request<{ xml: string; problems: WorkProblem[]; steps: number; connections: number }>(
      "POST", `/work/definitions/${id}/generate`, undefined, { description, by }),
  workRoles: () => request<{ person: string[]; agent: string[] }>("GET", "/work/roles"),
  createWorkItem: (title: string, role: string, by: string, checklist: string[] = []) =>
    request<{ id: number }>("POST", "/work/items", undefined, { type: "task", title, role, by, checklist }),
  addChecklist: (itemId: number, by: string, items: { text: string; subjects?: string[] }[]) =>
    request<CheckItem[]>("POST", `/work/items/${itemId}/checklist`, undefined, { by, items }),
  changeChecklist: (itemId: number, checkId: number, by: string, change: { state?: CheckItem["state"]; note?: string; text?: string }) =>
    request<CheckItem>("POST", `/work/items/${itemId}/checklist/${checkId}`, undefined, { by, ...change }),
  sendChecklist: (itemId: number, checkId: number, by: string, to: string, note?: string) =>
    request<CheckItem>("POST", `/work/items/${itemId}/checklist/${checkId}/send`, undefined, { by, to, note }),
  removeChecklist: (itemId: number, checkId: number, by: string) =>
    request<unknown>("DELETE", `/work/items/${itemId}/checklist/${checkId}`, { by }),
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
  caseCloseout: (id: number) => request<{ item_id: number; record: CaseCloseout | null;
    /** The follow-up case sent when it closed contained or not fixed. */ follow_up?: number | null; suggestion: CloseoutSuggestion;
    /** The case it most likely duplicates (the same equipment, open or just closed), suggested for Duplicate. */
    duplicate_of?: { item_id: number; title: string } | null;
    vocabulary: CloseoutVocabulary; repeats: { again: number; at: string }[]; equipment?: boolean;
    /** Work someone asked for: it closes as done as asked. */ asked?: boolean;
    /** A message's case: one that only said what was done closes as log only. */ message?: boolean }>("GET", `/work/items/${id}/closeout`),
  answerRecheck: (id: number, by: string, held: boolean, note?: string) =>
    request<CaseCloseout>("POST", `/work/items/${id}/recheck`, undefined, { by, held, note }),
  /** `item` is the case as the close-out left it (tour F12). */
  recordCaseCloseout: (id: number, body: CloseoutInput) =>
    request<CaseCloseout & { item?: CaseNow | null }>("POST", `/work/items/${id}/closeout`, undefined, body),
  closeoutMissing: () => request<{ id: number; title: string; type: string; closed_at: string }[]>("GET", "/closeout/missing"),
  workPerformance: () => request<WorkPerformance>("GET", "/work/performance"),
  detectorPerformance: () => request<DetectorPerformance>("GET", "/work/performance/detectors"),
  skillPerformance: () => request<SkillPerformance>("GET", "/work/performance/skills"),
  workUndetermined: () => request<Undetermined[]>("GET", "/work/undetermined"),
  workViews: (person: string) => request<SavedWorkView[]>("GET", "/work/views", { person }),
  saveWorkView: (person: string, role: string, name: string, list: string, filters: Record<string, string>) =>
    request<SavedWorkView>("POST", "/work/views", undefined, { person, role, name, list, filters }),
  removeWorkView: (id: number, person: string) => request("DELETE", `/work/views/${id}`, { person }),
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
    request<{ item: CaseNow }>("POST", `/work/items/${id}/reassign`, undefined, { by, to, reason }),
  transitionCase: (id: number, by: string, state: string, reason?: string) =>
    request<unknown>("POST", `/work/items/${id}/transition`, undefined, { by, state, reason }),
  cancelProcess: (id: number, by: string, reason?: string) =>
    request<unknown>("POST", `/work/processes/${id}/cancel`, undefined, { by, state: "cancelled", reason }),
  claim: (id: number, by: string) => request<unknown>("POST", `/work/tasks/${id}/claim`, undefined, { by }),
  release: (id: number, by: string) => request<unknown>("POST", `/work/tasks/${id}/release`, undefined, { by }),
  complete: (id: number, by: string, data: Record<string, unknown>, kept?: { client_id: string; at: string; project: string }) =>
    request<{ process_state: string }>("POST", `/work/tasks/${id}/complete`, undefined,
      { by, data, ...(kept ? { client_id: kept.client_id, at: kept.at } : {}) }, kept?.project),
  handOff: (id: number, by: string, to: string, reason?: string) =>
    request<unknown>("POST", `/work/tasks/${id}/hand-off`, undefined, { by, to, reason }),
  bulkComplete: (taskIds: number[], by: string, action: string, reason?: string) =>
    request<{ done: number[]; refused: { task_id: number; error: string }[] }>(
      "POST", "/work/tasks/bulk-complete", undefined, { by, task_ids: taskIds, action, reason }),
  briefVerdict: (itemId: number, by: string, followed: "yes" | "partly" | "no", note?: string) =>
    request<unknown>("POST", `/work/items/${itemId}/brief/verdict`, undefined, { by, followed, note }),
  comment: (itemId: number, by: string, text: string, project?: string) =>
    request<unknown>("POST", `/work/items/${itemId}/comment`, undefined, { by, text }, project),
  decideLesson: (id: number, decision: "approve" | "reject" | "edit", by: string, text?: string) =>
    request<{ id: number; status: string; version: number }>("POST", `/lessons/${id}`, undefined, { decision, by, text }),
  reviews: (kind: ReviewKind) => request<ReviewItem[]>("GET", "/review", { kind, limit: 500 }),
  /** One open question, as a list of one (none once it is answered). */
  review: (kind: ReviewKind, item: number) => request<ReviewItem[]>("GET", "/review", { kind, item }),
  /** A message's answer says the case it opened and what that case is about (#1382); `item` is the steward's case the
   * answer closed, as it now stands (tour F7). */
  decide: (id: number, d: Decision) => request<{ decided: number; case?: number | null;
    about?: { fuzer_id: string; label: string }[]; item?: CaseNow | null }>("POST", `/review/${id}`, undefined, d),
  /** Change the entities a message case is about: a wrong match, redone on the case (#1382). */
  changeMessageEntities: (exceptionId: number, by: string, fuzerIds: string[]) =>
    request<{ case: number | null; about: { fuzer_id: string; label: string }[]; item: CaseNow | null }>(
      "POST", `/exceptions/${exceptionId}/message/entities`, undefined, { by, fuzer_ids: fuzerIds }),
  reviewGroups: (kind: ReviewKind) => request<ReviewGroup[]>("GET", "/review/groups", { kind }),
  decideGroup: (body: { key: string; decision: Decision["decision"]; source?: string; value?: unknown; standing: boolean; by: string }) =>
    request<{ decided: number[]; skipped: { id: number; why: string }[] }>("POST", "/review/groups", undefined, body),
  withdrawGroup: (key: string, by: string) => request<{ withdrawn: string }>("DELETE", "/review/groups", { key, by }),
};

export interface BriefSections {
  what_happened?: string | null;
  evidence?: { ref?: string; says?: string }[] | null;
  analysis?: { method?: string; says?: string }[] | null;
  similar?: { case?: string; fixed_by?: string }[] | null;
  proposal?: { disposition?: string; confidence?: number | null; why?: string } | null;
  could_not_check?: string[] | null;
}

export interface CaseBriefView {
  expected: boolean;
  due: string;
  entry: null | {
    missing?: string;
    pack?: string;
    brief?: BriefSections;
    lessons?: { id: number; title: string; failure_mode?: string | null; by?: string | null; review_overdue?: boolean }[];
    ledger?: { cost_usd?: number };
    budget_usd?: number;
    over_budget?: boolean;
    late?: boolean;
    at?: string;
  };
  verdict: null | { by: string; at: string; followed: "yes" | "partly" | "no"; note?: string | null };
}

export type FindingOrigin = "deterministic" | "agentic" | "external" | "unknown";

/** How one detector is performing (P7.4a): its cases, how they closed out, and whether it earns its keep. */
export interface DetectorEarning {
  detector: string; title: string; kind: string | null; kind_label: string | null;
  cases: number; closed: number; real: number; false_alarm: number; duplicate: number; data_problem: number;
  noise: number; confirmed_rate: number | null; false_alarm_rate: number | null; noise_rate: number | null;
  hours_to_case: number | null; value_confirmed_usd: number | null; valued: number;
  by_origin: Record<FindingOrigin, number>; verdict: "earning" | "needs_tuning" | "mixed" | "too_few"; item_ids: number[];
  tuning_case: number | null;
}

export interface DetectorPerformance {
  detectors: DetectorEarning[]; confirmed: number;
  by_origin: Record<FindingOrigin, { cases: number; share: number | null }>; min_closed: number; noisy: number;
}

/** How one specialist skill is performing (P7.4b): briefs, how far they were followed, ratings and edits. */
export interface SkillEarning {
  skill: string; name: string; owner: string; briefs: number; brief_cases: number; brief_usd: number;
  brief_cost_per_case: number | null; followed: { yes: number; partly: number; no: number };
  rated: { useful: number; not_useful: number }; followed_rate: number | null; useful_rate: number | null;
  verdicts: number; short: number; verdict: "earning" | "needs_change" | "mixed" | "too_few";
  edits: { published: number; live_version: number | null; live_since: string | null; waiting: number | null;
    review_case: number | null };
}

/** What Fuzer couldn't determine (P6.6), grouped by the source, tool or skill that would have answered it: the most
 * cases and answers first (`count`), then the value at stake on those cases; `why` is the reasons in words and
 * `draft` the issue for a person to file. */
export interface Undetermined {
  needs: { kind: "source" | "tool" | "skill"; name: string }; count: number; value_usd: number | null;
  reasons: Record<string, number>; why: string; asked: string[]; sources: string[]; answers: number; last_at: string;
  cases: { id: number; title: string | null; type: string | null; value_usd: number | null; detail: string | null }[];
  draft: { title: string; body: string };
}

export interface SkillPerformance {
  skills: SkillEarning[]; min_rated: number;
  specialists: { specialist: string; steps: number; cases: number; cost_usd: number; cost_per_case: number | null }[];
}

/** A container (step 6 of the work model): one level a site groups work at, with a parent and a governing case. */
export interface WorkContainer {
  id: number; kind: string; code: string; title: string; parent_id: number | null; item_id: number | null;
  owner: string | null; status: string; start_at: string | null; finish_by: string | null; source: string | null;
  source_key: string | null; jobs: Record<string, number>; cases: number; children: number[];
}

export interface WorkContainerDetail extends Omit<WorkContainer, "jobs" | "cases" | "children"> {
  parents: { id: number; kind: string; title: string }[];
  children: { id: number; kind: string; title: string; status: string }[];
  jobs: { id: number; title: string; start_at: string; end_at: string; crew: string | null; item_id: number | null;
    stage: string }[];
  cases: { id: number; title: string; state: string; assignee: string | null; role: string | null }[];
}

export interface PlanLineNew {
  by: string; title: string; kind?: string | null; subjects?: string[]; period_start?: string | null;
  period_end?: string | null; estimate_hours?: number | null; estimate_cost?: number | null;
  estimate_value_usd?: number | null; owner?: string | null; note?: string | null;
}

export interface PlanActual {
  cases: number; cases_closed: number; jobs: number; jobs_done: number; hours_done: number; hours_booked: number;
  cost: number | null;
}

/** An intent line of a business plan (step 9), with its cases' actuals. */
export interface PlanLine {
  id: number; container_id: number; title: string; kind: string | null; subjects: string[];
  period_start: string | null; period_end: string | null; estimate_hours: number | null; estimate_cost: number | null;
  estimate_value_usd: number | null; owner: string | null; note: string | null; status: "open" | "dropped";
  actual?: PlanActual; state?: "not started" | "in progress" | "done" | "dropped"; case_ids?: number[];
  hours_variance?: number | null; cost_variance?: number | null;
}

export interface PlanAgainstActual {
  container: { id: number; kind: string; title: string };
  lines: PlanLine[];
  totals: { lines: number; estimate_hours: number; estimate_cost: number; hours_done: number; hours_booked: number;
    not_started: number; done: number; unplanned_cases: number; unplanned: PlanActual };
  unplanned: { id: number; title: string; state: string; type: string }[];
}

/** One row of a system's status mapping (step 8): its status, the Fuzer state it means, what a move sends back. */
export interface StatusMapRow {
  id: number; system: string; object_type: string; status: string; user_status: string; state: string;
  move: string | null; note: string | null; updated_by: string; updated_at: string;
}
