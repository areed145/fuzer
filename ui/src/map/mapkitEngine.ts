import { DASH, type BasemapName, type EngineEvents, type HotspotSpec, type LatLon, type LineSpec, type MapEngine,
         type PointSpec } from "./engine";
import { onMapKitFailure, type MapKit } from "./mapkitLoader";

/* Apple Maps through MapKit JS. Lines are overlays; points, hotspots and crews are DOM annotations drawn with the
 * same classes as on the Leaflet map, so map.css styles both. Popups open in a small card at the tap, since
 * MapKit's callouts belong to annotations and lines need them too. */

const TILE = 256;  // Leaflet's zoom levels are in 256-pixel tiles; fits and "find my location" speak in them

/** A 0×0 anchor at the coordinate with the drawn element centred (or offset) on it, so placement doesn't depend on
 * MapKit's default bottom-centre anchor. */
function anchored(inner: HTMLElement, left: number, top: number): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "mk-anchor";
  inner.style.left = `${left}px`;
  inner.style.top = `${top}px`;
  wrap.appendChild(inner);
  return wrap;
}

export function mapkitEngine(mapkit: MapKit, el: HTMLElement, events: EngineEvents): MapEngine {
  const map = new mapkit.Map(el, {
    showsMapTypeControl: false,
    showsCompass: mapkit.FeatureVisibility.Hidden,
    showsUserLocationControl: false,
    // Fuzer's own + and − stand in the map's tool stack (zoomBy), the same size as the tools beside them.
    showsZoomControl: false,
    isRotationEnabled: false,
    ...(mapkit.PointOfInterestFilter ? { pointOfInterestFilter: mapkit.PointOfInterestFilter.excludingAllCategories }
                                     : { showsPointsOfInterest: false }),
  });
  const coord = ([lat, lon]: LatLon) => new mapkit.Coordinate(lat, lon);
  let network: { overlays: unknown[]; annotations: unknown[] } = { overlays: [], annotations: [] };
  let spots: unknown[] = [];
  let ring: unknown = null;
  let location: { overlays: unknown[]; annotations: unknown[] } = { overlays: [], annotations: [] };
  const unsubscribe = onMapKitFailure(() => events.onFatal?.());

  // The popup card: opened at the last pointer position, closed by a tap elsewhere, a pan or its close button.
  const frame = el.parentElement ?? el;
  let pointer: { x: number; y: number } | null = null;
  let card: HTMLElement | null = null;
  let openedAt = 0;
  const onPointer = (e: PointerEvent) => { pointer = { x: e.clientX, y: e.clientY }; };
  el.addEventListener("pointerdown", onPointer, { capture: true });
  const closeCard = () => { card?.remove(); card = null; };
  const openCard = (content: HTMLElement) => {
    closeCard();
    openedAt = performance.now();
    card = document.createElement("div");
    card.className = "map-popup";
    card.setAttribute("role", "dialog");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "map-popup-close";
    close.setAttribute("aria-label", "Close");
    close.textContent = "×";
    close.addEventListener("click", closeCard);
    card.append(close, content);
    frame.appendChild(card);
    const f = frame.getBoundingClientRect();
    const at = pointer ?? { x: f.left + f.width / 2, y: f.top + f.height / 2 };
    const w = card.offsetWidth, h = card.offsetHeight;
    const x = Math.min(Math.max(at.x - f.left - w / 2, 8), Math.max(8, f.width - w - 8));
    let y = at.y - f.top - h - 14;
    if (y < 8) y = at.y - f.top + 14;
    card.style.left = `${x}px`;
    card.style.top = `${Math.min(y, Math.max(8, f.height - h - 8))}px`;
  };
  const closeUnlessJustOpened = () => { if (performance.now() - openedAt > 350) closeCard(); };
  const onSelect = (e: { overlay?: { data?: { popup?: () => HTMLElement } } }) => {
    const popup = e.overlay?.data?.popup;
    if (popup) openCard(popup());
  };
  map.addEventListener("select", onSelect);
  map.addEventListener("deselect", closeUnlessJustOpened);
  map.addEventListener("single-tap", closeUnlessJustOpened);
  map.addEventListener("region-change-start", closeUnlessJustOpened);
  const zoomNow = () => Math.log2(((el.clientWidth || 600) * 360) / (TILE * Math.max(map.region.span.longitudeDelta, 1e-9)));
  let lastZoom = NaN;
  map.addEventListener("region-change-end", () => {  // a pan ends here too: only a change of zoom is news
    const z = zoomNow();
    if (!(Math.abs(z - lastZoom) < 0.05)) { lastZoom = z; events.onZoom?.(z); }
  });

  const style = (o: Record<string, unknown>) => new mapkit.Style({ lineCap: "round", lineJoin: "round", ...o });
  const spanForZoom = (zoom: number) => {
    const w = el.clientWidth || 600, h = el.clientHeight || 400;
    const lon = (w * 360) / (TILE * 2 ** zoom);
    return { lon, lat: (lon * h) / w };
  };
  // MapKit holds annotations back until its renderer and the map's manifest have loaded, seconds after the map
  // itself shows. Until then a stand-in layer draws copies of the markers at the same places, so they show with the
  // first tiles rather than after them. It goes as soon as MapKit draws its own.
  let standin: HTMLElement | null = document.createElement("div");
  standin.className = "mk-standin";
  standin.setAttribute("aria-hidden", "true");
  el.appendChild(standin);
  const standins = new Map<string, { at: LatLon; node: HTMLElement; drawn: HTMLElement }[]>();
  let placedFor = "";
  const placeStandins = () => {
    if (!standin) return;
    if ([...standins.values()].some((list) => list.some((s) => s.drawn.isConnected))) {
      standin.remove();
      standin = null;
      return;
    }
    const r = map.region;
    const key = r ? `${r.center.latitude},${r.center.longitude},${r.span.latitudeDelta},${r.span.longitudeDelta},${el.clientWidth}x${el.clientHeight}` : "";
    if (key !== placedFor) {
      placedFor = key;
      const box = el.getBoundingClientRect();
      for (const list of standins.values()) {
        for (const s of list) {
          const p = map.convertCoordinateToPointOnPage(coord(s.at));
          s.node.style.transform = `translate(${p.x - box.left - window.scrollX}px, ${p.y - box.top - window.scrollY}px)`;
        }
      }
    }
    requestAnimationFrame(placeStandins);
  };
  const standIn = (layer: string, list: { at: LatLon; node: HTMLElement }[]) => {
    if (!standin) return;
    for (const s of standins.get(layer) ?? []) s.node.remove();
    const copies = list.map(({ at, node }) => {
      const copy = node.cloneNode(true) as HTMLElement;
      copy.querySelectorAll("[tabindex], [title], [role], [aria-label]").forEach((n) => {
        for (const a of ["tabindex", "title", "role", "aria-label"]) n.removeAttribute(a);
      });
      standin?.appendChild(copy);
      return { at, node: copy, drawn: node };
    });
    standins.set(layer, copies);
    placedFor = "";
  };
  requestAnimationFrame(placeStandins);
  const clear = (layer: { overlays: unknown[]; annotations: unknown[] }) => {
    if (layer.overlays.length) map.removeOverlays(layer.overlays);
    if (layer.annotations.length) map.removeAnnotations(layer.annotations);
  };

  return {
    kind: "mapkit",
    setBasemap(name: BasemapName | "none", dark) {
      const T = mapkit.Map.MapTypes;
      // Satellite carries place names (Hybrid). Apple Maps has no terrain map, so anything else is Standard.
      map.mapType = name === "satellite" ? T.Hybrid : T.Standard;
      map.colorScheme = dark ? mapkit.Map.ColorSchemes.Dark : mapkit.Map.ColorSchemes.Light;
    },
    drawNetwork(lines: LineSpec[], points: PointSpec[]) {
      clear(network);
      closeCard();
      const overlays: unknown[] = [];
      for (const l of lines) {
        const shapes = (color: string, weight: number, opacity: number, dash: number[], popup?: () => HTMLElement) => {
          const s = style({ strokeColor: color, lineWidth: weight, strokeOpacity: opacity, lineDash: dash,
                            fillColor: color, fillOpacity: l.closed && popup ? 0.2 : 0 });
          const opts = { style: s, enabled: !!popup, data: popup ? { popup } : undefined };
          return l.closed
            ? [new mapkit.PolygonOverlay(l.paths.map((p) => p.map(coord)), opts)]
            : l.paths.map((p) => new mapkit.PolylineOverlay(p.map(coord), opts));
        };
        if (l.halo) overlays.push(...shapes(l.halo.color, l.halo.weight, 0.9, []));
        overlays.push(...shapes(l.color, l.weight, l.opacity, DASH(l.dash), l.popup));
      }
      // Squares (platforms) after dots (wells): later annotations draw on top.
      const ordered = [...points.filter((p) => p.shape !== "square"), ...points.filter((p) => p.shape === "square")];
      const marks = ordered.map((p) => {
        const dot = document.createElement("div");
        const d = 2 * p.radius + p.strokeWidth;
        dot.className = p.shape === "square" ? "mk-point mk-square" : "mk-point";
        Object.assign(dot.style, { width: `${d}px`, height: `${d}px`, background: p.fill, borderColor: p.stroke,
                                   borderWidth: `${p.strokeWidth}px`, opacity: String(p.fillOpacity) });
        if (p.tip) dot.title = p.tip;
        dot.addEventListener("click", (e) => { e.stopPropagation(); openCard(p.popup()); });
        const node = anchored(dot, -d / 2, -d / 2);
        return { at: p.at, node };
      });
      standIn("network", marks);
      const annotations = marks.map(({ at, node }) =>
        new mapkit.Annotation(coord(at), () => node, { calloutEnabled: false, animates: false }));
      map.addOverlays(overlays);
      map.addAnnotations(annotations);
      network = { overlays, annotations };
    },
    drawHotspots(list: HotspotSpec[]) {
      if (spots.length) map.removeAnnotations(spots);
      // DOM order is stacking order: the chosen place last, on top.
      const ordered = [...list.filter((s) => !s.selected), ...list.filter((s) => s.selected)];
      const marks: { at: LatLon; node: HTMLElement }[] = [];
      spots = ordered.flatMap((s) => {
        const out: unknown[] = [];
        const dot = document.createElement("div");
        dot.className = `mk-hotspot ${s.className}`;
        Object.assign(dot.style, { width: `${s.size}px`, height: `${s.size}px` });
        dot.innerHTML = s.html;
        dot.title = s.title;
        dot.tabIndex = 0;
        dot.setAttribute("role", "button");
        dot.setAttribute("aria-label", s.title);
        dot.addEventListener("click", (e) => { e.stopPropagation(); s.onSelect(); });
        dot.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") { e.preventDefault(); s.onSelect(); }
        });
        const node = anchored(dot, -s.size / 2, -s.size / 2);
        marks.push({ at: s.at, node });
        out.push(new mapkit.Annotation(coord(s.at), () => node, { calloutEnabled: false, animates: false, title: s.name }));
        if (s.crew) {
          const crew = document.createElement("div");
          crew.className = "mk-crew hotspot-crew";
          crew.innerHTML = s.crew.html;
          crew.title = s.crew.title;
          const badge = anchored(crew, -11, -30);
          marks.push({ at: s.at, node: badge });
          out.push(new mapkit.Annotation(coord(s.at), () => badge,
                                         { calloutEnabled: false, animates: false, enabled: false }));
        }
        return out;
      });
      standIn("spots", marks);
      map.addAnnotations(spots);
    },
    fit(coords: LatLon[], padding, maxZoom) {
      if (!coords.length) return;
      const lats = coords.map((c) => c[0]), lons = coords.map((c) => c[1]);
      const n = Math.max(...lats), s = Math.min(...lats), e = Math.max(...lons), w = Math.min(...lons);
      const W = el.clientWidth || 600, H = el.clientHeight || 400;
      const grow = (px: number) => px / Math.max(1, px - 2 * padding);
      const min = spanForZoom(maxZoom);
      const lat = (n + s) / 2;
      const span = new mapkit.CoordinateSpan(Math.max((n - s) * grow(H), min.lat * Math.cos((lat * Math.PI) / 180)),
                                             Math.max((e - w) * grow(W), min.lon));
      map.setRegionAnimated(new mapkit.CoordinateRegion(new mapkit.Coordinate(lat, (e + w) / 2), span), false);
    },
    setInset(inset) { map.padding = new mapkit.Padding(inset.top, inset.right, inset.bottom, inset.left); },
    reveal(at) {
      const r = map.region;
      const inside = Math.abs(at[0] - r.center.latitude) < r.span.latitudeDelta * 0.4
        && Math.abs(at[1] - r.center.longitude) < r.span.longitudeDelta * 0.4;
      if (!inside) map.setCenterAnimated(coord(at), true);
    },
    highlight(at, label) {
      if (ring) map.removeAnnotation(ring);
      ring = null;
      if (!at) return;
      const mark = document.createElement("div");
      mark.className = "map-highlight";
      mark.innerHTML = "<span></span>";
      if (label) mark.title = label;
      ring = new mapkit.Annotation(coord(at), () => anchored(mark, -32, -32), { calloutEnabled: false, animates: false, enabled: false });
      map.addAnnotation(ring);
      this.reveal(at);
    },
    showLocation(at, accuracy, colors) {
      clear(location);
      const ring = new mapkit.CircleOverlay(coord(at), accuracy, {
        enabled: false, style: style({ strokeColor: colors.accent, fillColor: colors.accent, fillOpacity: 0.12, lineWidth: 1 }) });
      const dot = document.createElement("div");
      dot.className = "mk-point mk-location";
      Object.assign(dot.style, { width: "20px", height: "20px", background: colors.accent, borderColor: colors.panel,
                                 borderWidth: "3px" });
      dot.title = "Your location";
      const you = new mapkit.Annotation(coord(at), () => anchored(dot, -10, -10), { calloutEnabled: false, animates: false });
      map.addOverlay(ring);
      map.addAnnotation(you);
      location = { overlays: [ring], annotations: [you] };
      const street = spanForZoom(14);
      if (map.region.span.longitudeDelta > street.lon) {
        map.setRegionAnimated(new mapkit.CoordinateRegion(coord(at), new mapkit.CoordinateSpan(street.lat, street.lon)), true);
      } else {
        map.setCenterAnimated(coord(at), true);
      }
    },
    zoom: zoomNow,
    zoomBy(step) {
      const r = map.region, k = step > 0 ? 0.5 : 2;
      map.setRegionAnimated(new mapkit.CoordinateRegion(r.center,
        new mapkit.CoordinateSpan(Math.min(r.span.latitudeDelta * k, 170), Math.min(r.span.longitudeDelta * k, 359))), true);
    },
    project(at) {
      const p = map.convertCoordinateToPointOnPage(coord(at));
      const r = el.getBoundingClientRect();
      return { x: p.x - r.left - window.scrollX, y: p.y - r.top - window.scrollY };
    },
    setPan(on) { map.isScrollEnabled = on; },
    resize() { /* MapKit follows its element's size by itself */ },
    destroy() {
      unsubscribe();
      closeCard();
      standin?.remove();
      standin = null;
      el.removeEventListener("pointerdown", onPointer, { capture: true });
      map.destroy();
    },
  };
}
