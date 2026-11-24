// The custom report editor (docs/reports.md, "Custom reports"): edit in place on iPad and desktop. Each panel gets move
// up and down, its size and remove; "Add panel" between rows picks a type, then a measure, series or row set from the
// model (GET /model) for the report's scope kinds. The definition view is the same definition as YAML, checked by the
// server as a save would. Saving keeps a new version; history brings an earlier one back. Phones only read.
import { useState } from "react";
import { api } from "../api";
import { ago } from "../format";
import { Status, useAction, useAsync } from "../lib";
import { Button, Card, Checkbox, ErrorText, Field, Input, List, Line, Select, Text, Textarea, confirmDialog } from "../ui";
import { kindWord, person, say } from "../wording";
import { addRow, canMove, move, newPanelId, remove, resize, type Place } from "./edit";
import type {
  CasesFilter, CustomReport, FigurePanel, Panel, PanelColumn, PanelSize, PanelType, ReportDefinition, ReportModel,
} from "./spec";

export const TYPE_WORDS: Record<PanelType, string> = {
  stat: "Number", trend: "Trend", figure: "Breakdown", table: "Table", cases: "Cases", note: "Note", summary: "AI note",
  narrative: "Daily report", view: "Analysis view",
};
const SIZE_WORDS: Record<PanelSize, string> = { 1: "Quarter", 2: "Half", 3: "Three quarters", 4: "Full width" };
const DEFAULT_SIZE: Partial<Record<PanelType, PanelSize>> = { stat: 1, table: 4, cases: 4 };
const CHART_WORDS: Record<FigurePanel["chart"], string> = { bar: "Bars", pareto: "Pareto", share: "Share" };

/** Move, size and remove one panel (or a column of panels) on the page being edited. */
export function SlotTools({ definition, place, item, onChange }: {
  definition: ReportDefinition; place: Place; item: Panel | PanelColumn; onChange: (d: ReportDefinition) => void;
}) {
  const name = item.type === "column" ? "column" : item.title || item.id;
  return <div className="report-slot-tools" role="group" aria-label={`Arrange ${name}`}>
    <Button size="sm" variant="ghost" aria-label={`Move ${name} up`} disabled={!canMove(definition, place, -1)}
      onClick={() => onChange(move(definition, place, -1))}>↑</Button>
    <Button size="sm" variant="ghost" aria-label={`Move ${name} down`} disabled={!canMove(definition, place, 1)}
      onClick={() => onChange(move(definition, place, 1))}>↓</Button>
    <Select aria-label={`Size of ${name}`} value={item.size}
      onChange={(e) => onChange(resize(definition, place, Number(e.target.value) as PanelSize))}>
      {([1, 2, 3, 4] as PanelSize[]).map((s) => <option key={s} value={s}>{SIZE_WORDS[s]}</option>)}
    </Select>
    <Button size="sm" variant="ghost" aria-label={`Remove ${name}`} onClick={() => onChange(remove(definition, place))}>Remove</Button>
  </div>;
}

type Measure = ReportModel["measures"][number];

/** A measure picker: the ones that answer for the report's scope first, then those for the whole register only. */
function MeasureSelect({ model, value, onChange, label, need, optional }: {
  model: ReportModel; value: string; onChange: (v: string) => void; label: string;
  need?: (m: Measure) => boolean; optional?: boolean;
}) {
  const fits = model.measures.filter((m) => !need || need(m));
  const groups = new Map<string, Measure[]>();
  for (const m of fits) {
    const g = m.scoped ? m.group : "Whole register only";
    groups.set(g, [...(groups.get(g) ?? []), m]);
  }
  return <Field label={label}>
    <Select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label}>
      <option value="">{optional ? "None" : "Pick a measure"}</option>
      {[...groups].map(([g, ms]) => <optgroup key={g} label={g}>
        {ms.map((m) => <option key={m.key} value={m.key}>{m.label}{m.unit ? ` (${m.unit})` : ""}</option>)}
      </optgroup>)}
    </Select>
  </Field>;
}

