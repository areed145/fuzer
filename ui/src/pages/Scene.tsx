import { useCallback } from "react";
import { api, type RelationDef } from "../api";
import { useAsync } from "../lib";
import { PageHeader } from "../ui";
import { NetworkScene } from "./NetworkScene";

/** Explore's 3D tab (P5.16a): a facility or an area drawn as generic shapes, laid out from its links. What it shows,
 * the picked item and the labels live in the address, so a view can be shared and Back returns to it. */
export function Scene({ query = new URLSearchParams() }: { query?: URLSearchParams }) {
  const set = useCallback((values: Record<string, string | undefined>) => {
    const q = new URLSearchParams(query);
    for (const [k, v] of Object.entries(values)) if (v === undefined) q.delete(k); else q.set(k, v);
    history.replaceState(history.state, "", `#/3d${q.size ? `?${q}` : ""}`);
    window.dispatchEvent(new Event("hashchange"));
  }, [query]);
  const declared = useAsync(() => api.ontologyDeclared(), []);
  const relations = declared.data?.relations;
  const relDef = useCallback((name: string): RelationDef | undefined => relations?.find((r) => r.name === name), [relations]);
  return (
    <section>
      <PageHeader subtitle="A facility as generic shapes, laid out from its links.">3D</PageHeader>
      <NetworkScene query={query} set={set} relDef={relDef} />
    </section>
  );
}
