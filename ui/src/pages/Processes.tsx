import { Button, PageHeader, Table, Tabs, Card as UICard, Field, Input, Select , Badge as UIBadge, Disclosure } from "../ui";
// Processes and task forms (#97): the list of process definitions, the modeler page (draft → checked →
// published, compared with the published version), the process monitor (work waiting at each step,
// drawn on the diagram) and the form-js form editor.

import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type Modeler from "bpmn-js/lib/Modeler";
import { api, type ProcessStats, type WorkChange, type WorkCompare, type WorkDefinition, type WorkProblem } from "../api";
import { caseHref, taskHref, useAct } from "./workflows/shared";
import { Status, href, useAsync } from "../lib";
import { Actor, Due, holder, span, usePerson, when } from "../work";
import { blankProcess, focusElement } from "../bpmnSubset";

const BpmnModeler = lazy(() => import("../BpmnModeler"));
const BpmnView = lazy(() => import("../BpmnView"));
const FormEditor = lazy(() => import("../FormEditor"));
const TaskForm = lazy(() => import("../TaskForm"));

const ADMIN = "workflow_admin";
const isOpenDraft = (d: WorkDefinition, defs: WorkDefinition[]) =>
  (d.state === "draft" || d.state === "checked") &&
  d.version > Math.max(0, ...defs.filter((x) => x.state === "published" || x.state === "superseded").map((x) => x.version));

function StateBadge({ state }: { state: string }) {
  const cls = state === "published" ? "ok" : state === "checked" ? "soon" : state === "draft" ? "human" : "";
  return <UIBadge className={`badge ${cls}`}>{state}</UIBadge>;
}

function useAdmin(person: string) {
  const people = useAsync(() => api.workPeople(), []);
  return !!people.data?.find((p) => p.name === person)?.roles.includes(ADMIN);
}

function NeedName({ person }: { person: string }) {
  if (person) return null;
  return <div className="status">Say who you are in the sidebar (bottom left) to edit processes.</div>;
}

const download = (name: string, text: string, type: string) => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  URL.revokeObjectURL(a.href);
};

// --- list ---------------------------------------------------------------------------------------------

const hours = (h: number | null) => (h == null ? "–" : h < 48 ? `${h} h` : `${Math.round(h / 24)} d`);

