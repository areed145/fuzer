import { projectUrl } from "../authState";
import "../styles/entity.css";
import { Bar, Button, ButtonLink, PageHeader, Table, Tabs, Input, Select , Card as UICard, Badge as UIBadge} from "../ui";
import { Suspense, lazy, useMemo, useState, type ReactNode } from "react";
import { api, type Edge, type EntityDetail } from "../api";
import { DrawingViewer } from "../DrawingViewer";
import { NotThisRecord, SameAs } from "./masterdata/Curation";
import { AssetContextCard } from "./masterdata/Context";

const EntityMapView = lazy(() => import("../MapView").then((m) => ({ default: m.MapView })));
const LiveFigure = lazy(() => import("../analytics/LiveFigure").then((m) => ({ default: m.LiveFigure })));

/** Where this thing is (or the place it belongs to) and the located things linked to it. */
function EntityMap({ id }: { id: string }) {
  const m = useAsync(() => api.entityMap(id), [id]);
  if (!m.data?.features.length) return null;
  const own = m.data.focus === id;
  return (
    <UICard  as="section">
      <h2>
        Map{" "}
        <span className="muted small">
          {own ? "its location and what connects to it" : "where it sits, and what connects there"}
        </span>
      </h2>
      <EntityMapView data={m.data} focus={m.data.focus} height="380px" />
    </UICard>
  );
}
import { IncidentDetail, IncidentList } from "./Incidents";
import { MeasurementsCard } from "../Trend";
import { TrendView } from "../TrendView";
import { MemoryCard } from "../Memory";
import { WorkHere } from "../WorkHere";
import { WatchButton } from "../Watch";
import { Related } from "../Related";
import { kindColor } from "../kinds";
import { Dates, KindBadge, SourceBadge, Status, entityHref, fieldLabel, fmt, href, timestamp, useAsync } from "../lib";

// The graph tab pulls in Cytoscape; load it only when opened.
const EntityGraph = lazy(() => import("./EntityGraph").then((m) => ({ default: m.EntityGraph })));
const GraphCanvas = lazy(() => import("../graph").then((m) => ({ default: m.GraphCanvas })));

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
  classified_as: ["Class", "Members"],
  instance_of: ["Model", "Instances"],
  is_a: ["Is a", "Subtypes / models"],
  concerns: ["Concerns", "Maintenance records"],
  for_notification: ["For notification", "Work orders"],
  related_to: ["Related events", "Related notifications"],
  affects: ["Affects", "Downtime"],
  caused_by: ["Caused by", "Downtime caused"],
  reports_on: ["Reports on", "Daily reports"],
  about: ["About", "Incidents"],
  applies_to: ["Applies to", "Documents"],
  supersedes: ["Supersedes", "Superseded by"],
  uses_part: ["Spare parts", "Used by"],
  includes: ["Includes", "Part of incident"],
  drives: ["Drives", "Driven by"],
  pipes_to: ["Piped to", "Piped from"],
  lift_gas_to: ["Lift gas to", "Lift gas from"],
};
/** A relation the list above doesn't name reads as words, never as its key: "tested_by" in is "Tested by (from)". */
const relLabel = (e: Edge) => REL_LABELS[e.rel]?.[e.direction === "out" ? 0 : 1]
  ?? `${e.rel.charAt(0).toUpperCase()}${e.rel.slice(1).replaceAll("_", " ")} (${e.direction === "out" ? "to" : "from"})`;

type EntityTab = "overview" | "trends" | "connections" | "documents" | "record" | "graph";
const TAB_LABELS: Record<EntityTab, string> = {
  overview: "Overview", trends: "Timeline", connections: "Connections", documents: "Documents", record: "Record",
  graph: "Graph",
};
const NO_TRENDS = ["document", "document_section", "log_entry"];
const MAINTAINED = ["tag", "equipment", "equipment_model", "equipment_class"];

