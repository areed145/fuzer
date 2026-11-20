/** A registered metric's value as Metrics and Home show it: one formatter, one target rule, one trend line, so the
 * two pages can't word or colour the same number differently. */
import type { MetricDeclaration } from "../api";
import { quantity } from "../format";

export const show = (v: number | null, unit: string) => (v == null ? "–" : quantity(v, unit));

/** Whether a value meets its target (#1078): at least it when higher is better, at most when lower. Null when either is
 * missing, so a number with no target is never coloured as good or bad. */
export function onTarget(m: Pick<MetricDeclaration, "better" | "target">, v: number | null | undefined): boolean | null {
  if (v == null || m.target == null || m.better === "none") return null;
  return m.better === "higher" ? v >= m.target : v <= m.target;
}

/** The value over earlier periods as a small line, its last point marked; a dashed line at the target. */
export function Sparkline({ points, target, label }: { points: { at: string; value: number }[]; target?: number | null; label: string }) {
  if (points.length < 2) return <span className="muted small">no history yet</span>;
  const vs = points.map((p) => p.value).concat(target == null ? [] : [target]);
  const lo = Math.min(...vs), hi = Math.max(...vs), span = hi - lo || 1;
  const W = 96, H = 24;
  const x = (i: number) => (i / (points.length - 1)) * (W - 4) + 2;
  const y = (v: number) => H - 2 - ((v - lo) / span) * (H - 4);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  return (
    <svg className="sparkline" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img"
         aria-label={`${label}: ${points.map((p) => `${p.at} ${p.value}`).join(", ")}`}>
      {target != null && <line x1={0} x2={W} y1={y(target)} y2={y(target)} className="sparkline-target" />}
      <path d={d} />
      <circle cx={x(points.length - 1)} cy={y(last.value)} r={2.2} />
    </svg>
  );
}

