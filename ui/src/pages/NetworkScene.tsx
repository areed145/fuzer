import { lazy, Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type RelationDef, type Scene, type SceneLink, type SceneStatus, type SceneTrip } from "../api";
import { commonName } from "../entityRefs";
import { GraphCanvas } from "../graph";
import { ALL_RELS, PRODUCTS, linkColor } from "../kinds";
import { entityHref, href, Status, useAsync } from "../lib";
import type { LabelBy, SceneLook } from "../Scene3D";
import { MOVED, badge, headers, parseMove, reroute, trace, worst, type StatusKind } from "../sceneLogic";
import { THEME_CHANGED } from "../theme";
import { Button, ButtonLink, Card as UICard, Empty, Notice, Select, Table } from "../ui";
import { NodePanel } from "./Network";

// three.js arrives with this view and no other (P5.16a).
const Scene3D = lazy(() => import("../Scene3D"));

const words = (s: string) => s.replace(/_/g, " ");
const FLOW_PRODUCT = { flows_to: true, lift_gas_to: true, relieves_to: true }; // the link types a product is worked out for
const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
// The new run of a rerouted well takes the accent, so it reads apart from every product colour.
const lineColor = (l: SceneLink) => (l.id === MOVED ? cssVar("--accent") : linkColor(l.rel, l.product));
const STATUS: Record<StatusKind, { token: string; label: string }> = {
  down: { token: "--status-down", label: "Down now" },
  alarm: { token: "--status-alarm", label: "Alarm sounding" },
  work: { token: "--status-work", label: "Open work" },
};
const plural = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;
const quantity = (q: string) => words(q.replace(/_rate$/, ""));
type ItemStatus = SceneStatus["status"][string];
const named = (label: string, description: string | null | undefined) => {
  const name = commonName(label, description);
  return name ? `${label} ${name}` : label;
};

/** The Network page's 3D view: a facility or an area as generic shapes, laid out from its links on the server.
 * What it shows (`at`) and the picked item (`node`) live in the address, so a view can be shared and Back returns. */