/** The views this kind of record has, in order. Each view loads only its own data. */
function entityTabs(e: EntityDetail): EntityTab[] {
  const trends = !NO_TRENDS.includes(e.kind) && e.kind !== "timeseries";
  // Connections is the flow diagram, so only a record on the flow network has it; its links are on Graph.
  const flow = e.edges.some((x) => x.rel === "flows_to");
  const documents = [...MAINTAINED, "area", "well", "facility", "pipeline"].includes(e.kind);
  return ["overview", ...(trends ? ["trends" as const] : []), ...(flow ? ["connections" as const] : []),
    ...(documents ? ["documents" as const] : []), "record", "graph"];
}

export function Entity({ id, tab }: { id: string; tab: string }) {
  const [asOf, setAsOf] = useState("");
  const ent = useAsync(() => api.entity(id, asOf || undefined), [id, asOf]);

  if (!ent.data) return <section><PageHeader>Asset and evidence</PageHeader>
    <Status loading={ent.loading} error={ent.error} onRetry={ent.reload} />
    {ent.error && <p>The register record may be unavailable or the request may have failed.
      {" "}<a href={href("/browse")}>Return to Explore</a> to find a loaded record.</p>}
  </section>;
  const e = ent.data;

  if (e.merged_into) {
    return (
      <div className="status">
        This ID was merged into <a href={entityHref(e.merged_into)}>{e.merged_into}</a>.
      </div>
    );
  }

  const tabs = entityTabs(e);
  const view: EntityTab = tabs.includes(tab as EntityTab) ? (tab as EntityTab) : "overview";
  const fid = e.fuzer_id;
  // Assets people work on open on their work and history; any other record opens on its values.
  const operated = ["tag", "equipment", "well", "facility", "area"].includes(e.kind);
  const description = e.golden.description ? fmt(e.golden.description.value) : undefined;

  return (
    <article className="entity">
      <header className="entity-head">
        <div>
          <PageHeader className="mono" subtitle={description}>{e.label ?? fid}</PageHeader>
          <div className="row">
            <KindBadge kind={e.kind} />
            <WatchButton target={fid} label="asset" />
            <a className="small" href={href("/ask", { subject: fid })}>Ask about this asset</a>
          </div>
        </div>
        <label className="asof">
          As of
          <Input type="date" value={asOf} onChange={(x) => setAsOf(x.target.value)} />
          {asOf && (
            <Button variant="link" onClick={() => setAsOf("")}>
              clear
            </Button>
          )}
        </label>
      </header>

      <Tabs className="page-tabs" label="Views of this record" items={tabs.map((t) => ({
        key: t, label: TAB_LABELS[t], current: t === view,
        href: href(`/e/${encodeURIComponent(id)}`, t === "overview" ? undefined : { tab: t }),
      }))} />

      {view === "graph" && (
        <Suspense fallback={<Status loading />}>
          <EntityGraph id={fid} asOf={asOf || undefined} />
          <Links edges={e.edges} counts={e.edge_counts} asOf={asOf} />
        </Suspense>
      )}

      {view === "overview" && (
        <>
          {operated && <WorkHere id={fid} area={e.kind === "area"} />}
          {e.kind === "incident" && <IncidentDetail id={fid} />}
          {e.kind === "document" && e.records.some((r) => r.source === "pid") && <DrawingViewer id={fid} />}
          {e.kind === "document" && e.golden.doc_type && (
            <UICard  as="section">
              <div className="row spread">
                <h2>
                  {fmt(e.golden.title?.value)}{" "}
                  <span className="muted small">
                    {fmt(e.golden.doc_type.value).replace("_", " ")} · rev {fmt(e.golden.revision?.value)} ·{" "}
                    {fmt(e.golden.status?.value)}
                  </span>
                </h2>
                <ButtonLink href={projectUrl(`/documents/${encodeURIComponent(fid)}/file`)} target="_blank"
                   rel="noreferrer">Open document</ButtonLink>
              </div>
              {e.golden.status?.value === "superseded" && (
                <p className="error small">This revision is superseded; see "Superseded by" under Graph.</p>
              )}
              <iframe
                className="doc-file"
                title={fmt(e.golden.title?.value)}
                src={projectUrl(`/documents/${encodeURIComponent(fid)}/file`)}
              />
            </UICard>
          )}
          {e.kind === "document_section" && (
            <UICard  as="section">
              <h2>
                {fmt(e.golden.heading?.value)}{" "}
                {e.golden.page && <span className="muted small">page {fmt(e.golden.page.value)}</span>}
              </h2>
              <p className="section-text">{fmt(e.golden.text?.value)}</p>
            </UICard>
          )}
          {e.kind === "timeseries" && <TrendView id={fid} />}
          {MAINTAINED.includes(e.kind) && (
            <><IncidentsCard id={fid} />
            <Related about={{ subject: fid }} incidents={false} /></>
          )}
          {MAINTAINED.includes(e.kind) && <MaintenanceCard id={fid} />}
          {["tag", "equipment", "equipment_model"].includes(e.kind) && <SparesCard id={fid} />}
          {[...MAINTAINED, "well", "facility"].includes(e.kind) && <DowntimeCard id={fid} />}
          {["tag", "equipment", "well"].includes(e.kind) && <ConversationsCard id={fid} />}
          {e.kind !== "event" && <Events id={fid} />}
          <Suspense fallback={null}>
            <EntityMap id={fid} />
          </Suspense>
          {!operated && <div className="grid">
            <Fields e={e} />
            <Records e={e} />
          </div>}
        </>
      )}

      {view === "trends" && (
        <>
          <TrendView id={fid} />
          <MeasurementsCard id={fid} />
          {["pipeline", "pipeline_segment"].includes(e.kind) && <AlongCard id={fid} />}
          {e.kind === "well" && <DepthCard id={fid} />}
        </>
      )}

      {view === "connections" && (
        <>
          <Trace id={fid} label={e.label ?? fid} kind={e.kind} asOf={asOf || undefined} />
        </>
      )}

      {view === "documents" && (
        <>
          <LinkedDrawings id={fid} />
          <KnowledgeCard id={fid} />
        </>
      )}

      {view === "record" && (
        <>
          {operated && <div className="grid">
            <Fields e={e} />
            <Records e={e} />
          </div>}
          {["tag", "equipment", "well"].includes(e.kind) && <AssetContextCard id={fid} />}
          {!NO_TRENDS.includes(e.kind) && e.kind !== "timeseries" && <MemoryCard id={fid} />}
          <HistoryCard id={fid} />
        </>
      )}
    </article>
  );
}

