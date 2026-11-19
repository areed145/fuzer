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