export function ProcessesPage() {
  const defs = useAsync(() => api.workDefinitions(), []);
  const stats = useAsync(() => api.workStats(), []);
  const statOf = (k: string): ProcessStats | undefined => stats.data?.find((x) => x.process_key === k);
  const forms = useAsync(() => api.workForms(), []);
  const [person] = usePerson();
  const admin = useAdmin(person);
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);

  const groups = useMemo(() => {
    const by = new Map<string, WorkDefinition[]>();
    for (const d of defs.data ?? []) by.set(d.process_key, [...(by.get(d.process_key) ?? []), d]);
    return [...by.entries()];
  }, [defs.data]);
  const taken = groups.some(([k]) => k === key);
  const keyOk = /^[a-z][a-z0-9_]*$/.test(key) && !taken;
  const keyProblem = !key || keyOk ? null : taken ? "That key is taken. Open it in the list to change it."
    : "Use lower case letters, digits and _, starting with a letter.";

  const create = async () => {
    setError(undefined);
    setBusy("Creating the draft…");
    try {
      await api.importDefinition(blankProcess(key, name || key), person);
      location.hash = href(`/processes/${key}`, { tab: "model" });
    } catch (e) {
      setError(`Couldn't create the draft: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };
  const upload = async (f: File) => {
    setError(undefined);
    setBusy(`Importing ${f.name}…`);
    try {
      const rows = await api.importDefinition(await f.text(), person);
      location.hash = href(`/processes/${rows[0].process_key}`, { tab: "model", v: String(rows[0].id) });
    } catch (e) {
      setError(`Couldn't import ${f.name}: ${(e as Error).message}. Fix the file and import it again.`);
    } finally {
      setBusy(null);
      if (file.current) file.current.value = "";
    }
  };

  return (
    <section>
      <PageHeader subtitle="How work moves between people and agents." help="workflow">Processes</PageHeader>
      <p className="small muted" id="who-edits">Anyone who says who they are can draft a change and check it.
        Publishing it needs the workflow admin role{person ? (admin ? ", which you have" : ", which you don't have") : ""}.
        Which queue each type of work goes to is set on <a href={href("/routing")}>Routing</a>, not here.</p>
      <NeedName person={person} />
      <Status loading={defs.loading && !defs.data} error={defs.error} onRetry={defs.reload}
        empty={!defs.loading && !defs.error && !groups.length}>
        No processes yet. {person ? "Create one below, or import a .bpmn file." : "Say who you are to create one."}
      </Status>
      {stats.error && <p className="small muted" role="status">Running counts are unavailable: {stats.error}{" "}
        <Button size="sm" onClick={stats.reload}>Try again</Button></p>}
      {groups.length > 0 && (
        <Table variant="list" className="phone-rows" aria-label="Processes">
          <thead><tr><th>Process</th><th>Published</th><th>Draft</th><th>Running</th><th>Steps waiting</th><th>Overdue</th>
            <th title="last 30 days">Cycle time (median / p90)</th><th title="person steps past their SLA, last 30 days">SLA missed</th><th>Open</th></tr></thead>
          <tbody>
            {groups.map(([k, vs]) => {
              const pub = vs.find((d) => d.state === "published");
              const draft = vs.find((d) => isOpenDraft(d, vs));
              const st = statOf(k);
              const label = pub?.name ?? vs[0].name ?? k;
              return (
                <tr key={k}>
                  <td data-label="Process"><a href={href(`/processes/${k}`)}><strong>{label}</strong></a> <span className="mono small muted">{k}</span></td>
                  <td data-label="Published">{pub ? <>v{pub.version} <span className="small muted">{pub.published_at && when(pub.published_at)}</span></> : <span className="muted">not yet</span>}</td>
                  <td data-label="Draft">{draft ? <>v{draft.version} <StateBadge state={draft.state} /></> : <span className="muted">none</span>}</td>
                  <td data-label="Running"><a href={href(`/processes/${k}`, { tab: "running" })} aria-label={`${label}: running cases`}>{vs.reduce((n, d) => n + d.running, 0)}</a></td>
                  <td data-label="Steps waiting">{st?.waiting ?? "–"}</td>
                  <td data-label="Overdue">{st?.overdue ? <UIBadge className="badge late">{st.overdue}</UIBadge> : st ? 0 : "–"}</td>
                  <td data-label="Cycle time" className="small">{st ? `${hours(st.median_h)} / ${hours(st.p90_h)}` : "–"}</td>
                  <td data-label="SLA missed" className="small">{st?.sla_breach_pct != null ? `${st.sla_breach_pct}%` : "–"}</td>
                  <td data-label="Open" className="small">
                    <a href={href(`/processes/${k}`, { tab: "model" })} aria-label={`${label}: model`}>Model</a> · <a href={href(`/processes/${k}`, { tab: "monitor" })} aria-label={`${label}: monitor`}>Monitor</a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      )}

      {person && (
        <UICard title="New process">
          <p className="small muted">A new process starts as a draft. Nothing runs on it until it is checked and published.</p>
          <div className="actions">
            <Field label="Key" hint="lower case, e.g. lesson_review">
              <Input value={key} aria-invalid={!!keyProblem} aria-describedby={keyProblem ? "new-key-problem" : undefined}
                onChange={(e) => setKey(e.target.value.trim())} />
            </Field>
            <Field label="Name" hint="what people read, e.g. Lesson review">
              <Input value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Button variant="primary" disabled={!keyOk || !!busy} onClick={create}>Create draft</Button>
            <span className="muted small">or</span>
            <Button disabled={!!busy} onClick={() => file.current?.click()}>Import .bpmn</Button>
            <Input ref={file} type="file" accept=".bpmn,.xml" hidden aria-label="BPMN file to import"
              onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
          </div>
          {keyProblem && <p className="small error" id="new-key-problem" role="alert">{keyProblem}</p>}
          {busy && <p className="small" role="status">{busy}</p>}
          {error && <p className="small error" role="alert">{error}</p>}
        </UICard>
      )}

      <UICard title="Task forms" actions={person ? <a className="small" href={href("/processes/forms/new")}>New form</a> : undefined}>
        <Status loading={forms.loading && !forms.data} error={forms.error} onRetry={forms.reload}
          empty={!forms.loading && !forms.error && !forms.data?.length}>
          No forms yet. A person's step without a form asks only for a decision and a note.</Status>
        <ul className="plain">
          {forms.data?.map((f) => (
            <li key={f.key} className="row small">
              <a className="mono" href={href(`/processes/forms/${f.key}`)}>{f.key}</a>
              <span className="muted">v{f.version} · <Actor who={f.created_by} /> {when(f.created_at)}</span>
            </li>
          ))}
        </ul>
      </UICard>
    </section>
  );
}

// --- one process: model and monitor -------------------------------------------------------------------

const PROCESS_TABS = [["overview", "Overview"], ["running", "Running"], ["model", "Model"], ["monitor", "Monitor"],
                      ["routing", "SLAs & routing"]] as const;

export function ProcessPage({ processKey, query }: { processKey: string; query: URLSearchParams }) {
  const asked = query.get("tab");
  const tab = PROCESS_TABS.some(([k]) => k === asked) ? asked! : "overview";
  const defs = useAsync(() => api.workDefinitions(processKey), [processKey]);
  const list = defs.data ?? [];
  const published = list.find((d) => d.state === "published");
  const draft = list.find((d) => isOpenDraft(d, list));
  const chosen = list.find((d) => d.id === Number(query.get("v"))) ?? draft ?? published ?? list[0];
  const title = chosen?.name ?? processKey;

  return (
    <section>
      <PageHeader subtitle={chosen && <WhereYouAre chosen={chosen} published={published} draft={draft} />}
        actions={<a href={href("/processes")}>All processes</a>}>
        {title} <span className="mono small muted">{processKey}</span></PageHeader>
      <Tabs label="Process views" items={PROCESS_TABS.map(([k, label]) => ({
        key: k, label, href: href(`/processes/${processKey}`, { tab: k }), current: tab === k }))} />
      <Status loading={defs.loading && !defs.data} error={defs.error} onRetry={defs.reload} empty={!defs.loading && !defs.error && !list.length}>
        No process '{processKey}'. <a href={href("/processes")}>Create one</a>.
      </Status>
      {chosen && tab === "overview" && <OverviewTab processKey={processKey} defs={list} published={published} />}
      {chosen && tab === "running" && <RunningTab processKey={processKey} />}
      {chosen && tab === "model" && <ModelTab key={chosen.id} def={chosen} defs={list} draft={draft} reload={defs.reload} />}
      {chosen && tab === "monitor" && <MonitorTab processKey={processKey} defs={list} published={published} />}
      {chosen && tab === "routing" && <RoutingTab def={published ?? chosen} />}
    </section>
  );
}

/** Which version the page shows and how it relates to what runs, so a change is never made to the wrong one. */
function WhereYouAre({ chosen, published, draft }: { chosen: WorkDefinition; published?: WorkDefinition; draft?: WorkDefinition }) {
  const pub = published ? `v${published.version} is published${published.running ? `, with ${published.running} running` : ""}` : "nothing is published yet";
  const which = chosen.state === "published" ? `You're looking at v${chosen.version}, the published version`
    : chosen.state === "draft" || chosen.state === "checked" ? `You're looking at draft v${chosen.version} (${chosen.state === "checked" ? "checked" : "not checked yet"})`
    : `You're looking at v${chosen.version}, ${chosen.state}`;
  const more = chosen.state === "published" && draft ? `; draft v${draft.version} is open` : "";
  return <span data-testid="where-you-are">{which}{chosen.state === "published" ? "" : `; ${pub}`}{more}.</span>;
}

/** What each step of a change does, with the step this version is at marked. */
function Lifecycle({ state }: { state: string }) {
  const at = state === "draft" ? 0 : state === "checked" ? 1 : 2;
  const steps = [["Draft", "Save changes. Nothing runs on a draft."], ["Check", "Find problems that stop it running, such as a step with no SLA."],
                 ["Publish", "New cases start on it. Cases already running stay on their version."]];
  return <ol className="lifecycle small" aria-label="How a change goes live">
    {steps.map(([name, what], i) => <li key={name} aria-current={i === at ? "step" : undefined} className={i === at ? "on" : i < at ? "done" : ""}>
      <strong>{name}</strong> <span className="muted">{what}</span></li>)}
  </ol>;
}

function Versions({ defs, current }: { defs: WorkDefinition[]; current: WorkDefinition }) {
  return (
    <label className="small row">
      Version
      <Select value={current.id} onChange={(e) => (location.hash = href(`/processes/${current.process_key}`, { v: e.target.value }))}>
        {defs.map((d) => (
          <option key={d.id} value={d.id}>v{d.version} · {d.state}{d.running ? ` · ${d.running} running` : ""}</option>
        ))}
      </Select>
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
  const [canvasXml, setCanvasXml] = useState<string>();
  const [savedXml, setSavedXml] = useState<string>();
  const [description, setDescription] = useState("");
  const [proposal, setProposal] = useState<Awaited<ReturnType<typeof api.generateWorkflow>>>();
  const [previousCanvas, setPreviousCanvas] = useState<string>();
  const [ready, setReady] = useState(false);
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
    setSavedXml(xml);
    setDirty(false);
    setPreviousCanvas(undefined);
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
    location.hash = href(`/processes/${def.process_key}`, { tab: "model", v: String(d.id) });
    reload();
  });
  const onDownload = () => run("download", async () => {
    const text = modeler.current && editable ? (await modeler.current.saveXML({ format: true })).xml! : savedXml ?? xml!;
    download(`${def.process_key}-v${def.version}.bpmn`, text, "application/xml");
  });

  const onGenerate = () => run("generate", async () => {
    setProposal(undefined);
    setProposal(await api.generateWorkflow(def.id, description, person));
  });
  const onApply = () => run("review", async () => {
    if (!proposal || !modeler.current) return;
    if (!window.confirm("Replace the current canvas with this proposal? It will remain unsaved until you save the draft.")) return;
    setPreviousCanvas((await modeler.current.saveXML({ format: true })).xml!);
    setReady(false);
    setCanvasXml(proposal.xml);
    setProblems(proposal.problems);
    setCompare(undefined);
    setDirty(true);
    setProposal(undefined);
    setMsg({ text: "Agent draft loaded for review. Adjust steps, parameters and connections, then save and check before publishing." });
  });
  const onReady = useCallback((m: Modeler | undefined) => {
    modeler.current = m;
    setReady(!!m);
  }, []);
  const onDirty = useCallback(() => setDirty(true), []);

  return (
    <>
      <div className="work-toolbar">
        <Versions defs={defs} current={def} />
        <StateBadge state={def.state} />
        <span className="small muted">by <Actor who={def.created_by} /> {when(def.created_at)}</span>
        {dirty && <UIBadge className="badge soon">unsaved changes</UIBadge>}
        <span className="spacer" />
        {editable ? (
          <>
            <Button disabled={!person || !!busy || !dirty || !ready} onClick={onSave}>Save draft</Button>
            <Button disabled={!person || !!busy || !ready} onClick={onCheck}>Check</Button>
            <Button disabled={!person || !!busy || !ready} onClick={onCompare}>{compare ? "Hide comparison" : "Compare with published"}</Button>
            <Button variant="primary" disabled={!person || !admin || !!busy || !ready} onClick={onPublish}
              title={admin ? "" : `Publishing needs the ${ADMIN.replace(/_/g, " ")} role`}>Publish</Button>
          </>
        ) : draft ? (
          <a href={href(`/processes/${def.process_key}`, { v: String(draft.id) })}>Open draft v{draft.version}</a>
        ) : (
          <Button variant="primary" disabled={!person || !!busy} onClick={() => onDraft(def.state === "published" ? undefined : def.id)}>
            {def.state === "published" ? "Edit as a new draft" : `New draft from v${def.version}`}
          </Button>
        )}
        <Button disabled={!xml} onClick={onDownload}>Download .bpmn</Button>
      </div>
      {editable && <Lifecycle state={def.state} />}
      {editable && !admin && person && <p className="small muted">You can edit and check drafts; publishing needs the workflow admin role.</p>}
      {msg && <div className={`status small ${msg.error ? "error" : "ok"}`} role={msg.error ? "alert" : "status"}>{msg.text}</div>}
      <NeedName person={person} />
      {editable && <UICard className="workflow-agent">
        <h3>Workflow drafting agent</h3>
        <p className="small muted">Describe the workflow to draft. Review the proposal before replacing the canvas;
          nothing is saved or run automatically. BPMN is maintained behind the canvas; import and download remain available.</p>
        <label className="prop">
          <span>Workflow description</span>
          <textarea rows={3} maxLength={12000} value={description} onChange={(e) => setDescription(e.target.value)}
            placeholder="Detect anomalies, inspect changed data, then ask an engineer to review the evidence…" />
        </label>
        <div className="actions">
          <Button disabled={!person || !!busy || !description.trim()} onClick={onGenerate}>
            {busy === "generate" ? "Drafting…" : "Draft workflow"}
          </Button>
          {previousCanvas && <Button disabled={!!busy} onClick={() => {
            setReady(false);
            setCanvasXml(previousCanvas);
            setPreviousCanvas(undefined);
            setProblems([]);
            setCompare(undefined);
            setDirty(true);
            setMsg({ text: "Previous canvas restored. Review it before saving." });
          }}>Restore previous canvas</Button>}
        </div>
        {proposal && <>
          <p className="small">{proposal.steps} steps · {proposal.connections} connections. Inspect the proposal below,
            then review and refine it on the canvas.</p>
          {!!proposal.problems.length && <div className="status small error">
            <strong>Fix before publishing:</strong>
            <ul>{proposal.problems.map((p, i) => <li key={i}>{p.element}: {p.message}</li>)}</ul>
          </div>}
          <Suspense fallback={<div className="status">Loading proposal…</div>}><BpmnView xml={proposal.xml} /></Suspense>
          <div className="actions">
            <Button variant="primary" disabled={!!busy || !ready} onClick={onApply}>Review on canvas</Button>
            <Button disabled={!!busy} onClick={() => setProposal(undefined)}>Discard proposal</Button>
          </div>
        </>}
      </UICard>}

      {problems.length > 0 && (
        <UICard className="problems">
          <h3>Problems</h3>
          <ul className="plain">
            {problems.map((p, i) => (
              <li key={i} className="small">
                {p.element ? <Button variant="link" className="mono" aria-label={`Show ${p.element} on the diagram`}
                  onClick={() => modeler.current && focusElement(modeler.current, p.element!)}>{p.element}</Button>
                  : <span className="muted">file</span>}: {p.message}
              </li>
            ))}
          </ul>
        </UICard>
      )}

      {xmlError && <div className="status error" role="alert">Couldn't load the diagram: {xmlError}{" "}
        <Button onClick={() => location.reload()}>Reload the page</Button></div>}
      {xml && (
        <Suspense fallback={<div className="status">Loading modeler…</div>}>
          {editable ? <BpmnModeler xml={canvasXml ?? xml} classes={classes} ctx={ctx} onReady={onReady} onDirty={onDirty} />
            : <BpmnView xml={savedXml ?? xml} tall />}
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
  if (!compare.against) return <UICard className=""><h3>Comparison</h3><p className="small muted">Nothing published yet: everything is new.</p></UICard>;
  return (
    <UICard className="">
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
    </UICard>
  );
}

const show = (v: unknown) => (v === undefined || v === null ? "—" : String(v));

function ChangeRow({ c, onPick }: { c: WorkChange; onPick: (id: string) => void }) {
  return (
    <li className="small">
      <UIBadge className={`badge change-${c.change}`}>{c.change}</UIBadge>{" "}
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
          <Select value={version ?? ""} onChange={(e) => setVersion(e.target.value ? Number(e.target.value) : undefined)}>
            <option value="">all versions</option>
            {defs.filter((d) => d.state === "published" || d.state === "superseded").map((d) => (
              <option key={d.id} value={d.id}>v{d.version} · {d.state}{d.running ? ` · ${d.running} running` : ""}</option>
            ))}
          </Select>
        </label>
        <label className="small row">
          Finished in the last
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[1, 7, 30, 90, 365].map((d) => <option key={d} value={d}>{d === 1 ? "day" : `${d} days`}</option>)}
          </Select>
        </label>
        <span className="small muted">{running} running{drawn && version === undefined ? ` · drawn on v${drawn.version}` : ""}</span>
        <Button onClick={m.reload}>Refresh</Button>
      </div>
      <Status loading={m.loading && !m.data} error={m.error} onRetry={m.reload} />
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
        <UICard className="">
          <h3>Steps</h3>
          {rows.length === 0 ? <p className="small muted">Nothing waiting and nothing finished in this period.</p> : (
            <Table variant="list">
              <thead><tr><th>Step</th><th>Waiting</th><th>Overdue</th><th>Oldest wait</th><th>Done ({days} d)</th><th>Median time</th></tr></thead>
              <tbody>
                {rows.map(([id, c]) => (
                  <tr key={id}>
                    <td>{c.name ?? <span className="mono">{id}</span>}</td>
                    <td>{c.waiting || ""}</td>
                    <td>{c.overdue ? <UIBadge className="badge late">{c.overdue}</UIBadge> : ""}</td>
                    <td>{c.oldest ? span(Date.now() - new Date(c.oldest).getTime()) : ""}</td>
                    <td>{c.done || ""}</td>
                    <td>{c.median_seconds != null ? span(c.median_seconds * 1000) : ""}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
          {ended.length > 0 && (
            <p className="small muted">
              Ended in the last {days} d: {ended.map((p) => `${p.n} ${p.outcome ?? p.state} (v${p.version})`).join(", ")}
            </p>
          )}
        </UICard>
      )}
    </>
  );
}

// --- task forms -------------------------------------------------------------------------------------

const BLANK_FORM = (key: string) => ({ id: key, type: "default", schemaVersion: 19, components: [] });

/** Bulk actions are JSON; say what is wrong with them before a save is tried. */
function bulkProblem(text: string): string | null {
  if (!text.trim()) return null;
  try {
    const v = JSON.parse(text);
    return v && typeof v === "object" ? null : "Bulk actions must be a JSON object or list.";
  } catch (e) {
    return `Bulk actions aren't valid JSON: ${(e as Error).message}`;
  }
}

export function FormPage({ formKey, query }: { formKey: string; query: URLSearchParams }) {
  const isNew = formKey === "new";
  const [person] = usePerson();
  const [newKey, setNewKey] = useState(query.get("key") ?? "");
  const [schema, setSchema] = useState<Record<string, unknown>>();
  const [loadError, setLoadError] = useState<{ text: string; missing: boolean }>();
  const [tries, setTries] = useState(0);
  const [version, setVersion] = useState<number>();
  const [bulk, setBulk] = useState("");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [preview, setPreview] = useState<Record<string, unknown>>();
  const [msg, setMsg] = useState<{ text: string; error?: boolean }>();
  const saver = useRef<(() => Record<string, unknown>) | undefined>(undefined);

  useEffect(() => {
    setLoadError(undefined);
    setDirty(false);
    setPreview(undefined);
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
    }, (e: Error & { status?: number }) => setLoadError({ text: e.message, missing: e.status === 404 }));
  }, [formKey, isNew, tries]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const key = isNew ? newKey : formKey;
  const keyOk = /^[a-z0-9][a-z0-9_-]*$/.test(key);
  const keyProblem = isNew && newKey && !keyOk ? "Use lower case letters, digits, - and _, starting with a letter or digit." : null;
  const badBulk = bulkProblem(bulk);
  const save = async () => {
    setMsg(undefined);
    setSaving(true);
    try {
      const out = { ...saver.current!(), id: key } as Record<string, any>;
      const fuzer = { ...(out.fuzer ?? {}) };
      if (bulk.trim()) fuzer.bulk = JSON.parse(bulk);
      else delete fuzer.bulk;
      if (Object.keys(fuzer).length) out.fuzer = fuzer;
      else delete out.fuzer;
      const r = await api.saveForm(out, person);
      setDirty(false);
      if (isNew) location.hash = href(`/processes/forms/${r.key}`);
      else {
        setVersion(r.version);
        setMsg({ text: `Saved v${r.version}. Steps that use this form show v${r.version} from now on; steps already filled in keep what was entered.` });
      }
    } catch (e) {
      setMsg({ text: `Couldn't save: ${(e as Error).message}. Your changes are still here; try again.`, error: true });
    } finally {
      setSaving(false);
    }
  };
  const showPreview = () => {
    if (preview) return setPreview(undefined);
    if (saver.current) setPreview(saver.current());
  };
  const onReady = useCallback((s: (() => Record<string, unknown>) | undefined) => (saver.current = s), []);
  const onDirty = useCallback(() => { setDirty(true); setPreview(undefined); }, []);

  return (
    <section>
      <PageHeader actions={<a href={href("/processes")}>All processes and forms</a>}
        subtitle={isNew ? "A form is what a person fills in on a process step. Name it, add fields, then create it."
          : version != null ? `v${version}. Saving makes a new version; earlier versions stay with the steps that used them.` : undefined}>
        {isNew ? "New form" : <>Form <span className="mono">{formKey}</span></>}</PageHeader>
      <NeedName person={person} />
      {isNew && (
        <div className="actions">
          <Field label="Form key" hint="what a process step names, e.g. lesson-review">
            <Input value={newKey} aria-invalid={!!keyProblem} onChange={(e) => setNewKey(e.target.value.trim())} />
          </Field>
        </div>
      )}
      {keyProblem && <p className="small error" role="alert">{keyProblem}</p>}
      {loadError && (loadError.missing
        ? <div className="status error" role="alert">There is no form “{formKey}”.{" "}
            <a href={href("/processes/forms/new", { key: formKey })}>Create it</a></div>
        : <div className="status error" role="alert">Couldn't load the form: {loadError.text}{" "}
            <Button onClick={() => setTries((n) => n + 1)}>Try again</Button></div>)}
      {schema && <>
        <div className="work-toolbar">
          {dirty && <UIBadge className="badge soon">unsaved changes</UIBadge>}
          <span className="spacer" />
          <Button aria-pressed={!!preview} onClick={showPreview}>{preview ? "Back to editing" : "Preview"}</Button>
          <Button variant="primary" disabled={!person || !keyOk || !!badBulk || saving} onClick={save}>
            {saving ? "Saving…" : isNew ? "Create form" : "Save new version"}</Button>
        </div>
        {msg && <div className={`status small ${msg.error ? "error" : "ok"}`} role={msg.error ? "alert" : "status"}>{msg.text}</div>}
        {preview && <UICard title="Preview" className="form-preview">
          <p className="small muted">How a person sees it on their step. Nothing you enter here is kept.</p>
          <Suspense fallback={<div className="status">Loading preview…</div>}>
            <TaskForm schema={preview} data={{}} onSubmit={() => undefined} />
          </Suspense>
        </UICard>}
        <div hidden={!!preview}>
          <Suspense fallback={<div className="status">Loading form editor…</div>}>
            <FormEditor schema={schema} onReady={onReady} onDirty={onDirty} />
          </Suspense>
        </div>
        <Disclosure className="card small" label={<>Bulk actions {bulk.trim() ? "(set)" : ""}</>}>
          <label className="prop">
            <span>Bulk actions (JSON): the decisions a lead can apply to many tasks at once</span>
            <textarea className="mono" rows={5} value={bulk} aria-invalid={!!badBulk}
              onChange={(e) => { setBulk(e.target.value); setDirty(true); }} />
          </label>
          {badBulk && <p className="small error" role="alert">{badBulk}</p>}
        </Disclosure>
      </>}
    </section>
  );
}


// --- overview, running cases, SLAs & routing --------------------------------------------------------------

function OverviewTab({ processKey, defs, published }: { processKey: string; defs: WorkDefinition[]; published?: WorkDefinition }) {
  const stats = useAsync(() => api.workStats(), []);
  const s = stats.data?.find((x) => x.process_key === processKey);
  const facts: [string, ReactNode][] = [
    ["Published", published ? <>v{published.version} {published.published_at && <span className="muted">{when(published.published_at)}</span>}</> : "not yet"],
    ["Versions", defs.map((d) => `v${d.version} ${d.state}`).join(", ")],
    ["Running", s ? <a href={href(`/processes/${processKey}`, { tab: "running" })}>{s.running} cases</a> : "–"],
    ["Steps waiting", s ? <>{s.waiting}{s.overdue ? <> (<UIBadge className="badge late">{s.overdue} overdue</UIBadge>)</> : null}</> : "–"],
    ["Finished (30 days)", s?.finished ?? "–"],
    ["Cycle time (30 days)", s ? `median ${hours(s.median_h)}, 90% within ${hours(s.p90_h)}` : "–"],
    ["SLA missed (30 days)", s?.sla_breach_pct != null ? `${s.sla_breach_pct}% of ${s.steps} person steps` : "no person steps yet"],
  ];
  return (
    <>
      <Table variant="keyValue"><tbody>{facts.map(([k, v]) => <tr key={k}><th>{k}</th><td>{v}</td></tr>)}</tbody></Table>
      <p className="small muted">Where work piles up: <a href={href(`/processes/${processKey}`, { tab: "monitor" })}>Monitor</a>. Who does each step
        and when it escalates: <a href={href(`/processes/${processKey}`, { tab: "routing" })}>SLAs &amp; routing</a>.</p>
    </>
  );
}

function RunningTab({ processKey }: { processKey: string }) {
  const [state, setState] = useState("running");
  const list = useAsync(() => api.workInstances(processKey, state), [processKey, state]);
  const [person] = usePerson();
  const a = useAct();
  return (
    <>
      <div className="tabs small">
        {["running", "completed", "cancelled", "all"].map((s) => <a key={s} className={state === s ? "on" : ""} onClick={() => setState(s)}>{s}</a>)}
      </div>
      <Status loading={list.loading && !list.data} error={list.error} onRetry={list.reload} empty={!!list.data && !list.data.length}>None.</Status>
      {list.data && list.data.length > 0 && (
        <Table variant="results">
          <thead><tr><th>Case</th><th>Waiting on</th><th>Age</th><th>Version</th><th>Started by</th><th /></tr></thead>
          <tbody>
            {list.data.map((i) => (
              <tr key={i.id}>
                <td><a href={caseHref(i.item_id)}>{i.title}</a>{i.state !== "running" && <UIBadge className="badge"> {i.state}{i.outcome ? `: ${i.outcome}` : ""}</UIBadge>}</td>
                <td className="small">
                  {i.open_steps.map((t) => (
                    <div key={t.task_id}>
                      {t.kind === "agent" ? t.step : <a href={taskHref(t.task_id)}>{t.step}</a>} <span className="muted">with {holder(t.assignee, t.role, t.kind)}</span>{" "}
                      {t.escalated && <UIBadge className="badge late">escalated</UIBadge>} <Due at={t.due_at} />
                    </div>
                  ))}
                </td>
                <td className="small">{hours(Math.round(i.age_h))}</td>
                <td className="small">v{i.version}</td>
                <td className="small"><Actor who={i.started_by} /></td>
                <td>{i.state === "running" && person && (
                  <Button variant="link" className="small" disabled={!!a.busy}
                    onClick={() => confirm(`Cancel "${i.title}"?`) && a.run(`c${i.id}`, () => api.cancelProcess(i.id, person, "cancelled from the process page"), list.reload)}>cancel</Button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {a.error && <p className="error small">{a.error}</p>}
    </>
  );
}

function RoutingTab({ def }: { def: WorkDefinition }) {
  const r = useAsync(() => api.workRouting(def.id), [def.id]);
  if (!r.data) return <Status loading={r.loading} error={r.error} onRetry={r.reload} />;
  return (
    <>
      <p className="small muted">Version {def.version}. A step's own SLA and escalation win; where it sets none, its role's defaults apply
        (edit them under <a href={href("/users", { view: "workflow" })}>Users</a>). Change a step's own settings in the Model tab.</p>
      <Table variant="list">
        <thead><tr><th>Step</th><th>Done by</th><th>Assigned</th><th>SLA</th><th>When it passes, goes to</th><th>Form</th></tr></thead>
        <tbody>
          {r.data.map((s) => (
            <tr key={s.element}>
              <td><strong>{s.step}</strong>{s.requires_human && <UIBadge className="badge human"> needs a person</UIBadge>}</td>
              <td>{s.kind === "agent" ? <>{s.role} agent{s.fallback_role && <span className="muted small"> (fails to {s.fallback_role})</span>}</>
                : <a href={href("/teams", { role: s.role ?? "" })}>{s.role}</a>}</td>
              <td className="small">{s.kind === "person" ? (s.assignment ?? "queue").replace("_", " ") : "agent worker"}</td>
              <td>{s.sla ?? <span className="muted">none</span>} {s.sla_from === "role" && <span className="muted small">(role default)</span>}</td>
              <td className="small">{s.escalation.length ? s.escalation.join(" → ") : <span className="muted">stays with its role</span>}
                {s.escalation_from === "role" && <span className="muted"> (role chain)</span>}</td>
              <td className="small">{s.form ? <a className="mono" href={href(`/processes/forms/${s.form}`)}>{s.form}</a> : ""}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  );
}
