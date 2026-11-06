// Small shared hooks and components.

import { Badge as UIBadge, Button } from "./ui";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { Mark } from "./brand";

/** Hash-based routing: #/ , #/e/<id> , #/review. Keeps the API free to own real paths. */
export function useRoute(): { path: string[]; query: URLSearchParams } {
  const read = () => {
    const [p, q] = window.location.hash.replace(/^#/, "").split("?");
    try {
      return { path: (p || "/").split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q) };
    } catch (error) {
      console.warn("Fuzer received a malformed route", error);
      return { path: ["invalid-route"], query: new URLSearchParams() };
    }
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export function href(path: string, query?: Record<string, string | undefined>): string {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(query ?? {})) if (v) qs.set(k, v);
  return `#${path}${qs.size ? `?${qs}` : ""}`;
}

export const entityHref = (id: string) => href(`/e/${encodeURIComponent(id)}`);

/** Fires when data changes (e.g. after a review decision) so views can refresh. */
export const CHANGED = "fuzer:changed";
export const notifyChanged = () => window.dispatchEvent(new Event(CHANGED));

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const key = useMemo(() => ({}), deps);
  const [state, setState] = useState<{ key: object; data?: T; error?: string; loading: boolean }>({ key, loading: true });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  // Any data change (a review answer, a finished background rebuild) refreshes every view.
  useEffect(() => {
    window.addEventListener(CHANGED, reload);
    return () => window.removeEventListener(CHANGED, reload);
  }, [reload]);
  useEffect(() => {
    let live = true;
    setState((s) => s.key === key ? { ...s, loading: true, error: undefined } : { key, loading: true });
    fn().then(
      (data) => live && setState({ key, data, loading: false }),
      (e: Error) => live && setState({ key, error: e.message, loading: false }),
    );
    return () => {
      live = false;
    };
  }, [...deps, tick]);
  return { ...state, ...(state.key === key ? {} : { data: undefined, error: undefined, loading: true }), reload };
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function fmt(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

/** Operational timestamps have offsets; never assign a timezone to source-local event dates. */
export function timestamp(value: string | null | undefined): string {
  if (!value) return "Time unknown";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Time unknown";
  if (!/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return `${value} (timezone not supplied)`;
  return date.toISOString().replace("T", " ").replace(".000Z", " UTC").replace("Z", " UTC");
}

export function KindBadge({ kind }: { kind: string }) {
  return <UIBadge className={`badge kind kind-${kind}`}>{kind.replace("_", " ")}</UIBadge>;
}

export function SourceBadge({ source }: { source: string }) {
  return <UIBadge className={`badge source source-${source}`}>{source}</UIBadge>;
}

export function Dates({ from, to }: { from: string | null; to: string | null }) {
  if (!from && !to) return null;
  return (
    <span className="dates">
      {from ?? "…"} → {to ?? "now"}
    </span>
  );
}

export function Status({ loading, error, empty, children, onRetry }: {
  loading?: boolean;
  error?: string;
  empty?: boolean;
  children?: ReactNode;
  onRetry?: () => void;
}) {
  if (error) return <div className="status error" role="alert">Couldn't load: {error}
    {onRetry && <> <Button disabled={loading} onClick={onRetry}>Try again</Button></>}
  </div>;
  if (loading) return <div className="status" role="status"><Mark />Loading…</div>;
  if (empty) return <div className="status">{children ?? "Nothing here."}</div>;
  return null;
}
