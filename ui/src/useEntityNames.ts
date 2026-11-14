import { api } from "./api";
import type { EntityNames } from "./entityRefs";
import { useAsync } from "./lib";

const MAX_IDS = 200;

// Every row of a list asks for its own subjects' names. Asked one by one, All open and the Board sent 40-odd lookups
// per page, each waiting its turn in the request queue (#1069). Lookups asked in the same moment go out together, in
// requests of at most MAX_IDS ids (the server's limit), and each caller gets the names it asked for.
let batch: { ids: Set<string>; done: Promise<EntityNames> } | undefined;

function lookup(ids: string[]): Promise<EntityNames> {
  if (!batch) {
    const ids = new Set<string>();
    const done = new Promise<EntityNames>((resolve, reject) => {
      setTimeout(() => {
        batch = undefined;
        const all = [...ids];
        const chunks: string[][] = [];
        for (let i = 0; i < all.length; i += MAX_IDS) chunks.push(all.slice(i, i + MAX_IDS));
        Promise.all(chunks.map((c) => api.entityNames(c))).then((got) => resolve(Object.assign({}, ...got)), reject);
      }, 0);
    });
    batch = { ids, done };
  }
  ids.forEach((id) => batch!.ids.add(id));
  return batch.done.then((all) => Object.fromEntries(ids.filter((id) => id in all).map((id) => [id, all[id]])));
}

/** Undefined until known. A failed lookup is an empty answer, so ids show rather than vanish. */
export function useEntityNames(ids: string[]): EntityNames | undefined {
  const key = ids.slice(0, MAX_IDS).join(",");
  const names = useAsync(() => key ? lookup(key.split(",")) : Promise.resolve({}), [key]);
  return names.error ? {} : names.data;
}
