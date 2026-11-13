import "./styles/map.css";
import { useTheme } from "./theme";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GeoCollection, GeoFeature } from "./api";
import { entityHref } from "./lib";
import { Button, Select } from "./ui";

// Gas services are dashed so colour is not the only cue.
const SERVICES: { key: string; label: string; token: string; dash?: string }[] = [
  { key: "production flowline", label: "Production flowline", token: "--map-flow" },
  { key: "oil export", label: "Oil export", token: "--map-oil" },
  { key: "gas lift", label: "Gas lift", token: "--map-gas-lift", dash: "6 6" },
  { key: "gas export", label: "Gas export", token: "--map-gas-export", dash: "14 6" },
  { key: "gas", label: "Gas", token: "--map-gas", dash: "2 6" },
  { key: "production trunkline", label: "Production trunkline", token: "--map-trunkline" },
];
const POINTS: { key: string; label: string; radius: number }[] = [
  { key: "area", label: "Platforms, jackets, pads", radius: 7 },
  { key: "facility", label: "Plants and terminals", radius: 8 },
  { key: "well", label: "Wells", radius: 3 },
];
const BASEMAPS = {
  osm: {
    label: "OpenStreetMap",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  },
  light: {
    label: "Light",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  },
  dark: {
    label: "Dark",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  },
  satellite: {
    label: "Satellite",
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attribution: 'Tiles &copy; <a href="https://www.esri.com/">Esri</a> — Sources: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
    maxZoom: 19,
  },
} as const;
type Basemap = keyof typeof BASEMAPS | "none";

