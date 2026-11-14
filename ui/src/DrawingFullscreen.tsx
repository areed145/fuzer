/** A P&ID on the whole screen (P5.14), for a phone above all: pinch or wheel to zoom, drag to pan, double-tap to zoom in
 * or back to the whole sheet. A tap on a mark says what it is and opens it; a tap on an off-page connector, or on a
 * reading that names another sheet, goes through to that sheet here, so a line can be followed across drawings. Back
 * returns to the sheet before; Close leaves the drawings for the page they were opened from.
 *
 * It opens over any page from the link (`drawing=<document>`, `ring=<entities>`), so Back and a shared link work. */
import "./styles/drawing.css";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, type Drawing, type DrawingMark } from "./api";
import { appDepth } from "./appHistory";
import { projectUrl } from "./authState";
import { entityHref, useAsync } from "./lib";
import { MARK_STATUS, MarkShape } from "./DrawingViewer";
import { lockPageScroll } from "./scrollLock";
import { Button } from "./ui";

type View = { s: number; x: number; y: number };
type Pick = { kind: "mark"; i: number } | { kind: "off"; i: number };

/** The depth the first drawing was opened at, so Close can step back past every sheet followed since. */
let openedAt: number | null = null;

/** The link to this page with a drawing open over it (or a new sheet in place of the one open). */
export function openDrawing(id: string, ring: string[] = []) {
  const [path, qs] = location.hash.split("?");
  const params = new URLSearchParams(qs ?? "");
  if (!params.has("drawing")) openedAt = appDepth();
  params.set("drawing", id);
  if (ring.length) params.set("ring", ring.join(","));
  else params.delete("ring");
  location.hash = `${path || "#/"}?${params}`;
}

function closeHref(): string {
  const [path, qs] = location.hash.split("?");
  const params = new URLSearchParams(qs ?? "");
  params.delete("drawing");
  params.delete("ring");
  return `${path || "#/"}${params.size ? `?${params}` : ""}`;
}

export function closeDrawing() {
  const back = openedAt === null ? 0 : appDepth() - openedAt;
  openedAt = null;
  if (back > 0) history.go(-back);
  else location.replace(closeHref());
}

/** The sheets this one connects to: its off-page connectors, then any other sheet a reading names. */
export function connectedSheets(g: Drawing): { id: string; sheet: string; direction?: "to" | "from" }[] {
  const out = new Map<string, { id: string; sheet: string; direction?: "to" | "from" }>();
  for (const o of g.offpage ?? []) if (o.sheet_id && !out.has(o.sheet_id)) out.set(o.sheet_id, { id: o.sheet_id, sheet: o.sheet, direction: o.direction });
  for (const m of g.marks) if (m.sheet_id && !out.has(m.sheet_id)) out.set(m.sheet_id, { id: m.sheet_id, sheet: m.sheet ?? m.text });
  return [...out.values()];
}

export function DrawingFullscreen({ id, ring }: { id: string; ring: string[] }) {
  const d = useAsync(() => api.drawing(id).catch(() => null), [id]);
  const g = d.data;
  useEffect(() => {
    const unlock = lockPageScroll("drawing-open");
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") closeDrawing(); };
    window.addEventListener("keydown", key);
    return () => {
      unlock();
      window.removeEventListener("keydown", key);
    };
  }, []);
  return (
    <div className="drawing-full" role="dialog" aria-modal="true" aria-label={g ? `P&ID ${g.sheet}` : "P&ID"}>
      {g ? <Sheet key={id} g={g} ring={ring} /> : (
        <div className="drawing-full-bar">
          <Button variant="ghost" className="df-btn" aria-label="Close drawing" onClick={closeDrawing}>{CLOSE}</Button>
          <span className="df-title">{d.loading ? "Loading the drawing…" : "This drawing couldn't be opened."}</span>
        </div>
      )}
    </div>
  );
}

const CLOSE = <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>;

