// Four at once: with two, a couple of slow reads held every other request on the page past its wait, the app's
// own checks included ("API not reachable"). Half the server's default of eight (access.api_concurrency), which
// is for everyone signed in, so one browser never fills it.
const CONCURRENT = 4;
const MAX_WAITING = 32;
const WAIT_MS = 30_000;
let active = 0;
const waiting: (() => void)[] = [];
const reads = new Map<string, Promise<unknown>>();

async function admit(): Promise<void> {
  if (active < CONCURRENT) { active++; return; }
  if (waiting.length >= MAX_WAITING) throw new TypeError("Too many pending requests. Wait for current work to finish, then refresh.");
  await new Promise<void>((resolve, reject) => {
    const start = () => { clearTimeout(timer); resolve(); };
    const timer = setTimeout(() => {
      waiting.splice(waiting.indexOf(start), 1);
      // A TypeError, as an unreached fetch is (P5.17h): this request never left the phone, so a write the outbox can
      // keep is kept rather than lost, and a kept read is served, when lie-fi holds every slot.
      reject(new TypeError("Timed out waiting for other requests. Wait for current work to finish, then refresh."));
    }, WAIT_MS);
    waiting.push(start);
  });
}

export function scheduleRequest<T>(run: () => Promise<T>, readKey?: string): Promise<T> {
  const existing = readKey ? reads.get(readKey) : undefined;
  if (existing) return existing as Promise<T>;
  const result = (async () => {
    await admit();
    try { return await run(); }
    finally {
      const next = waiting.shift();
      if (next) next();
      else active--;
    }
  })();
  if (readKey) {
    reads.set(readKey, result);
    const forget = () => { if (reads.get(readKey) === result) reads.delete(readKey); };
    result.then(forget, forget);
  } else {
    // A write may change what an in-flight read answers: a read asked for after it is read again, never joined to
    // one that started before it (the field's On site or Done, then My work and its badge read again).
    const forget = () => reads.clear();
    result.then(forget, forget);
  }
  return result;
}
