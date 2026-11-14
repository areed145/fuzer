/** Whether the app can reach the server right now (P5.17b, #1091), read from what requests actually did rather than
 * `navigator.onLine` alone, which says online on a dead or captive Wi-Fi. A request that never reached the server
 * marks the signal lost, with the time; any answer from the server, even a refusal, marks it back. `offline` events
 * mark it lost at once; `online` events only prompt the next request to find out. Pages read it with `useSignal`. */
export interface Signal { live: boolean; since: string | null }

const CHANGED = "fuzer:signal";
let state: Signal = { live: typeof navigator === "undefined" || navigator.onLine !== false, since: null };
if (!state.live) state = { live: false, since: new Date().toISOString() };

function set(live: boolean) {
  if (live === state.live) return;
  state = { live, since: live ? null : new Date().toISOString() };
  window.dispatchEvent(new Event(CHANGED));
}
/** A request reached the server and got an answer. */
export const reached = () => set(true);
/** A request never reached the server. */
export const lost = () => set(false);
export const signal = () => state;

if (typeof window !== "undefined") window.addEventListener("offline", lost);

/** Call `onChange` whenever the signal changes; returns the unsubscribe. No React here: the transport imports this. */
export const subscribeSignal = (onChange: () => void) => {
  window.addEventListener(CHANGED, onChange);
  return () => window.removeEventListener(CHANGED, onChange);
};

/** The reason shown on an action that needs the server's latest state, so it can't be done without signal. */
export const NEEDS_SIGNAL = "Needs signal: someone else may have changed this since your phone last checked.";
