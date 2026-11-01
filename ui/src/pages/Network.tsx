import { useMemo, useState } from "react";
import { api, type GraphNode } from "../api";
import { GraphCanvas, Legend, subgraphElements } from "../graph";
import { KindBadge, Status, entityHref, href, useAsync } from "../lib";

export function Network() {
  const [asOf, setAsOf] = useState("");
  const [rel, setRel] = useState<"flows_to" | "lift_gas_to" | "pipes_to">("flows_to");
  const [selected, setSelected] = useState<string | null>(null);
  const net = useAsync(() => api.network(asOf || undefined, rel), [asOf, rel]);
  // Every link here is the same kind, so labels only add clutter; hover a link to see its dates.
  const elements = useMemo(
    () => (net.data ? subgraphElements(net.data, undefined, false, !asOf) : []),
    [net.data, asOf],
  );
  const node = net.data?.nodes.find((n) => n.id === selected);

  return (
    <section>
      <div className="row spread">
        <div>
          <h1>Flow network</h1>
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
          <button className={rel === "flows_to" ? "on" : ""} onClick={() => setRel("flows_to")}>Allocation (EC)</button>
          <button className={rel === "lift_gas_to" ? "on" : ""} onClick={() => setRel("lift_gas_to")}>Gas lift (EC)</button>
          <button className={rel === "pipes_to" ? "on" : ""} onClick={() => setRel("pipes_to")}>Piping (P&IDs)</button>
        </div>
        <label className="asof">
          As of
          <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} />
          {asOf && (
            <button className="link" onClick={() => setAsOf("")}>
              all dates
            </button>
          )}
        </label>
      </div>
      <Status loading={net.loading && !net.data} error={net.error} empty={net.data?.edges.length === 0}>
        No flow links{asOf ? " on this date" : ""}.
      </Status>
      {net.data && net.data.edges.length > 0 && (
        <>
          <div className="graph-layout">
            <GraphCanvas elements={elements} layout="flow" selectedId={selected} onSelect={setSelected} height={640} />
            <aside className="card graph-panel">
              {node ? <NodePanel node={node} /> : <p className="muted small">Click a node to see what it is.</p>}
            </aside>
          </div>
          <Legend kinds={[...new Set(net.data.nodes.map((n) => n.kind))]} />
          {net.data.truncated && <p className="muted small">Too many links to draw; showing the first 5,000.</p>}
        </>
      )}
    </section>
  );
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
        <a className="button" href={entityHref(node.id)}>
          Open
        </a>
        <a className="button" href={href(`/e/${encodeURIComponent(node.id)}`, { tab: "graph" })}>
          Graph from here
        </a>
        {onExpand && <button onClick={onExpand}>Expand</button>}
      </div>
    </>
  );
}
