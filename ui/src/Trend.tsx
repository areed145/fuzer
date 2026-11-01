// Small-multiple trend charts: one series per chart (the title names it, so no legend), 2px line,
// recessive grid, shaded upset window, crosshair + tooltip on hover, and a table view.

import { useState } from "react";
import { api, type MeasurementSeries } from "./api";
import { entityHref, Status, useAsync } from "./lib";

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
}

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
  const pad = (s.max - s.min) * 0.1 || 1;
  const lo = s.min - pad;
  const hi = s.max + pad;
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
      </figcaption>
      <div className="trend-plot">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
             role="img" aria-label={`${s.point} from ${fmt(s.min)} to ${fmt(s.max)} ${s.uom ?? ""}`}>
          {[0.25, 0.5, 0.75].map((f) => <line key={f} className="trend-grid-line" x1={0} x2={W} y1={H * f} y2={H * f} />)}
          {upsets.map((u) => (
            <rect key={u} className="trend-upset" x={Math.max(0, tx(u) - lead)} width={Math.min(lead, tx(u))} y={0} height={H} />
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
      <details className="small">
        <summary>Table</summary>
        <table className="list">
          <tbody>
            {s.values.filter((_, i) => i % Math.ceil(n / 24) === 0).map(([t, v]) => (
              <tr key={t}><td className="mono">{t.replace("T", " ")}</td><td>{fmt(v)}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

const WINDOWS = [24, 72, 168, 720]; // hours: 1 d, 3 d, 7 d, 30 d
const label = (h: number) => `${h / 24} d`;
const local = (iso: string) => iso.slice(0, 16);

/** Trends on an entity's page: a timeseries' own values, or the points linked straight to a tag or well.
 * Pick a window, step earlier / later, or jump to a time. The default window is centred on the latest
 * incident on the tag or well. */
export function TrendCard({ id, direct = false }: { id: string; direct?: boolean }) {
  const [hours, setHours] = useState(72);
  const [at, setAt] = useState<string | undefined>(undefined);
  const before = Math.round((hours * 2) / 3);
  const v = useAsync(() => api.values(id, at, before, hours - before, direct), [id, at, hours, direct]);
  const d = v.data;
  const one = d?.series.length === 1 ? d.series[0] : undefined;
  const shift = (sign: number) =>
    d && setAt(local(new Date(Date.parse(d.centre + "Z") + (sign * hours * 3600 * 1000) / 2).toISOString()));
  return (
    <section className="card">
      <h2>
        {direct ? "Process values" : "Trend"}{" "}
        {d && (
          <span className="muted small">
            {d.provider === "synthetic" ? "synthetic values: no PI / ADX connection configured"
              : `from ${d.provider.toUpperCase()}`} · every {d.step_minutes} min
            {d.upsets.length > 0 && " · shaded: the 12 h leading into each incident"}
          </span>
        )}
      </h2>
      <div className="row trend-controls">
        {WINDOWS.map((h) => (
          <button key={h} className={h === hours ? "primary" : ""} onClick={() => setHours(h)}>{label(h)}</button>
        ))}
        <span className="spacer" />
        <button onClick={() => shift(-1)} disabled={!d}>← Earlier</button>
        <input type="datetime-local" aria-label="Centre the window on" value={d ? local(d.centre) : ""}
               onChange={(e) => e.target.value && setAt(e.target.value)} />
        <button onClick={() => shift(1)} disabled={!d}>Later →</button>
        <button className="link" onClick={() => setAt(undefined)} disabled={at === undefined}>Latest incident</button>
      </div>
      <Status loading={v.loading && !d} error={v.error ?? d?.error} empty={!!d && d.series.length === 0}>
        No values between {d?.start.replace("T", " ")} and {d?.end.replace("T", " ")}
        {d?.errors?.length ? `: ${d.errors[0]}` : "."}
      </Status>
      {one && (
        <>
          <Trend s={one} upsets={d!.upsets} wide />
          <p className="muted small">
            min {one.min} · max {one.max} · mean {one.mean} · last {one.last} {one.uom}
          </p>
        </>
      )}
      {d && d.series.length > 1 && <TrendGrid series={d.series} upsets={d.upsets} />}
    </section>
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
    <section className="card">
      <h2>
        Measurements{" "}
        <span className="muted small">well tests, lab results and inspection readings, landed from their sources</span>
      </h2>
      <Status loading={m.loading && !m.data} error={m.error} />
      <div className="trend-grid">
        {series.map((s) => <MeasurementChart key={`${s.quantity}|${s.location}|${s.of.id}`} s={s} self={id} />)}
      </div>
      {m.data?.truncated && <p className="muted small">Showing the latest readings only.</p>}
    </section>
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
