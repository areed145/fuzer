import "./styles/map.css";
import { useTheme } from "./theme";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AttentionPlace, GeoCollection, GeoFeature } from "./api";
import { kindColor, productColor } from "./kinds";
import { entityHref, href } from "./lib";
import { Button, FullscreenButton, useFullscreen, Text } from "./ui";
import { clusterOnScreen, pathsOf, vertices, type BasemapName, type HotspotSpec, type LatLon, type LineSpec, type MapEngine,
         type PointSpec } from "./map/engine";
import { leafletEngine } from "./map/leafletEngine";
import { mapkitEngine } from "./map/mapkitEngine";
import { loadMapKit } from "./map/mapkitLoader";

// Each pipeline service in the colour of what it carries, the same as in the 3D view and the network (kinds.ts).
// Gas services are dashed so colour is not the only cue.
const SERVICES: { key: string; label: string; product: string; dash?: string }[] = [
  { key: "production flowline", label: "Production flowline", product: "multiphase" },
  { key: "oil export", label: "Oil export", product: "liquid" },
  { key: "gas lift", label: "Gas lift", product: "lift_gas", dash: "6 6" },
  { key: "gas export", label: "Gas export", product: "gas", dash: "14 6" },
  { key: "gas", label: "Gas", product: "gas", dash: "2 6" },
  { key: "production trunkline", label: "Production trunkline", product: "multiphase", dash: "18 3" },
  { key: "flare", label: "Flare and relief", product: "flare", dash: "3 4" },
];
const POINTS: { key: string; label: string; radius: number; square?: boolean }[] = [
  { key: "area", label: "Platforms, jackets, pads", radius: 7, square: true },
  { key: "facility", label: "Plants and terminals", radius: 8, square: true },
  { key: "well", label: "Wells", radius: 3 },
];
/* The base maps, each light or dark by the app's theme (System by default), so there is no separate dark or light
 * choice. With a MapKit key on the deployment they are Apple Maps: Standard, or Satellite with names. Without one they
 * are Google's public tiles, which add Terrain. See map/leafletEngine.ts and map/mapkitEngine.ts. */
const BASEMAPS: Record<BasemapName, string> = { standard: "Standard", satellite: "Satellite", terrain: "Terrain" };
/** MapKit JS has no terrain map, so Apple Maps offers two; a saved Terrain choice shows as Standard there. */
const APPLE: BasemapName[] = ["standard", "satellite"];
type Basemap = BasemapName | "none";
const BASEMAP_KEY = "fuzer.basemap";
const savedBasemap = (): BasemapName => {
  try {
    const v = localStorage.getItem(BASEMAP_KEY);
    if (v === "satellite" || v === "terrain") return v;
  } catch { /* storage blocked: use the default */ }
  return "standard";
};

const themeValue = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const tip = (f: GeoFeature) => `${f.properties.label ?? f.id}${f.properties.description ? ` · ${f.properties.description}` : ""}`;

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
    ["length", p.length_mi ? `${p.length_mi} mi` : null],
    ["MOP", p.mop_psig ? `${p.mop_psig} psig` : null],
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
  if (f.geometry.type === "Point") { // a site in 3D (P5.16e): a facility as its field, by location; else its area
    const to = document.createElement("a");
    const at = p.id ?? f.id;
    to.href = href("/3d", p.kind === "facility" ? { at, layout: "geo" } : { at });
    to.textContent = p.kind === "facility" ? "See the field in 3D" : "See it in 3D";
    to.className = "small map-popup-3d";
    div.appendChild(to);
  }
  return div;
}

const hotspotSize = (open: number) => Math.round(Math.min(46, 26 + 4 * Math.sqrt(open)));
const RANK = { high: 0, medium: 1, low: 2 };
const CLUSTER_UNTIL = 15;  // past this zoom a gathered marker's tap picks its most urgent place instead of zooming in

/** Places whose markers would overlap at this zoom, as one marker (#1074): the most urgent place leads it and names
 * it, and its counts are the group's. */
function gathered(group: AttentionPlace[]): AttentionPlace {
  const [lead, ...rest] = group;
  if (!rest.length) return lead;
  const sum = (f: (p: AttentionPlace) => number) => group.reduce((n, p) => n + f(p), 0);
  return { ...lead, open: sum((p) => p.open), overdue: sum((p) => p.overdue), unassigned: sum((p) => p.unassigned),
           name: `${lead.name ?? lead.label} and ${rest.length} more`, crews: group.flatMap((p) => p.crews) };
}

