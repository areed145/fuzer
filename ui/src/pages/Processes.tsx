// Processes and task forms (#97): the list of process definitions, the modeler page (draft → checked →
// published, compared with the published version), the process monitor (work waiting at each step,
// drawn on the diagram) and the form-js form editor.

import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type Modeler from "bpmn-js/lib/Modeler";
import { api, type WorkChange, type WorkCompare, type WorkDefinition, type WorkProblem } from "../api";
import { Status, href, useAsync } from "../lib";
import { Actor, span, usePerson, when } from "../work";
import { blankProcess, focusElement } from "../bpmnSubset";

const BpmnModeler = lazy(() => import("../BpmnModeler"));
const BpmnView = lazy(() => import("../BpmnView"));
const FormEditor = lazy(() => import("../FormEditor"));

const ADMIN = "workflow_admin";
const isOpenDraft = (d: WorkDefinition, defs: WorkDefinition[]) =>
  (d.state === "draft" || d.state === "checked") &&
  d.version > Math.max(0, ...defs.filter((x) => x.state === "published" || x.state === "superseded").map((x) => x.version));

function StateBadge({ state }: { state: string }) {
  const cls = state === "published" ? "ok" : state === "checked" ? "soon" : state === "draft" ? "human" : "";
  return <span className={`badge ${cls}`}>{state}</span>;
}

function useAdmin(person: string) {
  const people = useAsync(() => api.workPeople(), []);
  return !!people.data?.find((p) => p.name === person)?.roles.includes(ADMIN);
}

function NeedName({ person }: { person: string }) {
  if (person) return null;
  return <div className="status">Set your name on the <a href={href("/work")}>Work</a> page to edit processes.</div>;
}

const download = (name: string, text: string, type: string) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
};

// --- list ---------------------------------------------------------------------------------------------

