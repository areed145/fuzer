import { Button, Input, Table } from "../../ui";
/** Editing a mapping (#225): column by column (what each column means, which it links to, the key), or the YAML.
 * The form is written over the mapping on the server, so what it doesn't show is kept. */
import { useEffect, useRef, useState } from "react";
import { api, type MapperColumn, type MapperForm } from "../../api";
import { Status } from "../../lib";
import { useAction } from "./common";

export function MappingEditor({ yaml, dataset, columns, samples, onSave, onChange }: {
  yaml: string;
  dataset?: string;                                   // its latest profile gives the columns and samples
  columns?: string[];                                 // or the table's columns (a table not loaded yet) ...
  samples?: Record<string, unknown>[];                // ... and some of its rows
  onSave?: (text: string, note?: string) => Promise<unknown>;
  onChange?: (text: string) => void;
}) {
  const [text, setText] = useState(yaml);
  const [view, setView] = useState<"columns" | "yaml">("columns");
  const [note, setNote] = useState("");
  const a = useAction();
  const set = (t: string) => { setText(t); onChange?.(t); };
  useEffect(() => { setText(yaml); }, [yaml]);
  return (
    <>
      <div className="tabs small">
        <a className={view === "columns" ? "on" : ""} onClick={() => setView("columns")}>Columns</a>
        <a className={view === "yaml" ? "on" : ""} onClick={() => setView("yaml")}>YAML (advanced)</a>
      </div>
      {view === "columns"
        ? <ColumnForm text={text} dataset={dataset} columns={columns} samples={samples} onText={set} />
        : <textarea className="yaml mono" rows={Math.min(30, text.split("\n").length + 1)} value={text}
            onChange={(e) => set(e.target.value)} spellCheck={false} />}
      {onSave && (
        <div className="row">
          <input placeholder="what changed and why" value={note} onChange={(e) => setNote(e.target.value)} />
          <Button variant="primary" disabled={!!a.busy || text === yaml} onClick={() => a.run("save", () => onSave(text, note || undefined))}>
            Save as draft</Button>
          {text !== yaml && <Button variant="secondary" onClick={() => set(yaml)}>Undo edits</Button>}
        </div>
      )}
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function ColumnForm({ text, dataset, columns, samples, onText }: {
  text: string; dataset?: string; columns?: string[]; samples?: Record<string, unknown>[]; onText: (t: string) => void;
}) {
  const [form, setForm] = useState<MapperForm | null>(null);
  const [error, setError] = useState<string | null>(null);
  const last = useRef(text);
  const timer = useRef<number | undefined>(undefined);
  // read the form from the text when it changed from outside (opened, YAML edited, undo)
  useEffect(() => {
    if (form && text === last.current) return;
    last.current = text;
    api.mapperForm(text, { columns, dataset }).then((f) => { setForm(f); setError(null); }, (e: Error) => setError(e.message));
  }, [text]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error && !form) return <p className="error small">{error}: edit it as YAML.</p>;
  if (!form) return <Status loading />;
  const update = (f: MapperForm) => {
    setForm(f);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api.mapperYaml(f, last.current).then((r) => { last.current = r.yaml; setError(null); onText(r.yaml); },
        (e: Error) => setError(e.message));
    }, 350);
  };
  const setCol = (i: number, c: Partial<MapperColumn>) =>
    update({ ...form, columns: form.columns.map((x, j) => (j === i ? { ...x, ...c } : x)) });
  const sampleOf = (name: string) => {
    const p = form.profile?.[name];
    if (p?.samples?.length) return p.samples.join(", ");
    return (samples ?? []).map((r) => r[name]).filter((v) => v !== null && v !== undefined && v !== "").slice(0, 3).join(", ");
  };
  const names = form.columns.map((c) => c.name);
  return (
    <>
      <Table variant="keyValue" density="compact">
        <tbody>
          <tr><th>Each record is</th><td>
            <input value={form.kind ?? ""} placeholder="entity kind, e.g. activity, equipment, tag"
              onChange={(e) => update({ ...form, kind: e.target.value || null })} />
            <span className="muted"> record type </span>
            <Input width="sm" value={form.record_type} onChange={(e) => update({ ...form, record_type: e.target.value })} />
          </td></tr>
          <tr><th>Record key</th><td>
            <select multiple value={form.key} onChange={(e) => update({ ...form, key: Array.from(e.target.selectedOptions, (o) => o.value) })}
              size={Math.min(4, names.length)}>
              {names.map((n) => <option key={n}>{n}</option>)}
            </select>
            <span className="muted"> the column(s) that identify a row (several: joined by &gt;)</span>
          </td></tr>
          {form.kind && (
            <tr><th>Matched on</th><td>
              <select value={form.entity_key?.column ?? ""} onChange={(e) => update({ ...form, entity_key: e.target.value
                ? { column: e.target.value, as: form.entity_key?.as ?? null } : null })}>
                <option value="">the record key</option>
                {names.map((n) => <option key={n}>{n}</option>)}
              </select>{" "}
              {form.entity_key && <select value={form.entity_key.as ?? ""} onChange={(e) =>
                update({ ...form, entity_key: { column: form.entity_key!.column, as: e.target.value || null } })}>
                {Object.entries(form.choices.key_as).map(([k, v]) => <option key={k} value={k}>read as {v}</option>)}
              </select>}
              <span className="muted"> what makes two sources' records the same thing</span>
            </td></tr>
          )}
        </tbody>
      </Table>
      <div className="scroll-x">
        <Table variant="mapping" density="compact">
          <thead><tr><th>Column</th><th>Sample</th><th>Keep as a field</th><th>Links the record to</th></tr></thead>
          <tbody>
            {form.columns.map((c, i) => (
              <tr key={c.name}>
                <td><code>{c.name}</code>{form.key.includes(c.name) && <span className="badge key"> key</span>}</td>
                <td className="muted">{sampleOf(c.name).slice(0, 60)}</td>
                <td>
                  <input type="checkbox" checked={!!c.field}
                    onChange={(e) => setCol(i, { field: e.target.checked ? { name: c.name, as: null } : null })} />{" "}
                  {c.field && <>
                    <Input width="sm" value={c.field.name} onChange={(e) => setCol(i, { field: { ...c.field!, name: e.target.value } })} />{" "}
                    <select value={c.field.as ?? ""} onChange={(e) => setCol(i, { field: { ...c.field!, as: e.target.value || null } })}>
                      {Object.entries(form.choices.field_as).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                  </>}
                </td>
                <td>
                  <select value={c.link?.target ?? ""} onChange={(e) => setCol(i, {
                    link: e.target.value ? { rel: c.link?.rel ?? "concerns", target: e.target.value, keep: c.link?.keep ?? {} } : null })}>
                    <option value="">nothing</option>
                    {Object.entries(form.choices.targets).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>{" "}
                  {c.link && <Input width="xs" value={c.link.rel} title="the link's name (relationship)"
                    onChange={(e) => setCol(i, { link: { ...c.link!, rel: e.target.value } })} />}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
      {form.kept.length > 0 && (
        <p className="muted small">Also in this mapping, kept as it is (edit under YAML): {form.kept.join("; ")}.</p>
      )}
      {error && <p className="error small">{error}</p>}
    </>
  );
}
