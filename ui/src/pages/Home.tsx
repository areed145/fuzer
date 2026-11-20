/** Home is the field map (#1208): the map takes the page, layers on it carry open work, crews, incidents and the
 * network, the figures the person's seat watches sit over its top, and Needs you lists what waits on them, ringing on
 * the map the place each item is about. Home summarizes and links: every action stays on its owning page. */
import "../styles/home.css";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { api, type AttentionPlace, type JobsHealth, type Stats, type WorkInbox } from "../api";
import { openReviews } from "../figures";
import { count } from "../format";
import { Status, afterMap, href, useAsync, useLive } from "../lib";
import { Button, Card as UICard, PageHeader, Scroller, Stat } from "../ui";
import { usePermission } from "../Auth";
import { Sparkline, show } from "../analytics/Metric";
import { LiveFigure } from "../analytics/LiveFigure";
import type { HotspotSpec, Inset } from "../map/engine";
import { iocCounts, workHref } from "./AllWork";
import { FILTERS, PlaceDetail, keep, type Filter } from "./HomeAttention";
import { NeedsYou, type Ring } from "./HomeFeed";

const MapView = lazy(() => import("../MapView").then((m) => ({ default: m.MapView })));  // Leaflet loads with the map

/** Whose Home this is (#1074): which figures lead. Presentation only: it changes nothing a person may see or do. */
export type Seat = "dispatcher" | "operator" | "manager" | "steward" | "default";
export function seatOf(roles: string[]): Seat {
  if (roles.includes("ops_lead")) return "dispatcher";
  if (roles.includes("operator")) return "operator";
  if (roles.some((r) => r === "production_engineer" || r === "engineer")) return "manager";
  if (roles.includes("data_steward")) return "steward";
  return "default";
}
/** The registered metrics each seat watches over the map, each read from /metrics as Metrics shows it. */
const METRICS: Record<Seat, string[]> = {
  dispatcher: ["work.sla_misses"],
  operator: [],
  manager: ["production.efficiency", "maintenance.urgent_overdue", "work.sla_misses"],
  steward: ["sources.completeness", "context.completeness"],
  default: ["production.efficiency", "work.sla_misses"],
};

type Layer = "work" | "crews" | "incidents" | "network" | "charts";
const LAYERS: { key: Layer; label: string }[] = [
  { key: "work", label: "Open work" }, { key: "crews", label: "Crews" },
  { key: "incidents", label: "Incidents" }, { key: "network", label: "Network" }, { key: "charts", label: "Charts" },
];
/** The server-built production figures the Charts layer shows over the map. */
const HOME_FIGURES = [
  { kind: "production-efficiency", title: "Production efficiency" },
  { kind: "flow-capacity", title: "System capacity and bottleneck" },
];
const LAYERS_KEY = "fuzer.home.layers";
function savedLayers(): Set<Layer> {
  try {
    const v = JSON.parse(localStorage.getItem(LAYERS_KEY) ?? "null");
    if (Array.isArray(v)) return new Set(v.filter((k) => LAYERS.some((l) => l.key === k)));
  } catch { /* storage blocked: the defaults */ }
  return new Set(["work", "crews", "incidents", "network"]);
}

