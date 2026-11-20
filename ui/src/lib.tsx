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

/** A value kept in the page's link without a new history step (P5.14c): a filter typed on My work is still there when
 * Back returns to it, and a shared link opens with it. Changes rewrite the current entry, so Back doesn't walk through
 * each keystroke. */
export function useRouteState(name: string): [string, (value: string) => void] {
  const read = () => new URLSearchParams(window.location.hash.split("?")[1] ?? "").get(name) ?? "";
  const [value, setValue] = useState(read);
  const set = (next: string) => {
    setValue(next);
    const [path, qs] = window.location.hash.split("?");
    const params = new URLSearchParams(qs ?? "");
    if (next) params.set(name, next); else params.delete(name);
    history.replaceState(history.state, "", `${path || "#/"}${params.size ? `?${params}` : ""}`);
  };
  return [value, set];
}

export const entityHref = (id: string) => href(`/e/${encodeURIComponent(id)}`);
/** A work item's bar on Plan: the timeline opens a week before it starts with the bar open. */
export const planItemHref = (p: { id: number; start_at: string }) =>
  href("/plan", { item: String(p.id), from: new Date(new Date(p.start_at).getTime() - 7 * 86_400_000).toISOString().slice(0, 10) });

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
      (e: unknown) => live && setState({ key, error: errorMessage(e), loading: false }),
    );
    return () => {
      live = false;
    };
  }, [...deps, tick]);
  return { ...state, ...(state.key === key ? {} : { data: undefined, error: undefined, loading: true }), reload };
}

/** What went wrong, as text to show. */
export const errorMessage = (e: unknown): string => (e instanceof Error ? e.message : String(e));

/** The one way a button runs an async action: `busy` names the action in flight, `error` holds the failure, and a
 * success tells every view to refresh. A failed action is never retried. */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = async <T,>(name: string, fn: () => Promise<T>, after?: (r: T) => void) => {
    setBusy(name);
    setError(null);
    try {
      const r = await fn();
      after?.(r);
      notifyChanged();
      return r;
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };
  return { busy, error, run };
}

/** An action that goes in the history with the person's reason: it won't run without one, and a success clears the
 * reason and reports `after`. Settings, detector switches and thresholds use it. */
export function useReasonedAction(saved: () => void) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState("");
  const run = async (label: string, fn: (reason: string) => Promise<unknown>, after: string) => {
    setError(null);
    setDone("");
    if (reason.trim().length < 3) return setError("Give a short reason: it goes in the history.");
    setBusy(label);
    try {
      await fn(reason.trim());
      setDone(after);
      setReason("");
      saved();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };
  return { reason, setReason, busy, error, done, setDone, run };
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

// Field keys a person reads as labels (#392): standards and codes keep their own spelling, the rest read as words.
const FIELD_LABELS: Record<string, string> = {
  ec_code: "EC code", isa95_level: "ISA-95 level", iso14224_level: "ISO 14224 level", iso14224_class: "ISO 14224 class",
  sap_id: "SAP ID", pi_point: "PI point", api_number: "API number", uom: "Unit of measure", uwi: "UWI", id: "ID",
  start: "Started", end: "Ended", units: "Units affected", lat: "Latitude", lon: "Longitude", lng: "Longitude",
};

export function fieldLabel(key: string): string {
  if (FIELD_LABELS[key]) return FIELD_LABELS[key];
  const words = key.replace(/[_.]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function KindBadge({ kind, label }: { kind: string; label?: string }) {
  return <UIBadge data-kind={kind}>{label ?? kind.replace("_", " ")}</UIBadge>;
}

export function SourceBadge({ source }: { source: string }) {
  return <UIBadge mono>{/^[a-z]{2,4}$/.test(source) ? source.toUpperCase() : source}</UIBadge>;
}

export function Dates({ from, to }: { from: string | null; to: string | null }) {
  if (!from && !to) return null;
  return (
    <span className="dates">
      {from ?? "…"} → {to ?? "now"}
    </span>
  );
}

/** The state of a read. Pass the `useAsync` result as `of`: loading shows only until the first data arrives, and a
 * failed read always offers Try again. `empty` with children says the list has nothing in it. */
export function Status({ of, loading = !!of && of.loading && of.data === undefined, error = of?.error, empty, children, onRetry = of?.reload }: {
  of?: { loading: boolean; error?: string; data?: unknown; reload?: () => void };
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
  if (empty) return <div className="status status-empty">{children ?? "Nothing here."}</div>;
  return null;
}
