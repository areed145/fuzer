import { useEffect, useMemo, useState } from "react";
import { api, type Subgraph } from "../api";
import { ALL_RELS, GraphCanvas, Legend, subgraphElements, type LayoutName } from "../graph";
import { Status, useAsync } from "../lib";
import { NodePanel } from "./Network";

function merge(graphs: Subgraph[]): Subgraph {
  const nodes = new Map(graphs.flatMap((g) => g.nodes.map((n) => [n.id, n] as const)));
  const edges = new Map(graphs.flatMap((g) => g.edges.map((e) => [e.id, e] as const)));
  return { nodes: [...nodes.values()], edges: [...edges.values()], truncated: graphs.some((g) => g.truncated) };
}

export function EntityGraph({ id, asOf }: { id: string; asOf?: string }) {
  const [depth, setDepth] = useState(1);
  const [rels, setRels] = useState<string[]>(ALL_RELS);
  const [layout, setLayout] = useState<LayoutName>("force");
  const [expanded, setExpanded] = useState<string[]>([]);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    setExpanded([]);
    setSelected(null);
  }, [id]);

  const relKey = rels.join(",");
  const graph = useAsync(
    () =>
      Promise.all([
        api.neighborhood(id, depth, rels, asOf),
        ...expanded.map((x) => api.neighborhood(x, 1, rels, asOf)),
      ]).then(merge),
    [id, depth, relKey, asOf, expanded.join(",")],
  );
  const elements = useMemo(
    () => (graph.data ? subgraphElements(graph.data, id, undefined, !asOf) : []),
    [graph.data, id, asOf],
  );
  const node = graph.data?.nodes.find((n) => n.id === selected);
  const expand = (x: string) => setExpanded((e) => (e.includes(x) || x === id ? e : [...e, x]));
  const toggleRel = (r: string) => setRels((cur) => (cur.includes(r) ? cur.filter((x) => x !== r) : [...cur, r]));

  return (
    <section>
      <div className="graph-toolbar">
        <div className="tabs">
          {[1, 2].map((d) => (
            <button key={d} className={depth === d ? "on" : ""} onClick={() => setDepth(d)}>
              {d} {d === 1 ? "step" : "steps"}
            </button>
          ))}
        </div>
        <div className="chips">
          {ALL_RELS.map((r) => (
            <button key={r} className={rels.includes(r) ? "chip on" : "chip"} onClick={() => toggleRel(r)}>
              {r.replace("_", " ")}
            </button>
          ))}
        </div>
        <div className="tabs">
          {(["force", "flow", "tree"] as const).map((l) => (
            <button key={l} className={layout === l ? "on" : ""} onClick={() => setLayout(l)}>
              {l === "force" ? "Web" : l === "flow" ? "Left → right" : "Top ↓ down"}
            </button>
          ))}
        </div>
      </div>
      <Status loading={graph.loading && !graph.data} error={graph.error} />
      {graph.data && (
        <>
          <div className="graph-layout">
            <GraphCanvas
              elements={elements}
              layout={layout}
              selectedId={selected}
              onSelect={setSelected}
              onExpand={expand}
              height={600}
            />
            <aside className="card graph-panel">
              {node ? (
                <NodePanel node={node} onExpand={node.id === id ? undefined : () => expand(node.id)} />
              ) : (
                <p className="muted small">
                  Click a node to see what it is. Double-click to pull in its own links.
                  {graph.data.nodes.length > 1 &&
                    ` Showing ${graph.data.nodes.length} entities and ${graph.data.edges.length} links.`}
                </p>
              )}
              {graph.data.truncated && (
                <p className="muted small">Too many neighbours to draw them all; showing the first 250.</p>
              )}
            </aside>
          </div>
          <Legend kinds={[...new Set(graph.data.nodes.map((n) => n.kind))]} />
        </>
      )}
    </section>
  );
}
