/** Home is the field map (#1207): the map takes the page, layers on it carry open work, crews, incidents and the
 * network, the figures the person's seat watches sit over its top, and Needs you lists what waits on them, ringing on
 * the map the place each item is about. Home summarizes and links: every action stays on its owning page. */
import "../styles/home.css";
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { api, type AttentionPlace, type Stats } from "../api";
import { openReviews } from "../figures";
import { count } from "../format";
import { Status, href, useAsync, useLive } from "../lib";
import { usePerson } from "../work";
import { Button, Card as UICard, PageHeader, Stat } from "../ui";
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

export function HomePage({ stats }: {
  stats?: Stats;
  statsLoading: boolean;
  statsError?: string;
}) {
  const configure = usePermission("configure");
  const [person] = usePerson();
  const inbox = useAsync(() => person ? api.inbox(person) : Promise.resolve(null), [person]);
  const roles = inbox.data?.roles ?? [];
  const seat = seatOf(roles);
  const dispatcher = roles.includes("ops_lead");
  const steward = roles.includes("data_steward") || configure;
  const noRecords = stats?.records === 0;

  const spots = useAsync(() => api.attention(), []);
  const geo = useAsync(() => api.map(), []);
  const open = useAsync(() => (dispatcher ? api.exceptions({ state: "open" }) : Promise.resolve(null)), [dispatcher]);
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
  const incidents = useIncidents(layers.has("incidents"));
  const stage = useRef<HTMLDivElement>(null);
  const inset = useInset(stage);

  return (
    <section className="home home-map" data-seat={seat}>
      <PageHeader className="sr-only">Home</PageHeader>
      <div className="home-stage" ref={stage}>
        {network && (
          <Suspense fallback={<div className="map" />}>
            <MapView data={network} hotspots={places} selected={selected} onSelect={setSelected} crews={layers.has("crews")}
                     legend={false} height="100%" marks={incidents} highlight={ring} inset={inset} />
          </Suspense>
        )}
        <div className="home-over">
          <Figures seat={seat} dispatcher={dispatcher} stats={stats} open={open.data ?? undefined}
                   work={spots.data ? { open: spots.data.open, places: spots.data.places.length, unplaced: spots.data.unplaced } : undefined} />
          <div className="home-layers chips" role="group" aria-label="Map layers">
            {LAYERS.map((l) => (
              <Button key={l.key} className={layers.has(l.key) ? "chip on" : "chip"} aria-pressed={layers.has(l.key)}
                      onClick={() => toggle(l.key)}>{l.label}</Button>
            ))}
            {layers.has("work") && <span className="home-layer-filter" role="group" aria-label="Show places with">
              {FILTERS.map((f) => (
                <Button key={f.key} className={filter === f.key ? "chip on" : "chip"} aria-pressed={filter === f.key}
                        onClick={() => setFilter(f.key)}>{f.label}</Button>
              ))}
            </span>}
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
        <NeedsYou inbox={inbox} exceptions={dispatcher ? (open.error ? [] : open.data ?? undefined) : null} steward={steward} configure={configure} onRing={setRing}
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
      const s = el.getBoundingClientRect();
      const over = el.querySelector(".home-over")?.getBoundingClientRect();
      const feed = el.querySelector(".home-feed")?.getBoundingClientRect();
      const sheet = !!feed && feed.left - s.left < 20;  // a phone's sheet across the bottom, else a panel down the right
      const next = { top: over ? Math.round(over.bottom - s.top) : 0, left: 0,
                     // beside the feed panel, else clear of the map's own buttons down the right edge
                     right: feed && !sheet ? Math.round(s.right - feed.left) : 56,
                     // an open sheet covers the map until it closes, so the map keeps clear of its header only
                     bottom: feed && sheet ? (el.querySelector<HTMLElement>(".home-feed-head")?.offsetHeight ?? 0) : 0 };
      setInset((was) => was && JSON.stringify(was) === JSON.stringify(next) ? was : next);
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    el.querySelectorAll(".home-over, .home-feed").forEach((n) => ro.observe(n));
    return () => ro.disconnect();
  }, [stage]);
  return inset;
}

/** The figures over the map: counts that link to the view showing the same number, and the seat's metrics with their
 * trend. Each reads the field its owning page reads. */
function Figures({ seat, dispatcher, stats, open, work }: {
  seat: Seat; dispatcher: boolean; stats?: Stats;
  open?: Parameters<typeof iocCounts>[0]; work?: { open: number; places: number; unplaced: number };
}) {
  const people = useAsync(() => (dispatcher ? api.workPeople() : Promise.resolve([])), [dispatcher]);
  const operators = new Set((people.data ?? []).filter((p) => p.roles.includes("operator")).map((p) => p.name));
  const c = dispatcher && open && people.data ? iocCounts(open, operators) : null;
  const reviews = seat === "steward" && stats ? openReviews(stats) : null;
  return (
    <div className="home-figures" aria-label="Key figures">
      {c && <>
        <Tile to={workHref("dispatch")} label="To dispatch" value={count(c.dispatch)} late={c.dispatch > 0} />
        <Tile to={workHref("field")} label="With the field" value={count(c.field)} />
        <Tile to={workHref("back")} label="Back from the field" value={count(c.back)} late={c.back > 0} />
      </>}
      {work && seat !== "operator" && <Tile to={workHref("open")} label="Open work" value={count(work.open)}
        detail={`at ${count(work.places)} ${work.places === 1 ? "place" : "places"}${work.unplaced ? `, ${count(work.unplaced)} not located` : ""}`} />}
      {reviews !== null && <Tile to={href("/masterdata", { tab: "questions" })} label="Records to review" value={count(reviews)} />}
      {METRICS[seat].map((key) => <MetricTile key={key} metric={key} />)}
    </div>
  );
}

function Tile({ to, label, value, detail, late }: { to: string; label: string; value: string; detail?: string; late?: boolean }) {
  return <div className="home-figure"><Stat href={to} label={label} value={value} detail={detail} tone={late ? "danger" : undefined} /></div>;
}

/** A registered metric with its last few periods; left out while it has no value (no reports loaded for it). */
function MetricTile({ metric }: { metric: string }) {
  const m = useAsync(() => api.metric(metric, null, true), [metric]);
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
function useIncidents(on: boolean): HotspotSpec[] | undefined {
  const list = useAsync(() => (on ? api.incidents() : Promise.resolve(null)), [on]);
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
