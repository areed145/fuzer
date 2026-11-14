/** What the phone keeps for the field (P5.17a, #1090): IndexedDB, so photos are kept as files rather than text and the
 * store isn't capped at the few megabytes `localStorage` holds. Two stores: `outbox`, the writes made without signal,
 * keyed by their client id; and `kept`, answers kept to read without signal (the operator's jobs), keyed by name.
 *
 * A write resolves only once its transaction has committed, so "kept on this phone" is never said of something iOS
 * could still lose when it discards the app in the background. Where IndexedDB can't be opened (some private modes)
 * the same calls fall back to `localStorage`, holding photos as text as before. */

const DB = "fuzer-field";

/** The signed-in account ("" in local mode). What is kept is kept per account, so a shared phone never shows one
 * person's kept pages to another. Set by the sign-in gate through `outboxSession`. */
let scope = "";
export const setScope = (account: string) => { scope = account; };
export const getScope = () => scope;
const VERSION = 1;
export type StoreName = "outbox" | "kept";

let opening: Promise<IDBDatabase | null> | null = null;

function open(): Promise<IDBDatabase | null> {
  if (!opening) {
    opening = new Promise((resolve) => {
      try {
        if (typeof indexedDB === "undefined") return resolve(null);
        const req = indexedDB.open(DB, VERSION);
        req.onupgradeneeded = () => {
          const db = req.result;
          if (!db.objectStoreNames.contains("outbox")) db.createObjectStore("outbox", { keyPath: "client_id" });
          if (!db.objectStoreNames.contains("kept")) db.createObjectStore("kept");
        };
        req.onsuccess = () => {
          const db = req.result;
          // Another tab upgrading the store: let it, and open again next time.
          db.onversionchange = () => { db.close(); opening = null; };
          resolve(db);
        };
        req.onerror = () => resolve(null);
        req.onblocked = () => resolve(null);
      } catch {
        resolve(null);
      }
    });
  }
  return opening;
}

/** True when the store is IndexedDB (photos kept as files), false on the `localStorage` fallback. */
export const durable = async () => (await open()) !== null;

const fallbackKey = (store: StoreName) => `fuzer.field.store.${store}`;
function fallbackRead(store: StoreName): Record<string, unknown> {
  try { return JSON.parse(localStorage.getItem(fallbackKey(store)) ?? "{}") as Record<string, unknown>; } catch { return {}; }
}
function fallbackWrite(store: StoreName, all: Record<string, unknown>): boolean {
  try { localStorage.setItem(fallbackKey(store), JSON.stringify(all)); return true; } catch { return false; }
}

/** Run one transaction and resolve with `result` once it has committed. */
function commit<T>(db: IDBDatabase, store: StoreName, mode: IDBTransactionMode,
  work: (s: IDBObjectStore) => IDBRequest | void): Promise<T | undefined> {
  return new Promise((resolve, reject) => {
    let result: T | undefined;
    const tx = db.transaction(store, mode);
    const req = work(tx.objectStore(store));
    if (req) req.onsuccess = () => { result = req.result as T; };
    tx.oncomplete = () => resolve(result);
    tx.onerror = () => reject(tx.error ?? new Error("The phone could not keep this"));
    tx.onabort = () => reject(tx.error ?? new Error("The phone could not keep this"));
  });
}

export async function get<T>(store: StoreName, key: string): Promise<T | undefined> {
  const db = await open();
  if (!db) return fallbackRead(store)[key] as T | undefined;
  try { return await commit<T>(db, store, "readonly", (s) => s.get(key)); } catch { return undefined; }
}

export async function all<T>(store: StoreName): Promise<T[]> {
  const db = await open();
  if (!db) return Object.values(fallbackRead(store)) as T[];
  try { return (await commit<T[]>(db, store, "readonly", (s) => s.getAll())) ?? []; } catch { return []; }
}

/** Keep `value` under `key` (for `outbox`, its own `client_id`). Resolves true once committed, false if the phone has
 * no room or no store. */
export async function put(store: StoreName, value: unknown, key?: string): Promise<boolean> {
  const db = await open();
  if (!db) {
    const id = key ?? String((value as { client_id: string }).client_id);
    return fallbackWrite(store, { ...fallbackRead(store), [id]: value });
  }
  try {
    await commit(db, store, "readwrite", (s) => (store === "outbox" ? s.put(value) : s.put(value, key)));
    return true;
  } catch {
    return false;
  }
}

/** Every [key, value] in a store. */
export async function entries<T>(store: StoreName): Promise<[string, T][]> {
  const db = await open();
  if (!db) return Object.entries(fallbackRead(store)) as [string, T][];
  try {
    const keys = (await commit<IDBValidKey[]>(db, store, "readonly", (s) => s.getAllKeys())) ?? [];
    const values = (await commit<T[]>(db, store, "readonly", (s) => s.getAll())) ?? [];
    return keys.map((k, i) => [String(k), values[i]]);
  } catch {
    return [];
  }
}

export async function remove(store: StoreName, key: string): Promise<void> {
  const db = await open();
  if (!db) {
    const rest = fallbackRead(store);
    delete rest[key];
    fallbackWrite(store, rest);
    return;
  }
  try { await commit(db, store, "readwrite", (s) => s.delete(key)); } catch { /* gone already */ }
}

/** A photo as the phone keeps it: a file where IndexedDB holds it, the data URL itself on the fallback. */
export type KeptPhoto = Blob | string;

export async function keepPhoto(dataUrl: string): Promise<KeptPhoto> {
  if (!(await durable())) return dataUrl;
  const [head, body = ""] = dataUrl.split(",", 2);
  const type = /^data:([^;,]+)/.exec(head)?.[1] ?? "image/jpeg";
  if (!head.includes(";base64")) return new Blob([decodeURIComponent(body)], { type });
  const bin = atob(body);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type });
}

/** Back to the data URL the report API takes, when it is sent. */
export function photoUrl(photo: KeptPhoto): Promise<string> {
  if (typeof photo === "string") return Promise.resolve(photo);
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error ?? new Error("A kept photo could not be read"));
    reader.readAsDataURL(photo);
  });
}

const PERSIST = "fuzer.field.persist";
/** Ask once that the browser not clear what the phone keeps under storage pressure, and remember the answer. */
export async function askToPersist(): Promise<void> {
  try {
    if (localStorage.getItem(PERSIST) || !navigator.storage?.persist) return;
    const granted = (await navigator.storage.persisted?.()) || (await navigator.storage.persist());
    localStorage.setItem(PERSIST, granted ? "granted" : "denied");
  } catch { /* not offered here */ }
}
