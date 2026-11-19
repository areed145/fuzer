import "./styles/bpmn.css";
import { errorMessage } from "./lib";
import "./styles/graphs.css";
// A process diagram drawn by Fuzer from the BPMN file's diagram (processDrawing.ts), in the app's own colours, on a
// canvas that pans and zooms: drag to pan, pinch or the buttons to zoom, Fit for the whole process, Waiting step for
// where it stands. Marks show the path taken and the open steps; other markers and count overlays serve the
// comparison, routing and the monitor. Only the modeler still uses bpmn-js (and shows bpmn.io's watermark, as its
// licence asks); this view doesn't.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Button, FullscreenButton, useFullscreen } from "./ui";
import { parseProcess, roundedPath, wrap, type Drawing, type Shape } from "./processDrawing";

export interface Marks {
  done: string[];    // elements the process passed
  open: string[];    // elements waiting now
  flows: string[];   // sequence flows taken
}

// Below this zoom step names are too small to read (#406): a bigger process opens on its waiting step at this zoom,
// with the whole process one press away.
const MIN_ZOOM = 0.75;
const MAX_ZOOM = 2.5;
const FIT_MAX = 1.4;
const PAD = 24;
const TOOLS = 48;   // the tool row along the canvas's bottom

type View = { x: number; y: number; k: number };

const TASKS = new Set(["task", "userTask", "serviceTask", "sendTask", "receiveTask", "manualTask", "scriptTask", "businessRuleTask",
  "callActivity", "subProcess"]);
const EVENTS = new Set(["startEvent", "endEvent", "intermediateCatchEvent", "intermediateThrowEvent", "boundaryEvent"]);

const STATE_WORDS: Record<string, string> = { "fz-open": "waiting now", "fz-done": "done", "fz-problem": "has a problem",
  "fz-added": "added", "fz-changed": "changed", "fz-removed": "removed", "fz-busy": "work waiting", "fz-late": "overdue" };

