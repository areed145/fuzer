import { Button, ButtonLink, PageHeader, Input , Card as UICard} from "../ui";
import { useMemo, useState } from "react";
import { api, type GraphNode, type Subgraph } from "../api";
import { GraphCanvas, Legend, subgraphElements } from "../graph";
import { KindBadge, Status, entityHref, href, useAsync } from "../lib";

export function Network() {
  const [asOf, setAsOf] = useState("");
  const [rel, setRel] = useState<"flows_to" | "lift_gas_to" | "pipes_to">("flows_to");
  const [selected, setSelected] = useState<string | null>(null);
  const net = useAsync(() => api.network(asOf || undefined, rel), [asOf, rel]);
  // The whole network drawn at once is a tall column of unreadable nodes (#300), so a big one opens on its
  // largest first collector (a manifold: what the wells flow into) with everything up- and downstream of it.
  const groups = useMemo(() => (net.data ? collectors(net.data) : []), [net.data]);
  const [focus, setFocus] = useState<string | null>(null);
  const shownFocus = focus === "all" ? null
    : focus && groups.some((g) => g.id === focus) ? focus
    : (net.data?.nodes.length ?? 0) > BIG && groups.length > 1 ? groups[0].id : null;
  const shown = useMemo(() => (net.data && shownFocus ? around(net.data, shownFocus) : net.data), [net.data, shownFocus]);
  // Every link here is the same kind, so labels only add clutter; hover a link to see its dates.
  const elements = useMemo(
    () => (shown ? subgraphElements(shown, undefined, false, !asOf) : []),
    [shown, asOf],
  );
  const node = net.data?.nodes.find((n) => n.id === selected);

  return (
    <section>
      <div className="row spread">
        <div>
          <PageHeader>Flow network</PageHeader>
          <p className="muted">
            {rel === "lift_gas_to"
              ? "Gas lift from Energy Components: compression → lift gas headers → jacket gas lift manifolds (some through another jacket) → gas lifted wells."
              : rel === "pipes_to" ? "Process piping read from the P&IDs."
              : "Production and allocation flow from Energy Components: wells → manifolds (some through another jacket) → separators → meters → sales."}
            {!asOf && " Dashed lines are routings that have ended; pick a date to see the network as it was."} Hover a
            line for its dates.
          </p>
        </div>
        <div className="tabs">
          <Button className={rel === "flows_to" ? "on" : ""} onClick={() => setRel("flows_to")}>Allocation (EC)</Button>
          <Button className={rel === "lift_gas_to" ? "on" : ""} onClick={() => setRel("lift_gas_to")}>Gas lift (EC)</Button>
          <Button className={rel === "pipes_to" ? "on" : ""} onClick={() => setRel("pipes_to")}>Piping (P&IDs)</Button>
        </div>
        <label className="asof">
          As of
          <Input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          {asOf && (
            <Button variant="link" onClick={() => setAsOf("")}>
              all dates
            </Button>
          )}
        </label>
      </div>
      <Status loading={net.loading && !net.data} error={net.error} empty={net.data?.edges.length === 0}>
        No flow links{asOf ? " on this date" : ""}.
      </Status>
      {net.data && net.data.edges.length > 0 && (
        <>
          {groups.length > 1 && (
            <label className="row small">
              Show
              <select value={shownFocus ?? "all"} onChange={(e) => setFocus(e.target.value)} aria-label="Show part of the network">
                <option value="all">the whole network ({net.data.nodes.length} nodes)</option>
                {groups.map((g) => <option key={g.id} value={g.id}>{g.label} and what flows through it ({g.inputs} in)</option>)}
              </select>
            </label>
          )}
          <div className="graph-layout">
            <GraphCanvas elements={elements} layout="flow" selectedId={selected} onSelect={setSelected} height={640} />
            <UICard className="graph-panel" as="aside">
              {node ? <NodePanel node={node} /> : <p className="muted small">Click a node to see what it is.</p>}
            </UICard>
          </div>
          <Legend kinds={[...new Set(net.data.nodes.map((n) => n.kind))]} />
          {net.data.truncated && <p className="muted small">Too many links to draw; showing the first 5,000.</p>}
        </>
      )}
    </section>
  );
}

const BIG = 60;

/** First collectors: nodes fed directly by a node nothing flows into (wells → manifolds), most inputs first. */
function collectors(g: Subgraph): { id: string; label: string; inputs: number }[] {
  const fed = new Set(g.edges.map((e) => e.dst));
  const inputs = new Map<string, Set<string>>();
  for (const e of g.edges) if (!fed.has(e.src)) inputs.set(e.dst, (inputs.get(e.dst) ?? new Set()).add(e.src));
  const label = new Map(g.nodes.map((n) => [n.id, n.label ?? n.id]));
  return [...inputs].map(([id, from]) => ({ id, label: label.get(id) ?? id, inputs: from.size }))
    .sort((a, b) => b.inputs - a.inputs || a.label.localeCompare(b.label));
}

/** A node with everything upstream and downstream of it. */
function around(g: Subgraph, id: string): Subgraph {
  const walk = (next: (e: Subgraph["edges"][number]) => [string, string]) => {
    const seen = new Set([id]);
    for (let grew = true; grew;) {
      grew = false;
      for (const e of g.edges) {
        const [from, to] = next(e);
        if (seen.has(from) && !seen.has(to)) { seen.add(to); grew = true; }
      }
    }
    return seen;
  };
  const keep = new Set([...walk((e) => [e.dst, e.src]), ...walk((e) => [e.src, e.dst])]);
  return { ...g, nodes: g.nodes.filter((n) => keep.has(n.id)),
    edges: g.edges.filter((e) => keep.has(e.src) && keep.has(e.dst)) };
}

export function NodePanel({ node, onExpand }: { node: GraphNode; onExpand?: () => void }) {
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
      </div>
    </>
  );
}
