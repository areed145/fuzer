import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GeoCollection, GeoFeature } from "./api";
import { entityHref } from "./lib";

// Pipeline services: fixed colours (validated categorical palette), gas services dashed so colour isn't
// the only cue. Unknown services fall back to grey.
const SERVICES: { key: string; label: string; color: string; dash?: string }[] = [
  { key: "production flowline", label: "Production flowline", color: "#0072B2" },
  { key: "oil export", label: "Oil export", color: "#E69F00" },
  { key: "gas lift", label: "Gas lift", color: "#009E73", dash: "6 6" },
  { key: "gas export", label: "Gas export", color: "#D55E00", dash: "14 6" },
  { key: "gas", label: "Gas", color: "#CC79A7", dash: "2 6" },
  { key: "production trunkline", label: "Production trunkline", color: "#56B4E9" },
];
const POINTS: { key: string; label: string; radius: number }[] = [
  { key: "area", label: "Platforms, jackets, pads", radius: 7 },
  { key: "facility", label: "Plants and terminals", radius: 8 },
  { key: "well", label: "Wells", radius: 3 },
];
const OTHER = "#7a8594";

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const tip = (f: GeoFeature) =>
  esc(`${f.properties.label ?? f.id}${f.properties.description ? ` · ${f.properties.description}` : ""}`);

function serviceOf(f: GeoFeature) {
  const s = String(f.properties.service ?? "");
  return SERVICES.find((x) => x.key === s);
}

function layerKey(f: GeoFeature): string {
  if (f.geometry.type === "Point") return `point:${f.properties.kind}`;
  if (f.properties.status === "abandoned") return "line:abandoned";
  return `line:${serviceOf(f)?.key ?? "other"}`;
}

function popup(f: GeoFeature): HTMLElement {
  const p = f.properties;
  const div = document.createElement("div");
  const a = document.createElement("a");
  a.href = entityHref(p.id);
  a.textContent = p.label ?? p.id;
  a.className = "mono";
  div.appendChild(a);
  const rows: [string, unknown][] = [
    ["", p.description ?? p.gis_name],
    ["service", p.service],
    ["diameter", p.diameter_in ? `${p.diameter_in}"` : null],
    ["length", p.length_km ? `${p.length_km} km` : null],
    ["MOP", p.mop_barg ? `${p.mop_barg} barg` : null],
    ["status", p.status],
    ["type", p.facility_type],
    ["installed", p.install_year],
  ];
  for (const [k, v] of rows) {
    if (v === null || v === undefined || v === "") continue;
    const line = document.createElement("div");
    line.className = "small";
    line.textContent = k ? `${k}: ${v}` : String(v);
    div.appendChild(line);
  }
  return div;
}

/** Leaflet map of GeoJSON features with a legend that shows and hides each layer. */
export function MapView({ data, focus, height = "70vh", basemap = true }: {
  data: GeoCollection;
  focus?: string | null;
  height?: string;
  basemap?: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const tiles = useRef<L.TileLayer | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [showTiles, setShowTiles] = useState(basemap);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of data.features) c[layerKey(f)] = (c[layerKey(f)] ?? 0) + 1;
    return c;
  }, [data]);

  useEffect(() => {
    if (!el.current || map.current) return;
    map.current = L.map(el.current, { preferCanvas: true, zoomSnap: 0.5 });
    tiles.current = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    });
    layer.current = L.layerGroup().addTo(map.current);
    return () => {
      map.current?.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    if (!map.current || !tiles.current) return;
    if (showTiles) tiles.current.addTo(map.current);
    else tiles.current.remove();
  }, [showTiles]);

  useEffect(() => {
    const m = map.current;
    const g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    const shown = data.features.filter((f) => !hidden.has(layerKey(f)));
    // Lines under points; a white halo under each line keeps it readable on any basemap.
    const lines = shown.filter((f) => f.geometry.type !== "Point");
    const points = shown.filter((f) => f.geometry.type === "Point");
    for (const f of lines) {
      const s = serviceOf(f);
      const isFocus = f.id === focus;
      const abandoned = f.properties.status === "abandoned";
      const weight = Math.max(2, Math.min(6, Number(f.properties.diameter_in ?? 6) / 3)) + (isFocus ? 3 : 0);
      L.geoJSON(f as GeoJSON.Feature, { style: { color: "#ffffff", weight: weight + 3, opacity: 0.9 } }).addTo(g);
      L.geoJSON(f as GeoJSON.Feature, {
        style: {
          color: abandoned ? OTHER : (s?.color ?? OTHER),
          weight,
          dashArray: abandoned ? "2 8" : s?.dash,
          opacity: 0.95,
        },
      })
        .bindTooltip(tip(f), { sticky: true })
        .bindPopup(() => popup(f))
        .addTo(g);
    }
    for (const f of points) {
      const [lon, lat] = f.geometry.coordinates as number[];
      const kind = String(f.properties.kind);
      const isFocus = f.id === focus;
      const r = (POINTS.find((p) => p.key === kind)?.radius ?? 5) + (isFocus ? 4 : 0);
      L.circleMarker([lat, lon], {
        radius: r,
        color: "#ffffff",
        weight: kind === "well" ? 1 : 2,
        fillColor: isFocus ? "#c4302b" : kind === "well" ? "#5f6b7a" : "#1b1f24",
        fillOpacity: 1,
      })
        .bindTooltip(tip(f))
        .bindPopup(() => popup(f))
        .addTo(g);
    }
    if (shown.length) {
      const bounds = L.geoJSON({ type: "FeatureCollection", features: shown } as GeoJSON.FeatureCollection).getBounds();
      if (bounds.isValid()) m.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
    }
  }, [data, hidden, focus]);

  const toggle = (k: string) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  const entries: { key: string; label: string; color: string; dash?: string; line: boolean }[] = [
    ...SERVICES.map((s) => ({ key: `line:${s.key}`, label: s.label, color: s.color, dash: s.dash, line: true })),
    { key: "line:other", label: "Other lines", color: OTHER, line: true },
    { key: "line:abandoned", label: "Abandoned", color: OTHER, dash: "2 8", line: true },
    ...POINTS.map((p) => ({ key: `point:${p.key}`, label: p.label, color: p.key === "well" ? "#5f6b7a" : "#1b1f24", line: false })),
  ].filter((e) => counts[e.key]);

  return (
    <div className="mapview">
      <div className="map-legend" aria-label="Show or hide map layers">
        {entries.map((e) => (
          <button key={e.key} className={`chip ${hidden.has(e.key) ? "off" : ""}`} aria-pressed={!hidden.has(e.key)}
                  onClick={() => toggle(e.key)}>
            <svg width="26" height="12" aria-hidden="true">
              {e.line ? (
                <line x1="1" y1="6" x2="25" y2="6" stroke={e.color} strokeWidth="4" strokeDasharray={e.dash} />
              ) : (
                <circle cx="13" cy="6" r="5" fill={e.color} stroke="var(--panel)" strokeWidth="1.5" />
              )}
            </svg>
            {e.label} <span className="muted">{counts[e.key]}</span>
          </button>
        ))}
        <label className="small muted basemap">
          <input type="checkbox" checked={showTiles} onChange={(ev) => setShowTiles(ev.target.checked)} /> Basemap
        </label>
      </div>
      <div ref={el} className="map" style={{ height }} />
    </div>
  );
}