export default function BpmnView({ xml, marks, classes, overlays, tall }: {
  xml: string;
  marks?: Marks;
  classes?: Record<string, string>;   // element id -> marker class
  overlays?: Record<string, string>;  // element id -> html drawn over its top right corner
  tall?: boolean;
}) {
  const full = useFullscreen<HTMLDivElement>();
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const moved = useRef(false);   // the person panned or zoomed: a resize keeps their view

  const parsed = useMemo((): { d?: Drawing; error?: string } => {
    try { return { d: parseProcess(xml) }; } catch (e) { return { error: errorMessage(e) }; }
  }, [xml]);
  const d = parsed.d;

  // Each element's state: the marks first, then the given marker classes.
  const state = useMemo(() => {
    const s: Record<string, string> = {};
    if (marks) {
      marks.flows.forEach((id) => { s[id] = "fz-taken"; });
      marks.done.forEach((id) => { s[id] = "fz-done"; });
      marks.open.forEach((id) => { s[id] = "fz-open"; });
    }
    Object.entries(classes ?? {}).forEach(([id, cls]) => { s[id] = cls; });
    return s;
  }, [marks, classes]);

  const focus = useMemo(() => {
    if (!d) return undefined;
    const byId = new Map(d.nodes.map((n) => [n.id, n]));
    return (marks?.open ?? []).map((id) => byId.get(id)).find(Boolean);
  }, [d, marks]);

  // The canvas is as tall as the whole process drawn across its width, within limits, so a short process isn't a
  // small drawing floating in an empty panel.
  const maxH = full.on ? size.h : tall ? 560 : 380;
  const naturalH = d && size.w ? Math.round(d.box.h * Math.min((size.w - 2 * PAD) / d.box.w, FIT_MAX) + 2 * PAD) : 0;
  const height = full.on ? undefined : Math.max(240, Math.min(maxH, naturalH ? naturalH + TOOLS : maxH));

  const fitView = useCallback((w: number, h: number): View | undefined => {
    if (!d || !w || !h) return undefined;
    const k = Math.min((w - 2 * PAD) / d.box.w, (h - TOOLS - 2 * PAD) / d.box.h, FIT_MAX);
    return { k, x: (w - d.box.w * k) / 2 - d.box.x * k, y: (h - TOOLS - d.box.h * k) / 2 - d.box.y * k };
  }, [d]);
  const stepView = useCallback((w: number, h: number, k = Math.max(MIN_ZOOM, view.k)): View | undefined => {
    if (!focus || !d || !w || !h) return undefined;
    // centred on the step, but never past the drawing's edge, so no half of the canvas is left empty
    const clamp = (c: number, lo: number, size: number, span: number) =>
      span * k + 2 * PAD <= size ? (size - span * k) / 2 - lo * k : Math.min(PAD - lo * k, Math.max(size - PAD - (lo + span) * k, c));
    return { k, x: clamp(w / 2 - (focus.x + focus.w / 2) * k, d.box.x, w, d.box.w),
      y: clamp((h - TOOLS) / 2 - (focus.y + focus.h / 2) * k, d.box.y, h - TOOLS, d.box.h) };
  }, [focus, d, view.k]);

  const fitted = fitView(size.w, size.h);
  const opensOnStep = !!fitted && fitted.k < MIN_ZOOM && !!focus;

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setSize({ w: e.contentRect.width, h: e.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { moved.current = false; }, [xml, full.on]);
  useEffect(() => {
    if (moved.current || !size.w || !size.h) return;
    const v = opensOnStep ? stepView(size.w, size.h, MIN_ZOOM) : fitView(size.w, size.h);
    if (v) setView(v);
    // the initial view follows the size and the file, not the person's own zoom
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size.w, size.h, d, focus]);

  const zoomAt = (factor: number, cx = size.w / 2, cy = size.h / 2) => {
    moved.current = true;
    setView((v) => {
      const k = Math.min(MAX_ZOOM, Math.max(0.1, v.k * factor));
      return { k, x: cx - ((cx - v.x) * k) / v.k, y: cy - ((cy - v.y) * k) / v.k };
    });
  };
  const fit = () => { const v = fitView(size.w, size.h); if (v) { moved.current = true; setView(v); } };
  const toStep = () => { const v = stepView(size.w, size.h); if (v) { moved.current = true; setView(v); } };

  // Drag to pan; two fingers pinch to zoom.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const pinch = useRef<number | null>(null);
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if ((e.target as Element).closest("button, a, .fz-overlay")) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    pinch.current = null;
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const prev = pointers.current.get(e.pointerId);
    if (!prev) return;
    const all = pointers.current;
    if (all.size === 1) {
      moved.current = true;
      setView((v) => ({ ...v, x: v.x + e.clientX - prev.x, y: v.y + e.clientY - prev.y }));
    } else if (all.size === 2) {
      const [a, b] = [...all.values()];
      const other = a === prev ? b : a;
      const dist = Math.hypot(e.clientX - other.x, e.clientY - other.y);
      if (pinch.current) {
        const r = box.current!.getBoundingClientRect();
        zoomAt(dist / pinch.current, (e.clientX + other.x) / 2 - r.left, (e.clientY + other.y) / 2 - r.top);
      }
      pinch.current = dist;
    }
    all.set(e.pointerId, { x: e.clientX, y: e.clientY });
  };
  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => { pointers.current.delete(e.pointerId); pinch.current = null; };

  // A trackpad pinch (ctrl + wheel) zooms; a plain scroll still scrolls the page.
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY / 200), e.clientX - r.left, e.clientY - r.top);
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  });

  const onKey = (e: ReactKeyboardEvent) => {
    if (e.key === "+" || e.key === "=") zoomAt(1.25);
    else if (e.key === "-") zoomAt(0.8);
    else if (e.key === "0") fit();
    else if (e.key.startsWith("Arrow")) {
      moved.current = true;
      const s = 40;
      setView((v) => ({ ...v, x: v.x + (e.key === "ArrowLeft" ? s : e.key === "ArrowRight" ? -s : 0),
        y: v.y + (e.key === "ArrowUp" ? s : e.key === "ArrowDown" ? -s : 0) }));
    } else return;
    e.preventDefault();
  };

  if (parsed.error || !d) return <div className="status error">Couldn't draw the process: {parsed.error}</div>;

  const describe = d.nodes.filter((n) => n.name).map((n) => `${n.name}${STATE_WORDS[state[n.id]] ? ` (${STATE_WORDS[state[n.id]]})` : ""}`);
  const laneOf = (lane: Shape) => d.pools.find((p) => lane.x >= p.x && lane.y >= p.y && lane.y < p.y + p.h);

  return (
    <>
      <div ref={full.ref} className={`graph-canvas process-canvas${full.on ? " ui-fullscreen" : ""}`}
        style={height ? { height } : undefined}>
        <div ref={box} className="process-surface" tabIndex={0} role="group" aria-label="Process diagram. Drag to pan; plus and minus zoom; 0 fits."
          onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
          onKeyDown={onKey}>
          <svg width="100%" height="100%" role="img" aria-label={`Steps: ${describe.join(", ")}`}>
            <defs>
              <marker id="fz-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0 1 L10 5 L0 9 z" className="fz-arrow" />
              </marker>
              <marker id="fz-arrow-taken" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0 1 L10 5 L0 9 z" className="fz-arrow taken" />
              </marker>
            </defs>
            <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
              {d.pools.map((p) => {
                const hasLanes = d.lanes.some((l) => laneOf(l) === p);
                const x = hasLanes ? p.x + 30 : p.x;
                return <g key={p.id} className="fz-pool">
                  <rect x={x} y={p.y} width={p.w - (x - p.x)} height={p.h} rx={14} />
                  {!hasLanes && p.name && <text x={x + 14} y={p.y + 20} className="fz-lane-label">{p.name}</text>}
                </g>;
              })}
              {d.lanes.map((l, i) => (
                <g key={l.id} className="fz-lane">
                  {i > 0 && d.lanes.some((o) => o !== l && Math.abs(o.y + o.h - l.y) < 1) &&
                    <line x1={l.x} x2={l.x + l.w} y1={l.y} y2={l.y} />}
                  {l.name && <text x={l.x + 14} y={l.y + 20} className="fz-lane-label">{l.name}</text>}
                </g>
              ))}
              {d.edges.map((e) => {
                const st = state[e.id];
                const taken = st === "fz-taken";
                const [a, b] = e.points;
                const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
                const ux = (b.x - a.x) / len, uy = (b.y - a.y) / len;
                // the label on the flow's longest segment, unless the file places it
                let mid = e.points[0], best = -1;
                for (let i = 1; i < e.points.length; i++) {
                  const [p, q] = [e.points[i - 1], e.points[i]];
                  const l = Math.hypot(q.x - p.x, q.y - p.y);
                  if (l > best) { best = l; mid = { x: (p.x + q.x) / 2, y: (p.y + q.y) / 2 }; }
                }
                const at = e.label ? { x: e.label.x + e.label.w / 2, y: e.label.y + e.label.h / 2 } : { x: mid.x, y: mid.y - 6 };
                return <g key={e.id} className={`fz-flow ${st ?? ""}`}>
                  <path d={roundedPath(e.points)} markerEnd={`url(#${taken ? "fz-arrow-taken" : "fz-arrow"})`} />
                  {e.isDefault && <line className="fz-default" x1={a.x + ux * 10 - uy * 5 - ux * 4} y1={a.y + uy * 10 + ux * 5 - uy * 4}
                    x2={a.x + ux * 10 + uy * 5 + ux * 4} y2={a.y + uy * 10 - ux * 5 + uy * 4} />}
                  {e.name && <text x={at.x} y={at.y} className="fz-flow-label" textAnchor="middle">{e.name}</text>}
                </g>;
              })}
              {d.nodes.map((n) => <Node key={n.id} n={n} state={state[n.id]} />)}
            </g>
          </svg>
          {overlays && Object.entries(overlays).map(([id, html]) => {
            const n = d.nodes.find((x) => x.id === id);
            if (!n) return null;
            return <div key={id} className="fz-overlay"
              style={{ left: (n.x + n.w) * view.k + view.x - 10, top: n.y * view.k + view.y - 12 }}
              dangerouslySetInnerHTML={{ __html: `<div class="fz-count">${html}</div>` }} />;
          })}
        </div>
        <div className="graph-tools" role="toolbar" aria-label="View">
          <FullscreenButton on={full.on} onClick={() => void full.toggle()} />
          <Button variant="secondary" size="sm" onClick={fit} aria-label="Show the whole process" title="Show the whole process">Fit</Button>
          {focus && <Button variant="secondary" size="sm" onClick={toStep} aria-label="Zoom to the waiting step" title="Zoom to the waiting step">
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="3" /><path d="M12 3v3M12 18v3M3 12h3M18 12h3" /></svg>
          </Button>}
          <Button variant="secondary" size="sm" className="fz-zoom" onClick={() => zoomAt(1.25)} aria-label="Zoom in" title="Zoom in">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </Button>
          <Button variant="secondary" size="sm" className="fz-zoom" onClick={() => zoomAt(0.8)} aria-label="Zoom out" title="Zoom out">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14" /></svg>
          </Button>
        </div>
      </div>
      {marks && (
        <div className="legend bpmn-legend">
          <span><i className="dot fz-legend-open" /> waiting now</span>
          <span><i className="dot fz-legend-done" /> done</span>
          <span><i className="dot fz-legend-taken" /> path taken</span>
        </div>
      )}
    </>
  );
}