/** A hotspot: a place with open work, drawn by its highest band, how much is open and whether any is overdue. */
function hotspotSpec(p: AttentionPlace, selected: boolean, labelled: boolean,
                     crew: { who: string | null; status: string } | undefined, onSelect: () => void, cluster = false): HotspotSpec {
  const size = hotspotSize(p.open);
  const name = p.name ?? p.label;
  const html = `<span class="hotspot-dot" style="width:${size}px;height:${size}px">${p.open}</span>`
    + (p.overdue ? `<span class="hotspot-late" aria-hidden="true"><svg viewBox="-8 -8 16 16"><path d="M0,-4 L0,0 L3,2"/></svg></span>` : "")
    + (labelled ? `<span class="hotspot-label">${esc(name)}</span>` : "");
  return {
    at: [p.lat, p.lon], className: `hotspot hotspot-${p.band}${selected ? " selected" : ""}${cluster ? " hotspot-cluster" : ""}`,
    html, size, name, selected,
    title: `${name}: ${p.open} open${p.overdue ? `, ${p.overdue} overdue` : ""}`, onSelect,
    crew: crew && { html: `<span>${esc((crew.who ?? "?").replace(/^role:/, "").slice(0, 1).toUpperCase())}</span>`,
                    title: `${crew.who} ${crew.status}` },
  };
}

/** A map of GeoJSON features (Apple MapKit when the deployment has a key, else Leaflet on free tiles) with a legend that shows and hides each layer. With `hotspots` (Home), the
 * network is drawn faintly under the places that need attention, and `compact` leaves out the layer and basemap
 * controls. */
