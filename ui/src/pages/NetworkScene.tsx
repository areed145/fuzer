import { lazy, Suspense, useCallback, useMemo, useState } from "react";
import { api, type RelationDef } from "../api";
import { ALL_RELS } from "../kinds";
import { href, Status, useAsync } from "../lib";
import { ButtonLink, Card as UICard, Empty, Notice, Select } from "../ui";
import { NodePanel } from "./Network";

// three.js arrives with this view and no other (P5.16a).
const Scene3D = lazy(() => import("../Scene3D"));

const words = (s: string) => s.replace(/_/g, " ");
const chartColor = (i: number) =>
  getComputedStyle(document.documentElement).getPropertyValue(`--chart-${(i % 6) + 1}`).trim();

/** The Network page's 3D view: a facility or an area as generic shapes, laid out from its links on the server.
 * What it shows (`at`) and the picked item (`node`) live in the address, so a view can be shared and Back returns. */
export function NetworkScene({ query, set, relDef }: {
  query: URLSearchParams;
  set: (values: Record<string, string | undefined>) => void;
  relDef: (name: string) => RelationDef | undefined;
}) {
  const at = query.get("at") ?? undefined;
  const picked = query.get("node");
  const scene = useAsync(() => api.scene(at), [at]);
  const [noWebGL, setNoWebGL] = useState(false);
  const [centre, setCentre] = useState<{ id: string } | null>(null);
  const s = scene.data;
  // An item asked for by its own link (from its asset page) starts picked.
  const selected = picked ?? s?.focus ?? null;
  const rels = useMemo(() => [...(s?.rels ?? [])].sort((a, b) =>
    (ALL_RELS.indexOf(a) + 1 || 999) - (ALL_RELS.indexOf(b) + 1 || 999) || a.localeCompare(b)), [s]);
  const relColor = useCallback((rel: string) => chartColor(Math.max(rels.indexOf(rel), 0)), [rels]);
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
                    {under(f.id).map((a) => <option key={a.id} value={a.id}>{a.label ?? a.id}</option>)}
                  </optgroup>
                ))}
                {places.filter((p) => p.kind === "area" && !facilities.some((f) => f.id === p.parent))
                  .map((a) => <option key={a.id} value={a.id}>{a.label ?? a.id}</option>)}
              </Select>
            </label>
            <label>
              Find
              <Select value={selected ?? ""} aria-label="Find an item in the drawing"
                onChange={(e) => { select(e.target.value || null); if (e.target.value) setCentre({ id: e.target.value }); }}>
                <option value="">an item ({s.counts.items})</option>
                {s.items.map((i) => <option key={i.id} value={i.id}>{i.label}</option>)}
              </Select>
            </label>
            <span className="small muted" data-testid="scene-basis">
              Schematic, not to scale: {s.counts.items} items placed from {s.counts.links} links
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
                  <Scene3D scene={s} selected={selected} centre={centre} onSelect={select} relColor={relColor}
                    onUnavailable={() => setNoWebGL(true)} />
                </Suspense>
                <div className="legend" aria-label="What the lines are">
                  {rels.filter((r) => s.links.some((l) => l.rel === r)).map((r) => (
                    <span key={r}><i className={`scene-line scene-tone-${(rels.indexOf(r) % 6) + 1}`} /> {relDef(r)?.label ?? words(r)}</span>
                  ))}
                </div>
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
                        <ButtonLink href={href("/network", { view: "3d", at: item.plate, node: item.id })}>Show its area alone</ButtonLink>
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
