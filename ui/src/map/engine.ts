/* What MapView asks of a map library. MapView decides what to draw (colours, widths, dashes, popups, hotspots) and
 * an engine draws it: Leaflet on free tiles, or Apple MapKit JS when the deployment has a MapKit key. */

export type BasemapName = "standard" | "satellite" | "terrain";
export type LatLon = [number, number];

export interface LineSpec {
  paths: LatLon[][];
  closed: boolean;  // a polygon's rings: filled faintly
  color: string;
  weight: number;
  dash?: string;
  opacity: number;
  halo?: { color: string; weight: number };  // drawn under the line so it reads on any base map
  popup: () => HTMLElement;
  tip?: string;  // plain text shown on hover
}

export interface PointSpec {
  at: LatLon;
  /** A platform, pad or plant is a square, so it reads apart from a well's dot at field zoom (#1075). */
  shape?: "circle" | "square";
  radius: number;
  stroke: string;
  strokeWidth: number;
  fill: string;
  fillOpacity: number;
  popup: () => HTMLElement;
  tip?: string;
}

export interface HotspotSpec {
  at: LatLon;
  className: string;
  html: string;
  size: number;
  title: string;
  name: string;
  selected: boolean;
  onSelect: () => void;
  crew?: { html: string; title: string };
}

export interface MapEngine {
  readonly kind: "leaflet" | "mapkit";
  setBasemap(name: BasemapName | "none", dark: boolean): void;
  drawNetwork(lines: LineSpec[], points: PointSpec[]): void;
  drawHotspots(spots: HotspotSpec[]): void;
  fit(coords: LatLon[], padding: number, maxZoom: number): void;
  /** Pan so the place is in view, unless it already is. */
  reveal(at: LatLon): void;
  /** Mark the person's location and centre on it, at a street-level zoom or closer. */
  showLocation(at: LatLon, accuracyMetres: number, colors: { accent: string; panel: string }): void;
  /** The zoom level, in Leaflet's 256-pixel-tile terms, so clustering speaks the same on both engines. */
  zoom(): number;
  /** Where a coordinate is drawn, in pixels from the map element's top left. */
  project(at: LatLon): { x: number; y: number };
  /** Whether one finger (or a mouse drag) pans the map. Off for a small map on a touch screen (Home on a phone), so a
   * swipe scrolls the page past it; pinch still zooms, and full screen turns it back on. */
  setPan(on: boolean): void;
  resize(): void;
  destroy(): void;
}

export interface EngineEvents {
  /** The base map's tiles could not load (Leaflet), so features draw on a plain background. */
  onBaseError?: (failed: boolean) => void;
  /** The map library stopped working (MapKit refused its token): draw with Leaflet instead. */
  onFatal?: () => void;
  /** The zoom level changed (after the gesture ends). */
  onZoom?: (zoom: number) => void;
}

export const DASH = (dash?: string) => (dash ? dash.split(/\s+/).map(Number) : []);

/** Every vertex of the given lines: what a fit to those lines covers. */
export const vertices = (lines: { paths: LatLon[][] }[]): LatLon[] => lines.flatMap((l) => l.paths.flat());

/** The paths of a GeoJSON line or polygon as [lat, lon] lists. */
export function pathsOf(geometry: { type: string; coordinates: unknown }): { paths: LatLon[][]; closed: boolean } {
  const ll = (ring: number[][]): LatLon[] => ring.map(([lon, lat]) => [lat, lon]);
  const c = geometry.coordinates as never;
  switch (geometry.type) {
    case "LineString": return { paths: [ll(c)], closed: false };
    case "MultiLineString": return { paths: (c as number[][][]).map(ll), closed: false };
    case "Polygon": return { paths: (c as number[][][]).map(ll), closed: true };
    case "MultiPolygon": return { paths: (c as number[][][][]).flat().map(ll), closed: true };
    default: return { paths: [], closed: false };
  }
}

/** Markers that would overlap on screen, gathered (#1074): items in priority order, each joining the first group whose
 * leading marker it would touch (within `gap` pixels), else starting its own. The first item of a group leads it. */
export function clusterOnScreen<T>(items: T[], at: (item: T) => { x: number; y: number }, size: (item: T) => number,
                                   gap = 4): T[][] {
  const groups: { lead: { x: number; y: number; r: number }; items: T[] }[] = [];
  for (const item of items) {
    const p = at(item), r = size(item) / 2;
    const near = groups.find((g) => Math.hypot(g.lead.x - p.x, g.lead.y - p.y) < g.lead.r + r + gap);
    if (near) near.items.push(item);
    else groups.push({ lead: { ...p, r }, items: [item] });
  }
  return groups.map((g) => g.items);
}
