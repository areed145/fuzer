import { api, type Stats } from "../api";
import { count } from "../format";
import { KindBadge, Status, entityHref, fmt, href, useAsync, useDebounced } from "../lib";
import { Button, Disclosure, PageHeader, Table, Input, Text } from "../ui";
import { kindCount } from "../Hierarchy";
import { kindWord } from "../wording";

/** The register's places and things, in the order of the plant: these are Explore's first list. Every other kind
 * (documents, work orders, readings) is one click away under "Documents and records". */
export const ASSET_LIST = ["facility", "area", "well", "pipeline", "equipment", "tag"];
const ALL = "all";

/** Explore: every entity in the register, assets first. Each row opens the entity's page, which holds everything about
 * it and everything beneath it. */
export function Search({ query, stats }: { query: URLSearchParams; stats?: Stats }) {
  const q = query.get("q") ?? "";
  const kind = query.get("kind") ?? undefined;
  const debounced = useDebounced(q);
  const asked = kind === ALL ? undefined : kind ?? ASSET_LIST.join(",");
  const hits = useAsync(() => api.search(debounced, asked), [debounced, asked]);

  const go = (next: { q?: string; kind?: string }) =>
    window.location.replace(href("/browse", { q: next.q ?? q, kind: "kind" in next ? next.kind : kind }));

  const kinds = Object.entries(stats?.entities ?? {});
  const assets = ASSET_LIST.map((k) => [k, stats?.entities?.[k] ?? 0] as [string, number]).filter(([, n]) => n > 0);
  const rest = kinds.filter(([k]) => !ASSET_LIST.includes(k));
  const chip = ([k, n]: [string, number]) => (
    <Button key={k} aria-pressed={kind === k} className={kind === k ? "chip on" : "chip"} onClick={() => go({ kind: k })}>
      {kindCount(n, k)}
    </Button>
  );
  const assetCount = assets.reduce((n, [, c]) => n + c, 0);
  const inRest = rest.some(([k]) => k === kind) || kind === ALL;
  return (
    <section>
      <PageHeader>Explore</PageHeader>
      <Input
        className="search"
        autoFocus
        aria-label="Search entities"
        placeholder="Search wells, equipment, tags, areas, documents and more"
        value={q}
        onChange={(e) => go({ q: e.target.value })}
      />
      <div className="chips">
        <Button aria-pressed={!kind} className={!kind ? "chip on" : "chip"} onClick={() => go({ kind: undefined })}>
          All assets <Text muted>{count(assetCount)}</Text>
        </Button>
        {assets.map(chip)}
      </div>
      {rest.length > 0 && <Disclosure key={String(inRest)} className="more-kinds" label={`Documents and records (${rest.length} kinds)`} open={inRest}>
        <div className="chips">
          <Button aria-pressed={kind === ALL} className={kind === ALL ? "chip on" : "chip"} onClick={() => go({ kind: ALL })}>
            Everything</Button>
          {rest.map(chip)}
        </div>
      </Disclosure>}

      <Status of={hits} empty={hits.data?.length === 0}>
        {q ? <>No {kind && kind !== ALL ? kindWord(kind).toLowerCase() : kind === ALL ? "entity" : "asset"} matches “{q}”.
          {kind !== ALL && <> <a href={href("/browse", { q, kind: ALL })}>Search everything</a></>}</> : "Nothing here yet."}
      </Status>
      {hits.data && hits.data.length > 0 && (
        <Table variant="results">
          <thead>
            <tr>
              <th>Name</th>
              <th>Kind</th>
              <th>In</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
            {hits.data.map((h) => (
              <tr key={h.fuzer_id} onClick={() => (window.location.hash = entityHref(h.fuzer_id))}>
                <td>
                  <a href={entityHref(h.fuzer_id)} className="mono">
                    {h.label ?? h.fuzer_id}
                  </a>
                </td>
                <td>
                  <KindBadge kind={h.kind} />
                </td>
                <td>{h.parent ? <a href={entityHref(h.parent)} onClick={(e) => e.stopPropagation()}>{h.parent_label}</a> : <Text muted>—</Text>}</td>
                <td className="muted">{fmt(h.description)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {hits.data?.length === 200 && <Text as="p" small>Showing the first 200. Narrow your search.</Text>}
    </section>
  );
}
