/** The app's own history, for the installed app (P5.14a, #985). Added to a phone's home screen, Fuzer runs without
 * the browser's back and forward buttons (iOS has no back gesture there either), so the shell draws its own Back.
 *
 * Each history entry carries its depth in the app: 0 for the first page this launch, one more for each page opened
 * from inside the app. Back walks history while there is a page behind; with none (opened from a link, or a cold
 * launch) it goes up to the page's owner instead, so it never leaves the app.
 *
 * A redirect (location.replace) doesn't add an entry, and neither does a page rewriting its own query. A new entry
 * is told from a replaced one by history growing; when that can't be told (a new page opened right after one step
 * back), the depth is kept, which can only send Back up a level, never out of the app. */
import { useEffect, useState } from "react";

const KEY = "fzDepth";
const LAST = "fuzer.lastRoute";
/** A cold launch older than this starts at Home, not on yesterday's page. */
const RESUME_MS = 12 * 60 * 60 * 1000;

let depth = 0;
let length = 0;
const listeners = new Set<() => void>();

const stateDepth = (): number | undefined => {
  const value = (history.state as Record<string, unknown> | null)?.[KEY];
  return typeof value === "number" ? value : undefined;
};

function record(next: number) {
  depth = next;
  length = history.length;
  history.replaceState({ ...(history.state as object | null), [KEY]: next }, "");
}

/** Installed on the home screen and opened from there (standalone display), not in a browser tab. */
export function installed(): boolean {
  try {
    return matchMedia("(display-mode: standalone)").matches || matchMedia("(display-mode: fullscreen)").matches
      || (navigator as Navigator & { standalone?: boolean }).standalone === true;
  } catch {
    return false;
  }
}

function remember() {
  try { localStorage.setItem(LAST, JSON.stringify({ hash: location.hash, at: Date.now() })); } catch { /* private mode */ }
}

/** Where a cold launch of the installed app should land: the last page from the last 12 hours, when it was opened at
 * Home with no route of its own. */
export function resumeRoute(now = Date.now()): string | undefined {
  if (!installed() || (location.hash && location.hash !== "#/" && location.hash !== "#")) return undefined;
  try {
    const last = JSON.parse(localStorage.getItem(LAST) ?? "null") as { hash?: string; at?: number } | null;
    if (!last?.hash || !last.hash.startsWith("#/") || last.hash === "#/" || !last.at || now - last.at > RESUME_MS) return undefined;
    return last.hash;
  } catch {
    return undefined;
  }
}

function onChange() {
  const known = stateDepth();
  if (known !== undefined) {
    depth = known;  // back, forward, or a page that kept the state it had
    length = history.length;
  } else {
    record(history.length > length ? depth + 1 : depth);
  }
  remember();
  listeners.forEach((listener) => listener());
}

let started = false;
/** Call once before the app renders. Returns true when it redirected to resume the last page. */
export function startAppHistory(): boolean {
  if (started) return false;
  started = true;
  record(stateDepth() ?? 0);
  window.addEventListener("hashchange", onChange);
  window.addEventListener("popstate", onChange);
  document.documentElement.classList.toggle("installed", installed());
  const resume = resumeRoute();
  if (resume) {
    location.replace(resume);
    return true;
  }
  remember();
  return false;
}

/** How many pages back the app's own history goes from here (0: this is the first page). */
export function useAppDepth(): number {
  const [value, setValue] = useState(depth);
  useEffect(() => {
    const update = () => setValue(depth);
    listeners.add(update);
    update();
    return () => { listeners.delete(update); };
  }, []);
  return value;
}

/** Back: a step through the app's history when there is one, otherwise up to `up` (replacing this entry). */
export function goBack(up: string) {
  if (depth > 0) history.back();
  else location.replace(up);
}
