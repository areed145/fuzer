import { api, type Stats } from "../api";
import { KindBadge, Status, entityHref, fmt, href, useAsync, useDebounced } from "../lib";
import { Button, Disclosure, PageHeader, Table, Input } from "../ui";

/** The kinds people look for by name, shown first; every other kind is one click away under "More kinds". */
const FIRST_KINDS = ["equipment", "tag", "well", "facility", "area", "pipeline", "document", "work_order", "notification", "incident"];

export function Search({ query, stats }: { query: URLSearchParams; stats?: Stats }) {
  const q = query.get("q") ?? "";
  const kind = query.get("kind") ?? undefined;
  const debounced = useDebounced(q);
  const hits = useAsync(() => api.search(debounced, kind), [debounced, kind]);

  const go = (next: { q?: string; kind?: string }) =>
    window.location.replace(href("/browse", { q: next.q ?? q, kind: "kind" in next ? next.kind : kind }));

  const kinds = Object.entries(stats?.entities ?? {});
  const rest = kinds.filter(([k]) => !FIRST_KINDS.includes(k));
  const chip = ([k, n]: [string, number]) => (
    <Button key={k} aria-pressed={kind === k} className={kind === k ? "chip on" : "chip"} onClick={() => go({ kind: k })}>
      {k.replace(/_/g, " ")} <span className="muted">{n.toLocaleString()}</span>
    </Button>
  );
  return (
    <section>
      <PageHeader subtitle="Find assets and records and follow their links.">Browse</PageHeader>
      <Input
        className="search"
        autoFocus
        aria-label="Search facility records"
        placeholder="Search assets, tags and records"
        value={q}
        onChange={(e) => go({ q: e.target.value })}
      />
      <div className="chips">
        <Button aria-pressed={!kind} className={!kind ? "chip on" : "chip"} onClick={() => go({ kind: undefined })}>
          all
        </Button>
        {kinds.filter(([k]) => FIRST_KINDS.includes(k)).map(chip)}
      </div>
      {rest.length > 0 && <Disclosure key={String(rest.some(([k]) => k === kind))} className="more-kinds" label={`More kinds (${rest.length})`} open={rest.some(([k]) => k === kind)}>
        <div className="chips">{rest.map(chip)}</div>
      </Disclosure>}

      <Status loading={hits.loading && !hits.data} error={hits.error} empty={hits.data?.length === 0}>
        No matches for “{q}”.
      </Status>
      {hits.data && hits.data.length > 0 && (
        <Table variant="results">
          <thead>
            <tr>
              <th>Name</th>
              <th>Kind</th>
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
                <td className="muted">{fmt(h.description)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {hits.data?.length === 200 && <p className="small">Showing the first 200. Narrow your search.</p>}
    </section>
  );
}
