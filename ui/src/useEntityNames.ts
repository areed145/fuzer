import { api } from "./api";
import type { EntityNames } from "./entityRefs";
import { useAsync } from "./lib";

const MAX_IDS = 200;

/** Undefined until known. A failed lookup is an empty answer, so ids show rather than vanish. */
export function useEntityNames(ids: string[]): EntityNames | undefined {
  const key = ids.slice(0, MAX_IDS).join(",");
  const names = useAsync(() => key ? api.entityNames(key.split(",")) : Promise.resolve({}), [key]);
  return names.error ? {} : names.data;
}
