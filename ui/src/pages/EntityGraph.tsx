import { Button, Card as UICard, Empty, Text, Tabs } from "../ui";
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
        <Tabs label="How far" items={[1, 2].map((d) => ({ key: String(d), label: `${d} ${d === 1 ? "step" : "steps"}`, current: depth === d, onSelect: () => setDepth(d) }))} />
        <div className="chips">
          {ALL_RELS.map((r) => (
            <Button key={r} className={rels.includes(r) ? "chip on" : "chip"} onClick={() => toggleRel(r)}>
              {r.replace("_", " ")}
            </Button>
          ))}
        </div>
        <Tabs label="Layout" items={(["force", "flow", "tree"] as const).map((l) => ({
          key: l, label: l === "force" ? "Web" : l === "flow" ? "Left → right" : "Top ↓ down", current: layout === l, onSelect: () => setLayout(l),
        }))} />
      </div>
      <Status of={graph} />
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
            <UICard className="graph-panel" as="aside">
              {node ? (
                <NodePanel node={node} onExpand={node.id === id ? undefined : () => expand(node.id)} />
              ) : (
                <Empty className="small">Select a node to see what it is. Select it twice to pull in its own links.</Empty>
              )}
              {graph.data.truncated && (
                <Text as="p" small>Too many neighbours to draw them all; showing the first 250.</Text>
              )}
            </UICard>
          </div>
          <Legend kinds={[...new Set(graph.data.nodes.map((n) => n.kind))]} />
        </>
      )}
    </section>
  );
}