const themeValue = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

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
  const frame = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const tiles = useRef<L.TileLayer | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const location = useRef<L.LayerGroup | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [basemapName, setBasemapName] = useState<Basemap>(basemap ? "osm" : "none");
  const [mapMessage, setMapMessage] = useState("");
  const [baseError, setBaseError] = useState(false);  // #398: say so when the base map tiles can't load
  const [isFullscreen, setIsFullscreen] = useState(false);
  const themeVersion = useTheme();
  const colors = {
    outline: themeValue("--map-outline"), label: themeValue("--map-label"), well: themeValue("--map-well"),
    other: themeValue("--map-other"), abandoned: themeValue("--map-abandoned"), focus: themeValue("--map-focus"),
  };
  const serviceColors = Object.fromEntries(SERVICES.map((s) => [s.key, themeValue(s.token)]));
  const other = colors.other;

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of data.features) c[layerKey(f)] = (c[layerKey(f)] ?? 0) + 1;
    return c;
  }, [data]);

  useEffect(() => {
    if (!el.current || map.current) return;
    map.current = L.map(el.current, { preferCanvas: true, zoomSnap: 0.5 });
    layer.current = L.layerGroup().addTo(map.current);
    location.current = L.layerGroup().addTo(map.current);
    return () => {
      map.current?.remove();
      map.current = null;
      tiles.current = null;
      layer.current = null;
      location.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    tiles.current?.remove();
    tiles.current = null;
    if (basemapName !== "none") {
      const selected = BASEMAPS[basemapName];
      setBaseError(false);
      let loaded = false;
      tiles.current = L.tileLayer(selected.url, {
        maxZoom: selected.maxZoom,
        attribution: selected.attribution,
      }).on("tileload", () => { loaded = true; setBaseError(false); })
        .on("tileerror", () => { if (!loaded) setBaseError(true); })
        .addTo(m);
    }
  }, [basemapName]);

  useEffect(() => {
    const onFullscreenChange = () => {
      setIsFullscreen(document.fullscreenElement === frame.current);
      requestAnimationFrame(() => map.current?.invalidateSize());
    };
    document.addEventListener("fullscreenchange", onFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", onFullscreenChange);
  }, []);
  useEffect(() => {
    requestAnimationFrame(() => map.current?.invalidateSize());
  }, [isFullscreen]);

  const locate = () => {
    setMapMessage("");
    if (!navigator.geolocation) {
      setMapMessage("Location is not available in this browser.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        const m = map.current;
        const markers = location.current;
        if (!m || !markers) return;
        const point: L.LatLngExpression = [coords.latitude, coords.longitude];
        markers.clearLayers();
        L.circle(point, {
          radius: coords.accuracy,
          color: themeValue("--accent"),
          fillColor: themeValue("--accent"),
          fillOpacity: 0.12,
          weight: 1,
        }).addTo(markers);
        L.circleMarker(point, {
          radius: 7,
          color: themeValue("--panel"),
          fillColor: themeValue("--accent"),
          fillOpacity: 1,
          weight: 3,
        }).bindTooltip("Your location").addTo(markers);
        m.setView(point, Math.max(m.getZoom(), 14));
      },
      (error) => {
        setMapMessage(error.code === error.PERMISSION_DENIED
          ? "Location permission was denied."
          : error.code === error.POSITION_UNAVAILABLE
            ? "Your location is unavailable."
            : "Timed out while finding your location.");
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  };

  const toggleFullscreen = async () => {
    const target = frame.current;
    if (!target) return;
    setMapMessage("");
    if (document.fullscreenElement === target) {
      await document.exitFullscreen();
      return;
    }
    if (isFullscreen) {
      setIsFullscreen(false);
      return;
    }
    try {
      if (target.requestFullscreen) {
        await target.requestFullscreen();
        return;
      }
    } catch {
      // Fall back to the CSS fullscreen mode if the browser blocks native fullscreen.
    }
    setIsFullscreen(true);
  };

  useEffect(() => {
    const m = map.current;
    const g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    const shown = data.features.filter((f) => !hidden.has(layerKey(f)));
    // Lines under points; a white halo under each line keeps it readable on any basemap.
    const lines = shown.filter((f) => f.geometry.type !== "Point");
    const points = shown.filter((f) => f.geometry.type === "Point");
    // A tap on a phone opens the popup; a hover label there would stick beside it, saying the same thing twice.
    const hover = matchMedia("(hover: hover)").matches;
    for (const f of lines) {
      const s = serviceOf(f);
      const isFocus = f.id === focus;
      const abandoned = f.properties.status === "abandoned";
      const weight = Math.max(2, Math.min(6, Number(f.properties.diameter_in ?? 6) / 3)) + (isFocus ? 3 : 0);
      L.geoJSON(f as GeoJSON.Feature, { style: { color: colors.outline, weight: weight + 3, opacity: 0.9 } }).addTo(g);
      const line = L.geoJSON(f as GeoJSON.Feature, {
        style: {
          color: abandoned ? colors.abandoned : (s ? serviceColors[s.key] : other),
          weight,
          dashArray: abandoned ? "2 8" : s?.dash,
          opacity: 0.95,
        },
      })
        .bindPopup(() => popup(f))
        .addTo(g);
      if (hover) line.bindTooltip(tip(f), { sticky: true });
    }
    for (const f of points) {
      const [lon, lat] = f.geometry.coordinates as number[];
      const kind = String(f.properties.kind);
      const isFocus = f.id === focus;
      const r = (POINTS.find((p) => p.key === kind)?.radius ?? 5) + (isFocus ? 4 : 0);
      const marker = L.circleMarker([lat, lon], {
        radius: r,
        color: colors.outline,
        weight: kind === "well" ? 1 : 2,
        fillColor: isFocus ? colors.focus : kind === "well" ? colors.well : colors.label,
        fillOpacity: 1,
      })
        .bindPopup(() => popup(f))
        .addTo(g);
      if (hover) marker.bindTooltip(tip(f));
    }
    if (shown.length) {
      // Fit to the wells and platforms where the work is, not the export lines running off to a terminal (#398);
      // lines count only when no points are shown.
      const fit = points.length ? points : shown;
      const bounds = L.geoJSON({ type: "FeatureCollection", features: fit } as GeoJSON.FeatureCollection).getBounds();
      if (bounds.isValid()) m.fitBounds(bounds, { padding: [24, 24], maxZoom: 15 });
    }
  }, [data, hidden, focus, themeVersion]);

  const toggle = (k: string) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  const entries: { key: string; label: string; color: string; dash?: string; line: boolean }[] = [
    ...SERVICES.map((s) => ({ key: `line:${s.key}`, label: s.label, color: serviceColors[s.key], dash: s.dash, line: true })),
    { key: "line:other", label: "Other lines", color: other, line: true },
    { key: "line:abandoned", label: "Abandoned", color: colors.abandoned, dash: "2 8", line: true },
    ...POINTS.map((p) => ({ key: `point:${p.key}`, label: p.label, color: p.key === "well" ? colors.well : colors.label, line: false })),
  ].filter((e) => counts[e.key]);

  return (
    <div className="mapview">
      <div className="map-legend" aria-label="Show or hide map layers">
        {entries.map((e) => (
          <Button key={e.key} className={`chip ${hidden.has(e.key) ? "off" : ""}`} aria-pressed={!hidden.has(e.key)}
                  onClick={() => toggle(e.key)}>
            <svg width="26" height="12" aria-hidden="true">
              {e.line ? (
                <line x1="1" y1="6" x2="25" y2="6" stroke={e.color} strokeWidth="4" strokeDasharray={e.dash} />
              ) : (
                <circle cx="13" cy="6" r="5" fill={e.color} stroke="var(--panel)" strokeWidth="1.5" />
              )}
            </svg>
            {e.label} <span className="muted">{counts[e.key]}</span>
          </Button>
        ))}
      </div>
      <div ref={frame} className={`map-frame${isFullscreen ? " map-fullscreen" : ""}`}>
        <div className="map-controls">
          <label className="small muted">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="m12 3 9 5-9 5-9-5 9-5Z" />
              <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
            </svg>
            <Select
              aria-label="Basemap"
              value={basemapName}
              onChange={(ev) => setBasemapName(ev.target.value as Basemap)}
            >
              <option value="osm">{BASEMAPS.osm.label}</option>
              <option value="light">{BASEMAPS.light.label}</option>
              <option value="dark">{BASEMAPS.dark.label}</option>
              <option value="satellite">{BASEMAPS.satellite.label}</option>
              <option value="none">None</option>
            </Select>
          </label>
          <Button size="sm" className="map-control-button" onClick={locate} aria-label="Find my location" title="Find my location">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <circle cx="12" cy="12" r="7" />
              <path d="M12 2v3m0 14v3M2 12h3m14 0h3" />
              <circle cx="12" cy="12" r="1.5" fill="currentColor" />
            </svg>
          </Button>
          <Button
            size="sm"
            className="map-control-button"
            onClick={() => void toggleFullscreen()}
            aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
            title={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
          >
            {isFullscreen ? (
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path d="M8 3v5H3m13-5v5h5M8 21v-5H3m13 5v-5h5" />
              </svg>
            ) : (
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path d="M3 8V3h5m8 0h5v5M3 16v5h5m8 0h5v-5" />
              </svg>
            )}
          </Button>
        </div>
        {mapMessage && <div className="map-message" role="status">{mapMessage}</div>}
        {baseError && basemapName !== "none" && <div className="map-message" role="status">
          Base map unavailable: its tiles could not load, so features are drawn on a plain background.</div>}
        <div ref={el} className={`map${basemapName === "none" ? "" : ` map-basemap-${basemapName}`}`} style={{ height }} />
      </div>
    </div>
  );
}
