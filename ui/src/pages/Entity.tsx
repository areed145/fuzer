import { Suspense, lazy, useState } from "react";
import { api, type Edge, type EntityDetail } from "../api";
import { Dates, KindBadge, SourceBadge, Status, entityHref, fmt, href, useAsync } from "../lib";

// The graph tab pulls in Cytoscape; load it only when opened.
const EntityGraph = lazy(() => import("./EntityGraph").then((m) => ({ default: m.EntityGraph })));

// How each edge reads from this entity's point of view: [outgoing, incoming].
const REL_LABELS: Record<string, [string, string]> = {
  part_of: ["Part of", "Contains"],
  installed_at: ["Installed at", "Equipment installed here"],
  has_timeseries: ["Time series", "Measures"],
  flows_to: ["Flows to", "Fed by"],
  mentions: ["Mentions", "Mentioned in"],
  sidetrack_of: ["Sidetrack of", "Sidetracks"],
  reports: ["Reports event", "Reported in"],
  involves: ["Involves", "Events"],
};
const relLabel = (e: Edge) => REL_LABELS[e.rel]?.[e.direction === "out" ? 0 : 1] ?? `${e.rel} (${e.direction})`;

export function Entity({ id, tab }: { id: string; tab: "details" | "graph" }) {
  const [asOf, setAsOf] = useState("");
  const ent = useAsync(() => api.entity(id, asOf || undefined), [id, asOf]);

  if (!ent.data) return <Status loading={ent.loading} error={ent.error} />;
  const e = ent.data;

  if (e.merged_into) {
    return (
      <div className="status">
        This ID was merged into <a href={entityHref(e.merged_into)}>{e.merged_into}</a>.
      </div>
    );
  }

  const hasFlow = e.edges.some((x) => x.rel === "flows_to");

  return (
    <article className="entity">
      <header className="entity-head">
        <div>
          <h1 className="mono">{e.label ?? e.fuzer_id}</h1>
          <div className="row">
            <KindBadge kind={e.kind} />
            <span className="muted mono small">{e.fuzer_id}</span>
            {e.golden.description && <span>{fmt(e.golden.description.value)}</span>}
          </div>
        </div>
        <label className="asof">
          As of
          <input type="date" value={asOf} onChange={(x) => setAsOf(x.target.value)} />
          {asOf && (
            <button className="link" onClick={() => setAsOf("")}>
              clear
            </button>
          )}
        </label>
      </header>

      <div className="tabs page-tabs">
        <a className={tab === "details" ? "on" : ""} href={href(`/e/${encodeURIComponent(id)}`)}>
          Details
        </a>
        <a className={tab === "graph" ? "on" : ""} href={href(`/e/${encodeURIComponent(id)}`, { tab: "graph" })}>
          Graph
        </a>
      </div>

      {tab === "graph" ? (
        <Suspense fallback={<Status loading />}>
          <EntityGraph id={e.fuzer_id} asOf={asOf || undefined} />
        </Suspense>
      ) : (
        <>
          <div className="grid">
            <Fields e={e} />
            <Records e={e} />
          </div>
          {hasFlow && <Trace id={e.fuzer_id} asOf={asOf || undefined} />}
          {e.kind === "document" && e.golden.format?.value === "image" && <Drawing id={e.fuzer_id} />}
          {e.kind !== "event" && <Events id={e.fuzer_id} />}
          <Links edges={e.edges} asOf={asOf} />
        </>
      )}
    </article>
  );
}

