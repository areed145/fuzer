import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { api, type RelationDef, type SceneLink } from "../api";
import { commonName } from "../entityRefs";
import { ALL_RELS, PRODUCTS, linkColor } from "../kinds";
import { entityHref, href, Status, useAsync } from "../lib";
import type { LabelBy } from "../Scene3D";
import { ButtonLink, Card as UICard, Empty, Notice, Select } from "../ui";
import { NodePanel } from "./Network";

// three.js arrives with this view and no other (P5.16a).
const Scene3D = lazy(() => import("../Scene3D"));

const words = (s: string) => s.replace(/_/g, " ");
const FLOW_PRODUCT = { flows_to: true, lift_gas_to: true, relieves_to: true }; // the link types a product is worked out for
const lineColor = (l: SceneLink) => linkColor(l.rel, l.product);
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
  const scene = useAsync(() => api.scene(at, undefined, geo ? "geo" : undefined), [at, geo]);
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
  const places = s?.places ?? [];
  const facilities = places.filter((p) => p.kind === "facility");
  const under = (id: string) => places.filter((p) => p.parent === id);
  const select = useCallback((id: string | null) => set({ node: id ?? undefined }), [set]);

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
            <span className="small muted" data-testid="scene-basis">
              {s.layout === "geo" ? "Areas where they are, long distances shortened"
                : geo ? "No area here has a location, so this is the schematic" : "Schematic, not to scale"}
              : {s.counts.items} items placed from {s.counts.links} links
              {s.counts.not_drawn > 0 && `, ${s.counts.not_drawn} valves and instruments on no link left out`}.
            </span>
          </div>
          {s.items.length === 0 ? (
            <Empty>Nothing is recorded in {s.root.label} yet.</Empty>
          ) : noWebGL ? (
            <Notice tone="warn">
              This device can't draw 3D. <a href={href("/network")}>Open the network diagram</a> instead.
            </Notice>
          ) : (
            <div className="graph-layout">
              <div>
                <Suspense fallback={<Status loading />}>
                  <Scene3D scene={s} selected={selected} centre={centre} onSelect={select} lineColor={lineColor}
                    labelBy={labelBy} onUnavailable={() => setNoWebGL(true)} footer={<>
                      <div className="legend" aria-label="What the lines are">
                        {legend.map((e) => (
                          <span key={e.label}>
                            <svg className="scene-line" width="18" height="6" aria-hidden="true"><line x1="1" y1="3" x2="17" y2="3" stroke={e.color} strokeWidth="4" strokeLinecap="round" /></svg>
                            {e.label}
                          </span>
                        ))}
                      </div>
                      {item && (
                        <div className="scene-pick" aria-live="polite">
                          <strong>{item.label}</strong>
                          {commonName(item.label, item.description) && <span>{commonName(item.label, item.description)}</span>}
                          <span className="muted">{s.shapes[item.shape]?.label}{item.area ? `, in ${item.area}` : ""}</span>
                          <ButtonLink size="sm" href={entityHref(item.id)}>Open</ButtonLink>
                        </div>
                      )}
                    </>} />
                </Suspense>
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
                    {s.root.kind === "facility" && s.plates.find((p) => p.id === item.plate)?.area && (
                      <div className="actions">
                        <ButtonLink href={href("/3d", { at: item.plate, node: item.id })}>Show its area alone</ButtonLink>
                      </div>
                    )}
                  </>
                ) : <Empty className="small">Click a shape to see what it is.</Empty>}
              </UICard>
            </div>
          )}
          {s.truncated && <p className="small">Too many items to draw; showing the first 3,000.</p>}
        </>
      )}
    </>
  );
}
