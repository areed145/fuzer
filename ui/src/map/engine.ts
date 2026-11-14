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
  resize(): void;
  destroy(): void;
}

export interface EngineEvents {
  /** The base map's tiles could not load (Leaflet), so features draw on a plain background. */
  onBaseError?: (failed: boolean) => void;
  /** The map library stopped working (MapKit refused its token): draw with Leaflet instead. */
  onFatal?: () => void;
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
