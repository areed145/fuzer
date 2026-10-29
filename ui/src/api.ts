// Typed client for the Fuzer API.

export interface Stats {
  pack: string;
  facility: string;
  entities: Record<string, number>;
  open_reviews: Record<string, number>;
  records: number;
  edges: number;
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

export type ReviewItem =
  | { id: number; kind: "match"; subject: string; status: string; label: null; payload: MatchPayload }
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
  ontology: () => request<Ontology>("GET", "/ontology"),
  neighborhood: (id: string, depth: number, rels?: string[], asOf?: string) =>
    request<Subgraph>("GET", `/graph/${encodeURIComponent(id)}`, { depth, rels: rels?.join(","), as_of: asOf }),
  network: (asOf?: string) => request<Subgraph>("GET", "/network", { as_of: asOf }),
  reviews: (kind: "match" | "conflict") => request<ReviewItem[]>("GET", "/review", { kind, limit: 500 }),
  decide: (id: number, d: Decision) => request<{ decided: number }>("POST", `/review/${id}`, undefined, d),
};
