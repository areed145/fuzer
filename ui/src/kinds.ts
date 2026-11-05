// Entity kind colours and link-type views, shared by every graph and legend. Kept apart from graph.tsx (which
// pulls in cytoscape) so the app shell can load the ontology without the graph library.
import type { OntologyDeclared } from "./api";

/** Colours a person set on a kind in the Ontology (#241): a custom kind, or a shipped one recoloured. Every other
 * kind takes its colour from the theme (--kind-<name> in styles.css), so graphs follow light and dark mode. */
export const KIND_COLORS: Record<string, string> = {};

export const kindColor = (kind: string) => {
  if (KIND_COLORS[kind]) return KIND_COLORS[kind];
  const css = getComputedStyle(document.documentElement);
  return css.getPropertyValue(`--kind-${kind}`).trim() || css.getPropertyValue("--kind-other").trim();
};

/** Link types grouped by the ontology "view" they belong to. */
export const REL_VIEWS: { id: string; label: string; rels: string[] }[] = [
  { id: "physical", label: "Physical", rels: ["part_of", "installed_at", "sidetrack_of"] },
  { id: "classes", label: "Classes & models", rels: ["classified_as", "instance_of", "is_a"] },
  { id: "flow", label: "Flow / allocation", rels: ["flows_to"] },
  { id: "data", label: "Data", rels: ["has_timeseries"] },
  { id: "documents", label: "Documents & logs", rels: ["mentions"] },
  { id: "events", label: "Events", rels: ["reports", "involves", "related_to"] },
  { id: "maintenance", label: "Maintenance", rels: ["concerns", "for_notification"] },
  { id: "production", label: "Downtime & reports", rels: ["affects", "caused_by", "reports_on"] },
  { id: "incidents", label: "Incidents", rels: ["about", "includes"] },
  { id: "knowledge", label: "Documents", rels: ["applies_to", "supersedes"] },
];
export const ALL_RELS = REL_VIEWS.flatMap((v) => v.rels);

/** Take kind colours and link views from the declared ontology (#241). The arrays are updated in place, so
 * every page importing them sees the change on its next render. */
export function applyOntology(o: OntologyDeclared): void {
  for (const k of o.kinds) if (k.colour && (k.status !== "core" || k.edited)) KIND_COLORS[k.name] = k.colour;
  const views = o.views.map((v) => ({ ...v, rels: o.relations.filter((r) => r.view === v.id).map((r) => r.name) }))
    .filter((v) => v.rels.length > 0);
  const unviewed = o.relations.filter((r) => !r.view || !o.views.some((v) => v.id === r.view)).map((r) => r.name);
  if (unviewed.length) views.push({ id: "other", label: "Other", rels: unviewed });
  REL_VIEWS.splice(0, REL_VIEWS.length, ...views);
  ALL_RELS.splice(0, ALL_RELS.length, ...views.flatMap((v) => v.rels));
}

