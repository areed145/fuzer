import type { EntityNames } from "../entityRefs";
import { request } from "./transport";
import type { DocRef } from "./investigate";

export interface Stats {
  pack: string;
  facility: string;
  entities: Record<string, number>;
  open_reviews: Record<string, number>;
  records: number;
  edges: number;
  /** The latest day the loaded production reports cover; null when none are loaded (#389). */
  data_through?: string | null;
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
  /** The step before it on the path from the asset the trace started at. */
  parent: string | null;
}

export interface GeoFeature {
  type: "Feature";
  id: string;
  geometry: { type: string; coordinates: unknown };
  properties: { id: string; kind: string; label: string | null; [field: string]: unknown };
}

/** What is along a pipeline or segment, by station (#61). Golden values come as stored. */
export interface Along {
  line: string | null;
  segments: { id: string; label: string; description: unknown; station_from_m: unknown; station_to_m: unknown;
              wall_thickness_mm: unknown; grade: unknown; class_location: unknown }[];
  features: { id: string; label: string; feature_type: unknown; station_m: unknown; depth_pct: unknown;
              length_mm: unknown; clock: unknown; run_id: unknown; run_date: unknown; segment: string | null }[];
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
  connections: { from: string; to: string; line: string | null; from_id: string | null; to_id: string | null;
    via?: string[]; stream?: string }[];
  findings: { text: string; kind: string; issue: string }[];
  /** Lines leaving or entering the sheet (#475): confirmed when the other sheet shows them too. */
  offpage?: { direction: "to" | "from"; sheet: string; tag: string | null; other: string | null; line: string;
    status: "confirmed" | "one_sided" | "unread"; sheet_id: string | null }[];
  /** Instrument loops read from bubbles and signal lines: transmitter, controller, final element. */
  loops?: { members: string[] }[];
  /** Data tables on the sheet (equipment data, alarm and trip settings), as read (#475). */
  tables?: { title: string; columns: string[]; rows: string[][] }[];
  /** Numbered notes, with the registered tags each names. */
  notes?: { n: number; text: string; tags: string[] }[];
  /** Where other systems disagree with the sheet: alarms configured in the DCS and AF that differ from its alarm
   * and trip table, and instruments the register puts under another item (type "placement"). */
  checks?: { tag: string; point: string | null; point_id: string; type: string; detail: string }[];
}

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

export const exploreApi = {
  stats: () => request<Stats>("GET", "/stats"),
  entityNames: (ids: string[]) => request<EntityNames>("GET", `/entities/names?${new URLSearchParams(ids.map((id) => ["id", id]))}`),
  search: (q: string, kind?: string) => request<SearchHit[]>("GET", "/entities", { q, kind, limit: 200 }),
  entity: (id: string, asOf?: string) =>
    request<EntityDetail>("GET", `/entities/${encodeURIComponent(id)}`, { as_of: asOf }),
  trace: (id: string, direction: "up" | "down", asOf?: string, depth?: number, rel?: string) =>
    request<TraceRow[]>("GET", `/entities/${encodeURIComponent(id)}/trace`, { direction, as_of: asOf, depth, rel }),
  history: (id: string) =>
    request<{ record: string; at: string; change: string; fields: Record<string, { from: unknown; to: unknown }> }[]>(
      "GET", `/entities/${encodeURIComponent(id)}/history`, { limit: 20 }),
  spares: (id: string) =>
    request<{ fuzer_id: string; label: string; description: string; mpn: string; stock: number; bin: string;
              used_by: number; documents: { id: string; label: string }[] }[]>(
      "GET", `/entities/${encodeURIComponent(id)}/spares`),
  map: () => request<GeoCollection>("GET", "/map"),
  entityMap: (id: string) => request<GeoCollection>("GET", `/entities/${encodeURIComponent(id)}/map`),
  depths: (id: string) => request<{ trajectory: unknown[]; components: unknown[] }>(
    "GET", `/entities/${encodeURIComponent(id)}/depths`),
  along: (id: string) => request<Along>("GET", `/entities/${encodeURIComponent(id)}/along`),
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
  /** Every link of the given types (default: the ontology's flow view); the answer says which types it drew. */
  network: (asOf?: string, rels?: string[]) =>
    request<Subgraph & { rels: string[] }>("GET", "/network", { as_of: asOf, rels: rels?.join(",") }),
};
