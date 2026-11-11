import { api, type Stats } from "../api";
import { KindBadge, Status, entityHref, fmt, href, useAsync, useDebounced } from "../lib";
import { Button, PageHeader, Table, Input } from "../ui";

export function Search({ query, stats }: { query: URLSearchParams; stats?: Stats }) {
  const q = query.get("q") ?? "";
  const kind = query.get("kind") ?? undefined;
  const debounced = useDebounced(q);
  const hits = useAsync(() => api.search(debounced, kind), [debounced, kind]);

  const go = (next: { q?: string; kind?: string }) =>
    window.location.replace(href("/browse", { q: next.q ?? q, kind: "kind" in next ? next.kind : kind }));

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
        {Object.entries(stats?.entities ?? {}).map(([k, n]) => (
          <Button key={k} aria-pressed={kind === k} className={kind === k ? "chip on" : "chip"} onClick={() => go({ kind: k })}>
            {k.replace("_", " ")} <span className="muted">{n.toLocaleString()}</span>
          </Button>
        ))}
      </div>

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
      {hits.data?.length === 200 && <p className="muted small">Showing the first 200. Narrow your search.</p>}
    </section>
  );
}