function LinkedDrawings({ id }: { id: string }) {
  const d = useAsync(() => api.entityDrawings(id), [id]);
  const [pick, setPick] = useState<string>();
  const sheets = d.data?.drawings ?? [];
  if (sheets.length === 0) return null;
  const sheet = sheets.find((x) => x.id === pick) ?? sheets[0];
  const title = (
    <>
      On P&ID{" "}
      {sheets.length > 1 ? (
        <Select value={sheet.id} onChange={(ev) => setPick(ev.target.value)} aria-label="P&ID sheet">
          {sheets.map((x) => <option key={x.id} value={x.id}>{x.label}{x.title ? ` · ${x.title}` : ""}</option>)}
        </Select>
      ) : (
        <>{sheet.label}{sheet.title && <span className="muted"> · {sheet.title}</span>}</>
      )}{" "}
      <a className="small" href={entityHref(sheet.id)}>open</a>
    </>
  );
  return <DrawingViewer key={sheet.id} id={sheet.id} highlight={d.data!.highlight} title={title} compact />;
}

/** Why a value says what it says (#225): the rule, and each record that states one with its mapping and load. */
function Why({ id, field }: { id: string; field: string }) {
  const w = useAsync(() => api.why(id, field), [id, field]);
  if (!w.data) return <Status loading={w.loading} error={w.error} onRetry={w.reload} />;
  return (
    <div className="small why">
      <div className="muted">
        Chosen by {w.data.rule === "review" ? "a reviewer" : `priority ${(w.data.priority ?? []).join(" > ")}`}
        {w.data.review_on_conflict ? "; disagreements go to review" : ""}.
      </div>
      {w.data.assertions.map((a, i) => (
        <div key={i}>
          <SourceBadge source={a.source} /> {fmt(a.value)} {a.wins && <UIBadge className="badge ok">chosen</UIBadge>}{" "}
          <code className="muted">{a.record}</code>
          {a.mapping_version && <span className="muted"> · mapping v{a.mapping_version.id}</span>}
          {a.run && <span className="muted"> · ingestion run {a.run.id} ({a.run.dataset}, {timestamp(a.run.finished_at)})</span>}
        </div>
      ))}
    </div>
  );
}

