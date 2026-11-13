// Regulatory and emissions reports (P7.6): each report drafted from its records for a period, every figure with its
// basis (measured, estimated, missing) and the rows it is made of, the figures on estimated or missing data marked
// first, and the review case that holds it.
import { useState } from "react";
import { api, type RegulatoryListing, type ReportBasis, type ReportFigure, type ReportRow } from "../api";
import { Status, entityHref, href, useAsync } from "../lib";
import { usePerson, when } from "../work";
import { useAct } from "./workflows/shared";
import { Badge, Button, Card, Table } from "../ui";
import "../styles/reports.css";

const BASIS_TONE: Record<ReportBasis, "ok" | "warn" | "danger"> = { measured: "ok", estimated: "warn", missing: "danger" };

export function BasisBadge({ basis }: { basis: ReportBasis }) {
  return <Badge tone={BASIS_TONE[basis]}>{basis}</Badge>;
}

const num = (v: number | null, unit?: string | null) =>
  v === null ? "—" : `${v.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`;

/** Where a row drills to: the case, the point read that day, or the measurement record on the thing it measured. */
function RowRef({ row, subject }: { row: ReportRow; subject?: string }) {
  const ref = row.ref;
  if (!ref) return <span className="muted">no record</span>;
  if (ref.startsWith("case:")) return <a href={href(`/cases/${ref.slice(5)}`)}>case {ref.slice(5)}</a>;
  if (ref.startsWith("point:")) {
    const [id] = ref.slice(6).split("@");
    return <a href={entityHref(id)}>historian readings{row.readings ? ` (${row.readings})` : ""}</a>;
  }
  const record = ref.replace(/^record:/, "");
  return subject ? <a href={entityHref(subject)} title={record}>{record.split(":").pop()}</a> : <span>{record}</span>;
}

function Figure({ f, open, onToggle }: { f: ReportFigure; open: boolean; onToggle: () => void }) {
  return (
    <>
      <tr className="clickable" onClick={onToggle} aria-expanded={open}>
        <td>{open ? "▾" : "▸"} {f.label}</td>
        <td className="num">{num(f.value, f.unit)}</td>
        <td><BasisBadge basis={f.basis} /></td>
        <td className="small muted">{[...f.flags, f.note].filter(Boolean).join("; ")}</td>
      </tr>
      {open && f.rows.map((r, i) => (
        <tr key={`${f.key}-${i}`} className="small">
          <td className="report-row-label">{r.label}{r.text ? <div className="muted">{r.text}</div> : null}</td>
          <td className="num">{num(r.value, r.unit ?? f.unit)}</td>
          <td><BasisBadge basis={r.basis} /></td>
          <td><RowRef row={r} subject={f.subject} /></td>
        </tr>
      ))}
    </>
  );
}

export function RegulatoryReportView({ template, period, onChange }: { template: string; period: string; onChange: () => void }) {
  const [version, setVersion] = useState<number | undefined>();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [person] = usePerson();
  const a = useAct();
  const r = useAsync(() => api.regulatoryReport(template, period, version), [template, period, version]);
  const d = r.data;
  const toggle = (k: string) => setOpen((o) => ({ ...o, [k]: !o[k] }));
  return (
    <>
      <Status loading={r.loading && !d} error={r.error} />
      {d && (
        <>
          <Card as="section">
            <div className="row spread">
              <h2>{d.title}, {d.coverage.label}</h2>
              <span className="small row">
                {d.versions.map((v) => (
                  <Button key={v} className={v === d.version ? "chip on" : "chip"} onClick={() => setVersion(v)}>v{v}</Button>
                ))}
                {d.review?.item_id && <a href={href(`/cases/${d.review.item_id}`)}>review case ({d.review.state})</a>}
              </span>
            </div>
            <p className="small muted">
              Covers {d.coverage.since} to {d.coverage.through}
              {d.coverage.partial ? ", drafted through the data's last full day: the month is not over" : ""}.
              Drafted by {d.drafted_by} {when(d.created_at)}. Open a figure to see the records it is made of.
            </p>
            {d.marked.length > 0 ? (
              <div className="notice warn small">
                <strong>{d.marked.length} figure{d.marked.length > 1 ? "s" : ""} on estimated or missing data</strong>
                <ul>{d.marked.map((m) => <li key={m.key}>{m.label}: {m.flags.join("; ") || m.basis}</li>)}</ul>
              </div>
            ) : <p className="small">Every figure rests on measured data.</p>}
            <div className="actions">
              <Button disabled={!person || !!a.busy}
                title={person ? "Draft it again from the records; a new version only when a figure changed" : "Say who you are first"}
                onClick={() => a.run("draft", () => api.draftRegulatoryReport(template, period, person),
                  () => { setVersion(undefined); r.reload(); onChange(); })}>Draft again from the records</Button>
            </div>
            {a.error && <p className="error small">{a.error}</p>}
          </Card>
          {d.sections.map((s) => (
            <Card as="section" key={s.title}>
              <h2>{s.title}</h2>
              {s.note && <p className="small muted">{s.note}</p>}
              {s.figures.length === 0 ? <p className="small muted">Nothing for this period.</p> : (
                <Table>
                  <thead><tr><th>Figure</th><th>Value</th><th>Basis</th><th>Notes</th></tr></thead>
                  <tbody>
                    {s.figures.map((f) => <Figure key={f.key} f={f} open={!!open[f.key]} onToggle={() => toggle(f.key)} />)}
                  </tbody>
                </Table>
              )}
            </Card>
          ))}
        </>
      )}
    </>
  );
}

/** The regulatory reports drafted, and a way to draft this month's. */
export function RegulatoryList({ data, selected, onChange }: { data: RegulatoryListing; selected?: string; onChange: () => void }) {
  const [person] = usePerson();
  const a = useAct();
  const month = new Date().toISOString().slice(0, 7);
  const reports = Array.isArray(data.reports) ? data.reports : [];  // an API without regulatory reports answers otherwise
  const templates = Array.isArray(data.templates) ? data.templates : [];
  return (
    <div className="row wrap">
      {reports.map((x) => {
        const id = `${x.template}:${x.period}`;
        return (
          <a key={id} className={`badge${id === selected ? " on" : ""} ${x.marked ? "warn" : "ok"}`}
            href={href("/reports", { report: id })} title={`${x.title}; ${x.marked} figure(s) on estimated or missing data`}>
            {x.title}, {x.coverage.label}{x.version > 1 ? ` v${x.version}` : ""}{x.marked ? " ⚠" : ""}
          </a>
        );
      })}
      {templates.filter((t) => !reports.some((x) => x.template === t.name && x.period === month)).map((t) => (
        <Button key={t.name} size="sm" disabled={!person || !!a.busy} title={t.description}
          onClick={() => a.run("draft", () => api.draftRegulatoryReport(t.name, month, person), () => {
            onChange();
            window.location.assign(href("/reports", { report: `${t.name}:${month}` }));
          })}>Draft {t.title.toLowerCase()} for {month}</Button>
      ))}
      {a.error && <span className="error small">{a.error}</span>}
    </div>
  );
}