export function NetworkScene({ query, set, relDef }: {
  query: URLSearchParams;
  set: (values: Record<string, string | undefined>) => void;
  relDef: (name: string) => RelationDef | undefined;
}) {
  const at = query.get("at") ?? undefined;
  const picked = query.get("node");
  const labelBy: LabelBy = query.get("labels") === "tags" ? "tags" : "names";
  const geo = query.get("layout") === "geo";
  const asOf = query.get("as_of") ?? undefined; // a case's time: its links as they were then (P5.16c)
  const scene = useAsync(() => api.scene(at, undefined, geo ? "geo" : undefined, asOf), [at, geo, asOf]);
  // Status for every item, in one read beside the drawing so the shapes don't wait for the historian (P5.16b).
  const status = useAsync(() => api.sceneStatus(at), [at]);
  const only = query.get("only") === "work";
  const path = query.get("path") === "up" ? "up" : query.get("path") === "down" ? "down" : null;
  const trip = query.get("trip") === "1";
  const move = parseMove(query.get("move"));
  const flat = query.get("draw") === "2d";
  const [theme, setTheme] = useState(0);
  useEffect(() => {
    const changed = () => setTheme((t) => t + 1);
    window.addEventListener(THEME_CHANGED, changed);
    return () => window.removeEventListener(THEME_CHANGED, changed);
  }, []);
  const [noWebGL, setNoWebGL] = useState(false);
  const [centre, setCentre] = useState<{ id: string } | null>(null);
  const s = scene.data;
  // An item asked for by its own link (from its asset page) starts picked.
  const selected = picked ?? s?.focus ?? null;
  const rels = useMemo(() => [...(s?.rels ?? [])].sort((a, b) =>
    (ALL_RELS.indexOf(a) + 1 || 999) - (ALL_RELS.indexOf(b) + 1 || 999) || a.localeCompare(b)), [s]);
  // The legend says what each colour of line is: a product where a line's is known, else its link type.
  const legend = useMemo(() => {
    const seen = new Map<string, { label: string; color: string; order: number }>();
    for (const l of s?.links ?? []) {
      const product = l.product && PRODUCTS[l.product] ? l.product : null;
      const key = product ? `p:${product}` : `r:${l.rel}`;
      if (seen.has(key)) continue;
      seen.set(key, product
        ? { label: PRODUCTS[product], color: lineColor(l), order: Object.keys(PRODUCTS).indexOf(product) }
        : { label: `${relDef(l.rel)?.label ?? words(l.rel)}${l.rel in FLOW_PRODUCT ? ", product not known" : ""}`, color: lineColor(l),
          order: 100 + rels.indexOf(l.rel) });
    }
    return [...seen.values()].sort((a, b) => a.order - b.order);
  }, [s, rels, relDef]);
  const item = s?.items.find((i) => i.id === selected);
  const st: Record<string, ItemStatus> = status.data?.status && !Array.isArray(status.data.status) ? status.data.status : {};
  const totals = status.data?.totals;
  const tripRead = useAsync(() => (trip && selected ? api.sceneTrip(selected) : Promise.resolve(null)), [trip, selected]);
  const routes = useMemo(() => (s ? headers(s.items, s.links) : null), [s]);
  const rerouted = useMemo(() => (s && move ? reroute(s.items, s.links, move) : null), [s, move?.well, move?.to]);
  const drawn = useMemo(() => (s && rerouted ? { ...s, links: [...s.links, rerouted.added as SceneLink] } : s), [s, rerouted]);
  const labelOf = (id: string) => s?.items.find((i) => i.id === id)?.label ?? id;
  // What stays lit: a reroute's old and new runs, else what a trip takes down, else the path picked, else open work.
  const lit = useMemo(() => {
    if (!s) return null;
    if (rerouted) return { items: new Set([rerouted.old.src, rerouted.old.dst, rerouted.added.dst]), links: new Set([rerouted.old.id, MOVED]) };
    const t = tripRead.data;
    if (trip && t) {
      const ids = new Set([t.item.id, ...[...t.units, ...t.behind, ...t.starved, ...t.wells].map((x) => x.id)]);
      return { items: ids, links: new Set(s.links.filter((l) => ids.has(l.src) && ids.has(l.dst)).map((l) => l.id)) };
    }
    if (path && selected) return trace(s.links, selected, path);
    if (only) return { items: new Set(Object.keys(st).filter((id) => st[id].open_work > 0)), links: new Set<number>() };
    return null;
  }, [s, rerouted, trip, tripRead.data, path, selected, only, st]);
  const look = useMemo<SceneLook>(() => {
    const tint = new Map<string, string>(), badges = new Map<string, { text: string; color: string; title: string }>();
    for (const [id, x] of Object.entries(st)) {
      const w = worst(x);
      if (!w) continue;
      const color = cssVar(STATUS[w].token);
      tint.set(id, color);
      badges.set(id, { text: badge(x), color, title: describe(x) });
    }
    return { tint, badges, lit };
  }, [st, lit, theme]);
  const places = s?.places ?? [];
  const facilities = places.filter((p) => p.kind === "facility");
  const under = (id: string) => places.filter((p) => p.parent === id);
  const select = useCallback((id: string | null) => set({ node: id ?? undefined }), [set]);
  const wellNow = item && routes?.now.get(item.id);

  // Under the drawing (and with it on the full screen): what the lines are, what the colours say, and the picked item.
  const footer = (pick: ReactNode) => (
    <>
      <div className="legend" aria-label="What the lines are">
        {legend.map((e) => (
          <span key={e.label}>
            <svg className="scene-line" width="18" height="6" aria-hidden="true"><line x1="1" y1="3" x2="17" y2="3" stroke={e.color} strokeWidth="4" strokeLinecap="round" /></svg>
            {e.label}
          </span>
        ))}
      </div>
      <div className="scene-status" aria-label="What the colours say">
        {(Object.keys(STATUS) as StatusKind[]).map((k) => (
          <span key={k}><span className={`swatch swatch-${k}`} />
            {STATUS[k].label}{totals ? ` (${totals[k === "work" ? "open_work" : k === "alarm" ? "alarms" : "down"]})` : ""}</span>
        ))}
        <Button size="sm" aria-pressed={only} onClick={() => set({ only: only ? undefined : "work" })}>Only items with open work</Button>
        <span className="small muted">
          {status.error ? "Status couldn't be read." : status.data ? `Alarms over the last ${status.data.alarm_hours} h.` : "Reading status…"}
        </span>
      </div>
      {pick}
    </>
  );

  return (
    <>
      <Status loading={scene.loading && !s} error={scene.error} onRetry={scene.reload} />
      {s && !s.root && <Empty>{s.basis === "not in an area" ? "This record is in no area, so there is nothing to draw around it."
        : "No facility or area is loaded yet."}</Empty>}
      {s?.root && (
        <>
          <div className="scene-controls">
            <label>
              Show
              <Select value={s.root.id} aria-label="Facility or area to draw"
                onChange={(e) => set({ at: e.target.value, node: undefined })}>
                {!places.some((p) => p.id === s.root!.id) && <option value={s.root.id}>{s.root.label}</option>}
                {facilities.map((f) => (
                  <optgroup key={f.id} label={f.label ?? f.id}>
                    <option value={f.id}>All of {f.label ?? f.id}</option>
                    {under(f.id).map((a) => <option key={a.id} value={a.id}>{named(a.label ?? a.id, a.description)}</option>)}
                  </optgroup>
                ))}
                {places.filter((p) => p.kind === "area" && !facilities.some((f) => f.id === p.parent))
                  .map((a) => <option key={a.id} value={a.id}>{named(a.label ?? a.id, a.description)}</option>)}
              </Select>
            </label>
            <label>
              Find
              <Select value={selected ?? ""} aria-label="Find an item in the drawing"
                onChange={(e) => { select(e.target.value || null); if (e.target.value) setCentre({ id: e.target.value }); }}>
                <option value="">an item ({s.counts.items})</option>
                {s.items.map((i) => <option key={i.id} value={i.id}>{named(i.label, i.description)}</option>)}
              </Select>
            </label>
            <label>
              Layout
              <Select value={geo ? "geo" : "schematic"} aria-label="Lay the drawing out"
                onChange={(e) => set({ layout: e.target.value === "geo" ? "geo" : undefined })}>
                <option value="schematic">Schematic</option>
                <option value="geo">By location, compacted</option>
              </Select>
            </label>
            <label>
              Labels
              <Select value={labelBy} aria-label="Label items by" onChange={(e) => set({ labels: e.target.value === "tags" ? "tags" : undefined })}>
                <option value="names">Names</option>
                <option value="tags">Tags</option>
              </Select>
            </label>
            <label>
              Drawing
              <Select value={flat || noWebGL ? "2d" : "3d"} aria-label="Draw in" disabled={noWebGL}
                onChange={(e) => set({ draw: e.target.value === "2d" ? "2d" : undefined })}>
                <option value="3d">3D</option>
                <option value="2d">Flat (2D)</option>
              </Select>
            </label>
            <span className="small muted" data-testid="scene-basis">
              {s.layout === "geo" ? "Areas where they are, long distances shortened"
                : geo ? "No area here has a location, so this is the schematic" : "Schematic, not to scale"}
              : {s.counts.items} items placed from {s.counts.links} links
              {s.counts.not_drawn > 0 && `, ${s.counts.not_drawn} valves and instruments on no link left out`}
              {asOf && `, links as they were at ${asOf.replace("T", " ")}`}.
            </span>
          </div>
          {s.items.length === 0 ? (
            <Empty>Nothing is recorded in {s.root.label} yet.</Empty>
          ) : (
            <div className="graph-layout">
              <div>
                {noWebGL && <Notice tone="warn">This device can't draw 3D, so the same scene is drawn flat.</Notice>}
                {flat || noWebGL ? (
                  <>
                    <Flat scene={drawn!} look={look} selected={selected} onSelect={select} />
                    {footer(null)}
                  </>
                ) : (
                  <Suspense fallback={<Status loading />}>
                    <Scene3D scene={drawn!} selected={selected} centre={centre} onSelect={select} lineColor={lineColor}
                      labelBy={labelBy} look={look} onUnavailable={() => setNoWebGL(true)} footer={footer(item ? (
                        <div className="scene-pick" aria-live="polite">
                          <strong>{item.label}</strong>
                          {commonName(item.label, item.description) && <span>{commonName(item.label, item.description)}</span>}
                          <span className="muted">{s.shapes[item.shape]?.label}{item.area ? `, in ${item.area}` : ""}</span>
                          {st[item.id] && <span>{describe(st[item.id])}</span>}
                          <ButtonLink size="sm" href={entityHref(item.id)}>Open</ButtonLink>
                        </div>
                      ) : null)} />
                  </Suspense>
                )}
              </div>
              <UICard className="graph-panel" as="aside">
                {item ? (
                  <>
                    <NodePanel node={item} />
                    <p className="small">
                      {s.shapes[item.shape]?.label}
                      {item.classes.length > 0 && ` (ISO 14224 ${item.classes.join(", ")})`}
                      {item.area ? `, in ${item.area}` : ""}. Placed by: {item.placed}.
                    </p>
                    <p className="small" data-testid="scene-item-status">
                      {status.loading && !status.data ? "Reading its status…" : describe(st[item.id])}
                    </p>
                    <div className="scene-actions" role="group" aria-label="Flow path and scenarios">
                      <Button size="sm" aria-pressed={path === "up"}
                        onClick={() => set({ path: path === "up" ? undefined : "up", trip: undefined, move: undefined })}>Upstream</Button>
                      <Button size="sm" aria-pressed={path === "down"}
                        onClick={() => set({ path: path === "down" ? undefined : "down", trip: undefined, move: undefined })}>Downstream</Button>
                      <Button size="sm" aria-pressed={trip}
                        onClick={() => set({ trip: trip ? undefined : "1", path: undefined, move: undefined })}>If this trips</Button>
                    </div>
                    {wellNow && routes && routes.into.length > 1 && (
                      <div className="scene-actions">
                        <label className="small">
                          Reroute to
                          <Select aria-label="Move this well to another header" value={move?.well === item.id ? move.to : ""}
                            onChange={(e) => set({ move: e.target.value ? `${item.id}~${e.target.value}` : undefined, path: undefined, trip: undefined })}>
                            <option value="">{labelOf(wellNow.dst)} (now)</option>
                            {routes.into.filter((h) => h !== wellNow.dst).sort((a, b) => labelOf(a).localeCompare(labelOf(b)))
                              .map((h) => <option key={h} value={h}>{labelOf(h)}</option>)}
                          </Select>
                        </label>
                      </div>
                    )}
                    {path && <p className="small" data-testid="scene-path">
                      {path === "down" ? "Downstream" : "Upstream"} of {item.label}: {plural((lit?.items.size ?? 1) - 1, "item")} over{" "}
                      {plural(lit?.links.size ?? 0, "link")}, the rest dimmed.
                    </p>}
                    {trip && <TripCard read={tripRead} />}
                    {s.root.kind === "facility" && s.plates.find((p) => p.id === item.plate)?.area && (
                      <div className="actions">
                        <ButtonLink href={href("/3d", { at: item.plate, node: item.id })}>Show its area alone</ButtonLink>
                      </div>
                    )}
                  </>
                ) : <Empty className="small">Click a shape to see what it is.</Empty>}
                {rerouted && (
                  <div className="scene-scenario" data-testid="scene-reroute">
                    <strong>{labelOf(rerouted.old.src)} rerouted to {labelOf(rerouted.added.dst)}</strong>
                    <p className="small">In this browser only: nothing is saved or sent anywhere, and no hydraulics are claimed. The old run and the
                      new one are lit; the new one is drawn straight over the deck, not routed.</p>
                    <Table density="compact">
                      <thead><tr><th>Header</th><th>Wells now</th><th>After</th></tr></thead>
                      <tbody>{rerouted.changed.map((c) => (
                        <tr key={c.header}><td>{labelOf(c.header)}</td><td>{c.before}</td><td>{c.after}</td></tr>
                      ))}</tbody>
                    </Table>
                    <Button size="sm" onClick={() => set({ move: undefined })}>Put it back</Button>
                  </div>
                )}
                {move && !rerouted && <p className="small">That reroute doesn't fit this drawing (the well or header isn't in it).</p>}
              </UICard>
            </div>
          )}
          {s.truncated && <p className="small">Too many items to draw; showing the first 3,000.</p>}
        </>
      )}
    </>
  );
}

