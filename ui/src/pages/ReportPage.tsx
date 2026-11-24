// Report pages (docs/reports.md): a definition of rows of panels, drawn for a scope from the register and a named
// window, with both in the link so a report opens on what the sender saw. Rows wrap: four columns on a desk, two on
// an iPad, one on a phone (stats two across), in the definition's order. The gallery lists every report: the
// standard ones, then the person's own and those shared with them. Any report can be copied to "my reports"; a
// person's own report is edited in place on iPad and desktop (reports/editor.tsx). Phones read.
import "../styles/report-pages.css";
import { Fragment, createContext, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { api } from "../api";
import { Status, href, useAction, useAsync, usePhone, useRouteState } from "../lib";
import { AddPanelSlot, CustomLine, DefinitionView, ShareSelect, SlotTools, VersionHistory } from "../reports/editor";
import { PANELS } from "../reports/panels";
import { DailyReview } from "../reports/panels/NarrativePanel";
import type {
  Panel, PanelColumn, ReportDefinition, ReportListing, ReportRow, ReportView, ScopeRef, WindowKey,
} from "../reports/spec";
import {
  Button, Card, Empty, ErrorText, Field, Heading, Input, PageHeader, Select, Text, confirmDialog, promptDialog,
} from "../ui";
import { kindWord, person, say } from "../wording";

const WINDOW_WORDS: Record<WindowKey, string> = {
  "1d": "Day", "7d": "7 days", "30d": "30 days", "90d": "90 days", qtd: "Quarter to date", ytd: "Year to date",
};

function ReportCard({ r }: { r: ReportListing }) {
  const shared = r.source !== "custom" ? null
    : !r.mine ? person(r.owner)
      : r.shared === "everyone" ? "Shared with everyone" : r.shared ? `Shared with ${say(r.shared)}` : "Only you";
  return <Card as="section" className="report-card" title={<a href={href(`/reports/${r.key}`)}>{r.title}</a>}>
    <Text small muted>{[shared, r.for.join(", "), WINDOW_WORDS[r.window.default]].filter(Boolean).join(" · ")}</Text>
  </Card>;
}

export function ReportsGallery() {
  const list = useAsync(() => api.reports(), []);
  const phone = usePhone();
  const a = useAction();
  const all = list.data ?? [];
  const standard = all.filter((r) => r.source !== "custom");
  const mine = all.filter((r) => r.source === "custom" && r.mine);
  const shared = all.filter((r) => r.source === "custom" && !r.mine);
  const start = async () => {
    const title = (await promptDialog("Name the new report", { confirm: "Start" }))?.trim();
    if (title) a.run("blank", () => api.blankReport(title), (r) => { location.hash = href(`/reports/${r.key}`, { edit: "1" }); });
  };
  return <section>
    <PageHeader>Reports</PageHeader>
    <Status of={list} empty={list.data?.length === 0}>No reports.</Status>
    <div className="report-gallery">
      {standard.map((r) => <ReportCard key={r.key} r={r} />)}
    </div>
    {list.data && (mine.length > 0 || !phone) && <>
      <Heading actions={!phone && <Button size="sm" disabled={!!a.busy} onClick={start}>Start blank</Button>}>My reports</Heading>
      <ErrorText>{a.error}</ErrorText>
      {mine.length ? <div className="report-gallery">{mine.map((r) => <ReportCard key={r.key} r={r} />)}</div>
        : <Empty>Copy any report to change it, or start blank.</Empty>}
    </>}
    {shared.length > 0 && <>
      <Heading>Shared with me</Heading>
      <div className="report-gallery">{shared.map((r) => <ReportCard key={r.key} r={r} />)}</div>
    </>}
  </section>;
}

/** What a panel reads apart from how big it is: a panel whose reading changed since the save is read as a draft. */
const reading = (p: Panel) => JSON.stringify({ ...p, size: undefined });

interface Editing {
  definition: ReportDefinition;
  saved: Map<string, string>;
  adding: number | null;
  onAdding: (index: number | null) => void;
  onChange: (d: ReportDefinition) => void;
}

export function ReportPage({ reportKey }: { reportKey: string }) {
  const [scope, setScope] = useRouteState("scope");
  const [window_, setWindow] = useRouteState("window");
  const [at, setAt] = useRouteState("at");
  const [edit, setEdit] = useRouteState("edit");
  const view = useAsync(() => api.report(reportKey, { scope, window: window_ as WindowKey, at }), [reportKey, scope, window_, at]);
  const phone = usePhone();
  const a = useAction();
  const [draft, setDraft] = useState<ReportDefinition | null>(null);
  const [adding, setAdding] = useState<number | null>(null);
  const [pane, setPane] = useState<"" | "definition" | "versions">("");
  const v = view.data;
  const custom = v?.custom ?? null;
  const editing = !!custom?.mine && !phone && edit === "1" && !!v;
  useEffect(() => {
    if (editing && v && !draft) setDraft(v.definition);
    if (!editing && draft) { setDraft(null); setAdding(null); setPane(""); }
  }, [editing, v, draft]);
  const d = editing && draft ? draft : v?.definition;
  const dirty = editing && !!draft && !!v && JSON.stringify(draft) !== JSON.stringify(v.definition);

  const copy = () => a.run("copy", () => api.copyReport(reportKey),
    (r) => { location.hash = href(`/reports/${r.key}`, phone ? undefined : { edit: "1" }); });
  const save = () => draft && v && a.run("save", () => api.saveReport(reportKey, { definition: draft, base: v.definition.version }),
    () => { setEdit(""); setDraft(null); view.reload(); });
  const cancel = async () => {
    if (dirty && !(await confirmDialog("Leave without saving? Your changes are dropped.", { confirm: "Leave", danger: true }))) return;
    setEdit(""); setDraft(null);
  };
  const drop = async () => {
    if (!(await confirmDialog(`Take down ${v?.definition.title}? It is kept, marked removed, and no one can open it.`, { confirm: "Take down", danger: true }))) return;
    a.run("remove", () => api.removeReport(reportKey), () => { location.hash = href("/reports"); });
  };

  const [all, setAll] = useState(false);
  const printAll = () => { setAll(true); setTimeout(() => print(), 2500); };  // every row read before the page prints
  const actions = editing ? null : <>
    {custom?.mine && !phone && <Button onClick={() => setEdit("1")}>Edit</Button>}
    {v && !phone && <Button disabled={!!a.busy} onClick={copy}>Copy to my reports</Button>}
    <Button className="report-print" onClick={printAll}>Print</Button>
  </>;
  const shown: ReportView | undefined = v && d ? { ...v, definition: d } : undefined;
  const editor: Editing | null = editing && draft && v ? {
    definition: draft, adding, onAdding: setAdding, onChange: setDraft,
    saved: new Map([...panelsOf(v.definition)].map((p) => [p.id, reading(p)])),
  } : null;

  return <section className={`report-page${editing ? " report-editing" : ""}`}>
    <PageHeader record actions={actions}>{v?.definition.title ?? "Report"}</PageHeader>
    <Status of={view} />
    {custom && !editing && <CustomLine report={custom} />}
    {!editing && <ErrorText>{a.error}</ErrorText>}
    {editor && custom && <div className="report-editbar" role="group" aria-label="Edit report">
      <Field label="Title" inline>
        <Input value={editor.definition.title} aria-label="Title" onChange={(e) => setDraft({ ...editor.definition, title: e.target.value })} />
      </Field>
      <Button variant="primary" disabled={!dirty || !!a.busy || !editor.definition.title.trim()} onClick={save}>Save</Button>
      <Button disabled={!!a.busy} onClick={cancel}>{dirty ? "Cancel" : "Done"}</Button>
      <Button variant="ghost" aria-pressed={pane === "definition"} onClick={() => setPane(pane === "definition" ? "" : "definition")}>Definition</Button>
      <Button variant="ghost" aria-pressed={pane === "versions"} onClick={() => setPane(pane === "versions" ? "" : "versions")}>Versions</Button>
      <ShareSelect report={custom} onShared={() => view.reload()} />
      <Button variant="danger" disabled={!!a.busy} onClick={drop}>Take down</Button>
      <ErrorText>{a.error}</ErrorText>
    </div>}
    {editor && pane === "versions" && <VersionHistory reportKey={reportKey} onRestored={() => { setEdit(""); setDraft(null); view.reload(); }} />}
    {editor && pane === "definition" && <DefinitionView definition={editor.definition} onApply={setDraft} onClose={() => setPane("")} />}
    {shown && pane !== "definition" && <Everything.Provider value={all || !!editor}>
      <div className="report-controls" role="group" aria-label="Scope and window">
        <Field label="Scope" inline>
          <Select value={shown.scope.id ?? ""} onChange={(e) => setScope(e.target.value)} aria-label="Scope">
            {shown.scopes.map((s) => <option key={s.id} value={s.id ?? ""}>{s.label} ({kindWord(s.kind ?? "")})</option>)}
          </Select>
        </Field>
        <Field label="Window" inline>
          <Select value={shown.window.key} onChange={(e) => setWindow(e.target.value)} aria-label="Window">
            {(v?.definition ?? shown.definition).window.options.map((w) => <option key={w} value={w}>{WINDOW_WORDS[w]}</option>)}
          </Select>
        </Field>
        <Field label="To" inline>
          <Input type="date" value={shown.window.at} max={new Date().toISOString().split("T")[0]}
            onChange={(e) => setAt(e.target.value)} aria-label="Window ends" />
        </Field>
      </div>
      <div className="report-print-only">{shown.scope.label} · {shown.window.label}</div>
      {/* The daily report's review goes in the header of the report that draws its narrative. */}
      {!editor && hasNarrative(shown) && <DailyReview day={shown.window.at} onDay={setAt} />}
      {editor && <AddPanelSlot definition={editor.definition} index={0} open={editor.adding === 0} onOpen={editor.onAdding} onChange={editor.onChange} />}
      {!editor && shown.definition.rows.length === 0 && <Empty>No panels yet.</Empty>}
      {shown.definition.rows.map((row, ri) => <Fragment key={row.id}>
        {row.repeat
          ? (editor ? (shown.repeats[row.id] ?? [shown.scope]).slice(0, 1) : shown.repeats[row.id] ?? [])
            .map((e) => <Row key={`${row.id}:${e.id}`} row={row} index={ri} view={shown} scope={e} editor={editor} />)
          : <Row row={row} index={ri} view={shown} scope={shown.scope} editor={editor} />}
        {editor && <AddPanelSlot definition={editor.definition} index={ri + 1} open={editor.adding === ri + 1}
          onOpen={editor.onAdding} onChange={editor.onChange} />}
      </Fragment>)}
    </Everything.Provider>}
  </section>;
}

const hasNarrative = (v: ReportView) =>
  v.definition.rows.some((r) => r.panels.some((p) => p.type === "narrative" || (p.type === "column" && p.panels.some((q) => q.type === "narrative"))));

function panelsOf(d: ReportDefinition): Panel[] {
  return d.rows.flatMap((r) => r.panels.flatMap((p) => (p.type === "column" ? p.panels : [p])));
}

/** True once printing or editing asks for every row, whether or not it has been scrolled to. */
const Everything = createContext(false);

/** A row is read when it comes within a screen of the view, so a long report (a row per well) costs only what is
 * looked at, and the browser's bounded request queue never overflows. */
function useNear(): [RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement | null>(null);
  const all = useContext(Everything);
  const [near, setNear] = useState(typeof IntersectionObserver === "undefined");
  useEffect(() => {
    if (near || !ref.current) return;
    const seen = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) setNear(true); },
      { rootMargin: "50% 0px" });
    seen.observe(ref.current);
    return () => seen.disconnect();
  }, [near]);
  return [ref, near || all];
}

