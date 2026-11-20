/** Now, on an operated asset's overview (#1076): whether it is running, and its key readings against their limits,
 * from the newest values the historian has. An engineer or operator opening a compressor wants this before its make
 * and serial. Every value is the trend view's own, with the time of the reading; generated values say so. */
import { api } from "./api";
import { when } from "./format";
import { href, useAsync } from "./lib";
import { reading, type TrendLimit, type TrendTrace, names } from "./trendFigure";
import { Badge, Card } from "./ui";

/** Roles that say most about a unit at a glance, first found first: is it turning, what it delivers, how it runs. */
const KEY_ROLES = ["speed", "discharge_pressure", "vibration", "discharge_temperature", "wellhead_pressure", "tubing_pressure",
  "flowline_pressure", "pressure", "level", "flow", "suction_flow", "power", "temperature"];
const MAX = 4;
const LIMIT_WORD: Record<TrendLimit["type"], string> = { hihi: "HH", hi: "H", lo: "L", lolo: "LL" };

/** Where a value sits against its alarm or operating limits: the limit it is past, else the nearest it is inside. */
export function against(value: number, limits: TrendLimit[] = []): { tone: "ok" | "warn" | "danger" | "muted"; text: string } {
  const set = limits.filter((l) => l.basis !== "learned");
  if (!set.length) return { tone: "muted", text: "no limit set" };
  const past = (l: TrendLimit) => (l.type.startsWith("hi") ? value > l.value : value < l.value);
  const worst = [...set].sort((a, b) => b.type.length - a.type.length).find(past);
  if (worst) {
    return { tone: worst.type.length === 4 ? "danger" : "warn",
             text: `${worst.type.startsWith("hi") ? "above" : "below"} ${LIMIT_WORD[worst.type]} ${reading(worst.value)}` };
  }
  const near = [...set].sort((a, b) => Math.abs(a.value - value) - Math.abs(b.value - value))[0];
  return { tone: "ok", text: `inside ${LIMIT_WORD[near.type]} ${reading(near.value)}` };
}

/** The readings to show: the key roles on the unit itself first, then each panel's first trace, at most four. */
export function keyReadings(traces: TrendTrace[]): TrendTrace[] {
  const own = traces.filter((t) => t.values.length && (!t.on || t.on.relation === "self" || t.on.relation === "driver"));
  const picked: TrendTrace[] = [];
  for (const role of KEY_ROLES) {
    const t = own.find((x) => x.role === role && !picked.includes(x));
    if (t) picked.push(t);
    if (picked.length === MAX) break;
  }
  for (const t of own) if (picked.length < MAX && !picked.includes(t)) picked.push(t);
  return picked;
}

/** Running or stopped, from a running-status point or the speed: under 5% of its high limit (or its window's peak)
 * reads stopped. Nothing to tell by, nothing said. */
export function running(traces: TrendTrace[]): boolean | null {
  const status = traces.find((t) => t.role === "running_status" && t.values.length);
  if (status) return status.last > 0.5;
  const speed = traces.find((t) => t.role === "speed" && t.values.length);
  if (!speed) return null;
  const scale = Math.max(speed.max, ...(speed.limits ?? []).filter((l) => l.type.startsWith("hi")).map((l) => l.value));
  return scale > 0 ? speed.last > 0.05 * scale : null;
}

const at = (iso: string) => when(iso.slice(0, 16));

/** The latest readings' read, shared with the asset page so it can draw its overview at once (#1069). */
export const readLatest = (id: string) => api.trendView(id, "latest", 6, 0).catch(() => null);

export function LiveReadings({ id, data }: { id: string; data?: Awaited<ReturnType<typeof readLatest>> }) {
  const own = useAsync(() => (data === undefined ? readLatest(id) : Promise.resolve(null)), [id, data === undefined]).data;
  const v = data === undefined ? own : data;
  if (!v || v.error || !Array.isArray(v.groups)) return null;
  const traces = v.groups.flatMap((g) => g.traces);
  const shown = keyReadings(traces);
  if (!shown.length) return null;
  const name = names(v);
  const state = running(traces);
  const last = shown.flatMap((t) => t.values.map(([when]) => when)).sort().pop() ?? v.end;
  return (
    <Card as="section" className="live-readings" aria-label="Now">
      <div className="row live-readings-head">
        <h2>Now</h2>
        {state !== null && <Badge tone={state ? "ok" : "warn"}>{state ? "Running" : "Stopped"}</Badge>}
        <span className="muted small">latest reading {at(last)} UTC</span>
        {v.provider === "synthetic" && <Badge title="Generated values: no PI or ADX historian is connected">Demo data</Badge>}
        <span className="spacer" />
        <a className="small" href={href(`/e/${encodeURIComponent(id)}`, { tab: "trends" })}>Timeline</a>
      </div>
      <dl className="live-readings-grid">
        {shown.map((t) => {
          const s = against(t.last, t.limits);
          return (
            <div key={t.id}>
              <dt>{name.get(t.id)}</dt>
              <dd><strong>{reading(t.last)}</strong>{t.uom ? ` ${t.uom}` : ""}</dd>
              <dd>{s.tone === "ok" || s.tone === "muted" ? <span className="muted small">{s.text}</span>
                : <Badge tone={s.tone}>{s.text}</Badge>}</dd>
            </div>
          );
        })}
      </dl>
    </Card>
  );
}
