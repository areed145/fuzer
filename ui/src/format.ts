/** The only number formatters pages use, so one value reads the same on every page. Pass the number as the API
 * returned it; never round, scale or abbreviate it first. Pages supply the words for a missing value. */

const grouped = (value: number, digits: number) =>
  value.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** Money: whole units from 100 up ("$5,540,400"), cents below ("$12.50", "<$0.01"). Never "k" or "M". Another
 * currency follows the number ("5,520,150 NOK"). */
export function money(value: number, currency = "USD"): string {
  const size = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (currency === "USD" && size > 0 && size < 0.01) return `${sign}<$0.01`;
  const text = grouped(size, size >= 100 || size === 0 ? 0 : 2);
  if (currency === "USD") return `${sign}$${text}`;
  return currency === "unknown" ? `${sign}${text} (currency unknown)` : `${sign}${text} ${currency}`;
}

/** A share from 0 to 1 as a percentage: 0.873 is "87%", or "87.3%" with `digits` 1. */
export function percent(fraction: number, digits = 0): string {
  return `${Number((fraction * 100).toFixed(digits))}%`;
}

/** A measured quantity with its unit: "1,250 bbl", "87%", "3.46". At most two decimals. */
export function quantity(value: number, unit?: string | null): string {
  const text = value.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return !unit ? text : unit === "%" ? `${text}%` : `${text} ${unit}`;
}

/** A count: "12,480". */
export const count = (value: number): string => value.toLocaleString("en-US");

/** An estimate to two significant figures (#14): "60,000", not "59,928.3", which claims a precision a latest-test-rate
 * times hours estimate doesn't have. Shown after "about". */
export const estimate = (value: number): string => count(twoFigures(value));

/** A value rounded to two significant figures, for an estimate shown as money: `money(twoFigures(usd))`. */
export function twoFigures(value: number): number {
  if (!value) return 0;
  const p = 10 ** Math.max(0, Math.floor(Math.log10(Math.abs(value))) - 1);
  return Math.round(value / p) * p;
}

type At = string | number | Date | null | undefined;
const ZONED = /(?:Z|[+-]\d{2}:?\d{2})$/i;
const WRITTEN = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?/;

/** A value with an offset is an instant. A value without one is the plant's own clock, read as written. */
function read(at: At): { date: Date; plant: boolean; dayOnly: boolean } | null {
  if (at == null || at === "") return null;
  if (typeof at === "string" && !ZONED.test(at)) {
    const m = WRITTEN.exec(at);
    return m && { date: new Date(Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] ?? 0), +(m[5] ?? 0), +(m[6] ?? 0))), plant: true, dayOnly: m[4] === undefined };
  }
  const date = new Date(at);
  return Number.isNaN(date.getTime()) ? null : { date, plant: false, dayOnly: false };
}

function shape(at: At, missing: string, clock: boolean, seconds: boolean): string {
  const t = read(at);
  if (!t) return typeof at === "string" && at ? at : missing;
  const year = t.plant ? t.date.getUTCFullYear() : t.date.getFullYear();
  const text = t.date.toLocaleString("en-US", {
    month: "short", day: "numeric",
    ...(clock && !t.dayOnly ? { hour: "numeric", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}) } : {}),
    ...(year === new Date().getFullYear() ? {} : { year: "numeric" }),
    ...(t.plant ? { timeZone: "UTC" } : {}),
  });
  return clock && t.plant && !t.dayOnly ? `${text} plant time` : text;
}

/** The app's one time format: "Nov 14, 2:11 PM", with the year only when it is not this year. An instant (a value
 * with an offset) is shown in the viewer's clock. A value without an offset is the plant's own clock: it is shown as
 * written and marked "plant time", never converted. A day alone ("2026-11-17") stays that day. `missing` is the text
 * for no value. */
export const when = (at: At, missing = "", seconds = false): string => shape(at, missing, true, seconds);

/** The day alone, in the same format: "Nov 14", or "Dec 30, 2025" in another year. */
export const whenDay = (at: At, missing = ""): string => shape(at, missing, false, false);

/** How long ago or how soon, within a week: "3 min ago", "in 2 h". Beyond that, and for plant-clock values, `when`. */
export function ago(at: At, missing = ""): string {
  const t = read(at);
  if (!t || t.plant) return when(at, missing);
  const s = (t.date.getTime() - Date.now()) / 1000;
  const a = Math.abs(s);
  const span = a < 90 ? `${Math.round(a)} s` : a < 5400 ? `${Math.round(a / 60)} min` : a < 129600 ? `${Math.round(a / 3600)} h`
    : a < 7 * 86400 ? `${Math.round(a / 86400)} d` : null;
  return !span ? when(at, missing) : s < 0 ? `${span} ago` : `in ${span}`;
}

/** The calendar day of a date or an ISO time as "2026-11-17", for a date input or a link; today when left out. */
export const isoDay = (at: Date | string = new Date()): string => (typeof at === "string" ? at : at.toISOString()).split("T")[0];