function Sheet({ g, ring }: { g: Drawing; ring: string[] }) {
  const stage = useRef<HTMLDivElement>(null);
  const sheet = useRef<HTMLDivElement>(null);
  const view = useRef<View>({ s: 1, x: 0, y: 0 });
  const fit = useRef<View>({ s: 1, x: 0, y: 0 });
  const [pick, setPick] = useState<Pick>();
  const [zoomed, setZoomed] = useState(false);
  const ringed = g.marks.findIndex((m) => !!m.id && ring.includes(m.id) && m.x !== undefined);
  const links = connectedSheets(g);

  const apply = useCallback((next: View) => {
    const f = fit.current.s;
    const s = Math.min(Math.max(next.s, f * 0.8), Math.max(f * 12, 3));
    view.current = { ...next, s };
    if (sheet.current) sheet.current.style.transform = `translate(${next.x}px, ${next.y}px) scale(${s})`;
    setZoomed(s > f * 1.05);
  }, []);
  const zoomAt = useCallback((px: number, py: number, factor: number) => {
    const v = view.current;
    const f = fit.current.s;
    const s = Math.min(Math.max(v.s * factor, f * 0.8), Math.max(f * 12, 3));
    const k = s / v.s;
    apply({ s, x: px - (px - v.x) * k, y: py - (py - v.y) * k });
  }, [apply]);
  const toFit = useCallback(() => {
    const el = stage.current;
    if (!el) return;
    const w = el.clientWidth, h = el.clientHeight;
    const s = Math.min(w / g.width, h / g.height);
    fit.current = { s, x: (w - g.width * s) / 2, y: (h - g.height * s) / 2 };
    apply(fit.current);
  }, [apply, g.width, g.height]);
  const centre = useCallback((x: number, y: number, s: number) => {
    const el = stage.current!;
    apply({ s, x: el.clientWidth / 2 - x * s, y: el.clientHeight / 2 - y * s });
  }, [apply]);
  const find = () => {
    const m = g.marks[ringed];
    centre(m.x!, m.y!, Math.max(fit.current.s * 4, 0.6));
    setPick({ kind: "mark", i: ringed });
  };
  const step = (factor: number) => {
    const el = stage.current!;
    zoomAt(el.clientWidth / 2, el.clientHeight / 2, factor);
  };

  useLayoutEffect(() => {
    toFit();
    const again = () => toFit();
    window.addEventListener("resize", again);
    return () => window.removeEventListener("resize", again);
  }, [toFit]);

  // Pointer gestures: one finger pans, two pinch, a still tap picks what is under it, two quick taps zoom.
  useEffect(() => {
    const el = stage.current!;
    const points = new Map<number, { x: number; y: number }>();
    let moved = 0;
    let start: EventTarget | null = null;
    let lastTap = { t: 0, x: 0, y: 0 };
    const local = (e: { clientX: number; clientY: number }) => {
      const r = el.getBoundingClientRect();
      return { x: e.clientX - r.left, y: e.clientY - r.top };
    };
    const down = (e: PointerEvent) => {
      if ((e.target as Element).closest(".df-ui")) return;
      start = points.size === 0 ? e.target : null;
      if (points.size === 0) moved = 0;
      points.set(e.pointerId, local(e));
      try { el.setPointerCapture(e.pointerId); } catch { /* a pointer the browser no longer tracks */ }
    };
    const move = (e: PointerEvent) => {
      const prev = points.get(e.pointerId);
      if (!prev) return;
      const now = local(e);
      if (points.size === 1) {
        const v = view.current;
        moved += Math.abs(now.x - prev.x) + Math.abs(now.y - prev.y);
        apply({ ...v, x: v.x + now.x - prev.x, y: v.y + now.y - prev.y });
      } else if (points.size === 2) {
        const [a, b] = [...points.entries()].map(([k, p]) => (k === e.pointerId ? now : p));
        const [pa, pb] = [...points.values()];
        const before = Math.hypot(pa.x - pb.x, pa.y - pb.y) || 1;
        const after = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        const pmid = { x: (pa.x + pb.x) / 2, y: (pa.y + pb.y) / 2 };
        moved += 100;
        const v = view.current;
        apply({ ...v, x: v.x + mid.x - pmid.x, y: v.y + mid.y - pmid.y });
        zoomAt(mid.x, mid.y, after / before);
      }
      points.set(e.pointerId, now);
    };
    const up = (e: PointerEvent) => {
      const at = points.get(e.pointerId);
      points.delete(e.pointerId);
      if (!at || points.size > 0 || moved > 10) return;
      const hit = (start as Element | null)?.closest?.("[data-mark], [data-off]");
      if (hit) {
        const mark = hit.getAttribute("data-mark");
        setPick(mark !== null ? { kind: "mark", i: Number(mark) } : { kind: "off", i: Number(hit.getAttribute("data-off")) });
        lastTap = { t: 0, x: 0, y: 0 };
        return;
      }
      const now = performance.now();
      if (now - lastTap.t < 320 && Math.hypot(at.x - lastTap.x, at.y - lastTap.y) < 30) {
        if (view.current.s > fit.current.s * 1.5) apply(fit.current);
        else zoomAt(at.x, at.y, 3);
        lastTap = { t: 0, x: 0, y: 0 };
      } else {
        lastTap = { t: now, ...at };
        setPick(undefined);
      }
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const at = local(e);
      zoomAt(at.x, at.y, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.002)));
    };
    el.addEventListener("pointerdown", down);
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
    el.addEventListener("wheel", wheel, { passive: false });
    return () => {
      el.removeEventListener("pointerdown", down);
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      el.removeEventListener("wheel", wheel);
    };
  }, [apply, zoomAt]);

  const keys = (e: React.KeyboardEvent) => {
    const v = view.current;
    const by = 80;
    const moves: Record<string, () => void> = {
      "+": () => step(1.4), "=": () => step(1.4), "-": () => step(1 / 1.4), "0": toFit,
      ArrowLeft: () => apply({ ...v, x: v.x + by }), ArrowRight: () => apply({ ...v, x: v.x - by }),
      ArrowUp: () => apply({ ...v, y: v.y + by }), ArrowDown: () => apply({ ...v, y: v.y - by }),
    };
    if (moves[e.key]) { e.preventDefault(); moves[e.key](); }
  };

  const picked = pick?.kind === "mark" ? g.marks[pick.i] : undefined;
  const pickedOff = pick?.kind === "off" ? g.offpage?.[pick.i] : undefined;
  return (
    <>
      <div className="drawing-full-bar df-ui">
        <Button variant="ghost" className="df-btn" aria-label="Close drawing" onClick={closeDrawing}>{CLOSE}</Button>
        <span className="df-title">
          <span className="mono">{g.sheet}</span>
          {g.title && <span className="muted"> · {g.title}</span>}
        </span>
        <div className="df-zoom" role="group" aria-label="Zoom">
          <Button variant="ghost" className="df-btn" aria-label="Zoom out" onClick={() => step(1 / 1.4)}>−</Button>
          <Button variant="ghost" className="df-btn" aria-label="Zoom in" onClick={() => step(1.4)}>+</Button>
          <Button variant="ghost" className="df-btn df-text" aria-pressed={!zoomed} onClick={toFit}>Fit</Button>
          {ringed >= 0 && <Button variant="ghost" className="df-btn df-text" onClick={find}>Find</Button>}
        </div>
      </div>
      <div className="drawing-full-stage" ref={stage} tabIndex={0} onKeyDown={keys}
           aria-label="Drawing: drag to move, pinch or scroll to zoom, double-tap to zoom in">
        <div className="drawing-full-sheet" ref={sheet} style={{ width: g.width, height: g.height }}>
          <img src={projectUrl(g.image)} alt={`P&ID ${g.sheet}`} draggable={false} />
          <svg viewBox={`0 0 ${g.width} ${g.height}`} preserveAspectRatio="none">
            {g.marks.map((m, i) => m.x !== undefined && m.y !== undefined && (
              <FullMark key={i} i={i} m={m} ringed={!!m.id && ring.includes(m.id)} on={pick?.kind === "mark" && pick.i === i} />
            ))}
            {(g.offpage ?? []).map((o, i) => o.x !== undefined && o.y !== undefined && (
              <g key={`o${i}`} data-off={i} className={`off-end ${o.sheet_id ? "st-sheet" : "st-line"} ${pick?.kind === "off" && pick.i === i ? "focus" : ""}`}>
                <title>{`${o.direction === "to" ? "To" : "From"} ${o.sheet}`}</title>
                <rect x={o.x - 90} y={o.y - 50} width={180} height={100} rx={18} />
              </g>
            ))}
          </svg>
        </div>
      </div>
      {(picked || pickedOff) ? (
        <div className="drawing-full-pick df-ui" role="status">
          {picked && <MarkPick m={picked} />}
          {pickedOff && (
            <>
              <div><strong>{pickedOff.direction === "to" ? "To" : "From"} <span className="mono">{pickedOff.sheet}</span></strong></div>
              <div className="muted small">
                {pickedOff.tag && <span className="mono">{pickedOff.tag}</span>} {pickedOff.direction === "to" ? "→" : "←"}{" "}
                {pickedOff.other && <span className="mono">{pickedOff.other}</span>} {pickedOff.line && <span className="mono">· {pickedOff.line}</span>}
              </div>
              {pickedOff.sheet_id ? <SheetActions id={pickedOff.sheet_id} sheet={pickedOff.sheet} />
                : <div className="muted small">That sheet isn't in the register.</div>}
            </>
          )}
          <Button variant="ghost" className="df-btn df-dismiss" aria-label="Dismiss" onClick={() => setPick(undefined)}>{CLOSE}</Button>
        </div>
      ) : links.length > 0 && (
        <nav className="drawing-full-links df-ui" aria-label="Connected sheets">
          {links.map((l) => (
            <Button key={l.id} variant="ghost" className="df-chip" onClick={() => openDrawing(l.id)}>
              {l.direction === "from" ? "← " : l.direction === "to" ? "→ " : ""}<span className="mono">{l.sheet}</span>
            </Button>
          ))}
        </nav>
      )}
    </>
  );
}

