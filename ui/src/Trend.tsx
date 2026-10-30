// Small-multiple trend charts: one series per chart (the title names it, so no legend), 2px line,
// recessive grid, shaded upset window, crosshair + tooltip on hover, and a table view.

import { useState } from "react";
import { api } from "./api";
import { Status, useAsync } from "./lib";

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

/** One PI point's trend on its own page: pick a window, step earlier / later, or jump to a time. The
 * default window is centred on the latest incident on the tag or well it measures. */
export function TimeseriesTrend({ id }: { id: string }) {
  const [hours, setHours] = useState(72);
  const [at, setAt] = useState<string | undefined>(undefined);
  const before = Math.round((hours * 2) / 3);
  const v = useAsync(() => api.values(id, at, before, hours - before), [id, at, hours]);
  const d = v.data;
  const s = d?.series[0];
  const shift = (sign: number) =>
    d && setAt(local(new Date(Date.parse(d.centre + "Z") + (sign * hours * 3600 * 1000) / 2).toISOString()));
  return (
    <section className="card">
      <h2>
        Trend{" "}
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
      <Status loading={v.loading && !d} error={v.error ?? d?.error} empty={!!d && !s}>
        No values for this point between {d?.start.replace("T", " ")} and {d?.end.replace("T", " ")}
        {d?.errors?.length ? `: ${d.errors[0]}` : "."}
      </Status>
      {s && (
        <>
          <Trend s={s} upsets={d!.upsets} wide />
          <p className="muted small">
            min {s.min} · max {s.max} · mean {s.mean} · last {s.last} {s.uom}
          </p>
        </>
      )}
    </section>
  );
}
