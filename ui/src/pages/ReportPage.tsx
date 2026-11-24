// Report pages (docs/reports.md): a definition of rows of panels, drawn for a scope from the register and a named
// window, with both in the link so a report opens on what the sender saw. Rows wrap: four columns on a desk, two on
// an iPad, one on a phone (stats two across), in the definition's order. The gallery lists every report.
import "../styles/report-pages.css";
import { api } from "../api";
import { Status, href, useAsync, useRouteState } from "../lib";
import { PANELS } from "../reports/panels";
import type { Panel, PanelColumn, ReportRow, ReportView, ScopeRef, WindowKey } from "../reports/spec";
import { Button, Card, Field, Heading, Input, PageHeader, Select, Text } from "../ui";
import { kindWord } from "../wording";

const WINDOW_WORDS: Record<WindowKey, string> = {
  "1d": "Day", "7d": "7 days", "30d": "30 days", "90d": "90 days", qtd: "Quarter to date", ytd: "Year to date",
};

export function ReportsGallery() {
  const list = useAsync(() => api.reports(), []);
  return <section>
    <PageHeader>Reports</PageHeader>
    <Status of={list} empty={list.data?.length === 0}>No reports.</Status>
    <div className="report-gallery">
      {list.data?.map((r) => <Card key={r.key} as="section" className="report-card" title={<a href={href(`/reports/${r.key}`)}>{r.title}</a>}>
        <Text small muted>{[r.for.join(", "), WINDOW_WORDS[r.window.default]].filter(Boolean).join(" · ")}</Text>
      </Card>)}
      <Card as="section" className="report-card" title={<a href={href("/reports", { day: "" })}>Daily reports</a>}>
        <Text small muted>Each production day's narrative and its review</Text>
      </Card>
    </div>
  </section>;
}

export function ReportPage({ reportKey }: { reportKey: string }) {
  const [scope, setScope] = useRouteState("scope");
  const [window_, setWindow] = useRouteState("window");
  const [at, setAt] = useRouteState("at");
  const view = useAsync(() => api.report(reportKey, { scope, window: window_ as WindowKey, at }), [reportKey, scope, window_, at]);
  const v = view.data;
  return <section className="report-page">
    <PageHeader record actions={<Button className="report-print" onClick={() => print()}>Print</Button>}>
      {v?.definition.title ?? "Report"}
    </PageHeader>
    <Status of={view} />
    {v && <>
      <div className="report-controls" role="group" aria-label="Scope and window">
        <Field label="Scope" inline>
          <Select value={v.scope.id ?? ""} onChange={(e) => setScope(e.target.value)} aria-label="Scope">
            {v.scopes.map((s) => <option key={s.id} value={s.id ?? ""}>{s.label} ({kindWord(s.kind ?? "")})</option>)}
          </Select>
        </Field>
        <Field label="Window" inline>
          <Select value={v.window.key} onChange={(e) => setWindow(e.target.value)} aria-label="Window">
            {v.definition.window.options.map((w) => <option key={w} value={w}>{WINDOW_WORDS[w]}</option>)}
          </Select>
        </Field>
        <Field label="To" inline>
          <Input type="date" value={v.window.at} max={new Date().toISOString().split("T")[0]}
            onChange={(e) => setAt(e.target.value)} aria-label="Window ends" />
        </Field>
      </div>
      <div className="report-print-only">{v.scope.label} · {v.window.label}</div>
      {v.definition.rows.map((row) => row.repeat
        ? (v.repeats[row.id] ?? []).map((e) => <Row key={`${row.id}:${e.id}`} row={row} view={v} scope={e} />)
        : <Row key={row.id} row={row} view={v} scope={v.scope} />)}
    </>}
  </section>;
}

function Row({ row, view, scope }: { row: ReportRow; view: ReportView; scope: ScopeRef }) {
  const repeated = !!row.repeat;
  return <div className="report-row-block">
    {(row.title || repeated) && <Heading small>{[row.title, repeated && scope.label].filter(Boolean).join(" · ")}</Heading>}
    <div className="report-row">
      {row.panels.map((p, i) => p.type === "column"
        ? <Column key={i} column={p} view={view} scope={scope} />
        : <Slot key={p.id} panel={p} view={view} scope={scope} />)}
    </div>
  </div>;
}

function Column({ column, view, scope }: { column: PanelColumn; view: ReportView; scope: ScopeRef }) {
  return <div className={`report-slot report-size-${column.size} report-column`}>
    {column.panels.map((p) => <Slot key={p.id} panel={p} view={view} scope={scope} inColumn />)}
  </div>;
}

function Slot({ panel, view, scope, inColumn }: { panel: Panel; view: ReportView; scope: ScopeRef; inColumn?: boolean }) {
  const Component = PANELS[panel.type];
  const className = ["report-slot", !inColumn && `report-size-${panel.size}`, `report-type-${panel.type}`,
    panel.phone === false && "report-no-phone"].filter(Boolean).join(" ");
  return <div className={className}>
    <Component report={view} panel={panel} scope={scope.id} window={view.window.key} at={view.window.at} />
  </div>;
}