/** What an item's status says, in words, as its asset page's status card says it. */
function describe(x: ItemStatus | undefined): string {
  if (!x) return "No downtime open, no open work and no alarm sounding.";
  return [x.down ? `Down since ${x.down.start?.slice(0, 10) ?? "an unknown date"}${x.down.reason ? `: ${x.down.reason}` : ""}` : "No downtime open",
    x.open_work ? plural(x.open_work, "open exception") : "no open work",
    x.alarms ? `${plural(x.alarms, "alarm")} sounding` : "no alarm sounding"].join(", ") + ".";
}

/** "If this trips": what goes down with it and the production at risk, labelled as the estimate it is. */
function TripCard({ read }: { read: { data?: SceneTrip | null; loading: boolean; error?: string } }) {
  const t = read.data;
  if (!t) return <Status loading={read.loading} error={read.error} />;
  const list = (xs: { label: string }[], n = 8) => xs.slice(0, n).map((x) => x.label).join(", ") + (xs.length > n ? ` and ${xs.length - n} more` : "");
  return (
    <div className="scene-scenario" data-testid="scene-trip">
      <strong>If {t.item.label} trips: an estimate over {t.hours} h</strong>
      {!t.on_network ? <p className="small">It is on no flow link and shares its parent with nothing on one, so nothing is shown going down with it.</p> : (
        <ul>
          <li>Goes down: {list(t.units)}</li>
          {t.behind.length > 0 && <li>Behind it, with nowhere to flow: {list(t.behind)}</li>}
          {t.starved.length > 0 && <li>Downstream, with nothing else feeding it: {list(t.starved)}</li>}
          <li>{plural(t.wells.length, "well")} behind it{t.wells.length ? `: ${list(t.wells, 6)}` : ""}</li>
          <li>Production at risk: {t.at_risk.length ? t.at_risk.map((x) => `${quantity(x.quantity)} ${x.value.toLocaleString()}${x.unit ? ` ${x.unit}` : ""}`).join(", ") : "none measured"}</li>
          {t.untested.length > 0 && <li>No well test for {list(t.untested.map((label) => ({ label })))}, so not counted</li>}
        </ul>
      )}
      <p className="small muted">{t.basis}</p>
    </div>
  );
}