function Fields({ e }: { e: EntityDetail }) {
  // A point's GeoJSON geometry repeats its latitude and longitude as raw JSON; the map shows it (#392).
  const located = "latitude" in e.golden && "longitude" in e.golden;
  const fields = Object.keys(e.golden).filter((f) => !(located && f === "geometry")).sort();
  const [why, setWhy] = useState<string | null>(null);
  return (
    <UICard  as="section">
      <h2>Values</h2>
      <Table variant="keyValue" headingCase="verbatim">
        <tbody>
          {fields.map((f) => {
            const g = e.golden[f];
            const all = e.values[f] ?? [];
            const others = all.filter((v) => fmt(v.value) !== fmt(g.value));
            return (
              <tr key={f}>
                <th>{fieldLabel(f)}</th>
                <td>
                  <div className="row">
                    <span>{fmt(g.value)}</span>
                    <SourceBadge source={g.source} />
                    {g.rule === "review" && <UIBadge className="badge ok">reviewed</UIBadge>}
                    {g.rule !== "derived" && (
                      <Button variant="link" className="small" onClick={() => setWhy(why === f ? null : f)}
                        title="the records, mapping versions and loads behind this value">why?</Button>
                    )}
                  </div>
                  {why === f && <Why id={e.fuzer_id} field={f} />}
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
      </Table>
    </UICard>
  );
}

/** A PI AF element's key is its path with backslash separators, which read as escapes (#401); show it as a path. */
function sourceKey(source: string, key: string): string {
  return source === "pi_af" && key.includes("\\") ? key.split("\\").filter(Boolean).join(" › ") : key;
}

function Records({ e }: { e: EntityDetail }) {
  return (
    <UICard  as="section">
      <h2>Source records</h2>
      <div className="mono small">Register ID {e.fuzer_id}</div>
      {!e.records.length && <p>No direct source records are linked. This may be a derived record; inspect Values and Links for supporting provenance.</p>}
      <Table variant="list">
        <tbody>
          {e.records.map((r) => (
            <tr key={`${r.source}:${r.record_type}:${r.source_key}`}>
              <td>
                <SourceBadge source={r.source} />
              </td>
              <td className="mono small" title={r.source_key}>{sourceKey(r.source, r.source_key)}</td>
              <td className="muted small">
                {r.method === "singleton" ? "only record" : r.method}
                {r.method === "score" && ` ${r.confidence.toFixed(2)}`}
              </td>
              {e.records.length > 1 && <td>
                <NotThisRecord fuzerId={e.fuzer_id} refId={`${r.source}:${r.record_type}:${r.source_key}`} />
              </td>}
            </tr>
          ))}
        </tbody>
      </Table>
      {e.records.length > 0 && <SameAs fuzerId={e.fuzer_id} kind={e.kind} />}
      <div className="keys">
        {e.keys.map((k) => (
          <UIBadge key={`${k.kind}:${k.key}`} className="badge key mono" title={k.kind}>
            {k.key}
          </UIBadge>
        ))}
      </div>
    </UICard>
  );
}

function Links({ edges, counts, asOf }: { edges: Edge[]; counts: Record<string, number>; asOf: string }) {
  const groups = new Map<string, Edge[]>();
  for (const e of edges) groups.set(relLabel(e), [...(groups.get(relLabel(e)) ?? []), e]);
  return (
    <UICard  as="section">
      <h2>Links {asOf && <span className="muted small">on {asOf}</span>}</h2>
      <Status empty={edges.length === 0}>No links{asOf ? " on this date" : ""}.</Status>
      {[...groups].map(([label, items]) => (
        <div key={label} className="link-group">
          <h3>
            {label}{" "}
            <span className="muted">
              {(() => {
                const total = counts[`${items[0].direction}:${items[0].rel}`] ?? items.length;
                return total > items.length
                  ? `showing ${items.length} of ${total.toLocaleString()}`
                  : items.length.toLocaleString();
              })()}
            </span>
          </h3>
          <LinkList items={items} />
        </div>
      ))}
    </UICard>
  );
}

// Relations longer than this show their first few, the rest one click away (#400).
const LINKS_SHOWN = 5;

function LinkList({ items }: { items: Edge[] }) {
  const [all, setAll] = useState(false);
  const shown = all || items.length <= LINKS_SHOWN + 1 ? items : items.slice(0, LINKS_SHOWN);
  return (
    <>
          <ul>
            {shown.map((x, i) => (
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
          {shown.length < items.length && (
            <Button variant="link" size="sm" onClick={() => setAll(true)}>Show all {items.length.toLocaleString()}</Button>
          )}
    </>
  );
}

const TRACE_NEAR = 2;  // what feeds it and what it feeds, two steps each way; the whole network on request
const TRACE_ALL = 10;

/** What flows into this asset (left) and where its flow goes (right), drawn left to right. */
function Trace({ id, label, kind, asOf }: { id: string; label: string; kind: string; asOf?: string }) {
  const [all, setAll] = useState(false);
  const depth = all ? TRACE_ALL : TRACE_NEAR;
  const flow = useAsync(() => Promise.all([api.trace(id, "up", asOf, depth), api.trace(id, "down", asOf, depth)]),
    [id, asOf, depth]);
  const elements = useMemo(() => {
    if (!flow.data) return [];
    const [up, down] = flow.data;
    const nodes = new Map([[id, { id, label, kind }]]);
    const edges = new Map<string, { src: string; dst: string }>();
    for (const [rows, upstream] of [[up, true], [down, false]] as const) {
      for (const r of rows) {
        nodes.set(r.fuzer_id, { id: r.fuzer_id, label: r.label ?? r.fuzer_id, kind: r.kind });
        const [src, dst] = upstream ? [r.fuzer_id, r.parent ?? id] : [r.parent ?? id, r.fuzer_id];
        edges.set(`${src}>${dst}`, { src, dst });
      }
    }
    // The same element shape graph.tsx draws (subgraphElements), built here so the canvas stays in its own chunk.
    return [
      ...[...nodes.values()].map((n) => ({ data: { ...n, color: kindColor(n.kind), size: n.id === id ? 34 : 22,
        root: n.id === id } })),
      ...[...edges.values()].map((x, i) => ({ classes: "", data: { id: `e${i}`, source: x.src, target: x.dst,
        rel: "flows_to", ended: false, label: "flows to" } })),
    ];
  }, [flow.data, id, label, kind]);
  const empty = flow.data && elements.length <= 1;
  return (
    <UICard  as="section">
      <div className="row spread">
        <h2>Flow {asOf && <span className="muted small">on {asOf}</span>}</h2>
        <Button variant="link" onClick={() => setAll(!all)}>{all ? "Show nearest only" : "Show whole network"}</Button>
      </div>
      <Status loading={flow.loading && !flow.data} error={flow.error} onRetry={flow.reload} empty={!!empty}>
        Nothing flows in or out{asOf ? " on this date" : ""}.
      </Status>
      {flow.data && !empty && <Suspense fallback={<Status loading />}>
        <GraphCanvas elements={elements} layout="flow" selectedId={id} height={all ? 600 : 320}
          onSelect={(to) => { if (to && to !== id) location.hash = entityHref(to); }} />
      </Suspense>}
    </UICard>
  );
}

function Events({ id }: { id: string }) {
  const evs = useAsync(() => api.events(id), [id]);
  if (!evs.data?.length) return null;
  return (
    <UICard  as="section">
      <h2>
        Events <span className="muted small">{evs.data.length} from operator logs, read by Claude</span>
      </h2>
      <ul className="timeline">
        {evs.data.map((ev) => (
          <li key={ev.id}>
            <span className="when">{ev.timestamp?.slice(0, 10) ?? "—"}</span>
            <div className="what">
              <div className="row">
                <UIBadge className="badge event-type">{(ev.type ?? "event").replace("_", " ")}</UIBadge>
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
    </UICard>
  );
}

function MaintenanceCard({ id }: { id: string }) {
  const m = useAsync(() => api.maintenance(id), [id]);
  if (!m.data?.total) return null;
  const top = Math.max(...m.data.failure_modes.map((f) => f.count));
  return (
    <UICard  as="section">
      <h2>
        Maintenance <span className="muted small">{m.data.total.toLocaleString()} SAP notifications</span>
      </h2>
      <div className="grid">
        <div>
          <h3>Failure modes (ISO 14224)</h3>
          <ul className="coverage">
            {m.data.failure_modes.map((f) => (
              <li key={f.code ?? "none"}>
                <span>{f.name ?? "Not coded"}</span>
                <Bar className="bar" value={(100 * f.count) / top} />
                <span className="muted small">{f.count}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Recent</h3>
          <ul className="timeline">
            {m.data.recent.slice(0, 8).map((n) => (
              <li key={n.id}>
                <span className="when">{n.created}</span>
                <div className="what">
                  <div className="row">
                    {n.failure_mode && <UIBadge className="badge event-type">{n.failure_mode}</UIBadge>}
                    <a href={entityHref(n.id)}>{n.text}</a>
                  </div>
                  {n.work_orders.map((w) => (
                    <div key={w.id} className="muted small">
                      WO <a href={entityHref(w.id)}>{w.label}</a> · {w.activity} · {w.start}
                    </div>
                  ))}
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </UICard>
  );
}

function DowntimeCard({ id }: { id: string }) {
  const d = useAsync(() => api.downtime(id), [id]);
  if (!d.data?.events) return null;
  return (
    <UICard  as="section">
      <h2>
        Downtime{" "}
        <span className="muted small">
          {Math.round(d.data.lost_oil_sm3).toLocaleString()} Sm³ oil lost over {d.data.events} events
        </span>
      </h2>
      <ul className="timeline">
        {d.data.recent.slice(0, 8).map((r) => (
          <li key={r.id}>
            <span className="when">{r.start?.slice(0, 10)}</span>
            <div className="what">
              <div className="row">
                <UIBadge className="badge event-type">{r.reason ?? "downtime"}</UIBadge>
                <a href={entityHref(r.id)}>{r.comment}</a>
              </div>
              <div className="muted small">
                {r.duration_h} h · {Math.round(r.lost_oil_sm3 ?? 0).toLocaleString()} Sm³
                {r.reason_check && <span className="error"> · {r.reason_check}</span>}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </UICard>
  );
}

function IncidentsCard({ id }: { id: string }) {
  const d = useAsync(() => api.incidents(id), [id]);
  if (!d.data?.total) return null;
  return (
    <section>
      <h2>
        Incidents <span className="muted small">{d.data.total.toLocaleString()}</span>
      </h2>
      <IncidentList items={d.data.items.slice(0, 8)} />
    </section>
  );
}

/** How people worked a problem on this asset in Teams (P5.8b): each thread's problem, what was tried and the fix,
 * with a link back to the thread. A summary only, with people named by their job title. */
function ConversationsCard({ id }: { id: string }) {
  const d = useAsync(() => api.assetThreads(id), [id]);
  const list = Array.isArray(d.data) ? d.data : [];
  if (!list.length) return d.error ? <Status error={d.error} onRetry={d.reload} /> : null;
  return (
    <UICard as="section" className="conversations" title="From Teams">
      <ul className="plain">{list.map((c) => <li key={c.id} className="conversation">
        <p><strong>{c.subject ?? c.problem ?? "Thread"}</strong>{" "}
          <UIBadge tone={c.state === "open" ? "warn" : "ok"}>{c.state === "open" ? "still open" : "fixed"}</UIBadge></p>
        <p className="small">{[c.channel, c.started_at ? timestamp(c.started_at) : null, c.participants.join(", ")].filter(Boolean).join(" · ")}</p>
        {c.problem && <p className="small">Problem: {c.problem}</p>}
        {c.tried.length > 0 && <p className="small">Tried: {c.tried.join(" · ")}</p>}
        {c.fix && <p className="small">Fix: {c.fix}</p>}
        {c.web_url && <a className="small" href={c.web_url} target="_blank" rel="noopener noreferrer">Open in Teams</a>}
      </li>)}</ul>
    </UICard>
  );
}

/** ts_headline marks matches with [[ ]]; render them bold without trusting any HTML in documents. */
function Highlighted({ text }: { text: string }) {
  const parts: ReactNode[] = text.split(/(\[\[.*?\]\])/g).map((p, i) =>
    p.startsWith("[[") ? <b key={i}>{p.slice(2, -2)}</b> : p,
  );
  return <>{parts}</>;
}

function KnowledgeCard({ id }: { id: string }) {
  const docs = useAsync(() => api.documentsFor(id), [id]);
  const [q, setQ] = useState("");
  const [asked, setAsked] = useState("");
  const results = useAsync(() => (asked ? api.knowledgeSearch(asked, id) : Promise.resolve([])), [asked, id]);
  if (!docs.data?.length) return null;
  return (
    <UICard  as="section">
      <h2>
        Documents <span className="muted small">manuals, procedures and guides that apply here</span>
      </h2>
      <ul className="plain">
        {docs.data.map((d) => (
          <li key={d.id} className="small">
            <a href={entityHref(d.id)}>{d.title ?? d.label}</a>{" "}
            <span className="muted">
              {d.doc_type?.replace("_", " ")} · rev {d.revision} · via {d.why}
            </span>
          </li>
        ))}
      </ul>
      <form className="actions" onSubmit={(ev) => { ev.preventDefault(); setAsked(q.trim()); }}>
        <Input value={q} placeholder="Ask the documents, e.g. what causes high vibration?" onChange={(ev) => setQ(ev.target.value)} />
        <Button disabled={!q.trim()}>Search</Button>
      </form>
      {asked && (
        <ul className="timeline">
          {results.data?.length === 0 && <li className="muted small">No passages found.</li>}
          {results.data?.map((r) => (
            <li key={r.id}>
              <span className="when">{r.page ? `p. ${r.page}` : r.kind === "incident" ? "incident" : ""}</span>
              <div className="what">
                <a href={r.kind === "lesson" ? href("/lessons", { status: "approved" }) : entityHref(r.id)}>
                  {r.kind === "lesson" ? `Lesson learned · ${r.title}` : r.document_label ? `${r.document_label} · ${r.heading}` : r.title}
                </a>
                <span className="small"><Highlighted text={r.snippet} /></span>
                {r.why && <span className="muted small">{r.why}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </UICard>
  );
}

function HistoryCard({ id }: { id: string }) {
  const h = useAsync(() => api.history(id), [id]);
  if (!h.data?.length) return null;
  return (
    <UICard  as="section">
      <h2>
        Change history <span className="muted small">how the source records behind this changed</span>
      </h2>
      <ul className="timeline">
        {h.data.map((c, i) => (
          <li key={i}>
            <span className="when">{c.at?.slice(0, 16).replace("T", " ")}</span>
            <div className="what">
              <span className="small">
                <UIBadge className="badge source">{c.record.split(":")[0]}</UIBadge> {c.record.split(":").slice(2).join(":")}{" "}
                <span className="muted">{c.change}</span>
              </span>
              {Object.entries(c.fields).map(([f, v]) => (
                <span key={f} className="small">
                  <b>{f}</b>: <span className="muted">{fmt(v.from)}</span> → {fmt(v.to)}
                </span>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </UICard>
  );
}

function SparesCard({ id }: { id: string }) {
  const parts = useAsync(() => api.spares(id), [id]);
  if (!parts.data?.length) return null;
  return (
    <UICard  as="section">
      <h2>Spare parts</h2>
      <Table variant="list">
        <tbody>
          {parts.data.map((p) => (
            <tr key={p.fuzer_id}>
              <td><a href={entityHref(p.fuzer_id)} className="mono">{p.label}</a></td>
              <td>{p.description}<div className="muted small mono">{p.mpn}</div></td>
              <td className={p.stock > 0 ? "small" : "small error"}>{p.stock > 0 ? `${p.stock} in stock` : "none in stock"}</td>
              <td className="muted small">{p.bin}</td>
              <td className="small">{p.documents.map((d) => <a key={d.id} href={entityHref(d.id)}>manual</a>)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </UICard>
  );
}

/** A well's trajectory and completion by depth (#61), when its survey or completion is loaded. */
function DepthCard({ id }: { id: string }) {
  const d = useAsync(() => api.depths(id), [id]);
  if (!d.data?.trajectory?.length && !d.data?.components?.length) return null;
  return (
    <UICard as="section">
      <h2>Trajectory and completion <span className="muted small">by measured and true vertical depth</span></h2>
      <Suspense fallback={null}><LiveFigure kind="well-trajectory" params={{ well: id }} /></Suspense>
    </UICard>
  );
}

const km = (m: unknown) => (typeof m === "number" ? (m / 1000).toFixed(3) : "");

/** A pipeline's segments and what in-line inspection found on it, by station (#61). */
function AlongCard({ id }: { id: string }) {
  const a = useAsync(() => api.along(id), [id]);
  if (!a.data?.segments?.length && !a.data?.features?.length) return null;
  const { segments = [], features = [] } = a.data ?? {};
  const seg = Object.fromEntries(segments.map((s) => [s.id, s.label]));
  return (
    <UICard as="section">
      <h2>Along the line <span className="muted small">by station, distance from the line's start</span></h2>
      {segments.length > 0 && (
        <Table variant="list">
          <thead><tr><th>Segment</th><th>From (km)</th><th>To (km)</th><th>Wall (mm)</th><th>Class location</th></tr></thead>
          <tbody>
            {segments.map((s) => (
              <tr key={s.id}>
                <td><a href={entityHref(s.id)} className="mono">{s.label}</a><div className="muted small">{fmt(s.description)}</div></td>
                <td className="small mono">{km(s.station_from_m)}</td>
                <td className="small mono">{km(s.station_to_m)}</td>
                <td className="small">{fmt(s.wall_thickness_mm)}</td>
                <td className="small">{fmt(s.class_location)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {features.length > 0 && (
        <>
          <h3>Found by in-line inspection</h3>
          <Table variant="list">
            <thead><tr><th>Feature</th><th>Station (km)</th><th>Clock</th><th>Segment</th><th>Depth (% of wall)</th><th>Length (mm)</th><th>Run</th></tr></thead>
            <tbody>
              {features.map((f) => (
                <tr key={f.id}>
                  <td><a href={entityHref(f.id)} className="mono">{f.label}</a><div className="small">{fmt(f.feature_type)}</div></td>
                  <td className="small mono">{km(f.station_m)}</td>
                  <td className="small mono">{fmt(f.clock)}</td>
                  <td className="small mono">{f.segment ? seg[f.segment] ?? "" : <span className="muted">no segment holds it</span>}</td>
                  <td className="small">{fmt(f.depth_pct)}</td>
                  <td className="small">{fmt(f.length_mm)}</td>
                  <td className="muted small">{fmt(f.run_id)} {f.run_date ? `· ${fmt(f.run_date)}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        </>
      )}
    </UICard>
  );
}
