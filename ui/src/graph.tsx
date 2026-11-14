import "./styles/graphs.css";
import { THEME_CHANGED } from "./theme";
// Shared graph drawing (Cytoscape.js) used by the ontology, network and entity graph views.

import cytoscape, { type Core, type ElementDefinition, type LayoutOptions } from "cytoscape";
import dagre from "cytoscape-dagre";
import fcose from "cytoscape-fcose";
import { useEffect, useRef, type CSSProperties } from "react";
import type { Subgraph } from "./api";
import { nodeLabel } from "./entityRefs";
import { Button, FullscreenButton, useFullscreen } from "./ui";

cytoscape.use(dagre);
cytoscape.use(fcose);

export { ALL_RELS, KIND_COLORS, REL_VIEWS, applyOntology, kindColor, linkColor, relColor } from "./kinds";
import { kindColor } from "./kinds";

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

/** Labels are drawn in graph units, so a big graph fitted on a phone shrinks them past reading. When zoomed out,
 * labels grow (up to a limit) to keep roughly this size on screen; in steps, so zooming restyles rarely. */
const MIN_LABEL_PX = 8;
const labelScale = (zoom: number) => Math.min(2.5, Math.max(1, Math.round((MIN_LABEL_PX / (10 * zoom)) * 4) / 4));

function stylesheet(scale = 1) {
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
        "font-size": 10 * scale,
        "font-family": "ui-monospace, Menlo, Consolas, monospace",
        "text-valign": "bottom",
        "text-margin-y": 4,
        "text-wrap": "wrap",
        "text-max-width": `${140 * scale}px`,
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
        "font-size": 9 * scale,
        color: t.muted,
        "text-rotation": "autorotate",
        "text-background-color": t.panel,
        "text-background-opacity": 1,
        "text-background-padding": "2px",
      },
    },
    { selector: "edge[color]", style: { "line-color": "data(color)", "target-arrow-color": "data(color)", opacity: 0.85 } },
    { selector: "edge.labeled, edge:selected, edge.hover, edge.near", style: { label: "data(label)" } },
    { selector: "edge[?ended]", style: { "line-style": "dashed" } },
    // The flow diagram draws two networks together: piping read from drawings, and allocation flow (dashed).
    { selector: "edge.piping", style: { width: 2 } },
    { selector: "edge.allocation", style: { "line-style": "dashed" } },
    {
      selector: "edge:selected, edge.hover, edge.near",
      style: { "line-color": t.accent, "target-arrow-color": t.accent, opacity: 1, color: t.text },
    },
  ];
}

/** Fit the whole graph in view. Small graphs would otherwise be fitted at a huge zoom. */
function fitGraph(cy: Core) {
  cy.fit(undefined, 24);
  if (cy.zoom() > 1.2) {
    cy.zoom(1.2);
    cy.center();
  }
}

/** Turn an API subgraph into Cytoscape elements. */
export function subgraphElements(
  g: Subgraph,
  rootId?: string,
  labelEdges = g.edges.length <= 80, // otherwise labels show on hover / select
  markEnded = true, // dash links that have ended; off when viewing "as of" a date
  relColor?: (rel: string) => string | undefined, // colour links by type when several are drawn together
): ElementDefinition[] {
  return [
    ...g.nodes.map((n) => ({
      data: {
        id: n.id,
        label: nodeLabel(n.label ?? n.id.replace(/^fz-/, ""), n.description),
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
        ...(relColor?.(e.rel) ? { color: relColor(e.rel) } : {}),
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
  const full = useFullscreen();
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
    // A hovered or selected node names its own links, so unlabelled graphs stay readable (#399).
    cy.on("mouseover", "node", (e) => e.target.connectedEdges().addClass("near"));
    cy.on("mouseout", "node", (e) => { if (!e.target.selected()) e.target.connectedEdges().removeClass("near"); });
    let scale = 1;
    const restyle = () => cy.style(stylesheet(scale) as cytoscape.StylesheetJson);
    cy.on("zoom", () => {
      const next = labelScale(cy.zoom());
      if (next !== scale) { scale = next; restyle(); }
    });
    window.addEventListener(THEME_CHANGED, restyle);
    // The canvas changes size when a phone turns or the side panel moves below the graph: redraw at the new
    // size, and refit when it changed much (a turn, the full screen) so the graph isn't left cut off or in a corner.
    let width = container.current?.clientWidth ?? 0, high = container.current?.clientHeight ?? 0;
    const resized = new ResizeObserver(() => {
      cy.resize();
      const now = container.current?.clientWidth ?? 0, tall = container.current?.clientHeight ?? 0;
      if (now && (Math.abs(now - width) > 40 || Math.abs(tall - high) > 120) && cy.elements().length) fitGraph(cy);
      width = now;
      high = tall;
    });
    if (container.current) resized.observe(container.current);
    cyRef.current = cy;
    return () => {
      resized.disconnect();
      window.removeEventListener(THEME_CHANGED, restyle);
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
    run.one("layoutstop", () => fitGraph(cy));
    run.run();
  }, [elements, layout]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.$(":selected").unselect();
    cy.edges(".near").removeClass("near");
    if (selectedId) {
      const picked = cy.getElementById(selectedId).select();
      if (picked.isNode()) picked.connectedEdges().addClass("near");
    }
  }, [selectedId, elements]);

  const fit = () => cyRef.current && fitGraph(cyRef.current);

  return (
    // Cytoscape caches where its container sits on the page; refresh that before each press so
    // content changing above the graph (text, banners) can't make clicks land in the wrong place.
    <div ref={full.ref} className={`graph-canvas${full.on ? " ui-fullscreen" : ""}`} style={{ "--graph-height": `${height}px` } as CSSProperties}
      onPointerDownCapture={() => cyRef.current?.resize()}>
      <div ref={container} className="graph-surface" />
      <div className="graph-tools" role="toolbar" aria-label="View">
        <FullscreenButton on={full.on} onClick={() => void full.toggle()} />
        <Button variant="secondary" size="sm" className="graph-fit" onClick={fit} title="Fit to view">
          Fit
        </Button>
      </div>
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
