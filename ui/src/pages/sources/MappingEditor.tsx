import { Button, Input, Select, Table , Badge as UIBadge} from "../../ui";
/** Editing a mapping (#225): column by column (what each column means, which it links to, the key), or the YAML.
 * The form is written over the mapping on the server, so what it doesn't show is kept. */
import { useEffect, useRef, useState } from "react";
import { api, type MapperColumn, type MapperForm, type OntologyDeclared } from "../../api";
import { Status, href, useAsync } from "../../lib";
import { reviewer, useAction } from "./common";

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
          <Input placeholder="what changed and why" value={note} onChange={(e) => setNote(e.target.value)} />
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
  const [warnings, setWarnings] = useState<string[]>([]);
  const model = useAsync(() => api.ontologyDeclared(), []);
  const last = useRef(text);
  const timer = useRef<number | undefined>(undefined);
  // read the form from the text when it changed from outside (opened, YAML edited, undo)
  useEffect(() => {
    if (form && text === last.current) return;
    last.current = text;
    api.mapperForm(text, { columns, dataset }).then((f) => { setForm(f); setWarnings(f.warnings ?? []); setError(null); },
      (e: Error) => setError(e.message));
  }, [text]);  // eslint-disable-line react-hooks/exhaustive-deps
  if (error && !form) return <p className="error small">{error}: edit it as YAML.</p>;
  if (!form) return <Status loading />;
  const update = (f: MapperForm) => {
    setForm(f);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      api.mapperYaml(f, last.current).then((r) => { last.current = r.yaml; setWarnings(r.warnings); setError(null); onText(r.yaml); },
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
            <KindSelect model={model.data} value={form.kind} onChange={(kind) => { update({ ...form, kind }); model.reload(); }} />
            <span className="muted"> record type </span>
            <Input width="sm" value={form.record_type} onChange={(e) => update({ ...form, record_type: e.target.value })} />
          </td></tr>
          <tr><th>Record key</th><td>
            {/* Checkboxes rather than a cramped multi-select (#414); several keys join in column order. */}
            <div className="key-picker" role="group" aria-label="Record key columns">
              {names.map((n) => (
                <label key={n} className={`chip${form.key.includes(n) ? " on" : ""}`}>
                  <input type="checkbox" checked={form.key.includes(n)} onChange={(e) => update({ ...form,
                    key: names.filter((x) => (x === n ? e.target.checked : form.key.includes(x))) })} /> <code>{n}</code>
                </label>
              ))}
            </div>
            <span className="muted small">the column(s) that identify a row (several: joined by &gt; in column order)</span>
          </td></tr>
          {form.kind && (
            <tr><th>Matched on</th><td>
              <Select value={form.entity_key?.column ?? ""} onChange={(e) => update({ ...form, entity_key: e.target.value
                ? { column: e.target.value, as: form.entity_key?.as ?? null } : null })}>
                <option value="">the record key</option>
                {names.map((n) => <option key={n}>{n}</option>)}
              </Select>{" "}
              {form.entity_key && <Select value={form.entity_key.as ?? ""} onChange={(e) =>
                update({ ...form, entity_key: { column: form.entity_key!.column, as: e.target.value || null } })}>
                {Object.entries(form.choices.key_as).map(([k, v]) => <option key={k} value={k}>read as {v}</option>)}
              </Select>}
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
                <td><code>{c.name}</code>{form.key.includes(c.name) && <UIBadge className="badge key"> key</UIBadge>}</td>
                <td className="muted">{sampleOf(c.name).slice(0, 60)}</td>
                <td>
                  <Input type="checkbox" checked={!!c.field}
                    onChange={(e) => setCol(i, { field: e.target.checked ? { name: c.name, as: null } : null })} />{" "}
                  {c.field && <>
                    <Input width="sm" value={c.field.name} onChange={(e) => setCol(i, { field: { ...c.field!, name: e.target.value } })} />{" "}
                    <Select value={c.field.as ?? ""} onChange={(e) => setCol(i, { field: { ...c.field!, as: e.target.value || null } })}>
                      {Object.entries(form.choices.field_as).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </Select>
                  </>}
                </td>
                <td>
                  <Select value={c.link?.target ?? ""} onChange={(e) => setCol(i, {
                    link: e.target.value ? { rel: c.link?.rel ?? "concerns", target: e.target.value, keep: c.link?.keep ?? {} } : null })}>
                    <option value="">nothing</option>
                    {Object.entries(form.choices.targets).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    {c.link?.target.startsWith("kind:") && !model.data?.kinds.some((k) => `kind:${k.name}` === c.link!.target) && (
                      <option value={c.link.target}>{c.link.target.slice(5)} (not in the ontology)</option>)}
                    {model.data && (
                      <optgroup label="Any kind, by its key">
                        {model.data.kinds.map((k) => <option key={k.name} value={`kind:${k.name}`}>{k.label.toLowerCase()} (by key)</option>)}
                      </optgroup>
                    )}
                  </Select>{" "}
                  {c.link && <RelSelect model={model.data} value={c.link.rel}
                    onChange={(rel) => setCol(i, { link: { ...c.link!, rel } })} />}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </div>
      {warnings.length > 0 && (
        <div className="small">
          <b>Against the ontology</b> <a href={href("/ontology", { tab: "model" })}>(define kinds and link types)</a>
          <ul>{warnings.map((w) => <li key={w}>{w}</li>)}</ul>
        </div>
      )}
      {form.kept.length > 0 && (
        <p className="muted small">Also in this mapping, kept as it is (edit under YAML): {form.kept.join("; ")}.</p>
      )}
      {error && <p className="error small">{error}</p>}
    </>
  );
}

/** The entity kind, picked from the ontology (#241); "new kind…" adds a draft kind to describe later. */
function KindSelect({ model, value, onChange }: {
  model?: OntologyDeclared; value: string | null; onChange: (kind: string | null) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const known = !value || !model || model.kinds.some((k) => k.name === value);
  const add = async () => {
    const name = prompt("Name of the new kind (lower case, words joined with _), e.g. activity")?.trim();
    if (!name) return;
    try {
      await api.kindAdd({ name, status: "draft" }, reviewer(), "made while mapping a dataset");
      setError(null);
      onChange(name);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <>
      <Select width="sm" value={value ?? ""} onChange={(e) => (e.target.value === "+" ? add() : onChange(e.target.value || null))}>
        <option value="">no entity (records only)</option>
        {!known && <option value={value!}>{value} (not in the ontology)</option>}
        {model?.groups.map((g) => {
          const ks = model.kinds.filter((k) => k.group === g);
          return ks.length ? <optgroup key={g} label={g}>{ks.map((k) => <option key={k.name} value={k.name}>{k.label}</option>)}</optgroup> : null;
        })}
        {model && model.kinds.some((k) => !k.group) && (
          <optgroup label="Other">{model.kinds.filter((k) => !k.group).map((k) => <option key={k.name} value={k.name}>{k.label}</option>)}</optgroup>
        )}
        <option value="+">new kind…</option>
      </Select>
      {error && <span className="error"> {error}</span>}
    </>
  );
}

/** A link's type, picked from the ontology's link types (#241). */
function RelSelect({ model, value, onChange }: { model?: OntologyDeclared; value: string; onChange: (rel: string) => void }) {
  const known = !model || model.relations.some((r) => r.name === value);
  return (
    <Select width="sm" value={value} title="the link's type (Ontology > Kinds & link types)" onChange={(e) => onChange(e.target.value)}>
      {!known && <option value={value}>{value} (not in the ontology)</option>}
      {model?.relations.map((r) => <option key={r.name} value={r.name} title={r.description}>{r.name}</option>)}
    </Select>
  );
}
