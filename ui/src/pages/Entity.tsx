import "../styles/entity.css";
import { when, whenDay, count } from "../format";
import { Bar, Button, ButtonLink, Notice, PageHeader, Table, Tabs, Input, Card as UICard, Badge as UIBadge, ErrorText, Line, Text, List, Heading } from "../ui";
import { Suspense, lazy, useMemo, useState, type ReactNode } from "react";
import { api, type Edge, type EntityDetail } from "../api";
import { DrawingViewer, SheetThumb } from "../DrawingViewer";
import { DocumentPages, documentHref, openDocument } from "../DocumentViewer";
import { NotThisRecord, SameAs } from "./masterdata/Curation";
import { AssetContextCard } from "./masterdata/Context";
import { caseHref } from "./workflows/shared";

const EntityMapView = lazy(() => import("../MapView").then((m) => ({ default: m.MapView })));
const LiveFigure = lazy(() => import("../analytics/LiveFigure").then((m) => ({ default: m.LiveFigure })));

/** Where this thing is (or the place it belongs to) and the located things linked to it. */
function EntityMap({ id }: { id: string }) {
  const m = useAsync(() => api.entityMap(id), [id]);
  if (!m.data?.features.length) return null;
  const own = m.data.focus === id;
  return (
    <UICard as="section" title="Map" detail={own ? "its location and what connects to it" : "where it sits, and what connects there"}>
      <EntityMapView data={m.data} focus={m.data.focus} height="380px" />
    </UICard>
  );
}
import { IncidentDetail, IncidentList } from "./Incidents";
import { MeasurementsCard } from "../Trend";
import { TrendView } from "../TrendView";
import { MemoryCard } from "../Memory";
import { WorkHere, readWork } from "../WorkHere";
import { EntityStatus, readStatus } from "../EntityStatus";
import { AssetProfile, readProfile } from "../AssetProfile";
import { LiveReadings, readLatest } from "../LiveReadings";
import { WatchButton } from "../Watch";
import { Related } from "../Related";
import { kindColor, relColor } from "../kinds";
import { nodeLabel } from "../entityRefs";
import { usePermission } from "../Auth";
import { keptAt } from "../fieldPack";
import { ASSET_KINDS, kindWord, person as personName } from "../wording";
import { Dates, KindBadge, SourceBadge, Status, entityHref, fieldLabel, fmt, href, useAsync } from "../lib";

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
  relieves_to: ["Relieves to", "Relieved from"],
};
/** A relation the list above doesn't name reads as words, never as its key: "tested_by" in is "Tested by (from)". */
const relLabel = (e: Edge) => REL_LABELS[e.rel]?.[e.direction === "out" ? 0 : 1]
  ?? `${e.rel.charAt(0).toUpperCase()}${e.rel.slice(1).replaceAll("_", " ")} (${e.direction === "out" ? "to" : "from"})`;

type EntityTab = "overview" | "maintenance" | "trends" | "connections" | "documents" | "record" | "graph";
const TAB_LABELS: Record<EntityTab, string> = {
  overview: "Overview", maintenance: "Maintenance", trends: "Timeline", connections: "Connections", documents: "Documents", record: "Data",
  graph: "Data",
};
const NO_TRENDS = ["document", "document_section", "log_entry"];
const MAINTAINED = ["tag", "equipment", "equipment_model", "equipment_class"];

/** The views this kind of record has, in order. Each view loads only its own data. */
function entityTabs(e: EntityDetail): EntityTab[] {
  const trends = !NO_TRENDS.includes(e.kind) && e.kind !== "timeseries";
  // Connections is the flow diagram, so only a record on the piping or allocation network has it; its links are on Graph.
  const flow = e.edges.some((x) => x.rel === "flows_to" || x.rel === "pipes_to");
  const documents = [...MAINTAINED, "area", "well", "facility", "pipeline"].includes(e.kind);
  // Maintenance is the field technician's view: work orders and notifications, spare parts, and downtime.
  const maintained = MAINTAINED.includes(e.kind);
  return ["overview", ...(maintained ? ["maintenance" as const] : []), ...(trends ? ["trends" as const] : []), ...(flow ? ["connections" as const] : []),
    // Record and Graph serve the data steward; they are one Data tab, and a `tab=graph` link still opens it (#1076).
    ...(documents ? ["documents" as const] : []), "record"];
}

