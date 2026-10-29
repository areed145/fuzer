// Shared graph drawing (Cytoscape.js) used by the ontology, network and entity graph views.

import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from "cytoscape";
import dagre from "cytoscape-dagre";
import fcose from "cytoscape-fcose";
import { useEffect, useRef } from "react";
import type { Subgraph } from "./api";

cytoscape.use(dagre);
cytoscape.use(fcose);

// Mid-tone colours that read on both the light and dark backgrounds.
export const KIND_COLORS: Record<string, string> = {
  facility: "#5f6b7a",
  area: "#1f9e9e",
  tag: "#2f7bd8",
  equipment: "#d9822b",
  timeseries: "#2a9d5c",
  well: "#8a5cd6",
  allocation_node: "#c4a000",
  document: "#7b8794",
  log_entry: "#a0679b",
  event: "#d6455d",
};
export const kindColor = (kind: string) => KIND_COLORS[kind] ?? "#8d98a7";

/** Link types grouped by the ontology "view" they belong to. */
export const REL_VIEWS: { id: string; label: string; rels: string[] }[] = [
  { id: "physical", label: "Physical", rels: ["part_of", "installed_at", "sidetrack_of"] },
  { id: "flow", label: "Flow / allocation", rels: ["flows_to"] },
  { id: "data", label: "Data", rels: ["has_timeseries"] },
  { id: "documents", label: "Documents & logs", rels: ["mentions"] },
  { id: "events", label: "Events", rels: ["reports", "involves"] },
];
export const ALL_RELS = REL_VIEWS.flatMap((v) => v.rels);

export type LayoutName = "force" | "flow" | "tree";

const LAYOUTS: Record<LayoutName, LayoutOptions> = {
  force: {
    name: "fcose",
    animate: false,
    padding: 24,
    nodeRepulsion: () => 9000,
    idealEdgeLength: () => 90,
    nodeSeparation: 80,
  } as LayoutOptions,
  flow: { name: "dagre", rankDir: "LR", nodeSep: 16, rankSep: 110, padding: 24, animate: false } as LayoutOptions,
  tree: { name: "dagre", rankDir: "TB", nodeSep: 24, rankSep: 70, padding: 24, animate: false } as LayoutOptions,
};

function themeColors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return { text: v("--text"), muted: v("--muted"), line: v("--line"), panel: v("--panel"), accent: v("--accent") };
}

function stylesheet() {
  const t = themeColors();
  return [
    {
      selector: "node",
      style: {
        "background-color": "data(color)",
        width: "data(size)",
        height: "data(size)",
        label: "data(label)",
        color: t.text,
        "font-size": 10,
        "font-family": "ui-monospace, Menlo, Consolas, monospace",
        "text-valign": "bottom",
        "text-margin-y": 4,
        "text-wrap": "wrap",
        "text-max-width": "140px",
        "text-background-color": t.panel,
        "text-background-opacity": 0.85,
        "text-background-padding": "1px",
      },
    },
    { selector: "node[?root]", style: { "border-width": 3, "border-color": t.text } },
    { selector: "node:selected", style: { "border-width": 4, "border-color": t.accent } },
    {
      selector: "edge",
      style: {
        width: 1.5,
        "line-color": t.muted,
        "target-arrow-color": t.muted,
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.8,
        "curve-style": "bezier",
        opacity: 0.7,
        "font-size": 9,
        color: t.muted,
        "text-rotation": "autorotate",
        "text-background-color": t.panel,
        "text-background-opacity": 1,
        "text-background-padding": "2px",
      },
    },
    { selector: "edge.labeled, edge:selected, edge.hover", style: { label: "data(label)" } },
    { selector: "edge[?ended]", style: { "line-style": "dashed" } },
    {
      selector: "edge:selected, edge.hover",
      style: { "line-color": t.accent, "target-arrow-color": t.accent, opacity: 1, color: t.text },
    },
  ];
}

/** Turn an API subgraph into Cytoscape elements. */
export function subgraphElements(
  g: Subgraph,
  rootId?: string,
  labelEdges = g.edges.length <= 80, // otherwise labels show on hover / select
  markEnded = true, // dash links that have ended; off when viewing "as of" a date
): ElementDefinition[] {
  return [
    ...g.nodes.map((n) => ({
      data: {
        id: n.id,
        label: n.label ?? n.id.replace(/^fz-/, ""),
        kind: n.kind,
        color: kindColor(n.kind),
        size: n.id === rootId ? 34 : 22,
        root: n.id === rootId,
      },
    })),
    ...g.edges.map((e) => ({
      classes: labelEdges ? "labeled" : "",
      data: {
        id: `e${e.id}`,
        source: e.src,
        target: e.dst,
        rel: e.rel,
        ended: markEnded && !!e.valid_to,
        label: e.valid_from || e.valid_to ? `${e.rel} ${e.valid_from ?? "…"} → ${e.valid_to ?? "now"}` : e.rel,
      },
    })),
  ];
}

export function GraphCanvas({ elements, layout, selectedId, onSelect, onExpand, height = 600 }: {
  elements: ElementDefinition[];
  layout: LayoutName;
  selectedId?: string | null;
  onSelect?: (id: string | null) => void;
  onExpand?: (id: string) => void;
  height?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const handlers = useRef({ onSelect, onExpand });
  handlers.current = { onSelect, onExpand };

  useEffect(() => {
    const cy = cytoscape({
      container: container.current,
      style: stylesheet() as cytoscape.StylesheetJson,
      minZoom: 0.15,
      maxZoom: 3,
    });
    cy.on("tap", "node, edge", (e) => handlers.current.onSelect?.(e.target.id()));
    cy.on("tap", (e) => e.target === cy && handlers.current.onSelect?.(null));
    cy.on("dbltap", "node", (e) => handlers.current.onExpand?.(e.target.id()));
    cy.on("mouseover", "edge", (e) => e.target.addClass("hover"));
    cy.on("mouseout", "edge", (e) => e.target.removeClass("hover"));
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    const restyle = () => cy.style(stylesheet() as cytoscape.StylesheetJson);
    scheme.addEventListener("change", restyle);
    cyRef.current = cy;
    return () => {
      scheme.removeEventListener("change", restyle);
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().remove();
      cy.add(elements);
    });
    const run = cy.layout(LAYOUTS[layout]);
    // Small graphs would otherwise be fitted at a huge zoom.
    run.one("layoutstop", () => {
      if (cy.zoom() > 1.2) {
        cy.zoom(1.2);
        cy.center();
      }
    });
    run.run();
  }, [elements, layout]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$(":selected").unselect();
    if (selectedId) cy.getElementById(selectedId).select();
  }, [selectedId, elements]);

  const fit = () => cyRef.current?.fit(undefined, 24);

  return (
    // Cytoscape caches where its container sits on the page; refresh that before each press so
    // content changing above the graph (text, banners) can't make clicks land in the wrong place.
    <div className="graph-canvas" style={{ height }} onPointerDownCapture={() => cyRef.current?.resize()}>
      <div ref={container} className="graph-surface" />
      <button className="graph-fit" onClick={fit} title="Fit to view">
        Fit
      </button>
    </div>
  );
}

export function Legend({ kinds }: { kinds: string[] }) {
  return (
    <div className="legend">
      {kinds.map((k) => (
        <span key={k}>
          <i style={{ background: kindColor(k) }} /> {k.replace("_", " ")}
        </span>
      ))}
    </div>
  );
}