export function MapView({ data, focus, height = "70vh", basemap = true, hotspots, selected, onSelect, crews = true,
                          compact = false, overlay, onFullscreen }: {
  data: GeoCollection;
  focus?: string | null;
  height?: string;
  basemap?: boolean;
  hotspots?: AttentionPlace[];
  selected?: string | null;
  onSelect?: (id: string) => void;
  crews?: boolean;
  compact?: boolean;
  /** Shown over the map while it is full screen (Home's chosen place), where nothing outside the map can show. */
  overlay?: ReactNode;
  onFullscreen?: (on: boolean) => void;
}) {
  const el = useRef<HTMLDivElement>(null);
  const full = useFullscreen();
  useEffect(() => { onFullscreen?.(full.on); }, [full.on]);  // eslint-disable-line react-hooks/exhaustive-deps
  // Apple MapKit when the deployment has a key and Apple accepts it; Leaflet otherwise, or with no base map at all.
  const [library, setLibrary] = useState<"pending" | "mapkit" | "leaflet">(basemap ? "pending" : "leaflet");
  const [engine, setEngine] = useState<MapEngine | null>(null);
  const fitted = useRef(false);
  const [zoomed, setZoomed] = useState(0);  // counts zoom changes, so overlapping hotspots gather again
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [basemapName, setBasemapName] = useState<Basemap>(basemap ? savedBasemap : "none");
  const [mapMessage, setMapMessage] = useState("");
  const [whole, setWhole] = useState(false);  // fit the whole network, export routes and terminals too
  const [baseError, setBaseError] = useState(false);  // #398: say so when the base map tiles can't load
  const themeVersion = useTheme();
  const dark = themeVersion === "dark";
  const colors = {
    outline: themeValue("--map-outline"), label: themeValue("--map-label"), well: themeValue("--map-well"),
    other: themeValue("--map-other"), abandoned: themeValue("--map-abandoned"), focus: themeValue("--map-focus"),
  };
  const serviceColors = Object.fromEntries(SERVICES.map((s) => [s.key, productColor(s.product)]));
  const other = colors.other;

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of data.features) c[layerKey(f)] = (c[layerKey(f)] ?? 0) + 1;
    return c;
  }, [data]);

  useEffect(() => {
    if (library !== "pending") return;
    let live = true;
    void loadMapKit().then((mk) => { if (live) setLibrary(mk ? "mapkit" : "leaflet"); });
    return () => { live = false; };
  }, [library]);

  useEffect(() => {
    if (!el.current || library === "pending") return;
    const events = { onBaseError: setBaseError, onFatal: () => setLibrary("leaflet"), onZoom: () => setZoomed((n) => n + 1) };
    let made: MapEngine;
    try {
      made = library === "mapkit" && window.mapkit ? mapkitEngine(window.mapkit, el.current, events)
        : leafletEngine(el.current, events);
    } catch {
      if (library === "mapkit") setLibrary("leaflet");
      return;
    }
    fitted.current = false;
    setBaseError(false);
    setEngine(made);
    return () => { made.destroy(); setEngine(null); };
  }, [library]);

  const choices = library === "mapkit" ? APPLE : (Object.keys(BASEMAPS) as BasemapName[]);
  const shown: Basemap = basemapName === "none" || choices.includes(basemapName) ? basemapName : "standard";
  useEffect(() => { engine?.setBasemap(shown, dark); }, [engine, shown, dark]);

  const chooseBasemap = (name: BasemapName) => {
    setBasemapName(name);
    try { localStorage.setItem(BASEMAP_KEY, name); } catch { /* storage blocked: the choice lasts this visit */ }
  };

  useEffect(() => {
    requestAnimationFrame(() => engine?.resize());
  }, [full.on]);  // eslint-disable-line react-hooks/exhaustive-deps

  // A small map on a touch screen lets a one-finger swipe scroll the page past it (#1074); pinch zooms, and full
  // screen pans as usual.
  useEffect(() => {
    engine?.setPan(full.on || !compact || !matchMedia("(pointer: coarse)").matches);
  }, [engine, full.on, compact]);

  const locate = () => {
    setMapMessage("");
    if (!navigator.geolocation) {
      setMapMessage("Location is not available in this browser.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        engine?.showLocation([coords.latitude, coords.longitude], coords.accuracy,
                             { accent: themeValue("--accent"), panel: themeValue("--panel") });
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


  useEffect(() => {
    if (!engine) return;
    const shown = data.features.filter((f) => !hidden.has(layerKey(f)));
    // Lines under points; a halo under each line keeps it readable on any basemap.
    const faint = hotspots !== undefined;  // under hotspots the network is context, not the subject
    const lines: LineSpec[] = shown.filter((f) => f.geometry.type !== "Point").map((f) => {
      const s = serviceOf(f);
      const isFocus = f.id === focus;
      const abandoned = f.properties.status === "abandoned";
      const weight = Math.max(2, Math.min(6, Number(f.properties.diameter_in ?? 6) / 3)) + (isFocus ? 3 : 0);
      return {
        ...pathsOf(f.geometry), weight,
        color: abandoned ? colors.abandoned : (s ? serviceColors[s.key] : other),
        dash: abandoned ? "2 8" : s?.dash,
        opacity: faint ? 0.5 : 0.95,
        halo: faint ? undefined : { color: colors.outline, weight: weight + 3 },
        popup: () => popup(f), tip: tip(f),
      };
    });
    const points: (PointSpec & { kind: string })[] = shown.filter((f) => f.geometry.type === "Point").map((f) => {
      const [lon, lat] = f.geometry.coordinates as number[];
      const kind = String(f.properties.kind);
      const isFocus = f.id === focus;
      const def = POINTS.find((p) => p.key === kind);
      return {
        at: [lat, lon], kind, shape: def?.square ? "square" : "circle",
        radius: ((def?.radius ?? 5) + (isFocus ? 4 : 0)) * (faint ? 0.6 : 1),
        // The picked thing keeps its kind's colour and is ringed in the accent, as a pick is on every view.
        stroke: isFocus ? colors.focus : colors.outline, strokeWidth: isFocus ? 4 : kind === "well" ? 1 : 2,
        fill: kindColor(kind), fillOpacity: faint ? 0.6 : 1,
        popup: () => popup(f), tip: tip(f),
      };
    });
    engine.drawNetwork(lines, points);
    if (shown.length && !(hotspots?.length)) {
      // Fit to the field: the wells and platforms where the work is, not the plants and terminals at the end of the
      // export lines (#398, #1075); the whole network is one tap away. Lines count only when no points are shown.
      const field = points.filter((p) => p.kind === "area" || p.kind === "well");
      const fit: LatLon[] = whole ? [...points.map((p) => p.at), ...vertices(lines)]
        : field.length ? field.map((p) => p.at) : points.length ? points.map((p) => p.at) : vertices(lines);
      engine.fit(fit, 24, 15);
    }
  }, [engine, data, hidden, focus, themeVersion, hotspots === undefined, whole]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!engine || !hotspots) return;
    // Least urgent first, so the most urgent sit on top where markers overlap. The chosen place is named on the map;
    // the rest name themselves on hover, with every name in the list beside the map. Places whose markers would
    // overlap at this zoom are one marker, which zooms in to part them (#1074); the chosen place always stands alone.
    if (!fitted.current && hotspots.length) {
      // Fit to where the work is the first time (before gathering, which reads the view); afterwards a person's pan
      // and zoom stay put.
      fitted.current = true;
      engine.fit(hotspots.map((p) => [p.lat, p.lon]), 48, 13);
    }
    const narrow = matchMedia("(max-width: 600px)").matches;  // a phone names only the chosen place
    const urgent = [...hotspots].sort((a, b) => RANK[a.band] - RANK[b.band] || b.overdue - a.overdue || b.open - a.open);
    const groups = [
      ...clusterOnScreen(urgent.filter((p) => p.id !== selected), (p) => engine.project([p.lat, p.lon]), (p) => hotspotSize(p.open)),
      ...urgent.filter((p) => p.id === selected).map((p) => [p]),
    ];
    const pick = (group: AttentionPlace[]) => () => {
      if (group.length > 1 && engine.zoom() < CLUSTER_UNTIL) engine.fit(group.map((p) => [p.lat, p.lon]), 64, CLUSTER_UNTIL + 1);
      else onSelect?.(group[0].id);
    };
    engine.drawHotspots(groups.map((g) => ({ g, p: gathered(g) })).sort((a, b) => RANK[b.p.band] - RANK[a.p.band]).map(({ g, p }) => hotspotSpec(
      p, p.id === selected, p.id === selected || (!narrow && groups.length <= 3),
      crews ? p.crews.find((x) => x.status === "on site" || x.status === "planned") : undefined,
      pick(g), g.length > 1)));
  }, [engine, hotspots, selected, crews, themeVersion, zoomed]);  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {  // a place picked in the list comes into view
    const p = hotspots?.find((x) => x.id === selected);
    if (p) engine?.reveal([p.lat, p.lon]);
  }, [selected]);  // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (k: string) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });
  const entries: { key: string; label: string; color: string; dash?: string; line: boolean; square?: boolean }[] = [
    ...SERVICES.map((s) => ({ key: `line:${s.key}`, label: s.label, color: serviceColors[s.key], dash: s.dash, line: true })),
    { key: "line:other", label: "Other lines", color: other, line: true },
    { key: "line:abandoned", label: "Abandoned", color: colors.abandoned, dash: "2 8", line: true },
    ...POINTS.map((p) => ({ key: `point:${p.key}`, label: p.label, color: kindColor(p.key), line: false, square: p.square })),
  ].filter((e) => counts[e.key]);

  return (
    <div className={`mapview${compact ? " mapview-compact" : ""}`}>
      {!compact && <div className="map-legend" aria-label="Show or hide map layers">
        {entries.map((e) => (
          <Button key={e.key} className={`chip ${hidden.has(e.key) ? "off" : ""}`} aria-pressed={!hidden.has(e.key)}
                  onClick={() => toggle(e.key)}>
            <svg width="26" height="12" aria-hidden="true">
              {e.line ? (
                <line x1="1" y1="6" x2="25" y2="6" stroke={e.color} strokeWidth="4" strokeDasharray={e.dash} />
              ) : e.square ? (
                <rect x="8" y="1" width="10" height="10" rx="2" fill={e.color} stroke="var(--panel)" strokeWidth="1.5" />
              ) : (
                <circle cx="13" cy="6" r="5" fill={e.color} stroke="var(--panel)" strokeWidth="1.5" />
              )}
            </svg>
            {e.label} <Text muted>{counts[e.key]}</Text>
          </Button>
        ))}
      </div>}
      <div ref={full.ref} className={`map-frame${full.on ? " map-fullscreen ui-fullscreen" : ""}`}>
        <div className="map-controls">
          {!compact && basemapName !== "none" && library !== "pending" && <div className="map-basemaps" role="group" aria-label="Base map">
            {choices.map((name) => (
              <Button key={name} type="button" variant="ghost" size="sm" aria-pressed={shown === name}
                      className={`map-basemap${shown === name ? " on" : ""}`}
                      onClick={() => chooseBasemap(name)}>
                {BASEMAPS[name]}</Button>
            ))}
          </div>}
          {!compact && data.features.some((f) => f.properties.kind === "facility") && (
            <Button size="sm" className="map-control-button map-extent" aria-pressed={whole} onClick={() => setWhole(!whole)}
                    title={whole ? "Fit to the field" : "Show the whole network, export routes too"}>{whole ? "Field" : "All"}</Button>)}
          {!compact && <Button size="sm" className="map-control-button" onClick={locate} aria-label="Find my location" title="Find my location">
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <circle cx="12" cy="12" r="7" />
              <path d="M12 2v3m0 14v3M2 12h3m14 0h3" />
              <circle cx="12" cy="12" r="1.5" fill="currentColor" />
            </svg>
          </Button>}
          <FullscreenButton on={full.on} onClick={() => void full.toggle()} className="map-control-button" />
        </div>
        {mapMessage && <div className="map-message" role="status">{mapMessage}</div>}
        {baseError && basemapName !== "none" && !compact && <div className="map-message" role="status">
          Base map unavailable: its tiles could not load, so features are drawn on a plain background.</div>}
        {full.on && overlay && <div className="map-overlay">{overlay}</div>}
        <div key={library} ref={el} className={`map map-${library}${basemapName === "none" ? "" : ` map-basemap-${shown}-${dark ? "dark" : "light"}`}`}
             style={{ height }} />
      </div>
    </div>
  );
}