/** One step, event or gateway, in its state. */
function Node({ n, state }: { n: Shape; state?: string }) {
  const cls = `fz-node fz-${n.type} ${state ?? ""}`;
  const title = <title>{[n.name || n.type, STATE_WORDS[state ?? ""]].filter(Boolean).join(": ")}</title>;
  const cx = n.x + n.w / 2, cy = n.y + n.h / 2;
  if (TASKS.has(n.type)) {
    const lines = wrap(n.name, n.w - 16, 6.4, 3);
    const top = cy - ((lines.length - 1) * 15) / 2 + 4;
    return <g className={cls}>
      {title}
      {state === "fz-open" && <rect className="fz-halo" x={n.x - 5} y={n.y - 5} width={n.w + 10} height={n.h + 10} rx={15} />}
      <rect className="fz-shape" x={n.x} y={n.y} width={n.w} height={n.h} rx={10} />
      {n.type === "callActivity" && <rect className="fz-call" x={n.x + 3} y={n.y + 3} width={n.w - 6} height={n.h - 6} rx={8} />}
      <TaskIcon type={n.type} x={n.x + 8} y={n.y + 8} />
      {lines.map((l, i) => <text key={i} x={cx} y={top + i * 15} textAnchor="middle" className="fz-name">{l}</text>)}
    </g>;
  }
  const label = n.name && (n.label
    ? <text x={n.label.x + n.label.w / 2} y={n.label.y + 11} textAnchor="middle" className="fz-label">{wrap(n.name, Math.max(n.label.w, 90), 6, 2).map((l, i) =>
        <tspan key={i} x={n.label!.x + n.label!.w / 2} dy={i ? 13 : 0}>{l}</tspan>)}</text>
    : <text x={cx} y={n.y + n.h + 16} textAnchor="middle" className="fz-label">{wrap(n.name, 110, 6, 2).map((l, i) =>
        <tspan key={i} x={cx} dy={i ? 13 : 0}>{l}</tspan>)}</text>);
  if (EVENTS.has(n.type)) {
    const r = n.w / 2;
    return <g className={cls}>
      {title}
      {state === "fz-open" && <circle className="fz-halo" cx={cx} cy={cy} r={r + 5} />}
      <circle className="fz-shape" cx={cx} cy={cy} r={r} />
      {n.type.startsWith("intermediate") || n.type === "boundaryEvent" ? <circle className="fz-inner" cx={cx} cy={cy} r={r - 4} /> : null}
      {n.event === "timer" && <g className="fz-glyph"><circle cx={cx} cy={cy} r={r - 9} /><path d={`M${cx} ${cy - r + 13}V${cy}l5 3`} /></g>}
      {n.event === "message" && <g className="fz-glyph"><rect x={cx - 8} y={cy - 5.5} width={16} height={11} rx={1.5} /><path d={`M${cx - 8} ${cy - 5}l8 6 8-6`} /></g>}
      {label}
    </g>;
  }
  if (n.type.endsWith("Gateway")) {
    const h = n.w / 2;
    return <g className={cls}>
      {title}
      {state === "fz-open" && <path className="fz-halo" d={`M${cx} ${cy - h - 6}L${cx + h + 6} ${cy}L${cx} ${cy + h + 6}L${cx - h - 6} ${cy}Z`} />}
      <path className="fz-shape" d={`M${cx} ${cy - h}L${cx + h} ${cy}L${cx} ${cy + h}L${cx - h} ${cy}Z`} strokeLinejoin="round" />
      <path className="fz-glyph" d={n.type === "parallelGateway" ? `M${cx} ${cy - 8}V${cy + 8}M${cx - 8} ${cy}H${cx + 8}`
        : n.type === "exclusiveGateway" ? `M${cx - 6} ${cy - 6}L${cx + 6} ${cy + 6}M${cx + 6} ${cy - 6}L${cx - 6} ${cy + 6}` : ""} />
      {label}
    </g>;
  }
  return <g className={cls}>{title}<rect className="fz-shape" x={n.x} y={n.y} width={n.w} height={n.h} rx={6} />{label}</g>;
}

/** A small mark in a step's corner: a person's step, an agent's, or a called workflow. */
function TaskIcon({ type, x, y }: { type: string; x: number; y: number }) {
  if (type === "userTask" || type === "manualTask") return <g className="fz-glyph fz-icon" transform={`translate(${x} ${y})`}>
    <circle cx={7} cy={4.5} r={3} /><path d="M1.5 14c.6-3.4 2.8-5 5.5-5s4.9 1.6 5.5 5" /></g>;
  if (type === "serviceTask" || type === "scriptTask" || type === "businessRuleTask") return <g className="fz-glyph fz-icon" transform={`translate(${x} ${y})`}>
    <rect x={1.5} y={3.5} width={11} height={9} rx={2.5} /><path d="M7 1v2.5M4.8 8h.01M9.2 8h.01" /></g>;
  if (type === "callActivity") return <g className="fz-glyph fz-icon" transform={`translate(${x} ${y})`}>
    <path d="M2 7h9M8 3.5 11.5 7 8 10.5" /></g>;
  return null;
}
