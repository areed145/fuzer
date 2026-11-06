const CONCURRENT = 2;
const MAX_WAITING = 32;
const WAIT_MS = 30_000;
let active = 0;
const waiting: (() => void)[] = [];
const reads = new Map<string, Promise<unknown>>();

async function admit(): Promise<void> {
  if (active < CONCURRENT) { active++; return; }
  if (waiting.length >= MAX_WAITING) throw new Error("Too many pending requests. Wait for current work to finish, then refresh.");
  await new Promise<void>((resolve, reject) => {
    const start = () => { clearTimeout(timer); resolve(); };
    const timer = setTimeout(() => {
      waiting.splice(waiting.indexOf(start), 1);
      reject(new Error("Timed out waiting for other requests. Wait for current work to finish, then refresh."));
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
    result.then(() => reads.delete(readKey), () => reads.delete(readKey));
  }
  return result;
}