export function HomePage({ stats, inbox, jobsHealth }: {
  stats?: Stats;
  statsLoading: boolean;
  statsError?: string;
  /** The shell's reads, shared so Home doesn't ask again and the My work tab's count moves with Home's. */
  inbox: { data?: WorkInbox | null; error?: string; reload: () => void };
  jobsHealth: { data?: JobsHealth | null; error?: string };
}) {
  const configure = usePermission("configure");
  const roles = inbox.data?.roles ?? [];
  const seat = seatOf(roles);
  const dispatcher = roles.includes("ops_lead");
  const steward = roles.includes("data_steward") || configure;
  const noRecords = stats?.records === 0;

  // The map and its open work first; the rest waits for the map, since the server answers reads largely one at a time.
  const geo = useAsync(() => api.map(), []);
  const spots = useAsync(() => api.attention(), []);
  const ready = geo.data !== undefined || !!geo.error;
  const open = useAsync(() => (dispatcher ? afterMap(ready, () => api.exceptions({ state: "open" })) : Promise.resolve(null)), [dispatcher, ready]);
  useLive([inbox.reload, spots.reload, open.reload]);

  const [layers, setLayers] = useState(savedLayers);
  const toggle = (k: Layer) => setLayers((s) => {
    const n = new Set(s);
    if (n.has(k)) n.delete(k); else n.add(k);
    try { localStorage.setItem(LAYERS_KEY, JSON.stringify([...n])); } catch { /* the choice lasts this visit */ }
    return n;
  });
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<string | null>(null);
  const [ring, setRing] = useState<Ring>(null);
  const [sheet, setSheet] = useState(false);  // the feed as a phone's sheet: open, or only its header
  const places = useMemo(() => layers.has("work") ? (spots.data?.places ?? []).filter((p) => keep(p, filter)) : [],
                         [spots.data, filter, layers]);
  const chosen = (spots.data?.places ?? []).find((p) => p.id === selected) ?? null;
  const close = useCallback(() => setSelected(null), []);
  useEffect(() => { if (chosen) setSheet(true); }, [chosen?.id]);  // eslint-disable-line react-hooks/exhaustive-deps
  const network = useMemo(() => geo.data && (layers.has("network") ? geo.data
    : { ...geo.data, features: geo.data.features.filter((f) => f.geometry.type === "Point" && f.properties.kind !== "well") }),
  [geo.data, layers]);
  const incidents = useIncidents(layers.has("incidents"), ready);
  const stage = useRef<HTMLDivElement>(null);
  const inset = useInset(stage);

  return (
    <section className="home home-map" data-seat={seat}>
      <PageHeader>Home</PageHeader>
      <div className="home-stage" ref={stage}>
        {network && (
          <Suspense fallback={<div className="map" />}>
            <MapView data={network} hotspots={places} selected={selected} onSelect={setSelected} crews={layers.has("crews")}
                     legend={false} height="100%" marks={incidents} highlight={ring} inset={inset} />
          </Suspense>
        )}
        <div className="home-over">
          <div className="home-top">
            <Figures seat={seat} dispatcher={dispatcher} stats={stats} ready={ready} open={open.data ?? undefined}
                     work={spots.data ? { open: spots.data.open, places: spots.data.places.length, unplaced: spots.data.unplaced } : undefined} />
            <div className="home-layers chips" role="group" aria-label="Map layers">
              {LAYERS.map((l) => (
                <Button key={l.key} className={layers.has(l.key) ? "chip on" : "chip"} aria-pressed={layers.has(l.key)}
                        onClick={() => toggle(l.key)}>{l.label}</Button>
              ))}
            </div>
            {layers.has("work") && <div className="home-layers chips" role="group" aria-label="Show places with">
              {FILTERS.map((f) => (
                <Button key={f.key} className={filter === f.key ? "chip on" : "chip"} aria-pressed={filter === f.key}
                        onClick={() => setFilter(f.key)}>{f.label}</Button>
              ))}
            </div>}
          </div>
          {spots.error && <div className="home-notice"><Status error={spots.error} onRetry={spots.reload} /></div>}
          {noRecords && <UICard className="home-notice" title="Connect data before exploring">
            <p>No source records have been loaded. Add a source, map its fields and load it to build the facility register.</p>
            {configure ? <a href={href("/sources", { view: "add" })}>Open source setup</a>
              : <p>Ask an administrator to load the demonstration records.</p>}
          </UICard>}
          {layers.has("charts") && !noRecords && <div className="home-charts">
            {HOME_FIGURES.map(({ kind, title }) => <LiveFigure key={kind} kind={kind} hideEmpty
              frame={(body) => <UICard title={title} className="home-card">{body}</UICard>} />)}
          </div>}
        </div>
        <NeedsYou inbox={inbox} jobsHealth={jobsHealth} ready={ready} exceptions={dispatcher ? (open.error ? [] : open.data ?? undefined) : null} steward={steward} configure={configure} onRing={setRing}
                  open={sheet} onToggle={() => setSheet(!sheet)}
                  top={chosen && <PlaceDetail place={chosen} onClose={close} />} />
      </div>
    </section>
  );
}

/** How far the figures and the feed reach over the map's edges, so the map fits the work into the part left open. */
function useInset(stage: RefObject<HTMLDivElement | null>): Inset | undefined {
  const [inset, setInset] = useState<Inset>();
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const measure = () => {
      // On a phone the map fills the screen under the shell's bars; the panels over it keep clear of them.
      const top = document.querySelector(".topbar")?.getBoundingClientRect();
      const tabs = document.querySelector(".bottombar")?.getBoundingClientRect();
      // Wider, the map runs under the left menu too, and the panels start beside it.
      const side = window.matchMedia("(min-width: 901px)").matches ? document.querySelector(".sidebar")?.getBoundingClientRect() : undefined;
      const left = side?.width ? Math.round(side.right) : 0;
      el.style.setProperty("--bar-left", `${left}px`);
      el.style.setProperty("--bar-top", `${top?.height ? Math.round(top.bottom) : 0}px`);
      el.style.setProperty("--bar-bottom", `${tabs?.height ? Math.round(window.innerHeight - tabs.top) : 0}px`);
      const s = el.getBoundingClientRect();
      const over = el.querySelector(".home-top")?.getBoundingClientRect();
      const feed = el.querySelector(".home-feed")?.getBoundingClientRect();
      const sheet = !!feed && feed.left - s.left < 20;  // a phone's sheet across the bottom, else a panel down the right
      const next = { top: over ? Math.round(over.bottom - s.top) : 0, left,
                     // beside the feed panel, else clear of the map's own buttons down the right edge
                     right: feed && !sheet ? Math.round(s.right - feed.left) : 56,
                     // an open sheet covers the map until it closes, so the map keeps clear of its header only
                     bottom: feed && sheet ? Math.round(s.bottom - feed.bottom) + (el.querySelector<HTMLElement>(".home-feed-head")?.offsetHeight ?? 0) : 0 };
      setInset((was) => was && JSON.stringify(was) === JSON.stringify(next) ? was : next);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    el.querySelectorAll(".home-top, .home-feed").forEach((n) => ro.observe(n));
    document.querySelectorAll(".topbar, .bottombar, .sidebar").forEach((n) => ro.observe(n));
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); window.removeEventListener("resize", measure); };
  }, [stage]);
  return inset;
}

