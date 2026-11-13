import { Button, ButtonLink, PageHeader, Input, Card as UICard, Disclosure, Empty, Tabs } from "../ui";
import { NetworkScene } from "./NetworkScene";
import { useMemo } from "react";
import { api, type GraphNode, type RelationDef, type Subgraph } from "../api";
import { ALL_RELS, GraphCanvas, REL_VIEWS, kindColor, subgraphElements } from "../graph";
import { KindBadge, Status, entityHref, href, useAsync } from "../lib";

const words = (s: string) => s.replace(/_/g, " ");
const list = (v: string | null) => (v ? v.split(",").filter(Boolean) : []);
const chartColor = (i: number) =>
  getComputedStyle(document.documentElement).getPropertyValue(`--chart-${(i % 6) + 1}`).trim();

/** One network for every asset and every link type (flow, gas lift, P&ID piping, or anything the ontology adds):
 * which links and which kinds of thing to draw are filters, kept in the address so a view can be shared. */
export function Network({ query = new URLSearchParams() }: { query?: URLSearchParams }) {
  const asOf = query.get("as_of") ?? "";
  const asked = list(query.get("rels"));
  const hidden = new Set(list(query.get("hide")));
  const focus = query.get("focus");
  const selected = query.get("node");
  // Filters live in the address (an empty `rels=` means every link type was switched off).
  const set = (values: Record<string, string | undefined>) => {
    const q = new URLSearchParams(query);
    for (const [k, v] of Object.entries(values)) if (v === undefined) q.delete(k); else q.set(k, v);
    history.replaceState(history.state, "", `#/network${q.size ? `?${q}` : ""}`);
    window.dispatchEvent(new Event("hashchange"));
  };

  const declared = useAsync(() => api.ontologyDeclared(), []);
  const relDef = (name: string): RelationDef | undefined => declared.data?.relations.find((r) => r.name === name);
  // The 3D view draws a facility as shapes instead of the link diagram (P5.16a); the diagram's own reads wait.
  const threeD = query.get("view") === "3d";
  const views = (
    <Tabs className="page-tabs" label="Views of the network" items={[
      { key: "diagram", label: "Diagram", href: href("/network"), current: !threeD },
      { key: "3d", label: "3D", href: href("/network", { view: "3d" }), current: threeD },
    ]} />
  );
  // No link types asked for: the server draws the ontology's flow view and says which ones that was.
  const none = query.get("rels") === "";
  const net = useAsync(
    () => (none || threeD ? Promise.resolve(undefined) : api.network(asOf || undefined, asked.length ? asked : undefined)),
    [asOf, asked.join(","), none, threeD],
  );
  const on = none ? [] : asked.length ? asked : net.data?.rels ?? [];
  const order = (rels: string[]) => [...rels].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
  const rank = (r: string) => (ALL_RELS.indexOf(r) + 1 || 999);
  const colors = useMemo(() => new Map(order(on).map((r, i) => [r, chartColor(i)])), [on.join(",")]);

  const flowRels = REL_VIEWS.find((v) => v.id === "flow")?.rels ?? [];
  const primary = order([...new Set([...flowRels, ...(net.data?.rels ?? []), ...on])]);
  const others = REL_VIEWS.map((v) => ({ ...v, rels: v.rels.filter((r) => !primary.includes(r)) })).filter((v) => v.rels.length);
  const toggleRel = (r: string) => {
    const next = on.includes(r) ? on.filter((x) => x !== r) : order([...on, r]);
    set({ rels: next.join(","), focus: undefined, node: undefined });
  };

  // What the drawing holds once kinds are filtered out: a link stays only when both of its ends are shown.
  const kinds = useMemo(() => {
    const n = new Map<string, number>();
    for (const x of net.data?.nodes ?? []) n.set(x.kind, (n.get(x.kind) ?? 0) + 1);
    return [...n].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [net.data]);
  const hideKey = [...hidden].sort().join(",");
  const visible = useMemo(() => {
    if (!net.data) return undefined;
    const nodes = net.data.nodes.filter((n) => !hidden.has(n.kind));
    const ids = new Set(nodes.map((n) => n.id));
    return { ...net.data, nodes, edges: net.data.edges.filter((e) => ids.has(e.src) && ids.has(e.dst)) };
  }, [net.data, hideKey]);
  const relCounts = useMemo(() => {
    const n = new Map<string, number>();
    for (const e of net.data?.edges ?? []) n.set(e.rel, (n.get(e.rel) ?? 0) + 1);
    return n;
  }, [net.data]);
  const toggleKind = (k: string) => {
    const next = new Set(hidden);
    if (next.has(k)) next.delete(k); else next.add(k);
    set({ hide: [...next].sort().join(",") || undefined, focus: undefined, node: undefined });
  };

  // The whole network drawn at once is a tall column of unreadable nodes (#300), so a big one opens on its
  // largest first collector (a manifold: what the wells flow into) with everything up- and downstream of it.
  // Any node can be the focus, from its panel.
  const groups = useMemo(() => (visible ? collectors(visible) : []), [visible]);
  const known = (id: string) => visible?.nodes.some((n) => n.id === id);
  const shownFocus = focus === "all" ? null
    : focus && known(focus) ? focus
    : (visible?.nodes.length ?? 0) > BIG && groups.length > 1 ? groups[0].id : null;
  const shown = useMemo(() => (visible && shownFocus ? around(visible, shownFocus) : visible), [visible, shownFocus]);
  // Links are coloured by type; labels only add clutter, so hover a link to see its type and dates.
  const elements = useMemo(
    () => (shown ? subgraphElements(shown, shownFocus ?? undefined, false, !asOf, (r) => colors.get(r)) : []),
    [shown, shownFocus, asOf, colors],
  );
  const node = net.data?.nodes.find((n) => n.id === selected);
  const focusLabel = (id: string) => net.data?.nodes.find((n) => n.id === id)?.label ?? id;

  if (threeD) {
    return (
      <section>
        <PageHeader subtitle="A facility as generic shapes, laid out from its links.">Network</PageHeader>
        {views}
        <NetworkScene query={query} set={set} relDef={relDef} />
      </section>
    );
  }

  return (
    <section>
      <div className="row spread">
        <div>
          <PageHeader subtitle="How assets connect. Dashed lines have ended.">Network</PageHeader>
        </div>
        <label className="asof">
          As of
          <Input type="date" value={asOf} onChange={(e) => set({ as_of: e.target.value || undefined })} />
          {asOf && (
            <Button variant="link" onClick={() => set({ as_of: undefined })}>
              all dates
            </Button>
          )}
        </label>
      </div>
      {views}
      <div className="network-filters">
        <div className="chips" role="group" aria-label="Link types">
          <span className="muted small">Links</span>
          {primary.map((r) => (
            <Button key={r} className={on.includes(r) ? "chip on" : "chip"} aria-pressed={on.includes(r)}
              title={relDef(r)?.description || undefined} onClick={() => toggleRel(r)}>
              {on.includes(r) && <i className="dot" style={{ background: colors.get(r) }} />} {relDef(r)?.label ?? words(r)}
              {relCounts.has(r) && <span className="muted"> {relCounts.get(r)}</span>}
            </Button>
          ))}
          {others.length > 0 && (
            <Disclosure className="more-rels" label={<>More link types</>}>
              {others.map((v) => (
                <div key={v.id} className="chips">
                  <span className="muted small">{v.label}</span>
                  {v.rels.map((r) => (
                    <Button key={r} className="chip" aria-pressed={false} title={relDef(r)?.description || undefined}
                      onClick={() => toggleRel(r)}>
                      {relDef(r)?.label ?? words(r)}
                    </Button>
                  ))}
                </div>
              ))}
            </Disclosure>
          )}
        </div>
        {kinds.length > 0 && (
          <div className="chips" role="group" aria-label="Kinds of thing">
            <span className="muted small">Kinds</span>
            {kinds.map(([k, n]) => (
              <Button key={k} className={hidden.has(k) ? "chip" : "chip on"} aria-pressed={!hidden.has(k)}
                onClick={() => toggleKind(k)}>
                <i className="dot" style={{ background: kindColor(k) }} /> {words(k)} <span className="muted">{n}</span>
              </Button>
            ))}
          </div>
        )}
      </div>
      {none ? (
        <Empty>Pick at least one link type to draw.</Empty>
      ) : (
        <Status loading={net.loading && !net.data} error={net.error} empty={net.data?.edges.length === 0}>
          No links of {on.length === 1 ? "this type" : "these types"}{asOf ? " on this date" : ""}.
        </Status>
      )}
      {!none && net.data && net.data.edges.length > 0 && visible && (
        <>
          {(groups.length > 1 || shownFocus) && (
            <label className="row small">
              Show
              <select value={shownFocus ?? "all"} onChange={(e) => set({ focus: e.target.value })} aria-label="Show part of the network">
                <option value="all">the whole network ({visible.nodes.length} nodes)</option>
                {shownFocus && !groups.some((g) => g.id === shownFocus) && (
                  <option value={shownFocus}>{focusLabel(shownFocus)} and everything linked up- and downstream</option>
                )}
                {groups.map((g) => <option key={g.id} value={g.id}>{g.label} and everything linked through it ({g.inputs} in)</option>)}
              </select>
            </label>
          )}
          {visible.edges.length === 0 ? (
            <Empty>Every link here joins a kind you have hidden.</Empty>
          ) : (
            <div className="graph-layout">
              <GraphCanvas elements={elements} layout="flow" selectedId={selected}
                onSelect={(id) => set({ node: id && known(id) ? id : undefined })} height={640} />
              <UICard className="graph-panel" as="aside">
                {node ? <NodePanel node={node} onFocus={node.id !== shownFocus ? () => set({ focus: node.id }) : undefined} />
                  : <Empty className="small">Click a node to see what it is.</Empty>}
              </UICard>
            </div>
          )}
          {net.data.truncated && <p className="small">Too many links to draw; showing the first 5,000.</p>}
        </>
      )}
    </section>
  );
}

const BIG = 60;

/** First collectors: nodes fed directly by a node nothing links into along the same link type (wells → manifolds,
 * a lift gas header → its manifolds), most inputs first. Each link type is read on its own, so mixing types (where
 * gas lift feeds the wells that flow to a manifold) still finds the manifolds. */
function collectors(g: Subgraph): { id: string; label: string; inputs: number }[] {
  const inputs = new Map<string, Set<string>>();
  for (const rel of new Set(g.edges.map((e) => e.rel))) {
    const edges = g.edges.filter((e) => e.rel === rel);
    const fed = new Set(edges.map((e) => e.dst));
    for (const e of edges) if (!fed.has(e.src)) inputs.set(e.dst, (inputs.get(e.dst) ?? new Set()).add(e.src));
  }
  const label = new Map(g.nodes.map((n) => [n.id, n.label ?? n.id]));
  return [...inputs].map(([id, from]) => ({ id, label: label.get(id) ?? id, inputs: from.size }))
    .sort((a, b) => b.inputs - a.inputs || a.label.localeCompare(b.label));
}

/** A node with everything up- and downstream of it along each link type, and one step of any shown link type from
 * there. Following one type at a time keeps loops between networks (separator gas feeding the compression that
 * supplies lift gas to every well) from pulling in the whole field. */
function around(g: Subgraph, id: string): Subgraph {
  const walk = (edges: Subgraph["edges"], next: (e: Subgraph["edges"][number]) => [string, string]) => {
    const seen = new Set([id]);
    for (let grew = true; grew;) {
      grew = false;
      for (const e of edges) {
        const [from, to] = next(e);
        if (seen.has(from) && !seen.has(to)) { seen.add(to); grew = true; }
      }
    }
    return seen;
  };
  const core = new Set<string>([id]);
  for (const rel of new Set(g.edges.map((e) => e.rel))) {
    const edges = g.edges.filter((e) => e.rel === rel);
    for (const n of [...walk(edges, (e) => [e.dst, e.src]), ...walk(edges, (e) => [e.src, e.dst])]) core.add(n);
  }
  const keep = new Set(core);
  for (const e of g.edges) {
    if (core.has(e.src)) keep.add(e.dst);
    if (core.has(e.dst)) keep.add(e.src);
  }
  return { ...g, nodes: g.nodes.filter((n) => keep.has(n.id)),
    edges: g.edges.filter((e) => keep.has(e.src) && keep.has(e.dst) && (core.has(e.src) || core.has(e.dst))) };
}

export function NodePanel({ node, onExpand, onFocus }: { node: GraphNode; onExpand?: () => void; onFocus?: () => void }) {
  return (
    <>
      <h2 className="mono">{node.label ?? node.id}</h2>
      <div className="row">
        <KindBadge kind={node.kind} />
      </div>
      {node.description && <p>{node.description}</p>}
      <div className="actions">
        <ButtonLink href={entityHref(node.id)}>
          Open
        </ButtonLink>
        <ButtonLink href={href(`/e/${encodeURIComponent(node.id)}`, { tab: "graph" })}>
          Graph from here
        </ButtonLink>
        {onExpand && <Button onClick={onExpand}>Expand</Button>}
        {onFocus && <Button onClick={onFocus}>Show what links to it</Button>}
      </div>
    </>
  );
}
