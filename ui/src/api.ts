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

export type ReviewKind = "match" | "conflict" | "schema";

export type ReviewItem =
  | { id: number; kind: "match"; subject: string; status: string; label: null; payload: MatchPayload }
  | { id: number; kind: "schema"; subject: string; status: string; label: null; payload: SchemaPayload }
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

export interface Decision {
  decision: "accept" | "reject" | "choose";
  value?: unknown;
  by: string;
}

type Params = Record<string, string | number | undefined | null>;

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
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
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
  lessons: (status: string) => request<Lesson[]>("GET", "/lessons", { status }),
  decideLesson: (id: number, decision: "approve" | "reject" | "edit", by: string, text?: string) =>
    request<{ id: number; status: string }>("POST", `/lessons/${id}`, undefined, { decision, by, text }),
  spares: (id: string) =>
    request<{ fuzer_id: string; label: string; description: string; mpn: string; stock: number; bin: string;
              used_by: number; documents: { id: string; label: string }[] }[]>(
      "GET", `/entities/${encodeURIComponent(id)}/spares`),
  values: (id: string) =>
    request<{ provider: string; upsets: string[]; series: import("./Trend").Series[] }>(
      "GET", `/entities/${encodeURIComponent(id)}/values`),
  documentsFor: (id: string) => request<DocRef[]>("GET", `/entities/${encodeURIComponent(id)}/documents`),
  ontology: () => request<Ontology>("GET", "/ontology"),
  neighborhood: (id: string, depth: number, rels?: string[], asOf?: string) =>
    request<Subgraph>("GET", `/graph/${encodeURIComponent(id)}`, { depth, rels: rels?.join(","), as_of: asOf }),
  network: (asOf?: string, rel: "flows_to" | "pipes_to" = "flows_to") =>
    request<Subgraph>("GET", "/network", { as_of: asOf, rel }),
  reviews: (kind: ReviewKind) => request<ReviewItem[]>("GET", "/review", { kind, limit: 500 }),
  decide: (id: number, d: Decision) => request<{ decided: number }>("POST", `/review/${id}`, undefined, d),
};