/** The same scene drawn flat on the network canvas (P5.16f), from above: where WebGL is missing or the person asks. */
function Flat({ scene, look, selected, onSelect }: { scene: Scene; look: SceneLook; selected: string | null; onSelect: (id: string | null) => void }) {
  const elements = useMemo(() => {
    const here = new Set(scene.items.map((i) => i.id));
    const off = (yes: boolean) => (look.lit && !yes ? "dim" : "");
    const steel = cssVar("--muted");
    return [
      ...scene.items.map((i) => {
        const b = look.badges?.get(i.id);
        return { data: { id: i.id, label: b ? `${i.label} · ${b.text}` : i.label, color: look.tint?.get(i.id) ?? steel, size: i.kind === "well" ? 12 : 18 },
          position: { x: i.x * 7, y: i.z * 7 }, classes: off(!!look.lit?.items.has(i.id)) };
      }),
      ...scene.links.filter((l) => here.has(l.src) && here.has(l.dst)).map((l) => ({
        data: { id: `link:${l.id}`, source: l.src, target: l.dst, color: lineColor(l) }, classes: off(!!look.lit?.links.has(l.id)) })),
    ];
  }, [scene, look]);
  return <div data-testid="scene-flat">
    <GraphCanvas elements={elements} layout="preset" selectedId={selected} height={560}
      onSelect={(id) => onSelect(id && !id.startsWith("link:") ? id : null)} />
  </div>;
}