/** The figures over the map: counts that link to the view showing the same number, and the seat's metrics with their
 * trend. Each reads the field its owning page reads. */
function Figures({ seat, dispatcher, stats, open, work, ready }: {
  seat: Seat; dispatcher: boolean; stats?: Stats; ready: boolean;
  open?: Parameters<typeof iocCounts>[0]; work?: { open: number; places: number; unplaced: number };
}) {
  const people = useAsync(() => (dispatcher ? afterMap(ready, () => api.workPeople()) : Promise.resolve([])), [dispatcher, ready]);
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const c = dispatcher && open && people.data ? iocCounts(open, operators) : null;
  const reviews = seat === "steward" && stats ? openReviews(stats) : null;
  return (
    <Scroller className="home-figures" label="Key figures">
      {c && <>
        <Tile to={workHref("dispatch")} label="To dispatch" value={count(c.dispatch)} late={c.dispatch > 0} />
        <Tile to={workHref("field")} label="With the field" value={count(c.field)} />
        <Tile to={workHref("back")} label="Back from the field" value={count(c.back)} late={c.back > 0} />
      </>}
      {work && seat !== "operator" && <Tile to={workHref("open")} label="Open work" value={count(work.open)}
        detail={`at ${count(work.places)} ${work.places === 1 ? "place" : "places"}${work.unplaced ? `, ${count(work.unplaced)} not located` : ""}`} />}
      {reviews !== null && <Tile to={href("/masterdata", { tab: "questions" })} label="Records to review" value={count(reviews)} />}
      {METRICS[seat].map((key) => <MetricTile key={key} metric={key} ready={ready} />)}
    </Scroller>
  );
}

function Tile({ to, label, value, detail, late }: { to: string; label: string; value: string; detail?: string; late?: boolean }) {
  return <div className="home-figure"><Stat href={to} label={label} value={value} detail={detail} tone={late ? "danger" : undefined} /></div>;
}

/** A registered metric with its last few periods; left out while it has no value (no reports loaded for it). */
function MetricTile({ metric, ready }: { metric: string; ready: boolean }) {
  const m = useAsync(() => afterMap(ready, () => api.metric(metric, null, true)), [metric, ready]);
  const v = m.data;
  if (!v || v.value == null) return null;
  return (
    <div className="home-figure">
      <Stat href={href(`/metrics/${encodeURIComponent(metric)}`)} label={v.label} value={show(v.value, v.unit)}
            detail={v.history && v.history.length > 1 ? <Sparkline points={v.history} target={v.target} label={v.label} /> : v.as_of ? `as of ${v.as_of}` : undefined} />
    </div>
  );
}

/** The incidents layer: recorded incidents gathered at the place each is about, numbered by how many, the
 * title the latest. A tap opens the latest one. */
function useIncidents(on: boolean, ready: boolean): HotspotSpec[] | undefined {
  const list = useAsync(() => (on ? afterMap(ready, () => api.incidents()) : Promise.resolve(null)), [on, ready]);
  const ids = (list.data?.items ?? []).map((i) => i.id);
  const where = useAsync(() => (ids.length ? api.attentionPlaces(ids) : Promise.resolve({})), [ids.join(",")]);
  return useMemo(() => {
    if (!on || !list.data || !where.data) return undefined;
    const at = new Map<string, { place: { lat: number; lon: number; name: string }; items: typeof list.data.items }>();
    for (const i of list.data.items) {
      const p = (where.data as Record<string, { id: string; lat: number; lon: number; name: string | null; label: string }>)[i.id];
      if (!p) continue;
      const spot = at.get(p.id) ?? { place: { lat: p.lat, lon: p.lon, name: p.name ?? p.label }, items: [] };
      spot.items.push(i);
      at.set(p.id, spot);
    }
    return [...at.values()].map(({ place, items }) => {
      const latest = [...items].sort((a, b) => b.start.localeCompare(a.start))[0];
      return {
        at: [place.lat, place.lon], className: "incident-mark", size: 22, selected: false, name: `${place.name}: incidents`,
        html: `<span class="incident-dot"><b>${items.length}</b></span>`,
        title: `${place.name}: ${items.length} ${items.length === 1 ? "incident" : "incidents"}. Latest ${latest.start}: ${latest.description}`,
        onSelect: () => { window.location.hash = href(`/e/${encodeURIComponent(latest.id)}`).replace(/^#/, ""); },
      } satisfies HotspotSpec;
    });
  }, [on, list.data, where.data]);
}

export type { AttentionPlace };