export function Entity({ id, tab }: { id: string; tab: string }) {
  const [asOf, setAsOf] = useState("");
  const [dated, setDated] = useState(false);
  const ent = useAsync(() => api.entity(id, asOf || undefined), [id, asOf]);
  const configure = usePermission("configure");
  // The overview's cards, read together and drawn together: each drawn as it answered, the later ones pushed the
  // earlier down the page (#1069).
  const oid = ent.data?.fuzer_id, okind = ent.data?.kind;
  const onOverview = !tab || tab === "overview";
  const overview = useAsync(async () => {
    if (!oid || !okind || !onOverview) return null;
    const worked = ASSET_KINDS.includes(okind);
    const [status, latest, work, profile] = await Promise.all([
      worked ? readStatus(oid) : undefined,
      ["tag", "equipment", "well"].includes(okind) ? readLatest(oid) : undefined,
      worked ? readWork(oid, okind === "area") : undefined,
      ["tag", "equipment", "equipment_model"].includes(okind) ? readProfile(oid) : undefined,
    ]);
    return { status, latest, work, profile };
  }, [oid, okind, onOverview]);

  // No signal and not kept on this phone (P5.17c): say so, and go back to the jobs that are.
  if (!ent.data && ent.error && ent.error.startsWith("Not kept on this phone")) return <section>
    <PageHeader>Entity</PageHeader>
    <Notice tone="warn">This page isn't kept on this phone, and there's no signal. The assets, drawings and documents of
      your jobs are. <a href={href("/inbox")}>Back to My work</a></Notice>
  </section>;
  if (!ent.data) return <section><PageHeader>Entity</PageHeader>
    <Status of={ent} />
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
  const asked = tab === "graph" ? "record" : tab;
  const view: EntityTab = tabs.includes(asked as EntityTab) ? (asked as EntityTab) : "overview";
  const fid = e.fuzer_id;
  // Assets people work on open on their work and history; any other record opens on its values.
  const operated = ASSET_KINDS.includes(e.kind);
  const kindName = kindWord(e.kind, { template: e.golden.template?.value, doc_type: e.golden.doc_type?.value });
  const description = e.golden.description ? fmt(e.golden.description.value) : undefined;
  const kept = keptAt(e);

  return (
    <article className="entity">
      {kept && <Notice tone="warn">No signal: kept on this phone {when(kept)}. Parts not kept say so.</Notice>}
      <header className="entity-head">
        <div>
          <PageHeader record className="mono" detail={description}>{e.label ?? fid}</PageHeader>
          <Line>
            {/* The kind links to where it is defined, for those who can see the ontology. */}
            {configure
              ? <a className="small" href={href("/ontology", { kind: e.kind })} title="See this kind in the ontology">{kindName}</a>
              : <KindBadge kind={e.kind} label={kindName} />}
            <WatchButton target={fid} label={operated ? "asset" : kindName.toLowerCase()} />
            <a className="small" href={href("/ask", { subject: fid })}>Ask about this {operated ? "asset" : kindName.toLowerCase()}</a>
            {/* On a phone the date picker waits behind this, so the header leaves room for the page (#1076). */}
            {!asOf && !dated && <Button variant="link" size="sm" className="asof-open" onClick={() => setDated(true)}>Earlier date</Button>}
          </Line>
        </div>
        <label className={`asof${asOf || dated ? " asof-shown" : ""}`}>
          As of
          <Input type="date" value={asOf} onChange={(x) => setAsOf(x.target.value)} />
          {asOf && (
            <Button variant="link" onClick={() => setAsOf("")}>
              clear
            </Button>
          )}
        </label>
      </header>

      <Tabs className="page-tabs" label="Views of this entity" items={tabs.map((t) => ({
        key: t, label: TAB_LABELS[t], current: t === view,
        href: href(`/e/${encodeURIComponent(id)}`, t === "overview" ? undefined : { tab: t }),
      }))} />


      {view === "overview" && (
        <>
          {/* Live picture first, then the work on it, then what it is (#1076). */}
          {overview.data === undefined && !overview.error && <Status loading />}
          {/* Everything else waits for the overview too: drawn first, the cards below sat at the top and were pushed
              down when it answered (#1069). */}
          {(overview.data !== undefined || overview.error) && <>
            {operated && <EntityStatus id={fid} data={overview.data?.status} />}
            {["tag", "equipment", "well"].includes(e.kind) && <LiveReadings id={fid} data={overview.data?.latest} />}
            {operated && <WorkHere id={fid} area={e.kind === "area"} data={overview.data?.work} />}
            {["tag", "equipment", "equipment_model"].includes(e.kind) && <AssetProfile id={fid} data={overview.data?.profile} />}
          {e.kind === "incident" && <IncidentDetail id={fid} />}
          {e.kind === "document" && e.records.some((r) => r.source === "pid") && <DrawingViewer id={fid} />}
          {e.kind === "document" && e.golden.doc_type && (
            <UICard as="section" title={fmt(e.golden.title?.value)}
              detail={<>{fmt(e.golden.doc_type.value).replace("_", " ")} · rev {fmt(e.golden.revision?.value)} ·{" "} {fmt(e.golden.status?.value)}</>}
              actions={<ButtonLink href={documentHref(fid)} onClick={(ev) => { ev.preventDefault(); openDocument(fid); }}>
                  Read full screen</ButtonLink>}>
              {e.golden.status?.value === "superseded" && (
                <ErrorText>This revision is superseded; see "Superseded by" under Data.</ErrorText>
              )}
              <DocumentPages id={fid} className="doc-preview" />
            </UICard>
          )}
          {e.kind === "document_section" && (
            <UICard as="section" title={fmt(e.golden.heading?.value)} detail={e.golden.page && <>page {fmt(e.golden.page.value)}</>}>
              <p className="section-text">{fmt(e.golden.text?.value)}</p>
            </UICard>
          )}
          {e.kind === "timeseries" && <TrendView key={fid} id={fid} />}
          {MAINTAINED.includes(e.kind) && (
            <><IncidentsCard id={fid} />
            <Related about={{ subject: fid }} incidents={false} /></>
          )}
          {["well", "facility"].includes(e.kind) && <DowntimeCard id={fid} />}
          {["tag", "equipment", "well"].includes(e.kind) && <ConversationsCard id={fid} />}
          {e.kind !== "event" && <Events id={fid} />}
          {/* A machine is placed on its P&ID, ringed; a map zoomed out to the export line said little (#1076). */}
          {["tag", "equipment"].includes(e.kind)
            ? <LinkedDrawings id={fid} fallback={<Suspense fallback={null}><EntityMap id={fid} /></Suspense>} />
            : <Suspense fallback={null}><EntityMap id={fid} /></Suspense>}
          {!operated && <div className="grid">
            <Fields e={e} />
            <Records e={e} />
          </div>}
          </>}
        </>
      )}

      {view === "maintenance" && (
        <>
          <MaintenanceCard id={fid} />
          {["tag", "equipment", "equipment_model"].includes(e.kind) && <SparesCard id={fid} />}
          <DowntimeCard id={fid} />
        </>
      )}

      {view === "trends" && (
        <>
          <TrendView key={fid} id={fid} linked />
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
          <KnowledgeCard id={fid} />
          <LinkedDrawings id={fid} />
        </>
      )}

      {view === "record" && (
        <>
          {operated && <div className="grid">
            <Fields e={e} />
            <Records e={e} />
          </div>}
          <LineageCard id={fid} />
          {["tag", "equipment", "well"].includes(e.kind) && <AssetContextCard id={fid} />}
          {!NO_TRENDS.includes(e.kind) && e.kind !== "timeseries" && <MemoryCard id={fid} />}
          <HistoryCard id={fid} />
          <Suspense fallback={<Status loading />}>
            <EntityGraph id={fid} asOf={asOf || undefined} />
            <Links edges={e.edges} counts={e.edge_counts} asOf={asOf} />
          </Suspense>
        </>
      )}
    </article>
  );
}