function FullMark({ i, m, ringed, on }: { i: number; m: DrawingMark; ringed: boolean; on: boolean }) {
  const kind = m.sheet_id ? "st-sheet" : `st-${m.status}`;
  return (
    <g data-mark={i} className={`mark ${kind} ${on ? "focus" : ""} ${ringed ? "hl" : ""}`}>
      {ringed && <circle className="hl-ring" cx={m.x} cy={m.y} r={70} />}
      {/* a wider, invisible target than the ring, for a finger */}
      <circle className="hit" cx={m.x} cy={m.y} r={56} />
      <MarkShape m={m} />
      {/* the full tag, where the sheet shows less of it (a bubble's "LT 4001"); never the drawn tag twice */}
      {(on || ringed) && m.key && m.key !== m.text && <text x={(m.x ?? 0) + 46} y={(m.y ?? 0) - 40}>{m.key}</text>}
    </g>
  );
}

function MarkPick({ m }: { m: DrawingMark }) {
  const status = MARK_STATUS.find((s) => s.key === m.status)?.label;
  return (
    <>
      <div><strong className="mono">{m.key ?? m.text}</strong>{m.key && m.key !== m.text && <span className="muted small mono"> drawn {m.text}</span>}</div>
      <div className="muted small">{m.sheet_id ? `Names ${m.sheet}` : status}</div>
      {m.id && <a className="ui-button ui-button-primary ui-button-md" href={entityHref(m.id)}>Open {m.key ?? m.text}</a>}
      {m.sheet_id && <SheetActions id={m.sheet_id} sheet={m.sheet ?? m.text} />}
    </>
  );
}

function SheetActions({ id, sheet }: { id: string; sheet: string }) {
  return (
    <div className="row">
      <Button variant="primary" onClick={() => openDrawing(id)}>Go to {sheet}</Button>
      <a className="ui-button ui-button-secondary ui-button-md" href={entityHref(id)}>Sheet page</a>
    </div>
  );
}
