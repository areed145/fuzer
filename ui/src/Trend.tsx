import "./styles/trends.css";
// Small-multiple trend charts: one series per chart (the title names it, so no legend), 2px line,
// recessive grid, shaded upset window, operating limits as labelled dashed lines, crosshair + tooltip on
// hover, and a table view.

import { useState } from "react";
import { api, type MeasurementSeries } from "./api";
import { entityHref, Status, useAsync } from "./lib";
import { Table, Card as UICard, Disclosure } from "./ui";

/** One operating limit on a point (#101): an alarm limit from its source, one side of an integrity
 * operating window, or the normal range learned from history when no source gives one. */
export interface LimitLine {
  type: "hihi" | "hi" | "lo" | "lolo";
  value: number;
  basis: "alarm" | "iow" | "learned";
  source?: string;
  level?: string;
  iow?: string;
  allowed_minutes?: number | null;
}

export interface Series {
  id: string;
  point: string;
  description: string | null;
  uom: string | null;
  values: [string, number][];
  min: number;
  max: number;
  mean: number;
  last: number;
  limits?: LimitLine[];
}

const limitName = (l: LimitLine) =>
  l.basis === "iow" ? `${l.iow ?? "IOW"} ${l.type === "hi" ? "high" : "low"}`
    : `${l.basis === "learned" ? "learned " : ""}${l.type === "hihi" ? "HiHi" : l.type === "lolo" ? "LoLo" : l.type === "hi" ? "Hi" : "Lo"}`;
const limitTitle = (l: LimitLine) =>
  l.basis === "iow" ? `${l.level ?? ""} IOW${l.allowed_minutes ? `, ${l.allowed_minutes / 60} h allowed outside` : ""}`
    : l.basis === "learned" ? "normal range learned from history while running" : `alarm limit from ${l.source}`;

const W = 600;
const H = 120;

const LEAD_IN_MS = 12 * 3600 * 1000;

export function TrendGrid({ series, upsets }: { series: Series[]; upsets: string[] }) {
  return (
    <div className="trend-grid">
      {series.map((s) => <Trend key={s.id} s={s} upsets={upsets} />)}
    </div>
  );
}

function Trend({ s, upsets, wide = false }: { s: Series; upsets: string[]; wide?: boolean }) {
  const [hover, setHover] = useState<number | null>(null);
  const n = s.values.length;
  // Limits near the values widen the scale so their line shows; ones far off are named in the caption.
  const span = Math.max(s.max - s.min, Math.abs(s.mean) * 0.05, 1e-6);
  const limits = s.limits ?? [];
  const near = limits.filter((l) => l.value >= s.min - span && l.value <= s.max + span);
  const far = limits.filter((l) => !near.includes(l));
  const vmin = Math.min(s.min, ...near.map((l) => l.value));
  const vmax = Math.max(s.max, ...near.map((l) => l.value));
  const pad = (vmax - vmin) * 0.1 || 1;
  const lo = vmin - pad;
  const hi = vmax + pad;
  const x = (i: number) => (i / Math.max(1, n - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const t0 = Date.parse(s.values[0]?.[0]);
  const t1 = Date.parse(s.values[n - 1]?.[0]);
  const tx = (iso: string) => ((Date.parse(iso) - t0) / Math.max(1, t1 - t0)) * W;
  const lead = (LEAD_IN_MS / Math.max(1, t1 - t0)) * W; // the 12 h leading into an incident
  const path = s.values.map(([, v], i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width) * (n - 1)))));
  };
  return (
    <figure className={wide ? "trend wide" : "trend"}>
      <figcaption>
        <span className="mono small">{s.point}</span> <span className="muted small">{s.description} ({s.uom})</span>
        {far.length > 0 && (
          <span className="muted small"> · off scale: {far.map((l) => `${limitName(l)} ${fmt(l.value)}`).join(", ")}</span>
        )}
      </figcaption>
      <div className="trend-plot">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
             role="img" aria-label={`${s.point} from ${fmt(s.min)} to ${fmt(s.max)} ${s.uom ?? ""}`}>
          {[0.25, 0.5, 0.75].map((f) => <line key={f} className="trend-grid-line" x1={0} x2={W} y1={H * f} y2={H * f} />)}
          {upsets.map((u) => (
            <rect key={u} className="trend-upset" x={Math.max(0, tx(u) - lead)} width={Math.min(lead, tx(u))} y={0} height={H} />
          ))}
          {near.map((l) => (
            <line key={`${l.basis}${l.iow ?? ""}${l.type}`} className={`trend-limit ${l.basis} ${l.type}`}
                  x1={0} x2={W} y1={y(l.value)} y2={y(l.value)}>
              <title>{`${limitName(l)} ${fmt(l.value)} ${s.uom ?? ""}: ${limitTitle(l)}`}</title>
            </line>
          ))}
          <path d={path} className="trend-line" />
          {hover !== null && (
            <>
              <line className="trend-cross" x1={x(hover)} x2={x(hover)} y1={0} y2={H} />
              <circle className="trend-dot" cx={x(hover)} cy={y(s.values[hover][1])} r={4} />
            </>
          )}
        </svg>
        <span className="trend-axis top">{fmt(hi)}</span>
        <span className="trend-axis bottom">{fmt(lo)}</span>
        {near.map((l) => (
          <span key={`${l.basis}${l.iow ?? ""}${l.type}`} className={`trend-limit-label ${l.basis} ${l.type}`}
                style={{ top: `${(y(l.value) / H) * 100}%` }} title={limitTitle(l)}>
            {limitName(l)} {fmt(l.value)}
          </span>
        ))}
        {hover !== null && (
          <div className="trend-tip" style={{ left: `${(hover / Math.max(1, n - 1)) * 100}%` }}>
            <b>{fmt(s.values[hover][1])}</b> {s.uom}
            <div className="muted">{s.values[hover][0].replace("T", " ")}</div>
          </div>
        )}
      </div>
      <div className="trend-x muted small">
        <span>{s.values[0]?.[0].replace("T", " ")}</span>
        <span>{s.values[n - 1]?.[0].replace("T", " ")}</span>
      </div>
      <Disclosure className="small" label={<>Table</>}>
        <Table variant="list" density="compact">
          <tbody>
            {s.values.filter((_, i) => i % Math.ceil(n / 24) === 0).map(([t, v]) => (
              <tr key={t}><td className="mono">{t.replace("T", " ")}</td><td>{fmt(v)}</td></tr>
            ))}
          </tbody>
        </Table>
      </Disclosure>
    </figure>
  );
}

