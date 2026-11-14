/** Where attention is needed (Home): the places with open work, on the field map and as a ranked list. The list is
 * the reading; the map says where. A plant with no located assets gets the list alone. Read only: dispatch and
 * every edit stay on All work and the case pages. */
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type TouchEvent } from "react";
import { api, type AttentionBand, type AttentionItem, type AttentionPlace } from "../api";
import { Badge, Button, ButtonLink, Card, Empty } from "../ui";
import { Status, entityHref, href, useAsync } from "../lib";
import { workHref } from "./AllWork";

const MapView = lazy(() => import("../MapView").then((m) => ({ default: m.MapView })));  // Leaflet loads with the map

type Filter = "all" | "high" | "overdue" | "unassigned";
const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All open" }, { key: "high", label: "High" },
  { key: "overdue", label: "Overdue" }, { key: "unassigned", label: "Unassigned" },
];
const BAND: Record<AttentionBand, { label: string; tone: "danger" | "warn" | "muted" }> = {
  high: { label: "High", tone: "danger" }, medium: { label: "Medium", tone: "warn" }, low: { label: "Low", tone: "muted" },
};
const keep = (p: AttentionPlace, f: Filter) =>
  f === "all" || (f === "high" ? p.counts.high > 0 : f === "overdue" ? p.overdue > 0 : p.unassigned > 0);
const placeName = (p: AttentionPlace) => p.name ?? p.label;
const holder = (i: AttentionItem) => i.assignee ?? (i.role ? `${i.role.replace(/_/g, " ")} queue` : "Unassigned");

export function AttentionCard() {
  const spots = useAsync(() => api.attention(), []);
  const geo = useAsync(() => api.map(), []);
  const [filter, setFilter] = useState<Filter>("all");
  const [crews, setCrews] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const places = useMemo(() => (spots.data?.places ?? []).filter((p) => keep(p, filter)), [spots.data, filter]);
  const chosen = places.find((p) => p.id === selected) ?? null;
  const close = useCallback(() => setSelected(null), []);
  const [fullscreen, setFullscreen] = useState(false);  // the map full screen carries the chosen place itself
  useEffect(() => { if (selected && !chosen) setSelected(null); }, [selected, chosen]);
  const mapped = (geo.data?.features.length ?? 0) > 0;
  const open = spots.data?.places.reduce((n, p) => n + p.open, 0) ?? 0;
  return (
    <Card as="section" className="home-card attention-card" title="Where attention is needed"
          actions={<a href={workHref("open")}>All work</a>}>
      <Status loading={spots.loading && !spots.data} error={spots.error} onRetry={spots.reload}>
        Loading open work.
      </Status>
      {spots.data && <>
        <div className="row spread wrap attention-bar">
          <div className="chips" role="group" aria-label="Show places with">
            {FILTERS.map((f) => (
              <Button key={f.key} className={filter === f.key ? "chip on" : "chip"} aria-pressed={filter === f.key}
                      onClick={() => setFilter(f.key)}>{f.label}</Button>
            ))}
            {mapped && <Button className={crews ? "chip on" : "chip"} aria-pressed={crews} onClick={() => setCrews(!crews)}>
              Crews</Button>}
          </div>
          <span className="small muted">
            {open.toLocaleString()} open across {spots.data.places.length.toLocaleString()} places
            {spots.data.unplaced ? ` · ${spots.data.unplaced} not located` : ""}
          </span>
        </div>
        {spots.data.places.length === 0 ? <Empty>No open work at any place.</Empty> : (
          <div className={mapped ? "attention-split" : ""}>
            {mapped && geo.data && (
              <Suspense fallback={<div className="map" />}>
                <MapView data={geo.data} hotspots={places} selected={selected} onSelect={setSelected} crews={crews}
                         compact height="460px" onFullscreen={setFullscreen}
                         overlay={chosen && <PlaceDetail place={chosen} onClose={close} />} />
              </Suspense>
            )}
            <div className="attention-side">
              {chosen && !fullscreen && <PlaceDetail place={chosen} onClose={close} />}
              {places.length === 0 ? <Empty>No places match this filter.</Empty> : (
                <ol className="attention-list" aria-label="Places by urgency">
                  {places.slice(0, 8).map((p) => (
                    <li key={p.id} className={p.id === selected ? "selected" : ""}>
                      <Button variant="ghost" className="attention-row" aria-pressed={p.id === selected}
                              onClick={() => setSelected(p.id === selected ? null : p.id)}>
                        <span className="attention-row-head">
                          <strong>{placeName(p)}</strong>
                          <Badge tone={BAND[p.band].tone}>{BAND[p.band].label}</Badge>
                        </span>
                        <span className="small">{p.open} open · {p.top[0].title}</span>
                        <span className="small attention-flags">
                          {p.overdue > 0 && <Badge tone="danger">{p.overdue} overdue</Badge>}
                          {p.unassigned > 0 && <Badge>{p.unassigned} unassigned</Badge>}
                          {p.crews[0] && <Badge tone="ok">{p.crews[0].who} {p.crews[0].status}</Badge>}
                        </span>
                      </Button>
                    </li>
                  ))}
                </ol>
              )}
              {places.length > 8 && <a href={workHref("open")}>{places.length - 8} more places in All work</a>}
            </div>
          </div>
        )}
      </>}
    </Card>
  );
}

