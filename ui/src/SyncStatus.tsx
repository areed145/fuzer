/** Signal and sending, in the phone's top bar on every page (P5.17b, #1091). Quiet when the server answers and nothing
 * waits on the phone; otherwise one short line that opens My work, where the outbox is. */
import { useOutbox } from "./fieldOffline";
import { href } from "./lib";
import { useSignal } from "./useSignal";

const clock = (at: string) => new Date(at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

export function SyncStatus() {
  const signal = useSignal();
  const { waiting, refused, sending } = useOutbox();
  const n = waiting.length;
  const text = refused.length ? `${refused.length} not accepted`
    // A phone's top bar has room for one of the two: what waits matters more than since when.
    : !signal.live ? (n ? `No signal · ${n} waiting` : `No signal${signal.since ? ` since ${clock(signal.since)}` : ""}`)
    : sending && n ? `Sending ${n}`
    : n ? `${n} waiting to send` : "";
  if (!text) return null;
  const tone = refused.length ? "bad" : !signal.live ? "off" : "busy";
  const since = !signal.live && signal.since ? `No signal since ${clock(signal.since)}. ` : "";
  return <a className={`sync-status ${tone}`} href={href("/inbox")} role="status" title={since || undefined}
    aria-label={`${since}${text}. Open My work`}><span>{text}</span></a>;
}
