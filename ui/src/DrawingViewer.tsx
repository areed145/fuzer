import "./styles/drawing.css";
import { useState, type ReactNode } from "react";
import { api, type DrawingMark, type MarkStatus } from "./api";
import { projectUrl } from "./authState";
import { entityHref, useAsync } from "./lib";
import { Button, Card as UICard, Disclosure, Empty } from "./ui";

const STATUS: { key: MarkStatus; label: string; dashed?: boolean }[] = [
  { key: "linked", label: "Linked to the register" },
  { key: "not_in_register", label: "Tag not in the register" },
  { key: "not_a_tag", label: "Not a tag" },
  { key: "line", label: "Line number", dashed: true },
];
const ZOOMS = [
  { key: 0, label: "Fit" },
  { key: 1, label: "100%" },
  { key: 2, label: "200%" },
];

/** A P&ID drawing with what was read from it marked on the sheet; hidden when the document has no drawing.
 * `highlight`: entity ids whose marks are ringed and labelled (an entity's own page). `compact` leaves out
 * the lists of what was read and the piping. */
export function DrawingViewer({ id, highlight = [], title, compact = false }: {
  id: string;
  highlight?: string[];
  title?: ReactNode;
  compact?: boolean;
}) {
  const d = useAsync(() => api.drawing(id).catch(() => null), [id]);
  const [hidden, setHidden] = useState<Set<MarkStatus>>(new Set());
  const [zoom, setZoom] = useState(0);
  const [focus, setFocus] = useState<number>();
  if (!d.data) return null;
  const g = d.data;
  const counts = Object.fromEntries(STATUS.map((s) => [s.key, g.marks.filter((m) => m.status === s.key).length]));
  const hl = (m: DrawingMark) => !!m.id && highlight.includes(m.id);
  const shown = g.marks.map((m, i) => ({ m, i })).filter(({ m }) => !hidden.has(m.status) || hl(m));
  const found = g.marks.filter((m) => hl(m) && m.x !== undefined).length;
  const toggle = (s: MarkStatus) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(s)) n.delete(s);
      else n.add(s);
      return n;
    });
  const source =
    g.read_by === "smart file"
      ? "smart P&ID: tags and piping from the file"
      : g.read_by
        ? `${g.format === "image" ? "scan" : "drawing"} read by ${g.read_by}`
        : "not read yet (run fuzer llm-link --source pids)";

  return (
    <UICard  as="section">
      <div className="row spread">
        <h2>
          {title ?? "Drawing"} <span className="muted small">{source}</span>
        </h2>
        <div className="tabs">
          {ZOOMS.map((z) => (
            <a key={z.key} className={zoom === z.key ? "on" : ""} onClick={() => setZoom(z.key)} role="button">
              {z.label}
            </a>
          ))}
        </div>
      </div>
      {g.marks.length > 0 && (
        <div className="drawing-tools" aria-label="Show or hide marks">
          {STATUS.filter((s) => counts[s.key]).map((s) => (
            <Button
              key={s.key}
              className={`chip st-${s.key} ${hidden.has(s.key) ? "off" : ""}`}
              aria-pressed={!hidden.has(s.key)}
              onClick={() => toggle(s.key)}
            >
              <span className={`swatch ${s.dashed ? "dashed" : ""}`} />
              <span style={{ color: "var(--text)" }}>
                {s.label} · {counts[s.key]}
              </span>
            </Button>
          ))}
        </div>
      )}
      <div className="viewer">
        <div className="sheet" style={{ width: zoom ? `${g.width * zoom}px` : "100%" }}>
          <img src={projectUrl(g.image)} alt={`P&ID ${g.sheet}`} />
          <svg viewBox={`0 0 ${g.width} ${g.height}`} preserveAspectRatio="none">
            {shown
              .filter(({ m }) => m.x !== undefined && m.y !== undefined)
              .map(({ m, i }) => (
                <Mark key={i} m={m} focus={focus === i} highlight={hl(m)}
                      onHover={(on) => setFocus(on ? i : undefined)} />
              ))}
          </svg>
        </div>
      </div>
      {highlight.length > 0 && (
        <p className="small">
          {found
            ? "Ringed: where this is on the sheet."
            : "The drawing mentions this, but its position on the sheet wasn't read."}
        </p>
      )}
      {!compact && <div className="drawing-lists">
        <div>
          <h3>Read from the drawing</h3>
          {g.marks.length === 0 && <Empty className="small">Nothing read yet.</Empty>}
          <ul>
            {shown.map(({ m, i }) => (
              <li
                key={i}
                className={focus === i ? "focus" : ""}
                onMouseEnter={() => setFocus(i)}
                onMouseLeave={() => setFocus(undefined)}
              >
                <span className={`status st-${m.status}`}>●</span>
                {m.id ? (
                  <a className="mono" href={entityHref(m.id)}>
                    {m.text}
                  </a>
                ) : (
                  <span className="mono">{m.text}</span>
                )}
                {m.key && m.key !== m.text && <span className="muted small mono">{m.key}</span>}
                <span className="muted small">{STATUS.find((s) => s.key === m.status)?.label}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Piping</h3>
          {g.connections.length === 0 && <Empty className="small">No lines read.</Empty>}
          <ul>
            {g.connections.map((c, i) => (
              <li key={i}>
                <End id={c.from_id} text={c.from} /> → <End id={c.to_id} text={c.to} />
                {c.line && <span className="muted small mono">{c.line}</span>}
                {c.stream && <span className="muted small">stream {c.stream}</span>}
                {c.via && c.via.length > 0 && <span className="muted small"> via {c.via.join(", ")}</span>}
              </li>
            ))}
          </ul>
          {(g.offpage ?? []).length > 0 && (
            <>
              <h3>Off-page</h3>
              <ul>
                {g.offpage!.map((o, i) => (
                  <li key={i}>
                    <span className="mono">{o.tag ?? "?"}</span> {o.direction === "to" ? "→" : "←"}{" "}
                    <span className="mono">{o.other ?? "?"}</span> on{" "}
                    {o.sheet_id ? <a className="mono" href={entityHref(o.sheet_id)}>{o.sheet}</a>
                      : <span className="mono">{o.sheet}</span>}
                    <span className="muted small mono"> {o.line}</span>{" "}
                    <span className="muted small">{o.status === "confirmed" ? "both sheets agree"
                      : o.status === "one_sided" ? "only this sheet shows it: in review" : "other sheet not read yet"}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {(g.loops ?? []).length > 0 && (
            <>
              <h3>Instrument loops</h3>
              <ul>
                {g.loops!.map((lp, i) => (
                  <li key={i} className="mono">{lp.members.join(" → ")}</li>
                ))}
              </ul>
            </>
          )}
          {(g.checks ?? []).length > 0 && (
            <>
              <h3>Where other systems disagree with the drawing</h3>
              <ul>
                {g.checks!.map((c, i) => (
                  <li key={i}>
                    <a className="mono" href={entityHref(c.point_id)}>{c.tag}</a>{" "}
                    <span className="muted small">{c.detail}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          {(g.notes ?? []).length > 0 && (
            <>
              <h3>Notes</h3>
              <ol className="drawing-notes">
                {g.notes!.map((n, i) => (
                  <li key={i} value={n.n}>
                    {n.text}
                    {n.tags.length > 0 && <span className="muted small mono"> {n.tags.join(", ")}</span>}
                  </li>
                ))}
              </ol>
            </>
          )}
          {(g.tables ?? []).map((t, i) => (
            <Disclosure key={i} className="drawing-table" label={<>{t.title || "Table"} <span className="muted small">{t.rows.length} rows</span></>}>
              <div className="table-scroll">
                <table className="small">
                  <thead><tr>{t.columns.map((c, j) => <th key={j}>{c}</th>)}</tr></thead>
                  <tbody>
                    {t.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k} className="mono">{c}</td>)}</tr>)}
                  </tbody>
                </table>
              </div>
            </Disclosure>
          ))}
          {g.findings.length > 0 && (
            <>
              <h3>Findings</h3>
              <ul>
                {g.findings.map((f, i) => (
                  <li key={i}>
                    <span className="mono">{f.text}</span> <span className="muted small">{f.issue}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>}
    </UICard>
  );
}

function Mark({ m, focus, highlight, onHover }: {
  m: DrawingMark;
  focus: boolean;
  highlight: boolean;
  onHover: (on: boolean) => void;
}) {
  const label = `${m.text}${m.key && m.key !== m.text ? ` (${m.key})` : ""}: ${
    STATUS.find((s) => s.key === m.status)?.label
  }`;
  const body = (
    <g
      className={`mark st-${m.status} ${focus ? "focus" : ""} ${highlight ? "hl" : ""}`}
      onMouseEnter={() => onHover(true)}
      onMouseLeave={() => onHover(false)}
    >
      <title>{label}</title>
      {highlight && <circle className="hl-ring" cx={m.x} cy={m.y} r={70} />}
      <circle cx={m.x} cy={m.y} r={m.status === "line" ? 26 : 38} />
      {(focus || highlight) && (
        <text x={(m.x ?? 0) + 46} y={(m.y ?? 0) - 40}>
          {m.key ?? m.text}
        </text>
      )}
    </g>
  );
  return m.id ? <a href={entityHref(m.id)}>{body}</a> : body;
}

function End({ id, text }: { id: string | null; text: string }) {
  return id ? (
    <a className="mono" href={entityHref(id)}>
      {text}
    </a>
  ) : (
    <span className="mono">{text}</span>
  );
}