/** Pick a panel's type, then what it shows, from the model for the report's scope kinds. */
export function AddPanel({ definition, onAdd, onCancel }: {
  definition: ReportDefinition; onAdd: (p: Panel) => void; onCancel: () => void;
}) {
  const kinds = definition.scope.kinds;
  const model = useAsync(() => api.reportModel(kinds), [kinds.join(",")]);
  const roles = useAsync(() => api.rolesCatalog(), []);
  const [type, setType] = useState<PanelType>("stat");
  const [measure, setMeasure] = useState("");
  const [more, setMore] = useState(["", ""]);
  const [grain, setGrain] = useState("");
  const [chart, setChart] = useState<FigurePanel["chart"]>("bar");
  const [list, setList] = useState("");
  const [view, setView] = useState("");  // a row set's key, or "" for a measure by grain
  const [filter, setFilter] = useState<CasesFilter>({ state: "open", sort: "due" });
  const [text, setText] = useState("");
  const [title, setTitle] = useState("");
  const [size, setSize] = useState<PanelSize | 0>(0);
  const m = model.data;
  const picked = m?.measures.find((x) => x.key === measure);
  const grains = picked?.grains ?? [];
  const byGrain = type === "figure" || (type === "table" && !list);
  const ready = type === "stat" || type === "trend" ? !!measure
    : byGrain ? !!measure && grains.includes(grain)
      : type === "view" ? !!view : true;

  const build = (): Panel => {
    const base = { size: (size || DEFAULT_SIZE[type] || 2) as PanelSize, ...(title.trim() ? { title: title.trim() } : {}) };
    const id = (stem: string) => newPanelId(definition, stem);
    switch (type) {
      case "stat": return { ...base, id: id(measure), type, measure };
      case "trend": return { ...base, id: id(measure), type, measures: [measure, ...more.filter((x) => x && x !== measure)] };
      case "figure": return { ...base, id: id(`${measure}-${grain}`), type, measure, grain, chart };
      case "table": return list ? { ...base, id: id(list), type, rows: list } : { ...base, id: id(`${measure}-${grain}`), type, measure, grain };
      case "cases": return { ...base, id: id("cases"), type, filter: Object.fromEntries(Object.entries(filter).filter(([, v]) => v !== "" && v !== undefined && v !== false)) as CasesFilter, limit: 10 };
      case "view": return { ...base, id: id(view), type, view };
      case "narrative": return { ...base, id: id("narrative"), type };
      case "note": return { ...base, id: id("note"), type, ...(text.trim() ? { text: text.trim() } : {}) };
      default: return { ...base, id: id("summary"), type: "summary" };
    }
  };

  return <Card as="section" className="report-add" title="Add panel"
    actions={<Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>}>
    <Status of={model} />
    {m && <div className="report-add-fields">
      <Field label="Panel type">
        <Select value={type} onChange={(e) => { setType(e.target.value as PanelType); setSize(0); }} aria-label="Panel type">
          {m.types.map((t) => <option key={t} value={t}>{TYPE_WORDS[t]}</option>)}
        </Select>
      </Field>
      {(type === "stat" || type === "trend") && <MeasureSelect model={m} value={measure} onChange={setMeasure} label="Measure" />}
      {type === "trend" && more.map((x, i) => <MeasureSelect key={i} model={m} value={x} optional label="And"
        onChange={(v) => setMore(more.map((y, j) => (j === i ? v : y)))} />)}
      {type === "table" && <Field label="Rows">
        <Select value={list} onChange={(e) => setList(e.target.value)} aria-label="Rows">
          <option value="">A measure by one of its grains</option>
          <optgroup label="Lists">
            {m.row_sets.map((r) => <option key={r.key} value={r.key}>{r.label}{r.scoped ? "" : " (whole register)"}</option>)}
          </optgroup>
        </Select>
      </Field>}
      {byGrain && <>
        <MeasureSelect model={m} value={measure} label="Measure" need={(x) => x.grains.length > 0}
          onChange={(v) => { setMeasure(v); setGrain(""); }} />
        <Field label="By">
          <Select value={grain} onChange={(e) => setGrain(e.target.value)} aria-label="By" disabled={!grains.length}>
            <option value="">Pick a grain</option>
            {grains.map((g) => <option key={g} value={g}>{kindWord(g) || g}</option>)}
          </Select>
        </Field>
      </>}
      {type === "figure" && <Field label="Chart">
        <Select value={chart} onChange={(e) => setChart(e.target.value as FigurePanel["chart"])} aria-label="Chart">
          {m.charts.map((c) => <option key={c} value={c}>{CHART_WORDS[c]}</option>)}
        </Select>
      </Field>}
      {type === "cases" && <>
        <Field label="State">
          <Select value={filter.state ?? "open"} onChange={(e) => setFilter({ ...filter, state: e.target.value as CasesFilter["state"] })} aria-label="State">
            <option value="open">Open</option><option value="closed">Closed</option><option value="all">All</option>
          </Select>
        </Field>
        <Field label="Queue">
          <Select value={filter.queue ?? ""} onChange={(e) => setFilter({ ...filter, queue: e.target.value || undefined })} aria-label="Queue">
            <option value="">Any queue</option>
            {(roles.data ?? []).filter((r) => r.kind === "person").map((r) => <option key={r.name} value={r.name}>{say(`role:${r.name}`)}</option>)}
          </Select>
        </Field>
        <Field label="Sort">
          <Select value={filter.sort ?? "due"} onChange={(e) => setFilter({ ...filter, sort: e.target.value as CasesFilter["sort"] })} aria-label="Sort">
            <option value="due">Due first</option><option value="opened">Newest</option><option value="value">Most value</option>
          </Select>
        </Field>
        <Checkbox checked={!!filter.overdue} onChange={(v) => setFilter({ ...filter, overdue: v || undefined })}>Overdue only</Checkbox>
      </>}
      {type === "view" && <Field label="View">
        <Select value={view} onChange={(e) => setView(e.target.value)} aria-label="View">
          <option value="">Pick a view</option>
          {m.views.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </Select>
      </Field>}
      {type === "note" && <Field label="Fixed text" width="full">
        <Textarea rows={2} width="full" value={text} onChange={(e) => setText(e.target.value)}
          placeholder="Leave empty for people's notes. {work.backlog} shows a live value." />
      </Field>}
      <Field label="Title">
        <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder={picked?.label ?? TYPE_WORDS[type]} />
      </Field>
      <Field label="Size">
        <Select value={size || DEFAULT_SIZE[type] || 2} onChange={(e) => setSize(Number(e.target.value) as PanelSize)} aria-label="Size">
          {([1, 2, 3, 4] as PanelSize[]).map((s) => <option key={s} value={s}>{SIZE_WORDS[s]}</option>)}
        </Select>
      </Field>
    </div>}
    {picked && !picked.scoped && <Text as="div" small>{picked.label} is for the whole register, whatever the report's scope.</Text>}
    {m && <Line wrap>
      <Button variant="primary" disabled={!ready} onClick={() => onAdd(build())}>Add</Button>
      {kinds.length > 0 && <Text small muted>For a {kinds.map((k) => kindWord(k)).join(" or ")}</Text>}
    </Line>}
  </Card>;
}

/** Add a panel as a row of its own, before row `index`. */
export function AddPanelSlot({ definition, index, open, onOpen, onChange }: {
  definition: ReportDefinition; index: number; open: boolean; onOpen: (index: number | null) => void;
  onChange: (d: ReportDefinition) => void;
}) {
  if (!open) {
    return <div className="report-add-between">
      <Button size="sm" variant="ghost" onClick={() => onOpen(index)}>+ Add panel</Button>
    </div>;
  }
  return <AddPanel definition={definition} onCancel={() => onOpen(null)}
    onAdd={(p) => { onChange(addRow(definition, index, p)); onOpen(null); }} />;
}

/** The definition being edited as YAML, checked by the server with the same checks a save runs. */
export function DefinitionView({ definition, onApply, onClose }: {
  definition: ReportDefinition; onApply: (d: ReportDefinition) => void; onClose: () => void;
}) {
  const shown = useAsync(() => api.checkReport({ definition }), [JSON.stringify(definition)]);
  const [text, setText] = useState<string | null>(null);
  const [ok, setOk] = useState(false);
  const a = useAction();
  const value = text ?? shown.data?.text ?? "";
  const apply = (close: boolean) => a.run("check", () => api.checkReport({ text: value }), (r) => {
    onApply({ ...r.definition, key: definition.key, version: definition.version, source: definition.source });
    setText(null);
    setOk(!close);
    if (close) onClose();
  });
  return <Card as="section" className="report-definition" title="Definition"
    actions={<Line wrap>
      <Button size="sm" disabled={!!a.busy || text === null} onClick={() => apply(false)}>Check</Button>
      <Button size="sm" variant="primary" disabled={!!a.busy} onClick={() => (text === null ? onClose() : apply(true))}>Back to the page</Button>
    </Line>}>
    <Status of={shown} />
    {shown.data && <Textarea className="report-definition-text" width="full" rows={24} spellCheck={false} value={value}
      aria-label="Report definition (YAML or JSON)" onChange={(e) => { setText(e.target.value); setOk(false); }} />}
    <ErrorText>{a.error}</ErrorText>
    {ok && !a.error && <Text as="div" small>Checked. Save keeps it as the next version.</Text>}
  </Card>;
}

/** Every kept version, newest first; Restore brings one back as the newest. */
export function VersionHistory({ reportKey, onRestored }: { reportKey: string; onRestored: () => void }) {
  const versions = useAsync(() => api.reportVersions(reportKey), [reportKey]);
  const a = useAction();
  const restore = async (v: number) => {
    if (!(await confirmDialog(`Bring back version ${v}? It becomes the newest version; the others stay kept.`, { confirm: "Restore" }))) return;
    a.run(`restore-${v}`, () => api.restoreReport(reportKey, v), () => { versions.reload(); onRestored(); });
  };
  return <Card as="section" title="Versions" className="report-versions-card">
    <Status of={versions} />
    <List plain>
      {versions.data?.map((v) => <li key={v.version}>
        <Line spread wrap>
          <Text small><strong>Version {v.version}</strong> · {v.title} · {v.panels} panel{v.panels === 1 ? "" : "s"} · {person(v.by)} · {ago(v.at)}{v.note && v.note !== "saved" ? ` · ${v.note}` : ""}</Text>
          {v.current ? <Text small muted>Current</Text>
            : <Button size="sm" disabled={!!a.busy} onClick={() => restore(v.version)}>Restore</Button>}
        </Line>
      </li>)}
    </List>
    <ErrorText>{a.error}</ErrorText>
  </Card>;
}

/** Who sees the report: only its owner, everyone, or a workflow role. Beyond yourself needs configure access. */
export function ShareSelect({ report, onShared }: { report: CustomReport; onShared: (r: CustomReport) => void }) {
  const roles = useAsync(() => api.rolesCatalog(), []);
  const a = useAction();
  const people = (roles.data ?? []).filter((r) => r.kind === "person");
  return <>
    <Field label="Shared with" inline>
      <Select value={report.shared} disabled={!!a.busy || (!report.can_share && !report.shared)} aria-label="Shared with"
        onChange={(e) => a.run("share", () => api.shareReport(report.key, e.target.value), onShared)}>
        <option value="">Only me</option>
        <option value="everyone">Everyone</option>
        {people.map((r) => <option key={r.name} value={`role:${r.name}`}>{say(`role:${r.name}`)}</option>)}
      </Select>
    </Field>
    {!report.can_share && <Text small muted>Sharing needs configure access</Text>}
    <ErrorText inline>{a.error}</ErrorText>
  </>;
}

/** Whose report it is and where it came from, in one line under the title. */
export function CustomLine({ report }: { report: CustomReport }) {
  const sharedWith = report.shared === "everyone" ? "shared with everyone"
    : report.shared ? `shared with ${say(report.shared)}` : "only you";
  const o = report.origin;
  const changed = o?.version != null && o.copied_version != null && o.version > o.copied_version;
  return <Text as="div" small muted>
    {[report.mine ? `Yours, ${sharedWith}` : `${person(report.owner)}'s, ${sharedWith}`, `version ${report.version}`].join(" · ")}
    {o && <> · copied from <a href={`#/reports/${encodeURIComponent(o.key)}`}>{o.title ?? "a report"}</a>
      {changed && ` (changed since: version ${o.version})`}</>}
  </Text>;
}
