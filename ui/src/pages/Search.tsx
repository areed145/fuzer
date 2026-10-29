import { api, type Stats } from "../api";
import { KindBadge, Status, entityHref, fmt, href, useAsync, useDebounced } from "../lib";

export function Search({ query, stats }: { query: URLSearchParams; stats?: Stats }) {
  const q = query.get("q") ?? "";
  const kind = query.get("kind") ?? undefined;
  const debounced = useDebounced(q);
  const hits = useAsync(() => api.search(debounced, kind), [debounced, kind]);

  const go = (next: { q?: string; kind?: string }) =>
    window.location.replace(href("/", { q: next.q ?? q, kind: "kind" in next ? next.kind : kind }));

  return (
    <section>
      <input
        className="search"
        autoFocus
        placeholder="Search tags, wells, PI points, serials, descriptions…  e.g. 20-V-2001, OSB.20PT1001A.PV, A-07"
        value={q}
        onChange={(e) => go({ q: e.target.value })}
      />
      <div className="chips">
        <button className={!kind ? "chip on" : "chip"} onClick={() => go({ kind: undefined })}>
          all
        </button>
        {Object.entries(stats?.entities ?? {}).map(([k, n]) => (
          <button key={k} className={kind === k ? "chip on" : "chip"} onClick={() => go({ kind: k })}>
            {k.replace("_", " ")} <span className="muted">{n.toLocaleString()}</span>
          </button>
        ))}
      </div>

      <Status loading={hits.loading && !hits.data} error={hits.error} empty={hits.data?.length === 0}>
        No matches for “{q}”.
      </Status>
      {hits.data && hits.data.length > 0 && (
        <table className="results">
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
        </table>
      )}
      {hits.data?.length === 200 && <p className="muted small">Showing the first 200. Narrow your search.</p>}
    </section>
  );
}
