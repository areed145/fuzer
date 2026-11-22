import { api } from "../api";

/* Apple MapKit JS, when the deployment has a MapKit key: the server signs short-lived tokens (GET /mapkit/token) and
 * the library comes from Apple's CDN. Without a key, or when anything here fails, the maps use Leaflet. Decided once
 * per page load, so every map on a page draws the same way. */

// MapKit JS ships no types this build uses; the engine touches a small, stable part of its API.
export type MapKit = any;
declare global { interface Window { mapkit?: MapKit } }

export const MAPKIT_SRC = "https://cdn.apple-mapkit.com/mk/5.x.x/mapkit.js";
const INIT_WAIT_MS = 8000;

let ready: Promise<MapKit | null> | null = null;
let failed = false;
const failures = new Set<() => void>();

function script(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.crossOrigin = "anonymous";
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("MapKit JS could not load"));
    document.head.appendChild(s);
  });
}

/* The token is kept in the tab until two minutes before it expires, so a refresh draws the map at once instead of
 * waiting for its token behind every other read the page makes. */
const KEPT = "fz:mapkit-token";
type Token = { token: string | null; expires?: number };
function kept(): Token | null {
  try {
    const k = JSON.parse(sessionStorage.getItem(KEPT) ?? "null") as Token | null;
    return k?.token && (k.expires ?? 0) * 1000 - Date.now() > 120_000 ? k : null;
  } catch { return null; }
}
async function token(): Promise<Token> {
  const t = await api.mapkitToken();
  try {
    if (t.token && t.expires) sessionStorage.setItem(KEPT, JSON.stringify(t));
    localStorage.setItem(HAS_KEY, t.token ? "1" : "0");
  } catch { /* storage refused */ }
  return t;
}

/* Whether this deployment gave this browser a token last time. When it did, Apple's script loads while the token is
 * asked for, rather than after it (#27); a deployment without a key never loads Apple's script. */
const HAS_KEY = "fz:mapkit-key";
const hadKey = () => { try { return localStorage.getItem(HAS_KEY) === "1"; } catch { return false; } };

async function start(): Promise<MapKit | null> {
  const early = hadKey() ? script(MAPKIT_SRC) : null;
  early?.catch(() => {});  // a failed load is met again below
  const first = kept() ?? await token().catch(() => null);
  if (typeof first?.token !== "string" || !first.token) return null;
  await (early ?? script(MAPKIT_SRC));
  const mk = window.mapkit;
  if (!mk?.init) return null;
  let initial: string | null = first.token;
  await new Promise<void>((resolve, reject) => {
    const wait = setTimeout(resolve, INIT_WAIT_MS);  // no word either way: try it, and an error later still falls back
    mk.addEventListener("configuration-change", (e: { status: string }) => {
      if (e.status === "Initialized" || e.status === "Refreshed") { clearTimeout(wait); resolve(); }
    });
    mk.addEventListener("error", (e: { status?: string }) => {
      clearTimeout(wait);
      failed = true;
      reject(new Error(`MapKit: ${e?.status ?? "error"}`));
      for (const f of failures) f();
    });
    mk.init({
      language: navigator.language,
      authorizationCallback: (done: (token: string) => void) => {
        if (initial) { done(initial); initial = null; return; }
        // Apple asks again before a token expires; a failed refresh shows as an "error" event above.
        token().then((r) => { if (r.token) done(r.token); }).catch(() => {});
      },
    });
  });
  return mk;
}

/** MapKit when it is set up and accepted its token, else null (use Leaflet). A page whose first sight is a map calls
 * this as it opens, so the library is under way while the page's other reads are answered (#27). */
export function loadMapKit(): Promise<MapKit | null> {
  ready ??= start().catch(() => null);
  return ready.then((mk) => (failed ? null : mk));
}

/** Called if MapKit stops working after a map was drawn (Apple refused a token): the map redraws with Leaflet. */
export function onMapKitFailure(f: () => void): () => void {
  failures.add(f);
  return () => failures.delete(f);
}
