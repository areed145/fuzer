// Small-multiple trend charts: one series per chart (the title names it, so no legend), 2px line,
// recessive grid, shaded upset window, crosshair + tooltip on hover, and a table view.

import { useState } from "react";

export interface Series {
  id: string;
  point: string;
  description: string | null;
  uom: string | null;
  values: [string, number][];
  min: number;
  max: number;
}

const W = 600;
const H = 120;

export function TrendGrid({ series, upsets }: { series: Series[]; upsets: string[] }) {
  return (
    <div className="trend-grid">
      {series.map((s) => <Trend key={s.id} s={s} upsets={upsets} />)}
    </div>
  );
}

function Trend({ s, upsets }: { s: Series; upsets: string[] }) {
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
  const path = s.values.map(([, v], i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const fmt = (v: number) => (Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(2));
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width) * (n - 1)))));
  };
  return (
    <figure className="trend">
      <figcaption>
        <span className="mono small">{s.point}</span> <span className="muted small">{s.description} ({s.uom})</span>
      </figcaption>
      <div className="trend-plot">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" onMouseMove={onMove} onMouseLeave={() => setHover(null)}
             role="img" aria-label={`${s.point} from ${fmt(s.min)} to ${fmt(s.max)} ${s.uom ?? ""}`}>
          {[0.25, 0.5, 0.75].map((f) => <line key={f} className="trend-grid-line" x1={0} x2={W} y1={H * f} y2={H * f} />)}
          {upsets.map((u) => (
            <rect key={u} className="trend-upset" x={Math.max(0, tx(u) - (12 / 72) * W)} width={(12 / 72) * W} y={0} height={H} />
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