function Fields({ e }: { e: EntityDetail }) {
  const fields = Object.keys(e.golden).sort();
  return (
    <section className="card">
      <h2>Values</h2>
      <p className="muted small">The chosen value, which source it came from, and what every other source says.</p>
      <table className="kv">
        <tbody>
          {fields.map((f) => {
            const g = e.golden[f];
            const all = e.values[f] ?? [];
            const others = all.filter((v) => fmt(v.value) !== fmt(g.value));
            return (
              <tr key={f}>
                <th>{f.replace("_", " ")}</th>
                <td>
                  <div className="row">
                    <span>{fmt(g.value)}</span>
                    <SourceBadge source={g.source} />
                    {g.rule === "review" && <span className="badge ok">reviewed</span>}
                  </div>
                  {others.length > 0 ? (
                    <ul className="others">
                      {others.map((v, i) => (
                        <li key={i}>
                          <SourceBadge source={v.source} /> {fmt(v.value)}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    all.length > 1 && <div className="muted small">{all.length} sources agree</div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function Records({ e }: { e: EntityDetail }) {
  return (
    <section className="card">
      <h2>Source records</h2>
      <p className="muted small">Every record in every system that Fuzer decided is this same thing.</p>
      <table className="list">
        <tbody>
          {e.records.map((r) => (
            <tr key={`${r.source}:${r.record_type}:${r.source_key}`}>
              <td>
                <SourceBadge source={r.source} />
              </td>
              <td className="mono small">{r.source_key}</td>
              <td className="muted small">
                {r.method === "singleton" ? "only record" : r.method}
                {r.method === "score" && ` ${r.confidence.toFixed(2)}`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="keys">
        {e.keys.map((k) => (
          <span key={`${k.kind}:${k.key}`} className="badge key mono" title={k.kind}>
            {k.key}
          </span>
        ))}
      </div>
    </section>
  );
}

function Links({ edges, asOf }: { edges: Edge[]; asOf: string }) {
  const groups = new Map<string, Edge[]>();
  for (const e of edges) groups.set(relLabel(e), [...(groups.get(relLabel(e)) ?? []), e]);
  return (
    <section className="card">
      <h2>Links {asOf && <span className="muted small">on {asOf}</span>}</h2>
      <Status empty={edges.length === 0}>No links{asOf ? " on this date" : ""}.</Status>
      {[...groups].map(([label, items]) => (
        <div key={label} className="link-group">
          <h3>
            {label} <span className="muted">{items.length}</span>
          </h3>
          <ul>
            {items.map((x, i) => (
              <li key={`${x.other}-${x.valid_from}-${i}`}>
                <a href={entityHref(x.other)} className="mono">
                  {x.other_label ?? x.other}
                </a>
                <KindBadge kind={x.other_kind} />
                {x.other_description && x.rel !== "has_timeseries" && (
                  <span className="muted small">{x.other_description}</span>
                )}
                <Dates from={x.valid_from} to={x.valid_to} />
                {typeof x.props?.text === "string" && <span className="quote">“{x.props.text}”</span>}
                {typeof x.props?.attribute === "string" && <span className="muted small">{x.props.attribute}</span>}
                <span className="sources">
                  {x.sources.map((s) => (
                    <SourceBadge key={s} source={s} />
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}

function Trace({ id, asOf }: { id: string; asOf?: string }) {
  const [direction, setDirection] = useState<"up" | "down">("up");
  const rows = useAsync(() => api.trace(id, direction, asOf), [id, direction, asOf]);
  return (
    <section className="card">
      <div className="row spread">
        <h2>Flow trace {asOf && <span className="muted small">on {asOf}</span>}</h2>
        <div className="tabs">
          <button className={direction === "up" ? "on" : ""} onClick={() => setDirection("up")}>
            Upstream
          </button>
          <button className={direction === "down" ? "on" : ""} onClick={() => setDirection("down")}>
            Downstream
          </button>
        </div>
      </div>
      <Status loading={rows.loading && !rows.data} error={rows.error} empty={rows.data?.length === 0}>
        Nothing {direction}stream{asOf ? " on this date" : ""}.
      </Status>
      <ul className="trace">
        {rows.data?.map((r) => (
          <li key={`${r.fuzer_id}-${r.depth}`} style={{ paddingLeft: `${(r.depth - 1) * 20}px` }}>
            <span className="muted">{direction === "up" ? "↑" : "↓"}</span>
            <a href={entityHref(r.fuzer_id)} className="mono">
              {r.label ?? r.fuzer_id}
            </a>
            <KindBadge kind={r.kind} />
            <Dates from={r.valid_from} to={r.valid_to} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function Events({ id }: { id: string }) {
  const evs = useAsync(() => api.events(id), [id]);
  if (!evs.data?.length) return null;
  return (
    <section className="card">
      <h2>
        Events <span className="muted small">{evs.data.length} from operator logs, read by Claude</span>
      </h2>
      <ul className="timeline">
        {evs.data.map((ev) => (
          <li key={ev.id}>
            <span className="when">{ev.timestamp?.slice(0, 10) ?? "—"}</span>
            <div className="what">
              <div className="row">
                <span className="badge event-type">{(ev.type ?? "event").replace("_", " ")}</span>
                <a href={entityHref(ev.id)}>{ev.summary}</a>
              </div>
              {ev.involves.length > 0 && (
                <div className="row small">
                  {ev.involves.map((x) => (
                    <span key={`${x.id}-${x.role}`}>
                      <a href={entityHref(x.id)} className="mono">
                        {x.label ?? x.id}
                      </a>
                      {x.role && x.role !== "subject" && <span className="muted"> ({x.role})</span>}
                    </span>
                  ))}
                </div>
              )}
              {ev.log && (
                <div className="quote">
                  <a href={entityHref(ev.log.id)}>{ev.log.label}</a>: “{ev.log.text}”
                </div>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Drawing({ id }: { id: string }) {
  const src = `/documents/${encodeURIComponent(id)}/image`;
  return (
    <section className="card">
      <h2>
        Scanned drawing <span className="muted small">tags read from it by Claude are listed under Mentions</span>
      </h2>
      <a href={src} target="_blank" rel="noreferrer">
        <img className="drawing" src={src} alt="Scanned P&ID" />
      </a>
    </section>
  );
}