function Row({ row, index, view, scope, editor }: {
  row: ReportRow; index: number; view: ReportView; scope: ScopeRef; editor: Editing | null;
}) {
  const repeated = !!row.repeat;
  const [ref, near] = useNear();
  return <div className="report-row-block" ref={ref}>
    {(row.title || repeated) && <Heading small>{[row.title, repeated && scope.label].filter(Boolean).join(" · ")}</Heading>}
    {!near ? <div className="report-row-waiting" aria-hidden="true" /> : <div className="report-row">
      {row.panels.map((p, i) => {
        const tools = editor && <SlotTools definition={editor.definition} place={{ row: index, item: i }} item={p} onChange={editor.onChange} />;
        return p.type === "column"
          ? <Column key={i} column={p} view={view} scope={scope} tools={tools} editor={editor} />
          : <Slot key={p.id} panel={p} view={view} scope={scope} tools={tools} editor={editor} />;
      })}
    </div>}
  </div>;
}

function Column({ column, view, scope, tools, editor }: {
  column: PanelColumn; view: ReportView; scope: ScopeRef; tools: ReactNode; editor: Editing | null;
}) {
  return <div className={`report-slot report-size-${column.size} report-column`}>
    {tools}
    {column.panels.map((p) => <Slot key={p.id} panel={p} view={view} scope={scope} inColumn editor={editor} />)}
  </div>;
}

function Slot({ panel, view, scope, inColumn, tools, editor }: {
  panel: Panel; view: ReportView; scope: ScopeRef; inColumn?: boolean; tools?: ReactNode; editor: Editing | null;
}) {
  const Component = PANELS[panel.type];
  const className = ["report-slot", !inColumn && `report-size-${panel.size}`, `report-type-${panel.type}`,
    panel.phone === false && "report-no-phone"].filter(Boolean).join(" ");
  const draft = !!editor && editor.saved.get(panel.id) !== reading(panel);
  return <div className={className}>
    {tools}
    <Component report={view} panel={panel} scope={scope.id} window={view.window.key} draft={draft}
      at={view.window.asked ? view.window.at : null} />
  </div>;
}