const quantityLabel = (q: string) => q.replaceAll("_", " ");
const num = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v.toPrecision(2));

/** Measurements PI doesn't have (well tests, lab results, wall thickness at CMLs) on an entity, one small
 * chart per quantity and location, placed in time (readings are irregular); grey dots are below the
 * detection limit. Hidden when there are none. */
export function MeasurementsCard({ id }: { id: string }) {
  const m = useAsync(() => api.measurements(id), [id]);
  const series = m.data?.series ?? [];
  if (!m.loading && !m.error && series.length === 0) return null;
  return (
    <UICard  as="section">
      {/* The trend above draws these over its window (P5.13d); this is their whole history, closed until asked for. */}
      <Disclosure label={<strong>Tests and samples, whole history ({series.length})</strong>}>
        <p className="small">Well tests, lab results and inspection readings, landed from their sources.</p>
        <Status loading={m.loading && !m.data} error={m.error} />
        <div className="trend-grid">
          {series.map((s) => <MeasurementChart key={`${s.quantity}|${s.location}|${s.of.id}`} s={s} self={id} />)}
        </div>
      </Disclosure>
      {m.data?.truncated && <p className="small">Showing the latest readings only.</p>}
    </UICard>
  );
}

function MeasurementChart({ s, self }: { s: MeasurementSeries; self: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const vals = s.values ?? [];
  const ts = vals.map((v) => Date.parse(v.at));
  const t0 = ts[0];
  const t1 = ts[ts.length - 1];
  const pad = (s.max - s.min) * 0.1 || Math.abs(s.max) * 0.05 || 1;
  const lo = s.min - pad;
  const hi = s.max + pad;
  const x = (t: number) => (t1 > t0 ? ((t - t0) / (t1 - t0)) * (W - 12) + 6 : W / 2);
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
  const path = vals.map((v, i) => `${i ? "L" : "M"}${x(ts[i]).toFixed(1)},${y(v.value).toFixed(1)}`).join("");
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    const t = t0 + ((e.clientX - r.left) / r.width) * (t1 - t0);
    let best = 0;
    ts.forEach((ti, i) => { if (Math.abs(ti - t) < Math.abs(ts[best] - t)) best = i; });
    setHover(best);
  };
  const h = hover !== null ? vals[hover] : null;
  return (
    <figure className="trend">
      <figcaption>
        <b className="small">{quantityLabel(s.quantity)}</b>{" "}
        <span className="muted small">
          {s.location}{s.of.id !== self && <> on <a href={entityHref(s.of.id)}>{s.of.label ?? s.of.id}</a></>}
          {" "}· latest {s.latest.qualifier ?? ""}{num(s.latest.value)} {s.unit} ({s.latest.at.slice(0, 10)})
          {s.per_year !== null && ` · ${s.per_year > 0 ? "+" : ""}${num(s.per_year)} ${s.unit}/year`}
        </span>
      </figcaption>
      <div className="trend-plot">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
             role="img" aria-label={`${quantityLabel(s.quantity)}: ${s.count} readings from ${num(s.min)} to ${num(s.max)} ${s.unit ?? ""}`}>
          {[0.25, 0.5, 0.75].map((f) => <line key={f} className="trend-grid-line" x1={0} x2={W} y1={H * f} y2={H * f} />)}
          <path d={path} className="trend-line thin" />
          {/* zero-length round-capped strokes: dots that stay round however the chart is stretched */}
          {vals.map((v, i) => (
            <path key={i} d={`M${x(ts[i]).toFixed(1)},${y(v.value).toFixed(1)}h0`}
                  className={`trend-mark${v.qualifier ? " below-limit" : ""}${hover === i ? " on" : ""}`} />
          ))}
        </svg>
        <span className="trend-axis top">{num(hi)}</span>
        <span className="trend-axis bottom">{num(lo)}</span>
        {h && (
          <div className="trend-tip" style={{ left: `${(x(ts[hover!]) / W) * 100}%` }}>
            <b>{h.qualifier ?? ""}{num(h.value)}</b> {s.unit}{h.reported && <span className="muted"> (reported {h.reported})</span>}
            <div className="muted">{h.at.slice(0, 16).replace("T", " ")}{h.method && ` · ${h.method}`}</div>
          </div>
        )}
      </div>
      <div className="trend-x muted small">
        <span>{s.first.slice(0, 10)}</span>
        <span>{s.count} readings</span>
        <span>{s.latest.at.slice(0, 10)}</span>
      </div>
    </figure>
  );
}
