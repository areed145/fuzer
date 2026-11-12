import { request } from "./transport";

export interface InvestigationRecord {
  source: string;
  record_type: string;
  key: string;
  observed_at: string;
}

export interface Investigation {
  title: string;
  since: string;
  until: string;
  asset_id: string;
  asset_label: string;
  asset_fields: { name: string; value: string; source: string }[];
  asset_records: InvestigationRecord[];
  evidence: {
    id: string;
    kind: string;
    label: string;
    at: string | null;
    text: string | null;
    lost_oil_sm3: number | null;
    records: InvestigationRecord[];
  }[];
  findings: { text: string; evidence_ids: string[] }[];
  limitations: string[];
}

export interface LiveFigureTable { title: string; note?: string; columns: string[]; rows: string[][] }

export interface LiveFigureData {
  kind: string;
  title: string;
  spec: { data: any[]; layout: Record<string, any> };
  summary: {
    context: string;
    headline: string | null;
    stats: { label: string; value: string; unit?: string }[];
    notes: string[];
    warnings: string[];
    tables: LiveFigureTable[];
    source: string;
  };
  sources: string[];
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

/** A chat thread about an asset (P5.8b): its summary and a link back to where it was said, never the chat. */
export interface ChatThread {
  id: string; subject: string | null; channel: string | null; state: string | null; started_at: string | null;
  last_post_at: string | null; problem: string | null; tried: string[]; fix: string | null; participants: string[];
  web_url: string | null;
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
  /** The loss whose records name no (classified) equipment, so the ranking adds up to the total (#403). */
  unattributed?: boolean;
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

export interface RelatedUpset {
  id: number; signature: string; start: string; end: string | null; lag_minutes: number; hops: number | null;
  seen_together: number; role: "likely_cause" | "likely_knock_on" | "same_time" | "seen_together" | "coincident";
  why: string; score: number; status: string; detections: number; units: number; work_item_id: number | null;
  case_title: string | null; case_state: string | null; merged_into: number | null; same_case: boolean;
}

export interface UpsetView {
  upset: Upset & { first_unit: string | null };
  signature: string;
  timeline: { id: number; start: string; end: string | null; unit: string | null; point: string | null;
    detector: string; key: string; magnitude: number | null; finding: string }[];
  detections_shown: number;
  by_unit: Record<string, number>;
  spread: { spec: FigureData["spec"] | null; title: string | null; note?: string;
    summary: { first_cause: string; first_detection: string; units: number;
      order: { unit: string; minutes_after_first: number }[] } | null };
  related: { related: RelatedUpset[]; considered: number; window_hours: number; basis: string };
  merged: { id: number; signature: string; start: string }[];
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
  series: import("../Trend").Series[];
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

export interface Attachment {
  name: string;
  media_type: "image/png" | "image/jpeg" | "image/gif" | "image/webp";
  data: string;
  url: string;
}

export interface AssistantCatalog {
  agents: { id: string; label: string; description: string; personas: string[]; skills: string[];
            limits: string[]; human_decisions: string[] }[];
  unavailable: { agent: string; capability: string; needed: string }[];
  /** The agents provider and whether its key reference is configured (#404); nothing is tested. */
  provider?: string | null;
  provider_configured?: boolean;
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
  item_id?: number | null; // the case it was opened from (P4.5a)
  subject?: string | null; // or the asset
}

/** What a conversation opened from a case or an asset is about (P4.5a). */
export interface AskContext {
  item_id: number | null;
  subject: string | null;
  label: string;
  text: string;
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
    due_at: string | null; error: string | null; created_at?: string | null; unclaimed?: boolean } | null;
}

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

export const investigateApi = {
  guidedInvestigation: () => request<Investigation>("GET", "/demo/investigation"),
  liveFigure: (kind: string, params?: Record<string, string>) =>
    request<LiveFigureData>("GET", `/analytics/figures/${encodeURIComponent(kind)}`, params),
  events: (id: string) => request<FuzerEvent[]>("GET", `/entities/${encodeURIComponent(id)}/events`, { limit: 200 }),
  maintenance: (id: string) =>
    request<Maintenance>("GET", `/entities/${encodeURIComponent(id)}/maintenance`, { limit: 25 }),
  downtime: (id?: string, since?: string) =>
    request<Downtime>("GET", id ? `/entities/${encodeURIComponent(id)}/downtime` : "/downtime", { since, limit: 25 }),
  downtimeRanking: (by: "tag" | "class" | "reason", since?: string) =>
    request<RankRow[]>("GET", "/downtime/ranking", { by, since, limit: 15 }),
  reasonChecks: () =>
    request<{ fuzer_id: string; label: string; issue: string; start: string }[]>("GET", "/downtime/reason-checks"),
  assetThreads: (id: string) => request<ChatThread[]>("GET", `/entities/${encodeURIComponent(id)}/conversations`),
  incidents: (id?: string) =>
    request<{ total: number; items: Incident[] }>(
      "GET", id ? `/entities/${encodeURIComponent(id)}/incidents` : "/incidents", { limit: 100 }),
  upsets: (status?: string, kind?: string, limit = 50) =>
    request<Upset[]>("GET", "/upsets", { status, kind, limit }),
  upsetView: (id: number) => request<UpsetView>("GET", `/upsets/${id}/view`),
  mergeUpset: (id: number, into: number, by: string, reason?: string) =>
    request<{ upset: number; into: number; item_id: number; cancelled_case: number | null; reason: string }>(
      "POST", `/upsets/${id}/merge`, undefined, { into, by, reason: reason ?? null }),
  incidentTimeline: (id: string) => request<TimelineItem[]>("GET", `/incidents/${encodeURIComponent(id)}/timeline`),
  similarIncidents: (id: string) =>
    request<(Incident & { score: number; same_unit: boolean })[]>("GET", `/incidents/${encodeURIComponent(id)}/similar`),
  knowledgeSearch: (q: string, ref?: string) => request<Passage[]>("GET", "/knowledge/search", { q, ref, limit: 8 }),
  incidentReading: (id: string) =>
    request<{ id: string; document_label: string | null; heading: string | null; page: number | null;
              failure_modes: string[] }[]>("GET", `/incidents/${encodeURIComponent(id)}/reading`),
  dailyReports: () => request<{ day: string; version: number; drafted_by: string; ok: boolean; item_id: number | null;
    status: DailyStatus }[]>("GET", "/reports/daily"),
  dailyReport: (day: string, version?: number) => request<DailyReport>("GET", `/reports/daily/${day}`, { version }),
  studies: () => request<Study[]>("GET", "/studies"),
  study: (id: number) => request<Study>("GET", `/studies/${id}`),
  startStudy: (kind: string, params: Record<string, unknown>, by: string) =>
    request<Study>("POST", "/studies", undefined, { kind, params, by }),
  rerunStudy: (id: number, by: string) => request<Study>("POST", `/studies/${id}/rerun`, { by }),
  lessons: (status: string) => request<Lesson[]>("GET", "/lessons", { status }),
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
  ask: (question: string, conversationId: string | undefined, by: string, images: Attachment[] = [],
        about?: { case_id?: number; subject?: string }) =>
    request<{ job_id: string; conversation_id: string }>("POST", "/assistant/ask", undefined, {
      question, conversation_id: conversationId, by,
      images: images.map(({ media_type, data }) => ({ media_type, data })), ...about,
    }),
  askContext: (about: { case?: number; subject?: string }) => request<AskContext>("GET", "/assistant/context", about),
  figure: (id: string) => request<FigureData>("GET", `/figures/${encodeURIComponent(id)}`),
  assistantCatalog: () => request<AssistantCatalog>("GET", "/assistant/agents"),
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
};
