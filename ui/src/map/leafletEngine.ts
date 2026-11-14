import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { BasemapName, EngineEvents, HotspotSpec, LatLon, LineSpec, MapEngine, PointSpec } from "./engine";

type Tiles = { url: string; attribution: string; maxNativeZoom: number; subdomains?: string };
/* Without a MapKit key the maps draw Google's public map tiles (mt0 to mt3): roadmap, satellite with names, and terrain.
 * Google has no dark tiles, so a dark theme dims the roadmap and terrain in CSS (.map-basemap-*-dark). These tile
 * endpoints are not a licensed Google Maps Platform API; the supported path is Apple MapKit (mapkitEngine.ts). */
const google = (layer: string, maxNativeZoom: number): Tiles => ({
  url: `https://mt{s}.google.com/vt/lyrs=${layer}&x={x}&y={y}&z={z}&hl=en`, subdomains: "0123", maxNativeZoom,
  attribution: 'Map data &copy; <a href="https://www.google.com/intl/en_us/help/terms_maps/">Google</a>',
});
const TILES: Record<BasemapName, Tiles> = {
  standard: google("m", 20),
  satellite: google("y", 20),
  terrain: google("p", 15),
};

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

export function leafletEngine(el: HTMLElement, events: EngineEvents): MapEngine {
  const map = L.map(el, { preferCanvas: true, zoomSnap: 0.5 });
  const network = L.layerGroup().addTo(map);
  const location = L.layerGroup().addTo(map);
  const spots = L.layerGroup().addTo(map);
  let tiles: L.TileLayer | null = null;
  // A tap on a phone opens the popup; a hover label there would stick beside it, saying the same thing twice.
  const hover = () => matchMedia("(hover: hover)").matches;
  map.on("zoomend", () => events.onZoom?.(map.getZoom()));

  return {
    kind: "leaflet",
    setBasemap(name, _dark) {  // one tile set for both themes; the dark theme dims it in CSS
      tiles?.remove();
      tiles = null;
      events.onBaseError?.(false);
      if (name === "none") return;
      const t = TILES[name];
      let loaded = false;
      tiles = L.tileLayer(t.url, { maxZoom: 20, maxNativeZoom: t.maxNativeZoom, subdomains: t.subdomains ?? "abc",
                                   attribution: t.attribution })
        .on("tileload", () => { loaded = true; events.onBaseError?.(false); })
        .on("tileerror", () => { if (!loaded) events.onBaseError?.(true); })
        .addTo(map);
    },
    drawNetwork(lines: LineSpec[], points: PointSpec[]) {
      network.clearLayers();
      const tips = hover();
      for (const l of lines) {
        if (l.halo) L.polyline(l.paths, { color: l.halo.color, weight: l.halo.weight, opacity: 0.9 }).addTo(network);
        const style = { color: l.color, weight: l.weight, dashArray: l.dash, opacity: l.opacity };
        const line = (l.closed ? L.polygon(l.paths, style) : L.polyline(l.paths, style)).bindPopup(l.popup).addTo(network);
        if (tips && l.tip) line.bindTooltip(esc(l.tip), { sticky: true });
      }
      for (const p of points) {
        const marker = L.circleMarker(p.at, { radius: p.radius, color: p.stroke, weight: p.strokeWidth, fillColor: p.fill,
                                              fillOpacity: p.fillOpacity })
          .bindPopup(p.popup).addTo(network);
        if (tips && p.tip) marker.bindTooltip(esc(p.tip));
      }
    },
    drawHotspots(list: HotspotSpec[]) {
      spots.clearLayers();
      for (const s of list) {
        const icon = L.divIcon({ className: s.className, html: s.html, iconSize: [s.size, s.size],
                                 iconAnchor: [s.size / 2, s.size / 2] });
        const marker = L.marker(s.at, { icon, title: s.title, alt: s.name, riseOnHover: true,
                                        zIndexOffset: s.selected ? 1000 : 0 })
          .on("click", s.onSelect).addTo(spots);
        if (!s.selected) marker.bindTooltip(esc(s.name), { direction: "right", offset: [14, 0] });
        marker.on("keypress", (e: L.LeafletKeyboardEvent) => {
          if (e.originalEvent.key === "Enter" || e.originalEvent.key === " ") s.onSelect();
        });
        if (s.crew) {
          L.marker(s.at, { icon: L.divIcon({ className: "hotspot-crew", html: s.crew.html, iconSize: [22, 20], iconAnchor: [11, 30] }),
                           interactive: false, keyboard: false, title: s.crew.title }).addTo(spots);
        }
      }
    },
    fit(coords: LatLon[], padding, maxZoom) {
      const bounds = L.latLngBounds(coords);
      if (bounds.isValid()) map.fitBounds(bounds, { padding: [padding, padding], maxZoom });
    },
    reveal(at) {
      if (!map.getBounds().pad(-0.1).contains(at)) map.panTo(at);
    },
    showLocation(at, accuracy, colors) {
      location.clearLayers();
      L.circle(at, { radius: accuracy, color: colors.accent, fillColor: colors.accent, fillOpacity: 0.12, weight: 1 })
        .addTo(location);
      L.circleMarker(at, { radius: 7, color: colors.panel, fillColor: colors.accent, fillOpacity: 1, weight: 3 })
        .bindTooltip("Your location").addTo(location);
      map.setView(at, Math.max(map.getZoom(), 14));
    },
    zoom() { return map.getZoom(); },
    project(at) { const p = map.latLngToContainerPoint(at); return { x: p.x, y: p.y }; },
    setPan(on) { if (on) map.dragging.enable(); else map.dragging.disable(); },
    resize() { map.invalidateSize(); },
    destroy() { map.remove(); },
  };
}