function LinkedDrawings({ id, fallback = null }: { id: string; fallback?: ReactNode }) {
  const d = useAsync(() => api.entityDrawings(id), [id]);
  const sheets = d.data?.drawings ?? [];
  if (d.loading) return null;
  if (sheets.length === 0) return <>{fallback}</>;
  return (
    <UICard as="section" title="On P&ID" detail={<>{sheets.length === 1 ? "one sheet" : `${sheets.length} sheets`}, ringed where this is</>}>
      <div className="sheet-thumbs">
        {sheets.map((x) => <SheetThumb key={x.id} id={x.id} label={x.label ?? x.id} title={x.title} highlight={d.data!.highlight} />)}
      </div>
    </UICard>
  );
}

/** Why a value says what it says (#225): the rule, and each record that states one with its mapping and load. */
function Why({ id, field }: { id: string; field: string }) {
  const w = useAsync(() => api.why(id, field), [id, field]);
  if (!w.data) return <Status of={w} />;
  return (
    <div className="small why">
      <Text as="div" muted>
        Chosen by {w.data.rule === "review" ? "a reviewer" : `priority ${(w.data.priority ?? []).join(" > ")}`}
        {w.data.review_on_conflict ? "; disagreements go to review" : ""}.
      </Text>
      {w.data.assertions.map((a, i) => (
        <div key={i}>
          <SourceBadge source={a.source} /> {fmt(a.value)} {a.wins && <UIBadge tone="ok">chosen</UIBadge>}{" "}
          <code className="muted">{a.record}</code>
          {a.mapping_version && <Text muted> · mapping v{a.mapping_version.id}</Text>}
          {a.run && <Text muted> · ingestion run {a.run.id} ({a.run.dataset}, {when(a.run.finished_at, "Time unknown")})</Text>}
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
    <UICard as="section" title="Values">
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
                  <Line>
                    <span>{fmt(g.value)}</span>
                    <SourceBadge source={g.source} />
                    {g.rule === "review" && <UIBadge tone="ok">reviewed</UIBadge>}
                    {g.rule !== "derived" && (
                      <Button variant="link" className="small" onClick={() => setWhy(why === f ? null : f)}
                        title="the records, mapping versions and loads behind this value">why?</Button>
                    )}
                  </Line>
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
                    all.length > 1 && <Text as="div" small muted>{all.length} sources agree</Text>
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
    <UICard as="section" title="Source records">
      <Text as="div" small mono>Register ID {e.fuzer_id}</Text>
      {!e.records.length && <p>No direct source records are linked. This may be a derived entity; inspect Values and Links for supporting provenance.</p>}
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
          <UIBadge key={`${k.kind}:${k.key}`} mono title={k.kind}>
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
    <UICard as="section" title="Links" detail={asOf && <>on {asOf}</>}>
      <Status empty={edges.length === 0}>No links{asOf ? " on this date" : ""}.</Status>
      {[...groups].map(([label, items]) => (
        <div key={label} className="link-group">
          <Heading level={3} detail={(() => {
            const total = counts[`${items[0].direction}:${items[0].rel}`] ?? items.length;
            return total > items.length ? `showing ${items.length} of ${count(total)}` : count(items.length);
          })()}>{label}</Heading>
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
                  <Text small muted>{x.other_description}</Text>
                )}
                <Dates from={x.valid_from} to={x.valid_to} />
                {typeof x.props?.text === "string" && <span className="quote">“{x.props.text}”</span>}
                {typeof x.props?.attribute === "string" && <Text small muted>{x.props.attribute}</Text>}
                <span className="sources">
                  {x.sources.map((s) => (
                    <SourceBadge key={s} source={s} />
                  ))}
                </span>
              </li>
            ))}
          </ul>
          {shown.length < items.length && (
            <Button variant="link" size="sm" onClick={() => setAll(true)}>Show all {count(items.length)}</Button>
          )}
    </>
  );
}

const TRACE_NEAR = 2;  // what feeds it and what it feeds, two steps each way; the whole network on request
const TRACE_ALL = 10;

const NETWORKS = [
  { rel: "pipes_to", cls: "piping", label: "pipes to", name: "Piping", from: "from drawings" },
  { rel: "flows_to", cls: "allocation", label: "flows to", name: "Allocation flow", from: "from the allocation network" },
] as const;

/** What comes into this asset (left) and where it goes (right), drawn left to right. Two networks are drawn
 * together because they answer different questions and can differ: the piping read from drawings (solid) and the
 * allocation flow from the production system (dashed). Either can be switched off. */
function Trace({ id, label, kind, asOf }: { id: string; label: string; kind: string; asOf?: string }) {
  const [all, setAll] = useState(false);
  const [off, setOff] = useState<string[]>([]);
  const depth = all ? TRACE_ALL : TRACE_NEAR;
  const flow = useAsync(() => Promise.all(NETWORKS.flatMap((n) =>
    [api.trace(id, "up", asOf, depth, n.rel), api.trace(id, "down", asOf, depth, n.rel)])), [id, asOf, depth]);
  const has = (i: number) => !!flow.data && flow.data[2 * i].length + flow.data[2 * i + 1].length > 0;
  const elements = useMemo(() => {
    if (!flow.data) return [];
    const nodes = new Map([[id, { id, label, kind }]]);
    const edges = new Map<string, { src: string; dst: string; net: typeof NETWORKS[number] }>();
    NETWORKS.forEach((net, i) => {
      if (off.includes(net.rel)) return;
      for (const [rows, upstream] of [[flow.data![2 * i], true], [flow.data![2 * i + 1], false]] as const) {
        for (const r of rows) {
          nodes.set(r.fuzer_id, { id: r.fuzer_id, label: nodeLabel(r.label ?? r.fuzer_id, r.description), kind: r.kind });
          const [src, dst] = upstream ? [r.fuzer_id, r.parent ?? id] : [r.parent ?? id, r.fuzer_id];
          edges.set(`${net.rel}:${src}>${dst}`, { src, dst, net });
        }
      }
    });
    // The same element shape graph.tsx draws (subgraphElements), built here so the canvas stays in its own chunk.
    return [
      ...[...nodes.values()].map((n) => ({ data: { ...n, color: kindColor(n.kind), size: n.id === id ? 34 : 22,
        root: n.id === id } })),
      ...[...edges.values()].map((x, i) => ({ classes: x.net.cls, data: { id: `e${i}`, source: x.src, target: x.dst,
        // The key above names each network and the arrows give the direction; a label on every link only showed on
        // the ones Cytoscape had room for, two of four (#1076).
        rel: x.net.rel, color: relColor(x.net.rel), ended: false, label: "" } })),
    ];
  }, [flow.data, id, label, kind, off]);
  const none = !!flow.data && !has(0) && !has(1);
  const both = has(0) && has(1);
  return (
    <UICard as="section" title="Flow" detail={asOf && <>on {asOf}</>}
      actions={<Line as="span">
          <Button variant="link" onClick={() => setAll(!all)}>{all ? "Show nearest only" : "Show whole network"}</Button>
          <ButtonLink variant="link" href={href("/3d", { at: id })}>See its area in 3D</ButtonLink>
        </Line>}>
      <Status of={flow} empty={none}>
        Nothing flows in or out{asOf ? " on this date" : ""}.
      </Status>
      {flow.data && !none && <>
        <p className="row small flow-networks">
          {NETWORKS.map((n, i) => has(i) && (both
            ? <label key={n.rel} className={`flow-network ${n.cls}`}>
              <input type="checkbox" checked={!off.includes(n.rel)}
                onChange={() => setOff(off.includes(n.rel) ? off.filter((r) => r !== n.rel) : [...off, n.rel])} />
              {n.name} <Text muted>{n.from}</Text></label>
            : <span key={n.rel} className={`flow-network ${n.cls}`}>{n.name} <Text muted>{n.from}</Text></span>))}
        </p>
        <Suspense fallback={<Status loading />}>
          {/* A short chain of neighbours takes a short canvas, not a tall empty one (#1076). */}
          <GraphCanvas elements={elements} layout="flow" selectedId={id}
            height={all ? 600 : elements.filter((x) => !("source" in x.data)).length > 8 ? 320 : 200}
            onSelect={(to) => { if (to && to !== id) location.hash = entityHref(to); }} />
        </Suspense>
      </>}
    </UICard>
  );
}

function Events({ id }: { id: string }) {
  const evs = useAsync(() => api.events(id), [id]);
  if (!evs.data?.length) return null;
  return (
    <UICard as="section" title="Events" detail={<>{evs.data.length} from operator logs, read by Claude</>}>
      <ul className="timeline">
        {evs.data.map((ev) => (
          <li key={ev.id}>
            <span className="when">{whenDay(ev.timestamp, "—")}</span>
            <div className="what">
              <Line>
                <UIBadge tone="danger">{(ev.type ?? "event").replace("_", " ")}</UIBadge>
                <a href={entityHref(ev.id)}>{ev.summary}</a>
              </Line>
              {ev.involves.length > 0 && (
                <Line small>
                  {ev.involves.map((x) => (
                    <span key={`${x.id}-${x.role}`}>
                      <a href={entityHref(x.id)} className="mono">
                        {x.label ?? x.id}
                      </a>
                      {x.role && x.role !== "subject" && <Text muted> ({x.role})</Text>}
                    </span>
                  ))}
                </Line>
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
    <UICard as="section" title="Maintenance" detail={<>{count(m.data.total)} SAP notifications</>}>
      <div className="maintenance-split">
        <div>
          <Heading level={3}>Failure modes (ISO 14224)</Heading>
          {/* Name, a bar to compare and the count, in columns: the count read as a superscript stuck to the word (#1076). */}
          <ul className="plain failure-modes">
            {m.data.failure_modes.map((f) => (
              <li key={f.code ?? "none"}>
                <span>{f.name ?? "Not coded"}</span>
                <Bar className="bar" value={(100 * f.count) / top} />
                <Text small muted>{f.count}</Text>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <Heading level={3}>Recent</Heading>
          <ul className="timeline">
            {m.data.recent.slice(0, 8).map((n) => (
              <li key={n.id}>
                <span className="when">{n.created}</span>
                <div className="what">
                  {/* One line that wraps as text: a flex row put a long title under its code, alone (#1076). */}
                  <div>
                    {n.failure_mode && <><UIBadge tone="danger" title={`ISO 14224 ${n.failure_mode}`}>
                      {n.failure_mode_name ?? n.failure_mode}</UIBadge>{" "}</>}
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
    <UICard as="section" title="Downtime" detail={<>{count(Math.round(d.data.lost_oil_bbl))} bbl oil lost over {d.data.events} events</>}>
      <ul className="timeline">
        {d.data.recent.slice(0, 8).map((r) => (
          <li key={r.id}>
            <span className="when">{whenDay(r.start)}</span>
            <div className="what">
              <Line>
                <UIBadge tone="danger">{r.reason ?? "downtime"}</UIBadge>
                <a href={entityHref(r.id)}>{r.comment}</a>
              </Line>
              <Text as="div" small muted>
                {r.duration_h} h · {count(Math.round(r.lost_oil_bbl ?? 0))} bbl
                {r.reason_check && <ErrorText inline> · {r.reason_check}</ErrorText>}
              </Text>
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
    <UICard as="section" title="Incidents" detail={count(d.data.total)}>
      <IncidentList items={d.data.items.slice(0, 8)} />
    </UICard>
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
      <List plain>{list.map((c) => <li key={c.id} className="conversation">
        <p><strong>{c.subject ?? c.problem ?? "Thread"}</strong>{" "}
          <UIBadge tone={c.state === "open" ? "warn" : "ok"}>{c.state === "open" ? "still open" : "fixed"}</UIBadge></p>
        <Text as="p" small>{[c.channel, c.started_at ? when(c.started_at, "Time unknown") : null, c.participants.join(", ")].filter(Boolean).join(" · ")}</Text>
        {c.problem && <Text as="p" small>Problem: {c.problem}</Text>}
        {c.tried.length > 0 && <Text as="p" small>Tried: {c.tried.join(" · ")}</Text>}
        {c.fix && <Text as="p" small>Fix: {c.fix}</Text>}
        {c.web_url && <a className="small" href={c.web_url} target="_blank" rel="noopener noreferrer">Open in Teams</a>}
      </li>)}</List>
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
    <UICard as="section" title="Documents">
      <List plain>
        {docs.data.map((d) => (
          <li key={d.id} className="small">
            <a href={documentHref(d.id)} onClick={(ev) => { ev.preventDefault(); openDocument(d.id); }}>{d.title ?? d.label}</a>{" "}
            <Text muted>
              {d.doc_type?.replace("_", " ")} · rev {d.revision} · via {d.why} · <a href={entityHref(d.id)}>details</a>
            </Text>
          </li>
        ))}
      </List>
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
                <Text small><Highlighted text={r.snippet} /></Text>
                {r.why && <Text small muted>{r.why}</Text>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </UICard>
  );
}

const RULE_SAYS: Record<string, string> = { priority: "by system priority", review: "by a reviewer",
  derived: "worked out from other values" };

/** Where this came from (#875): the systems behind the record and how each record was matched, the rule behind its
 * values, reviewer choices and fixes, merges and splits, and the work and write-backs that touched it. */
function LineageCard({ id }: { id: string }) {
  const l = useAsync(() => api.lineage(id), [id]);
  if (!l.data?.systems) return <Status of={l} />;
  const d = l.data;
  const rules = Object.entries(d.rules).map(([r, n]) => `${n} ${RULE_SAYS[r] ?? r}`).join(", ");
  return (
    <UICard as="section" title="Where this came from">
      <Heading level={3}>Systems</Heading>
      {!d.systems.length && <p>No source system's record is matched to this one.</p>}
      <Table headingCase="verbatim">
        <thead><tr><th>System</th><th>Records, and how each was matched</th><th>Values it supplies</th></tr></thead>
        <tbody>
          {d.systems.map((s) => (
            <tr key={s.source}>
              <td><SourceBadge source={s.source} /></td>
              <td className="small">
                {s.records.map((r) => (
                  <div key={r.record}><code>{sourceKey(s.source, r.key)}</code> <Text muted>
                    {r.record_type.replace(/_/g, " ")} · {r.matched_by}{r.confidence < 1 ? ` (${r.confidence})` : ""}</Text></div>
                ))}
                {!s.records.length && <Text muted>none matched</Text>}
              </td>
              <td className="small">{s.chosen_fields.map(fieldLabel).join(", ") || <Text muted>none chosen</Text>}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      {rules && <Text as="p" small>Values chosen {rules}. {d.note}</Text>}
      {(d.reviewed.length > 0 || d.fixes.length > 0) && <>
        <Heading level={3}>Reviewer choices</Heading>
        <List small>
          {d.reviewed.map((r) => (
            <li key={`r-${r.field}`}><b>{fieldLabel(r.field)}</b>: {fmt(r.value)}{" "}
              <Text muted>chosen by {r.by ?? "a reviewer"}{r.at ? `, ${when(r.at, "Time unknown")}` : ""}</Text></li>
          ))}
          {d.fixes.map((f, i) => (
            <li key={`f-${i}`}><b>{fieldLabel(f.field)}</b>: {fmt(f.old_value)} → {fmt(f.new_value)}{" "}
              <Text muted>fixed by {personName(f.by)}, {when(f.at, "Time unknown")}{f.reason ? `: ${f.reason}` : ""}</Text></li>
          ))}
        </List>
      </>}
      {(d.identity.length > 0 || d.pins.length > 0) && <>
        <Heading level={3}>Merges and splits</Heading>
        <List small>
          {d.identity.map((x, i) => (
            <li key={`i-${i}`}>
              {x.event === "merge" ? "Merged in " : "Split off from "}
              <a href={entityHref(x.other)}>{x.other_label ?? x.other}</a>{" "}
              <Text muted>{when(x.at, "Time unknown")}</Text>
            </li>
          ))}
          {d.pins.map((p, i) => (
            <li key={`p-${i}`}>
              <code>{p.left_ref}</code> {p.rule === "must" ? "is the same entity as" : "is not the same entity as"}{" "}
              <code>{p.right_ref}</code>{" "}
              <Text muted>by {p.by ?? "a steward"}, {when(p.at, "Time unknown")}{p.reason ? `: ${p.reason}` : ""}</Text>
            </li>
          ))}
        </List>
      </>}
      <Heading level={3}>Work and write-backs</Heading>
      {!d.work.count && <Text as="p" small>No work has been about this entity.</Text>}
      {d.work.count > 0 && (
        <List small>
          {d.work.recent.map((w) => (
            <li key={w.id}><a href={caseHref(w.id)}>{w.title}</a> <Text muted>{w.state} · {when(w.created_at, "Time unknown")}</Text></li>
          ))}
          {d.work.count > d.work.recent.length && (
            <li className="muted">and {d.work.count - d.work.recent.length} earlier</li>
          )}
        </List>
      )}
      {d.writebacks.length > 0 && (
        <List small>
          {d.writebacks.map((w) => (
            <li key={w.id}>
              <SourceBadge source={w.system} /> {w.kind.replace(/_/g, " ")} {w.record ? <code>{w.record}</code> : null}{" "}
              <Text muted>{w.state}{w.approved_by ? `, approved by ${w.approved_by}` : ""}
                {w.confirmed_at ? `, confirmed ${when(w.confirmed_at, "Time unknown")}` : ""}</Text>{" "}
              <a href={caseHref(w.item_id)}>case</a>
            </li>
          ))}
        </List>
      )}
    </UICard>
  );
}

function HistoryCard({ id }: { id: string }) {
  const h = useAsync(() => api.history(id), [id]);
  if (!h.data?.length) return null;
  return (
    <UICard as="section" title="Change history">
      <ul className="timeline">
        {h.data.map((c, i) => (
          <li key={i}>
            <span className="when">{when(c.at)}</span>
            <div className="what">
              <Text small>
                <UIBadge mono>{c.record.split(":")[0]}</UIBadge> {c.record.split(":").slice(2).join(":")}{" "}
                <Text muted>{c.change}</Text>
              </Text>
              {Object.entries(c.fields).map(([f, v]) => (
                <span key={f} className="small">
                  <b>{f}</b>: <Text muted>{fmt(v.from)}</Text> → {fmt(v.to)}
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
    <UICard as="section" title="Spare parts">
      <Table variant="list">
        <tbody>
          {parts.data.map((p) => (
            <tr key={p.fuzer_id}>
              <td><a href={entityHref(p.fuzer_id)} className="mono">{p.label}</a></td>
              <td>{p.description}<Text as="div" small muted mono>{p.mpn}</Text></td>
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
    <UICard as="section" title="Trajectory and completion">
      <Suspense fallback={null}><LiveFigure kind="well-trajectory" params={{ well: id }} /></Suspense>
    </UICard>
  );
}

const mi = (ft: unknown) => (typeof ft === "number" ? (ft / 5280).toFixed(3) : "");

/** A pipeline's segments and what in-line inspection found on it, by station (#61). */
function AlongCard({ id }: { id: string }) {
  const a = useAsync(() => api.along(id), [id]);
  if (!a.data?.segments?.length && !a.data?.features?.length) return null;
  const { segments = [], features = [] } = a.data ?? {};
  const seg = Object.fromEntries(segments.map((s) => [s.id, s.label]));
  return (
    <UICard as="section" title="Along the line">
      {segments.length > 0 && (
        <Table variant="list">
          <thead><tr><th>Segment</th><th>From (MP, mi)</th><th>To (MP, mi)</th><th>Wall (in)</th><th>Class location</th></tr></thead>
          <tbody>
            {segments.map((s) => (
              <tr key={s.id}>
                <td><a href={entityHref(s.id)} className="mono">{s.label}</a><Text as="div" small muted>{fmt(s.description)}</Text></td>
                <td className="small mono">{mi(s.station_from_ft)}</td>
                <td className="small mono">{mi(s.station_to_ft)}</td>
                <td className="small">{fmt(s.wall_thickness_in)}</td>
                <td className="small">{fmt(s.class_location)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {features.length > 0 && (
        <>
          <Heading level={3}>Found by in-line inspection</Heading>
          <Table variant="list">
            <thead><tr><th>Feature</th><th>Station (MP, mi)</th><th>Clock</th><th>Segment</th><th>Depth (% of wall)</th><th>Length (in)</th><th>Run</th></tr></thead>
            <tbody>
              {features.map((f) => (
                <tr key={f.id}>
                  <td><a href={entityHref(f.id)} className="mono">{f.label}</a><Text as="div" small>{fmt(f.feature_type)}</Text></td>
                  <td className="small mono">{mi(f.station_ft)}</td>
                  <td className="small mono">{fmt(f.clock)}</td>
                  <td className="small mono">{f.segment ? seg[f.segment] ?? "" : <Text muted>no segment holds it</Text>}</td>
                  <td className="small">{fmt(f.depth_pct)}</td>
                  <td className="small">{fmt(f.length_in)}</td>
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
