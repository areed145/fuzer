// The trend view's shape (GET /entities/{id}/trend-view) and the timeline's, with the names a trace reads by. The
// chart itself draws a trend spec (trends/figure.ts); this stays for the asset page's latest readings (LiveReadings).

export interface TrendLimit { type: "hihi" | "hi" | "lo" | "lolo"; value: number; basis: "alarm" | "iow" | "learned"; iow?: string }
export interface TrendTrace {
  id: string;
  point: string;
  description: string | null;
  uom: string | null;
  role?: string | null;
  role_label?: string | null;
  on?: { id: string; label: string | null; relation: string } | null;
  axis: string;
  show: boolean;
  values: [string, number][];
  min: number;
  max: number;
  mean: number;
  last: number;
  limits?: TrendLimit[];
  modelled?: boolean;
  /** A landed reading (a well test, a lab sample): drawn as steps with a marker per reading (P5.13d). */
  sampled?: boolean;
  methods?: (string | null)[];
  /** The record the trace's table row opens, when it is not the trace's own id. */
  link?: string;
}
export interface TrendGroup { key: string; label: string; show: boolean; traces: TrendTrace[] }
export interface TrendViewData {
  provider: string;
  centre: string;
  start: string;
  end: string;
  step_minutes: number;
  upsets: string[];
  unit: string;
  unit_label: string | null;
  classes: string[];
  template: { key: string; title: string; source?: "platform" | "bundle" };
  groups: TrendGroup[];
  other: TrendTrace[];
  missing: { role: string; role_label: string; relation: string }[];
  errors?: string[];
  error?: string;
}
export interface TimelineEvent {
  id: string | null;
  item_id?: number;
  kind: string;
  label: string | null;
  start: string;
  end: string | null;
  text: string | null;
  detail: string | null;
  status?: string | null;
}
export interface TimelineLane { key: string; label: string; events: TimelineEvent[] }
export interface TimelineData { unit: string; start: string; end: string; lanes: TimelineLane[] }



/** What a trace is called on a chip and in the hover: its role, and the neighbour it is on when not the unit. */
export function traceName(t: TrendTrace): string {
  const what = t.role_label ?? (t.description ? sentence(t.description) : t.point);
  return t.on && t.on.relation !== "self" && t.on.label ? `${what} · ${t.on.label}` : what;
}

/** Every trace's name: its role, and where two traces in a group would read the same (two bearing temperatures, four
 * exhaust thermocouples), its description instead. */
export function names(view: TrendViewData): Map<string, string> {
  const out = new Map<string, string>();
  for (const g of [...view.groups, { traces: view.other }]) {
    const base = g.traces.map(traceName);
    g.traces.forEach((t, i) => {
      const twins = g.traces.filter((_, j) => base[j] === base[i]);
      const twin = twins.length > 1;
      const own = twin ? sentence(distinct(t.description ?? t.point, twins.map((x) => x.description ?? x.point))) : "";
      out.set(t.id, twin ? (t.on && t.on.relation !== "self" && t.on.label ? `${own} · ${t.on.label}` : own) : base[i]);
    });
  }
  return out;
}

/** A name in sentence case, as every other series reads: "Thrust Bearing Temperature" reads "Thrust bearing
 * temperature". Codes and acronyms (all capitals, or with digits) keep their case. */
export function sentence(s: string): string {
  return s.split(" ").map((w, i) => (i && /^[A-Z][a-z]+(-[A-Za-z][a-z]+)*$/.test(w) ? w.toLowerCase() : w)).join(" ");
}

/** A point as a person reads it when no trace names it: its tag, without the historian's server prefix and value
 * suffix ("OK_GGCP:20-FT-2100B.PV" reads "20-FT-2100B"). */
export function pointTag(point: string): string {
  return point.replace(/^[^:]*:/, "").replace(/\.(PV|CV|OUT|SP|MV)$/i, "");
}

/** A description less the words every twin starts with: "Train B Compressor Thrust Bearing Temperature" beside
 * "Train B Compressor Journal Bearing Temperature" reads "Thrust Bearing Temperature". What is left is never a bare
 * number: "Exhaust Thermocouple 4" keeps the words that say what 4 is (#1082). */
function distinct(own: string, all: string[]): string {
  const words = all.map((x) => x.split(" "));
  let k = 0;
  while (words.every((w) => w.length > k + 1 && w[k] === words[0][k])) k += 1;
  const mine = own.split(" ");
  if (!/[A-Za-z]{2}/.test(mine.slice(k).join(" "))) k = Math.max(0, k - 2);
  return mine.slice(k).join(" ");
}

/** A value as a label reads it: three significant figures, no exponent below a million. */
export function reading(v: number): string {
  if (!Number.isFinite(v)) return "";
  const a = Math.abs(v);
  return a >= 1000 ? Math.round(v).toLocaleString("en-US") : a >= 1 ? String(Number(v.toPrecision(3))) : String(Number(v.toPrecision(2)));
}
