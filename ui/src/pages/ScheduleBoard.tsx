/** The schedule as an interactive Gantt board (P5.18): one row per shared resource (or per activity), each activity a
 * puck on the timeline. Drag a puck to hold its start, drag its right edge to change its duration, drag it onto another
 * resource's row to hand the work over, drag the dot after a selected puck onto another to link them, click it for its
 * properties. Every change recalculates the schedule, so the pucks land where the logic and the crews allow. The
 * selected activity shows its logic as arrows; a resource's row carries a strip of its daily load against capacity. */
import { type CSSProperties, type PointerEvent as RPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import type { ScheduleAnalysis, ScheduleRow } from "../api";
import { acronyms, dayStamp, stamp } from "../wording";

const HOUR = 3_600_000, DAY = 24 * HOUR;
const PUCK = 24, GAP = 4, PAD = 5, STRIP = 6, HEAD = 28;

type Grab = "move" | "stretch" | "link";
export type BoardRows = "resources" | "activities";
export type BoardChange = { id: number; code: string } & ({ kind: "move"; start: Date; earliest: boolean } | { kind: "stretch"; hours: number }
  | { kind: "reassign"; from: string | null; to: string | null; quantity: number } | { kind: "link"; to: number; toCode: string });

interface Puck { key: string; a?: ScheduleRow; title: string; start: number; end: number; lane: number; qty?: number; other?: boolean }
interface Row { key: string; label: string; sub?: string; over?: boolean; group?: boolean; pucks: Puck[]; lanes: number; top: number; height: number;
  strip?: { at: number; level: "none" | "ok" | "full" | "over" }[]; float?: [number, number] }

const ms = (at: string) => new Date(at).getTime();
const box = (left: number, width: number): CSSProperties => ({ left, width });
const num = (n: number) => (Number.isInteger(n) ? String(n) : n.toFixed(1));

/** Pucks packed into lanes: each takes the first lane free at its start, so overlapping work stacks. */
function pack(pucks: Omit<Puck, "lane">[]): { pucks: Puck[]; lanes: number } {
  const ends: number[] = [];
  const out = [...pucks].sort((x, y) => x.start - y.start || x.end - y.end).map((p) => {
    let lane = ends.findIndex((e) => e <= p.start);
    if (lane < 0) lane = ends.length;
    ends[lane] = Math.max(p.end, p.start + 1);
    return { ...p, lane };
  });
  return { pucks: out, lanes: Math.max(1, ends.length) };
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const watch = new ResizeObserver(() => setWidth(el.clientWidth));
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  return [ref, width] as const;
}

export function ScheduleBoard({ s, rows: mode, zoom, selected, onSelect, onChange, canEdit }: {
  s: ScheduleAnalysis; rows: BoardRows; zoom: number; selected: number | null; onSelect: (id: number | null) => void;
  onChange: (c: BoardChange) => void; canEdit: boolean;
}) {
  const [ref, width] = useWidth();
  // an activity row is named beside its bar, so its label column only needs the code
  const labelW = mode === "activities" ? (width && width < 560 ? 64 : 84) : width && width < 560 ? 96 : 200;
  const [drag, setDrag] = useState<{ id: number; kind: Grab; dx: number; dy: number; row?: string } | null>(null);
  const press = useRef<{ id: number; kind: Grab; x: number; y: number; row: string; moved: boolean } | null>(null);
  const body = useRef<HTMLDivElement>(null);

  const { t0, t1 } = useMemo(() => {
    const all = s.activities.flatMap((a) => [ms(a.start), ms(a.finish), ms(a.late_finish)]);
    all.push(ms(s.project.start_at));
    if (s.project.finish_by) all.push(ms(s.project.finish_by));
    const lo = Math.floor(Math.min(...all) / DAY) * DAY, hi = Math.ceil(Math.max(...all) / DAY) * DAY + DAY;
    return { t0: lo, t1: hi };
  }, [s]);
  const trackW = Math.max(240, (Math.max(width, 320) - labelW) * zoom);
  const pxPerMs = trackW / (t1 - t0);
  const x = (t: number) => (t - t0) * pxPerMs;

  const rows = useMemo(() => {
    const out: Row[] = [];
    const add = (r: Omit<Row, "top" | "height" | "pucks" | "lanes">, list: Omit<Puck, "lane">[]) => {
      const { pucks, lanes } = pack(list);
      const top = out.length ? out[out.length - 1].top + out[out.length - 1].height : 0;
      out.push({ ...r, pucks, lanes, top, height: r.group ? HEAD : PAD * 2 + lanes * PUCK + (lanes - 1) * GAP + (r.strip ? STRIP + 2 : 0) });
    };
    const puck = (a: ScheduleRow, key: string, qty?: number) => ({ key, a, title: `${a.code ?? ""} ${acronyms(a.name)}`.trim(), start: ms(a.start), end: ms(a.finish), qty });
    if (mode === "activities") {
      // grouped like a planner's layout: a band per work order (or "General"), its activities under it
      const groups = new Map<string, ScheduleRow[]>();
      for (const a of s.activities) {
        const g = a.group ? `${a.work_order ? `${a.work_order} · ` : ""}${acronyms(a.group)}` : a.work_order ? `Order ${a.work_order}` : "General";
        groups.set(g, [...(groups.get(g) ?? []), a]);
      }
      for (const [g, list] of groups) {
        const from = Math.min(...list.map((a) => ms(a.start))), to = Math.max(...list.map((a) => ms(a.finish)));
        if (groups.size > 1) add({ key: `g-${g}`, label: g, group: true, float: [from, to] }, []);
        for (const a of list) {
          add({ key: `a${a.id}`, label: a.code ?? String(a.id),
                float: a.float_hours > 0 ? [ms(a.finish), ms(a.late_finish)] : undefined }, [puck(a, `a${a.id}`)]);
        }
      }
      return out;
    }
    const start = ms(s.project.start_at);
    for (const r of s.resources) {
      const mine = s.activities.filter((a) => a.needs[r.code]).map((a) => puck(a, `${r.code}-${a.id}`, a.needs[r.code]));
      const others = s.other_work.filter((o) => o.resource === r.code).map((o) => ({ key: `${r.code}-o${o.id}`, title: acronyms(o.title),
        start: ms(o.start), end: ms(o.end), qty: o.quantity, other: true }));
      const days = s.profiles?.levelled?.[r.code] ?? [];
      const use = (d: (typeof days)[number]) => d.schedule + d.other;
      const peak = Math.max(0, ...days.map(use));
      const over = days.some((d) => d.capacity != null && use(d) > d.capacity + 1e-6);
      add({ key: r.code, label: r.name, sub: `${num(peak)} of ${num(r.capacity)} ${r.unit ?? ""}`.trim(), over,
            strip: days.map((d, i) => ({ at: start + i * DAY, level: !use(d) ? "none" : d.capacity != null && use(d) > d.capacity + 1e-6 ? "over"
              : d.capacity != null && use(d) >= d.capacity - 1e-6 ? "full" : "ok" })) }, [...mine, ...others]);
    }
    const free = s.activities.filter((a) => !Object.keys(a.needs).length);
    if (free.length) add({ key: "none", label: "No shared resource" }, free.map((a) => puck(a, `none-${a.id}`)));
    return out;
  }, [s, mode]);
  const height = rows.length ? rows[rows.length - 1].top + rows[rows.length - 1].height : 0;

  // Where each activity's first puck sits, for the logic arrows.
  const at = useMemo(() => {
    const m = new Map<number, { x1: number; x2: number; y: number }>();
    for (const r of rows) for (const p of r.pucks) {
      if (p.a && !m.has(p.a.id)) m.set(p.a.id, { x1: x(p.start), x2: x(p.end), y: r.top + PAD + p.lane * (PUCK + GAP) + PUCK / 2 });
    }
    return m;
  }, [rows, pxPerMs, t0]);
  const sel = selected != null ? s.activities.find((a) => a.id === selected) : undefined;
  const arrows = useMemo(() => {
    const byCode = new Map(s.activities.map((a) => [a.code, a]));
    const links: { from: ScheduleRow; to: ScheduleRow; type: string; on: boolean }[] = [];
    for (const a of s.activities) for (const p of a.predecessors) {
      const from = byCode.get(p.code);
      const on = !!sel && (a.id === sel.id || from?.id === sel.id);
      // the activity view draws all the logic; the resource view only the selected activity's
      if (from && (on || mode === "activities")) links.push({ from, to: a, type: p.type, on });
    }
    return links.sort((l, m) => Number(l.on) - Number(m.on)).flatMap(({ from, to, type, on }) => {
      const f = at.get(from.id), t = at.get(to.id);
      if (!f || !t) return [];
      const x1 = type[0] === "S" ? f.x1 : f.x2, x2 = type[1] === "F" ? t.x2 : t.x1;
      // out of the predecessor, down its side to the successor's row, then along to it; around when it starts earlier
      const side = t.y > f.y ? PUCK / 2 + GAP / 2 + 1 : -(PUCK / 2 + GAP / 2 + 1);
      const d = x2 >= x1 + 12 ? `M${x1} ${f.y} H${x1 + 6} V${t.y} H${x2}`
        : `M${x1} ${f.y} H${x1 + 6} V${t.y - side} H${x2 - 8} V${t.y} H${x2}`;
      return [{ key: `${from.id}-${to.id}`, d, on, tip: `${x2 - 5},${t.y - 3.5} ${x2},${t.y} ${x2 - 5},${t.y + 3.5}` }];
    });
  }, [sel, s, at, mode]);

  const ticks = useMemo(() => {
    const dayW = DAY * pxPerMs, every = Math.max(1, Math.ceil(56 / dayW));
    const out: number[] = [];
    for (let t = t0, i = 0; t < t1; t += DAY, i++) if (i % every === 0) out.push(t);
    return { at: out, grid: `${dayW}px` };
  }, [t0, t1, pxPerMs]);

  const editable = (a?: ScheduleRow) => canEdit && !!a && a.status !== "done" && a.status !== "in_progress";
  const down = (e: RPointerEvent<HTMLElement>, a: ScheduleRow, kind: Grab, row: string) => {
    if (!(kind === "link" ? canEdit : editable(a)) || e.button > 0) return;
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    press.current = { id: a.id, kind, x: e.clientX, y: e.clientY, row, moved: false };
  };
  /** The resource row under the pointer, when the work is dragged off its own row onto one that does not already carry it. */
  const rowUnder = (clientY: number, from: string, a?: ScheduleRow) => {
    if (mode !== "resources" || !body.current || !a) return undefined;
    const y = clientY - body.current.getBoundingClientRect().top;
    const r = rows.find((q) => y >= q.top && y < q.top + q.height);
    return r && r.key !== from && !a.needs[r.key] ? r : undefined;
  };
  const move = (e: RPointerEvent<HTMLElement>) => {
    const p = press.current;
    if (!p) return;
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    if (!p.moved && Math.abs(dx) < 5 && Math.abs(dy) < 5) return;
    p.moved = true;
    setDrag({ id: p.id, kind: p.kind, dx, dy, row: p.kind === "move" ? rowUnder(e.clientY, p.row, s.activities.find((a) => a.id === p.id))?.key : undefined });
  };
  const up = (e: RPointerEvent<HTMLElement>, a: ScheduleRow) => {
    const p = press.current;
    press.current = null;
    setDrag(null);
    if (!p || !p.moved) { if (!p || p.kind !== "link") onSelect(selected === a.id ? null : a.id); return; }
    const who = { id: a.id, code: a.code ?? "" };
    if (p.kind === "link") {
      // dropped on another activity: that one now follows this one, finish to start
      const hit = (document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null)?.closest<HTMLElement>("[data-act]");
      const to = s.activities.find((q) => q.id === Number(hit?.dataset.act));
      if (to && to.id !== a.id && !to.predecessors.some((q) => q.code === a.code)) onChange({ ...who, kind: "link", to: to.id, toCode: to.code ?? "" });
      return;
    }
    const onto = p.kind === "move" ? rowUnder(e.clientY, p.row, a) : undefined;
    if (onto) {
      onChange({ ...who, kind: "reassign", from: p.row === "none" ? null : p.row, to: onto.key === "none" ? null : onto.key, quantity: a.needs[p.row] ?? 1 });
      return;
    }
    const hours = Math.round((e.clientX - p.x) / pxPerMs / HOUR);
    if (!hours) return;
    if (p.kind === "stretch") onChange({ id: a.id, code: a.code ?? "", kind: "stretch", hours: Math.max(1, (a.duration_hours ?? 0) + hours) });
    else {
      const start = new Date(ms(a.start) + hours * HOUR);
      // dragged back to (or before) where its logic lets it start: nothing holds it any more
      onChange({ id: a.id, code: a.code ?? "", kind: "move", start, earliest: start.getTime() <= ms(a.early_start) && !a.held_back_hours });
    }
  };

  const dragged = drag ? s.activities.find((a) => a.id === drag.id) : undefined;
  const dragHours = drag ? Math.round(drag.dx / pxPerMs / HOUR) : 0;
  const onto = drag?.row ? rows.find((r) => r.key === drag.row) : undefined;
  const hint = dragged && drag ? (drag.kind === "link" ? `Drop on the activity that follows ${dragged.code}`
    : onto ? (onto.key === "none" ? `${dragged.code} needs no shared resource` : `${dragged.code} goes to ${onto.label}`) : drag.kind === "move" ? `${dragged.code} starts ${stamp(new Date(ms(dragged.start) + dragHours * HOUR))}`
    : `${dragged.code} takes ${num(Math.max(1, (dragged.duration_hours ?? 0) + dragHours))} h`) : null;
  const vars = { "--gb-label": `${labelW}px`, "--gb-track": `${trackW}px`, "--gb-day": ticks.grid } as CSSProperties;
  const ends = s.project.finish_by ? x(ms(s.project.finish_by)) : null;
  const now = Date.now() > t0 && Date.now() < t1 ? x(Date.now()) : null;
  const line = (left: number): CSSProperties => ({ left });
  const svg: CSSProperties = { width: trackW, height };

  return (
    <div className="gb" ref={ref} style={vars} data-rows={mode}>
      <div className="gb-scroll" onClick={() => onSelect(null)}>
        <div className="gb-inner">
          <div className="gb-row gb-head" aria-hidden="true">
            <div className="gb-label" />
            <div className="gb-track gb-ticks">
              {ticks.at.map((t) => <span key={t} className="gb-tick" style={line(x(t))}>{dayStamp(new Date(t).toISOString())}</span>)}
            </div>
          </div>
          <div className="gb-body" ref={body}>
            {rows.map((r) => {
              const rowStyle: CSSProperties = { height: r.height };
              if (r.group) return <div key={r.key} className="gb-row gb-group" style={rowStyle}>
                <div className="gb-label"><strong>{r.label}</strong></div>
                <div className="gb-track">{r.float && <span className="gb-rollup" style={box(x(r.float[0]), Math.max(4, x(r.float[1]) - x(r.float[0])))} />}</div>
              </div>;
              return <div key={r.key} className={`gb-row${drag?.row === r.key ? " gb-drop" : ""}`} style={rowStyle}>
                <div className="gb-label">
                  <strong>{r.label}</strong>
                  {r.sub && <span className={`small${r.over ? " error" : ""}`}>{r.sub}</span>}
                </div>
                <div className="gb-track">
                  {r.float && <span className="gb-float" style={box(x(r.float[0]), Math.max(0, x(r.float[1]) - x(r.float[0])))} />}
                  {r.pucks.map((p) => {
                    const mine = drag && p.a && drag.id === p.a.id ? drag : null;
                    const left = x(p.start) + (mine?.kind === "move" ? mine.dx : 0);
                    const w = Math.max(6, x(p.end) - x(p.start) + (mine?.kind === "stretch" ? mine.dx : 0));
                    const style: CSSProperties = { left, width: w, top: PAD + p.lane * (PUCK + GAP) + (mine?.kind === "move" && mode === "resources" ? mine.dy : 0) };
                    const a = p.a;
                    if (!a) return <span key={p.key} className="gb-puck other" style={style} title={`${p.title} (other planned work, ${num(p.qty ?? 0)})`}>
                      <span className="gb-puck-text">{p.title}</span></span>;
                    const code = a.code ?? "";
                    // an activity row names its bar beside it, like a planner's layout; a resource row names what fits inside
                    const inside = mode === "activities" ? "" : w >= 120 ? p.title : w >= 48 ? code : "";
                    const cls = `gb-puck${w < 34 ? " narrow" : ""}${a.critical ? " critical" : ""}${a.held_back_hours > 0 ? " held" : ""}${selected === a.id ? " on" : ""}`
                      + `${a.status === "done" ? " done" : ""}${mine && mine.kind !== "link" ? " dragging" : ""}${editable(a) ? " can-move" : ""}`;
                    const linkable = canEdit && selected === a.id && at.get(a.id)?.y === r.top + PAD + p.lane * (PUCK + GAP) + PUCK / 2;
                    const dot: CSSProperties = { left: left + w + 3, top: PAD + p.lane * (PUCK + GAP) + 5 };
                    const beside: CSSProperties = { left: left + w + (linkable ? 22 : 6), top: PAD + p.lane * (PUCK + GAP) };
                    return <span key={p.key} className="gb-cell"><span role="button" tabIndex={0} className={cls} style={style} data-act={a.id} aria-pressed={selected === a.id}
                      aria-label={`${p.title}, ${stamp(a.start)} to ${stamp(a.finish)}${a.critical ? ", critical" : ""}`}
                      title={`${p.title}\n${stamp(a.start)} to ${stamp(a.finish)}${p.qty ? `\nNeeds ${num(p.qty)}` : ""}`}
                      onClick={(e) => e.stopPropagation()}
                      onPointerDown={(e) => (editable(a) ? down(e, a, "move", r.key) : undefined)} onPointerMove={move}
                      onPointerUp={(e) => (press.current ? up(e, a) : onSelect(selected === a.id ? null : a.id))}
                      onPointerCancel={() => { press.current = null; setDrag(null); }}
                      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(selected === a.id ? null : a.id); } }}>
                      {a.not_before && <span className="gb-pin" aria-hidden="true" />}
                      {inside && <span className="gb-puck-text">{inside}</span>}
                      {editable(a) && <span className="gb-grip" aria-hidden="true" onPointerDown={(e) => down(e, a, "stretch", r.key)} />}
                    </span>{linkable && <span className="gb-link" style={dot} title={`Drag onto the activity that follows ${code}`} aria-hidden="true"
                      onClick={(e) => e.stopPropagation()} onPointerDown={(e) => down(e, a, "link", r.key)} onPointerMove={move}
                      onPointerUp={(e) => up(e, a)} onPointerCancel={() => { press.current = null; setDrag(null); }} />}{mode === "activities" && <span className="gb-beside" aria-hidden="true" style={beside}>{acronyms(a.name)}</span>}</span>;
                  })}
                  {r.strip && <span className="gb-strip" aria-hidden="true">
                    {r.strip.map((d) => <span key={d.at} className={`gb-load ${d.level}`} style={box(x(d.at), DAY * pxPerMs)} />)}
                  </span>}
                </div>
              </div>;
            })}
            <div className="gb-over" aria-hidden="true">
              {ends != null && <span className="gb-window-end" style={line(ends)}><span>Window ends</span></span>}
              {now != null && <span className="gb-now" style={line(now)} />}
              <svg className="gb-arrows" style={svg}>
                {arrows.map((l) => <g key={l.key} className={l.on ? "on" : undefined}><path d={l.d} /><polygon points={l.tip} /></g>)}
                {drag?.kind === "link" && at.get(drag.id) && <line className="gb-linking" x1={at.get(drag.id)!.x2 + 8} y1={at.get(drag.id)!.y}
                  x2={at.get(drag.id)!.x2 + 8 + drag.dx} y2={at.get(drag.id)!.y + drag.dy} />}
              </svg>
            </div>
          </div>
        </div>
      </div>
      {hint && <p className="gb-hint" role="status">{hint}</p>}
    </div>
  );
}