export function ProcessesPage() {
  const defs = useAsync(() => api.workDefinitions(), []);
  const forms = useAsync(() => api.workForms(), []);
  const [person] = usePerson();
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();
  const file = useRef<HTMLInputElement>(null);

  const groups = useMemo(() => {
    const by = new Map<string, WorkDefinition[]>();
    for (const d of defs.data ?? []) by.set(d.process_key, [...(by.get(d.process_key) ?? []), d]);
    return [...by.entries()];
  }, [defs.data]);
  const taken = groups.some(([k]) => k === key);
  const keyOk = /^[a-z][a-z0-9_]*$/.test(key) && !taken;

  const create = async () => {
    try {
      await api.importDefinition(blankProcess(key, name || key), person);
      location.hash = href(`/work/processes/${key}`);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const upload = async (f: File) => {
    try {
      const rows = await api.importDefinition(await f.text(), person);
      location.hash = href(`/work/processes/${rows[0].process_key}`, { v: String(rows[0].id) });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <section>
      <p className="small"><a href={href("/work")}>← Work</a></p>
      <h1>Processes</h1>
      <p className="muted">
        How work moves between agents and people. Each process is a BPMN diagram: edit a draft, check it, compare it with
        the published version, and publish (needs the workflow admin role). Running items stay on the version they started with.
      </p>
      <NeedName person={person} />
      <Status loading={defs.loading} error={defs.error} />
      {groups.length > 0 && (
        <table className="list">
          <thead><tr><th>Process</th><th>Published</th><th>Draft</th><th>Running</th><th /></tr></thead>
          <tbody>
            {groups.map(([k, vs]) => {
              const pub = vs.find((d) => d.state === "published");
              const draft = vs.find((d) => isOpenDraft(d, vs));
              return (
                <tr key={k}>
                  <td><a href={href(`/work/processes/${k}`)}><strong>{pub?.name ?? vs[0].name ?? k}</strong></a> <span className="mono small muted">{k}</span></td>
                  <td>{pub ? <>v{pub.version} <span className="small muted">{pub.published_at && when(pub.published_at)}</span></> : <span className="muted">not yet</span>}</td>
                  <td>{draft ? <>v{draft.version} <StateBadge state={draft.state} /></> : <span className="muted">—</span>}</td>
                  <td>{vs.reduce((n, d) => n + d.running, 0)}</td>
                  <td className="small">
                    <a href={href(`/work/processes/${k}`)}>Model</a> · <a href={href(`/work/processes/${k}`, { tab: "monitor" })}>Monitor</a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {person && (
        <div className="card">
          <h3>New process</h3>
          <div className="actions">
            <input placeholder="key, e.g. lesson_review" value={key} onChange={(e) => setKey(e.target.value.trim())} />
            <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} />
            <button className="primary" disabled={!keyOk} onClick={create}>Create draft</button>
            <span className="muted small">or</span>
            <button onClick={() => file.current?.click()}>Import .bpmn</button>
            <input ref={file} type="file" accept=".bpmn,.xml" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </div>
          {key && !keyOk && <p className="small error">{taken ? "That key is taken; open it above to edit." : "A key is lower case letters, digits and _."}</p>}
          {error && <p className="small error">{error}</p>}
        </div>
      )}

      <div className="card">
        <div className="row spread">
          <h3>Task forms</h3>
          {person && <a className="small" href={href("/work/forms/new")}>New form</a>}
        </div>
        <Status loading={forms.loading} error={forms.error} empty={!forms.loading && !forms.data?.length}>No forms yet.</Status>
        <ul className="plain">
          {forms.data?.map((f) => (
            <li key={f.key} className="row small">
              <a className="mono" href={href(`/work/forms/${f.key}`)}>{f.key}</a>
              <span className="muted">v{f.version} · <Actor who={f.created_by} /> {when(f.created_at)}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}

// --- one process: model and monitor -------------------------------------------------------------------

export function ProcessPage({ processKey, query }: { processKey: string; query: URLSearchParams }) {
  const tab = query.get("tab") === "monitor" ? "monitor" : "model";
  const defs = useAsync(() => api.workDefinitions(processKey), [processKey]);
  const list = defs.data ?? [];
  const published = list.find((d) => d.state === "published");
  const draft = list.find((d) => isOpenDraft(d, list));
  const chosen = list.find((d) => d.id === Number(query.get("v"))) ?? draft ?? published ?? list[0];
  const title = chosen?.name ?? processKey;

  return (
    <section>
      <p className="small"><a href={href("/work/processes")}>← Processes</a></p>
      <div className="row spread">
        <h1>{title} <span className="mono small muted">{processKey}</span></h1>
        <div className="tabs">
          <a className={tab === "model" ? "on" : ""} href={href(`/work/processes/${processKey}`)}>Model</a>
          <a className={tab === "monitor" ? "on" : ""} href={href(`/work/processes/${processKey}`, { tab: "monitor" })}>Monitor</a>
        </div>
      </div>
      <Status loading={defs.loading && !defs.data} error={defs.error} empty={!defs.loading && !list.length}>
        No process '{processKey}'. <a href={href("/work/processes")}>Create one</a>.
      </Status>
      {chosen && (tab === "model"
        ? <ModelTab key={chosen.id} def={chosen} defs={list} draft={draft} reload={defs.reload} />
        : <MonitorTab processKey={processKey} defs={list} published={published} />)}
    </section>
  );
}

function Versions({ defs, current }: { defs: WorkDefinition[]; current: WorkDefinition }) {
  return (
    <label className="small row">
      Version
      <select value={current.id} onChange={(e) => (location.hash = href(`/work/processes/${current.process_key}`, { v: e.target.value }))}>
        {defs.map((d) => (
          <option key={d.id} value={d.id}>v{d.version} · {d.state}{d.running ? ` · ${d.running} running` : ""}</option>
        ))}
      </select>
    </label>
  );
}

/** The diagram of a version, loaded once: a refresh elsewhere must never reload it under the modeler. */
function useXml(id: number | undefined) {
  const [state, setState] = useState<{ xml?: string; error?: string }>({});
  useEffect(() => {
    if (id === undefined) return setState({});
    let live = true;
    setState({});
    api.workDefinitionXml(id).then((xml) => live && setState({ xml }), (e: Error) => live && setState({ error: e.message }));
    return () => {
      live = false;
    };
  }, [id]);
  return state;
}

function ModelTab({ def, defs, draft, reload }: {
  def: WorkDefinition; defs: WorkDefinition[]; draft?: WorkDefinition; reload: () => void;
}) {
  const [person] = usePerson();
  const admin = useAdmin(person);
  const editable = def.state === "draft" || def.state === "checked";
  const { xml, error: xmlError } = useXml(def.id);
  const roles = useAsync(() => api.workRoles(), []);
  const forms = useAsync(() => api.workForms(), []);
  const keys = useAsync(() => api.workDefinitions(), []);
  const modeler = useRef<Modeler>(undefined);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState("");
  const [msg, setMsg] = useState<{ text: string; error?: boolean }>();
  const [problems, setProblems] = useState<WorkProblem[]>(def.problems ?? []);
  const [compare, setCompare] = useState<WorkCompare>();

  const ctx = useMemo(() => ({
    roles: roles.data ?? { person: [], agent: [] },
    forms: (forms.data ?? []).map((f) => f.key),
    processes: [...new Set((keys.data ?? []).filter((d) => d.state === "published").map((d) => d.process_key))]
      .filter((k) => k !== def.process_key).sort(),
  }), [roles.data, forms.data, keys.data, def.process_key]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const classes = useMemo(() => {
    const out: Record<string, string> = {};
    for (const c of compare?.changes ?? []) if (c.change !== "removed") out[c.element] = `fz-${c.change}`;
    for (const p of problems) if (p.element) out[p.element] = "fz-problem";
    return out;
  }, [problems, compare]);

  const run = async (what: string, fn: () => Promise<void>) => {
    setBusy(what);
    setMsg(undefined);
    try {
      await fn();
    } catch (e) {
      const body = (e as any).body;
      if (body?.problems) setProblems(body.problems);
      setMsg({ text: (e as Error).message, error: true });
    } finally {
      setBusy("");
    }
  };
  const save = async () => {
    const m = modeler.current;
    if (!m) return;
    const { xml } = await m.saveXML({ format: true });
    await api.saveDefinition(def.id, xml!, person);
    setDirty(false);
  };
  const saveIfDirty = async () => {
    if (dirty) await save();
  };

  const onSave = () => run("save", async () => {
    await save();
    setMsg({ text: `Saved draft v${def.version}.` });
    reload();
  });
  const onCheck = () => run("check", async () => {
    await saveIfDirty();
    const r = await api.checkDefinition(def.id, person);
    setProblems(r.problems);
    setMsg(r.problems.length ? { text: `${r.problems.length} problem(s) to fix before publishing.`, error: true }
      : { text: "Checks passed: ready to publish." });
    reload();
  });
  const onCompare = () => run("compare", async () => {
    if (compare) return setCompare(undefined);
    await saveIfDirty();
    setCompare(await api.compareDefinition(def.id));
  });
  const onPublish = () => run("publish", async () => {
    await saveIfDirty();
    const r = await api.publishDefinition(def.id, person);
    setProblems([]);
    setMsg({ text: `Published v${r.version}. New items start on it; running ones stay on their version.` });
    reload();
  });
  const onDraft = (fromId?: number) => run("draft", async () => {
    const d = await api.draftDefinition(def.process_key, person, fromId);
    location.hash = href(`/work/processes/${def.process_key}`, { v: String(d.id) });
    reload();
  });
  const onDownload = () => run("download", async () => {
    const text = modeler.current && editable ? (await modeler.current.saveXML({ format: true })).xml! : xml!;
    download(`${def.process_key}-v${def.version}.bpmn`, text, "application/xml");
  });

  const onReady = useCallback((m: Modeler | undefined) => (modeler.current = m), []);
  const onDirty = useCallback(() => setDirty(true), []);

  return (
    <>
      <div className="work-toolbar">
        <Versions defs={defs} current={def} />
        <StateBadge state={def.state} />
        <span className="small muted">by <Actor who={def.created_by} /> {when(def.created_at)}</span>
        {dirty && <span className="badge soon">unsaved changes</span>}
        <span className="spacer" />
        {editable ? (
          <>
            <button disabled={!person || !!busy || !dirty} onClick={onSave}>Save draft</button>
            <button disabled={!person || !!busy} onClick={onCheck}>Check</button>
            <button disabled={!person || !!busy} onClick={onCompare}>{compare ? "Hide comparison" : "Compare with published"}</button>
            <button className="primary" disabled={!person || !admin || !!busy} onClick={onPublish}
              title={admin ? "" : `Publishing needs the ${ADMIN.replace(/_/g, " ")} role`}>Publish</button>
          </>
        ) : draft ? (
          <a href={href(`/work/processes/${def.process_key}`, { v: String(draft.id) })}>Open draft v{draft.version}</a>
        ) : (
          <button className="primary" disabled={!person || !!busy} onClick={() => onDraft(def.state === "published" ? undefined : def.id)}>
            {def.state === "published" ? "Edit as a new draft" : `New draft from v${def.version}`}
          </button>
        )}
        <button disabled={!xml} onClick={onDownload}>Download .bpmn</button>
      </div>
      {editable && !admin && person && <p className="small muted">You can edit and check drafts; publishing needs the workflow admin role.</p>}
      {msg && <div className={`status small ${msg.error ? "error" : "ok"}`}>{msg.text}</div>}
      <NeedName person={person} />

      {problems.length > 0 && (
        <div className="card problems">
          <h3>Problems</h3>
          <ul className="plain">
            {problems.map((p, i) => (
              <li key={i} className="small">
                {p.element ? <a className="mono" onClick={() => modeler.current && focusElement(modeler.current, p.element!)}>{p.element}</a>
                  : <span className="muted">file</span>}: {p.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {xmlError && <div className="status error">Couldn't load the diagram: {xmlError}</div>}
      {xml && (
        <Suspense fallback={<div className="status">Loading modeler…</div>}>
          {editable ? <BpmnModeler xml={xml} classes={classes} ctx={ctx} onReady={onReady} onDirty={onDirty} />
            : <BpmnView xml={xml} tall />}
        </Suspense>
      )}
      {compare && <Comparison compare={compare} onPick={(id) => modeler.current && focusElement(modeler.current, id)} />}
    </>
  );
}

function Comparison({ compare, onPick }: { compare: WorkCompare; onPick: (id: string) => void }) {
  const { xml } = useXml(compare.against?.id);
  const classes = useMemo(() => Object.fromEntries(compare.changes.filter((c) => c.change !== "added")
    .map((c) => [c.element, c.change === "removed" ? "fz-removed" : "fz-changed"])), [compare]);
  if (!compare.against) return <div className="card"><h3>Comparison</h3><p className="small muted">Nothing published yet: everything is new.</p></div>;
  return (
    <div className="card">
      <h3>Changes against v{compare.against.version} ({compare.against.state})</h3>
      {compare.changes.length === 0 ? <p className="small muted">No changes to what the engine runs (moving shapes doesn't count).</p> : (
        <ul className="plain changes">
          {compare.changes.map((c) => <ChangeRow key={c.element} c={c} onPick={onPick} />)}
        </ul>
      )}
      <div className="legend">
        <span><i className="dot fz-legend-added" /> added</span>
        <span><i className="dot fz-legend-changed" /> changed</span>
        <span><i className="dot fz-legend-removed" /> removed (on the published diagram below)</span>
      </div>
      {xml && (
        <Suspense fallback={<div className="status">Loading diagram…</div>}>
          <BpmnView xml={xml} classes={classes} />
        </Suspense>
      )}
    </div>
  );
}

const show = (v: unknown) => (v === undefined || v === null ? "—" : String(v));

function ChangeRow({ c, onPick }: { c: WorkChange; onPick: (id: string) => void }) {
  return (
    <li className="small">
      <span className={`badge change-${c.change}`}>{c.change}</span>{" "}
      {c.change === "removed" ? <span className="mono">{c.element}</span> : <a className="mono" onClick={() => onPick(c.element)}>{c.element}</a>}
      {c.name && <> {c.name}</>} <span className="muted">({c.type})</span>
      {c.fields && (
        <ul className="plain fields">
          {c.fields.map((f) => <li key={f.field}><span className="muted">{f.field}:</span> <s>{show(f.old)}</s> → <strong>{show(f.new)}</strong></li>)}
        </ul>
      )}
    </li>
  );
}

function MonitorTab({ processKey, defs, published }: { processKey: string; defs: WorkDefinition[]; published?: WorkDefinition }) {
  const [version, setVersion] = useState<number | undefined>();
  const [days, setDays] = useState(30);
  const m = useAsync(() => api.workMonitor(processKey, version, days), [processKey, version, days]);
  const drawn = defs.find((d) => d.id === version) ?? published ?? defs[0];
  const { xml } = useXml(drawn?.id);
  const cells = m.data?.elements ?? {};

  const overlays = useMemo(() => Object.fromEntries(Object.entries(cells).filter(([, c]) => c.waiting || c.done).map(([id, c]) => [id,
    (c.waiting ? `<b>${c.waiting}</b> waiting` : "") + (c.overdue ? ` <span class="late">${c.overdue} overdue</span>` : "") +
    (c.done ? `<span class="done">${c.done} done</span>` : "")])), [cells]);
  const classes = useMemo(() => Object.fromEntries(Object.entries(cells).filter(([, c]) => c.waiting)
    .map(([id, c]) => [id, c.overdue ? "fz-late" : "fz-busy"])), [cells]);
  const rows = Object.entries(cells).sort(([, a], [, b]) => b.waiting - a.waiting || b.done - a.done);
  const running = (m.data?.processes ?? []).filter((p) => p.state === "running").reduce((n, p) => n + p.n, 0);
  const ended = (m.data?.processes ?? []).filter((p) => p.state !== "running");

  return (
    <>
      <div className="work-toolbar">
        <label className="small row">
          Version
          <select value={version ?? ""} onChange={(e) => setVersion(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">all versions</option>
            {defs.filter((d) => d.state === "published" || d.state === "superseded").map((d) => (
              <option key={d.id} value={d.id}>v{d.version} · {d.state}{d.running ? ` · ${d.running} running` : ""}</option>
            ))}
          </select>
        </label>
        <label className="small row">
          Finished in the last
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[1, 7, 30, 90, 365].map((d) => <option key={d} value={d}>{d === 1 ? "day" : `${d} days`}</option>)}
          </select>
        </label>
        <span className="small muted">{running} running{drawn && version === undefined ? ` · drawn on v${drawn.version}` : ""}</span>
        <button onClick={m.reload}>Refresh</button>
      </div>
      <Status loading={m.loading && !m.data} error={m.error} />
      {xml && (
        <Suspense fallback={<div className="status">Loading diagram…</div>}>
          <BpmnView xml={xml} classes={classes} overlays={overlays} tall />
        </Suspense>
      )}
      <div className="legend">
        <span><i className="dot fz-legend-busy" /> work waiting</span>
        <span><i className="dot fz-legend-late" /> some of it overdue</span>
      </div>
      {m.data && (
        <div className="card">
          <h3>Steps</h3>
          {rows.length === 0 ? <p className="small muted">Nothing waiting and nothing finished in this period.</p> : (
            <table className="list">
              <thead><tr><th>Step</th><th>Waiting</th><th>Overdue</th><th>Oldest wait</th><th>Done ({days} d)</th><th>Median time</th></tr></thead>
              <tbody>
                {rows.map(([id, c]) => (
                  <tr key={id}>
                    <td>{c.name ?? <span className="mono">{id}</span>}</td>
                    <td>{c.waiting || ""}</td>
                    <td>{c.overdue ? <span className="badge late">{c.overdue}</span> : ""}</td>
                    <td>{c.oldest ? span(Date.now() - new Date(c.oldest).getTime()) : ""}</td>
                    <td>{c.done || ""}</td>
                    <td>{c.median_seconds != null ? span(c.median_seconds * 1000) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {ended.length > 0 && (
            <p className="small muted">
              Ended in the last {days} d: {ended.map((p) => `${p.n} ${p.outcome ?? p.state} (v${p.version})`).join(", ")}
            </p>
          )}
        </div>
      )}
    </>
  );
}

// --- task forms -------------------------------------------------------------------------------------

const BLANK_FORM = (key: string) => ({ id: key, type: "default", schemaVersion: 19, components: [] });

export function FormPage({ formKey, query }: { formKey: string; query: URLSearchParams }) {
  const isNew = formKey === "new";
  const [person] = usePerson();
  const [newKey, setNewKey] = useState(query.get("key") ?? "");
  const [schema, setSchema] = useState<Record<string, unknown>>();
  const [loadError, setLoadError] = useState<string>();
  const [version, setVersion] = useState<number>();
  const [bulk, setBulk] = useState("");
  const [dirty, setDirty] = useState(false);
  const [msg, setMsg] = useState<{ text: string; error?: boolean }>();
  const saver = useRef<(() => Record<string, unknown>) | undefined>(undefined);

  useEffect(() => {
    setLoadError(undefined);
    setDirty(false);
    if (isNew) {
      setSchema(BLANK_FORM("new"));
      setBulk("");
      return;
    }
    api.workForm(formKey).then((f) => {
      setSchema(f.schema);
      setVersion(f.version);
      const b = (f.schema as any).fuzer?.bulk;
      setBulk(b ? JSON.stringify(b, null, 2) : "");
    }, (e: Error) => setLoadError(e.message));
  }, [formKey, isNew]);

  const key = isNew ? newKey : formKey;
  const keyOk = /^[a-z0-9][a-z0-9_-]*$/.test(key);
  const save = async () => {
    setMsg(undefined);
    try {
      const out = { ...saver.current!(), id: key } as Record<string, any>;
      const fuzer = { ...(out.fuzer ?? {}) };
      if (bulk.trim()) fuzer.bulk = JSON.parse(bulk);
      else delete fuzer.bulk;
      if (Object.keys(fuzer).length) out.fuzer = fuzer;
      else delete out.fuzer;
      const r = await api.saveForm(out, person);
      setDirty(false);
      if (isNew) location.hash = href(`/work/forms/${r.key}`);
      else {
        setVersion(r.version);
        setMsg({ text: `Saved v${r.version}. Tasks use the latest version from now on.` });
      }
    } catch (e) {
      setMsg({ text: e instanceof SyntaxError ? `Bulk actions aren't valid JSON: ${e.message}` : (e as Error).message, error: true });
    }
  };
  const onReady = useCallback((s: (() => Record<string, unknown>) | undefined) => (saver.current = s), []);
  const onDirty = useCallback(() => setDirty(true), []);

  return (
    <section>
      <p className="small"><a href={href("/work/processes")}>← Processes</a></p>
      <div className="row spread">
        <h1>{isNew ? "New form" : <>Form <span className="mono">{formKey}</span> <span className="muted small">v{version}</span></>}</h1>
        <div className="actions">
          {dirty && <span className="badge soon">unsaved changes</span>}
          <button className="primary" disabled={!person || !keyOk || !schema} onClick={save}>{isNew ? "Create form" : "Save new version"}</button>
        </div>
      </div>
      <p className="muted small">
        A person's task shows this form. Each field's key becomes process data that the next conditions read (for example
        <code> approval == "approved"</code>). Required fields must be filled to complete the task.
      </p>
      <NeedName person={person} />
      {isNew && (
        <div className="actions">
          <input placeholder="form key, e.g. lesson-review" value={newKey} onChange={(e) => setNewKey(e.target.value.trim())} />
          {newKey && !keyOk && <span className="small error">Lower case letters, digits, - and _.</span>}
        </div>
      )}
      {msg && <div className={`status small ${msg.error ? "error" : "ok"}`}>{msg.text}</div>}
      {loadError && <div className="status error">Couldn't load the form: {loadError}</div>}
      {schema && (
        <Suspense fallback={<div className="status">Loading form editor…</div>}>
          <FormEditor schema={schema} onReady={onReady} onDirty={onDirty} />
        </Suspense>
      )}
      <details className="card small">
        <summary>Bulk actions {bulk.trim() ? "(set)" : ""}</summary>
        <p className="muted">
          Low-risk ways to complete many tasks at once from the inbox, e.g. <code>[{`{"label": "Close as false alarm", "data": {"triage": "false_alarm"}, "reason_key": "triage_note"}`}]</code>.
          Never offered on a step that needs a person.
        </p>
        <textarea className="mono" rows={5} value={bulk} onChange={(e) => { setBulk(e.target.value); setDirty(true); }} />
      </details>
    </section>
  );
}