/** One place: its most urgent items and where to take them, a bottom sheet on a phone. It closes from its X, Escape, a
 * tap anywhere outside it (a drag of the map is not a tap) or, on a phone, a swipe down. */
function PlaceDetail({ place, onClose }: { place: AttentionPlace; onClose: () => void }) {
  const sheet = useRef<HTMLDivElement>(null);
  const swipe = useRef<number | null>(null);
  const [drag, setDrag] = useState(0);
  useEffect(() => {
    let down: { x: number; y: number } | null = null;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();  // the first Escape closes the place; a full-screen map stays until the next one
      onClose();
    };
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; };
    const onUp = (e: PointerEvent) => {
      const target = e.target as Element | null;
      const tap = down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 8;
      down = null;
      // Another place's marker or row picks that place instead; anything else outside the sheet closes it.
      if (!tap || !target || sheet.current?.contains(target) || target.closest(".hotspot, .attention-row")) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("pointerup", onUp);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("pointerup", onUp);
    };
  }, [onClose]);
  useEffect(() => {  // on a phone the sheet covers the list, so bring the map up where the chosen place shows
    if (matchMedia("(max-width: 600px)").matches && !sheet.current?.closest(".map-overlay")) {
      sheet.current?.closest(".attention-card")?.querySelector(".mapview")?.scrollIntoView({ block: "start", behavior: "smooth" });
    }
  }, [place.id]);
  useEffect(() => { sheet.current?.style.setProperty("--drag", `${drag}px`); }, [drag]);  // the sheet follows the finger
  const touch = {
    onTouchStart: (e: TouchEvent<HTMLDivElement>) => { swipe.current = e.touches[0].clientY; },
    onTouchMove: (e: TouchEvent<HTMLDivElement>) => { if (swipe.current !== null) setDrag(Math.max(0, e.touches[0].clientY - swipe.current)); },
    onTouchEnd: () => { if (drag > 70) onClose(); swipe.current = null; setDrag(0); },
  };
  return (
    <div ref={sheet} className={`attention-detail${drag ? " dragging" : ""}`} role="region" aria-label={placeName(place)}>
      <div className="attention-detail-head" {...touch}>
        <span className="attention-grab" aria-hidden="true" />
        <div className="attention-detail-title">
          <h3>{placeName(place)}</h3>
          <span className="small muted">
            {place.open} open · {place.overdue} overdue · {place.unassigned} unassigned · <span className="mono">{place.label}</span>
          </span>
        </div>
        <Button variant="secondary" className="attention-close" onClick={onClose} aria-label="Close" title="Close">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M6 6l12 12M18 6 6 18" />
          </svg>
        </Button>
      </div>
      <ul className="attention-items">
        {place.top.map((i) => (
          <li key={i.id}>
            <Badge tone={BAND[i.band].tone}>{BAND[i.band].label}</Badge>
            <div>
              <a href={i.item_id ? href(`/cases/${i.item_id}`) : workHref("open", { q: i.title })}>{i.title}</a>
              {i.overdue && <> <Badge tone="danger">overdue</Badge></>}
              <div className="small muted">{i.label} · {i.crew ? `${i.crew.who} ${i.crew.status}` : holder(i)}</div>
            </div>
          </li>
        ))}
      </ul>
      <div className="row wrap">
        <ButtonLink variant="primary" href={workHref("open", { at: place.places.join(",") })}>Open {place.open} in All work</ButtonLink>
        <a href={entityHref(place.id)}>Asset page</a>
      </div>
    </div>
  );
}
