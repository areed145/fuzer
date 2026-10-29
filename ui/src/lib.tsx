// Small shared hooks and components.

import { useCallback, useEffect, useState, type ReactNode } from "react";

/** Hash-based routing: #/ , #/e/<id> , #/review. Keeps the API free to own real paths. */
export function useRoute(): { path: string[]; query: URLSearchParams } {
  const read = () => {
    const [p, q] = window.location.hash.replace(/^#/, "").split("?");
    return { path: (p || "/").split("/").filter(Boolean).map(decodeURIComponent), query: new URLSearchParams(q) };
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
  const [state, setState] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    let live = true;
    setState((s) => ({ ...s, loading: true, error: undefined }));
    fn().then(
      (data) => live && setState({ data, loading: false }),
      (e: Error) => live && setState({ error: e.message, loading: false }),
    );
    return () => {
      live = false;
    };
  }, [...deps, tick]);
  return { ...state, reload };
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

export function KindBadge({ kind }: { kind: string }) {
  return <span className={`badge kind kind-${kind}`}>{kind.replace("_", " ")}</span>;
}

export function SourceBadge({ source }: { source: string }) {
  return <span className={`badge source source-${source}`}>{source}</span>;
}

export function Dates({ from, to }: { from: string | null; to: string | null }) {
  if (!from && !to) return null;
  return (
    <span className="dates">
      {from ?? "…"} → {to ?? "now"}
    </span>
  );
}

export function Status({ loading, error, empty, children }: {
  loading?: boolean;
  error?: string;
  empty?: boolean;
  children?: ReactNode;
}) {
  if (error) return <div className="status error">Couldn't load: {error}</div>;
  if (loading) return <div className="status">Loading…</div>;
  if (empty) return <div className="status">{children ?? "Nothing here."}</div>;
  return null;
}
