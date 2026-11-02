// The process modeler (#97): bpmn-js cut down to the engine's subset (bpmnSubset.ts) beside a Fuzer
// properties panel for what the engine reads: lane roles and agents, requiresHuman, SLAs and
// escalation, task forms, conditions, timers, messages, called processes and the score thresholds
// conditions use. bpmn.io's watermark stays visible, as its licence asks.

import { useEffect, useReducer, useRef, useState, type ReactNode } from "react";
import Modeler from "bpmn-js/lib/Modeler";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";
import { href } from "./lib";
import { fuzerModdle, subsetModule } from "./bpmnSubset";

const PALETTE_PX = 120;

export interface ModelerContext {
  roles: { person: string[]; agent: string[] };
  forms: string[];
  processes: string[];   // published process keys a call activity may call
}

export default function BpmnModeler({ xml, classes, ctx, onReady, onDirty }: {
  xml: string;
  classes?: Record<string, string>;   // element id -> marker class (problems, changes)
  ctx: ModelerContext;
  onReady: (m: Modeler | undefined) => void;
  onDirty: () => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [modeler, setModeler] = useState<Modeler>();
  const [error, setError] = useState<string>();
  const marked = useRef<Record<string, string>>({});

  useEffect(() => {
    const m = new Modeler({ container: box.current!, additionalModules: [subsetModule], moddleExtensions: { fuzer: fuzerModdle } });
    let live = true;
    m.importXML(xml).then(
      ({ warnings }) => {
        if (!live) return;
        // fit the diagram, then widen the view so the palette (on the left) doesn't cover it
        const canvas = m.get("canvas") as any;
        canvas.zoom("fit-viewport");
        const vb = canvas.viewbox();
        const grow = vb.outer.width / Math.max(vb.outer.width - PALETTE_PX, 1);
        canvas.viewbox({ x: vb.x - vb.width * (grow - 1), y: vb.y, width: vb.width * grow, height: vb.height * grow });
        if (warnings.length) setError(`${warnings.length} part(s) of the file couldn't be read: ${(warnings[0] as any).message ?? warnings[0]}`);
        marked.current = {};
        setModeler(m);
        onReady(m);
        m.on("commandStack.changed", onDirty);
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
      onReady(undefined);
      setModeler(undefined);
      m.destroy();
    };
    // a new file is a new modeler; the callbacks are stable enough for the page's life
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [xml]);

  useEffect(() => {
    if (!modeler) return;
    const canvas = modeler.get("canvas") as any;
    const registry = modeler.get("elementRegistry") as any;
    for (const [id, cls] of Object.entries(marked.current)) if (registry.get(id)) canvas.removeMarker(id, cls);
    const now: Record<string, string> = {};
    for (const [id, cls] of Object.entries(classes ?? {})) if (registry.get(id)) {
      canvas.addMarker(id, cls);
      now[id] = cls;
    }
    marked.current = now;
  }, [modeler, classes]);

  return (
    <div className="modeler">
      <div className="modeler-canvas">
        {error && <div className="status error small">{error}</div>}
        <div className="bpmn-canvas tall" ref={box} />
      </div>
      {modeler && <Properties modeler={modeler} ctx={ctx} />}
    </div>
  );
}

// --- properties panel ---------------------------------------------------------------------------

const is = (bo: any, type: string) => !!bo?.$instanceOf?.(type);

function Properties({ modeler, ctx }: { modeler: Modeler; ctx: ModelerContext }) {
  const [el, setEl] = useState<any>();
  const [, refresh] = useReducer((x: number) => x + 1, 0);

  useEffect(() => {
    const onSelect = (e: any) => setEl(e.newSelection.length === 1 ? e.newSelection[0] : undefined);
    const onChange = () => {
      // a removed element can't be edited; fall back to the process
      setEl((cur: any) => (cur && !(modeler.get("elementRegistry") as any).get(cur.id) ? undefined : cur));
      refresh();
    };
    modeler.on("selection.changed", onSelect);
    modeler.on("commandStack.changed", onChange);
    return () => {
      modeler.off("selection.changed", onSelect);
      modeler.off("commandStack.changed", onChange);
    };
  }, [modeler]);

  const ed = new Editing(modeler);
  const bo = el?.businessObject;
  let body: ReactNode;
  if (!el || is(bo, "bpmn:Participant") || is(bo, "bpmn:Collaboration") || is(bo, "bpmn:Process")) {
    body = <ProcessProps ed={ed} ctx={ctx} />;
  } else if (el.type === "label") {
    body = <p className="muted small">A label. Select its element to edit it.</p>;
  } else {
    body = <ElementProps ed={ed} el={el} ctx={ctx} />;
  }
  return <aside className="props">{body}</aside>;
}

/** The modeler services the panel edits through (every edit is one undoable command). */
class Editing {
  modeling: any;
  moddle: any;
  registry: any;
  root: any;
  constructor(public modeler: Modeler) {
    this.modeling = modeler.get("modeling");
    this.moddle = modeler.get("moddle");
    this.registry = modeler.get("elementRegistry");
    this.root = (modeler.get("canvas") as any).getRootElement();
  }

  /** The process (with a pool, the one the first pool holds). */
  get process(): any {
    const bo = this.root.businessObject;
    if (is(bo, "bpmn:Process")) return bo;
    return (bo.participants ?? []).find((p: any) => p.processRef)?.processRef;
  }

  set(el: any, props: Record<string, unknown>) {
    this.modeling.updateProperties(el, props);
  }

  setOn(moddleElement: any, props: Record<string, unknown>, el: any = this.root) {
    this.modeling.updateModdleProperties(el, moddleElement, props);
  }

  expr(body: string | undefined) {
    return body ? this.moddle.create("bpmn:FormalExpression", { body }) : undefined;
  }

  laneOf(el: any): any {
    const lane = this.registry.filter((e: any) => is(e.businessObject, "bpmn:Lane") &&
      (e.businessObject.flowNodeRef ?? []).includes(el.businessObject))[0];
    return lane?.businessObject;
  }

  message(name: string): any {
    const defs = (this.modeler as any).getDefinitions();
    const found = defs.rootElements.find((r: any) => is(r, "bpmn:Message") && r.name === name);
    if (found) return found;
    const base = `Message_${name.replace(/[^A-Za-z0-9_]/g, "_")}`;
    let id = base;
    for (let i = 2; defs.rootElements.some((r: any) => r.id === id); i++) id = `${base}_${i}`;
    const msg = this.moddle.create("bpmn:Message", { id, name });
    msg.$parent = defs;
    defs.rootElements.push(msg);
    return msg;
  }
}

const human = (s: string) => s.replace(/_/g, " ");

function ProcessProps({ ed, ctx }: { ed: Editing; ctx: ModelerContext }) {
  const p = ed.process;
  if (!p) return <p className="muted small">No process in this diagram.</p>;
  const thresholds: { name: string; value: string }[] = (p.extensionElements?.values ?? [])
    .filter((v: any) => v.$type === "fuzer:Threshold").map((v: any) => ({ name: v.name ?? "", value: v.value ?? "" }));
  const saveThresholds = (rows: { name: string; value: string }[]) => {
    const ext = p.extensionElements;
    const values = [...(ext?.values ?? []).filter((v: any) => v.$type !== "fuzer:Threshold"),
      ...rows.filter((r) => r.name).map((r) => ed.moddle.create("fuzer:Threshold", r))];
    if (ext) ed.setOn(ext, { values });
    else ed.setOn(p, { extensionElements: ed.moddle.create("bpmn:ExtensionElements", { values }) });
  };
  return (
    <>
      <h3>Process</h3>
      <Field label="Key" value={p.id} readOnly hint="Stays the same across versions; processes and the API start it by this." />
      <Text label="Name" value={p.name} onCommit={(v) => ed.setOn(p, { name: v })} />
      <Text label="Work item type" value={p.workItemType} placeholder={p.id}
        onCommit={(v) => ed.setOn(p, { workItemType: v })} hint="How its items are filed in the inbox and on the board." />
      <Text label="Correlation key" value={p.correlationKey} mono placeholder="incident_id"
        onCommit={(v) => ed.setOn(p, { correlationKey: v })}
        hint="An expression over the start data. New evidence with the same key joins the open process instead of starting another." />
      <Select label="Owner role" value={p.ownerRole} options={ctx.roles.person} blank="engineer (default)"
        onChange={(v) => ed.setOn(p, { ownerRole: v })} hint="Gets a step that can't go on (a failing condition, say)." />
      <Thresholds rows={thresholds} onSave={saveThresholds} />
      <p className="muted small">Select an element to edit it. Drag shapes from the palette on the left; only the parts the engine runs are offered.</p>
    </>
  );
}

function Thresholds({ rows, onSave }: { rows: { name: string; value: string }[]; onSave: (rows: { name: string; value: string }[]) => void }) {
  const [draft, setDraft] = useState(rows);
  useEffect(() => setDraft(rows), [JSON.stringify(rows)]);
  const update = (i: number, k: "name" | "value", v: string) => setDraft(draft.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
  const commit = (next = draft) => {
    if (JSON.stringify(next) !== JSON.stringify(rows)) onSave(next);
  };
  return (
    <div className="prop">
      <span>Score thresholds</span>
      {draft.map((r, i) => (
        <div key={i} className="row threshold">
          <input placeholder="name" value={r.name} onChange={(e) => update(i, "name", e.target.value)} onBlur={() => commit()} />
          <input placeholder="value" value={r.value} onChange={(e) => update(i, "value", e.target.value)} onBlur={() => commit()} />
          <button className="small" title="Remove" onClick={() => commit(draft.filter((_, j) => j !== i))}>×</button>
        </div>
      ))}
      <button className="small" onClick={() => setDraft([...draft, { name: "", value: "" }])}>Add threshold</button>
      <small>#100 value / risk cut-offs, read in conditions as <code>thresholds["name"]</code>.</small>
    </div>
  );
}

const TITLES: Record<string, string> = {
  "bpmn:UserTask": "Person's task", "bpmn:ServiceTask": "Agent's task", "bpmn:CallActivity": "Call activity",
  "bpmn:ExclusiveGateway": "Decision", "bpmn:ParallelGateway": "Parallel split / join", "bpmn:StartEvent": "Start",
  "bpmn:EndEvent": "End", "bpmn:IntermediateCatchEvent": "Wait", "bpmn:BoundaryEvent": "Boundary event",
  "bpmn:SequenceFlow": "Sequence flow", "bpmn:Lane": "Lane", "bpmn:TextAnnotation": "Note",
};

function ElementProps({ ed, el, ctx }: { ed: Editing; el: any; ctx: ModelerContext }) {
  const bo = el.businessObject;
  const def = bo.eventDefinitions?.[0];
  const lane = ed.laneOf(el);
  const title = TITLES[bo.$type] ?? bo.$type.replace("bpmn:", "");
  const supported = title !== bo.$type.replace("bpmn:", "") || is(bo, "bpmn:Association");
  return (
    <>
      <h3>{title}{bo.$type === "bpmn:EndEvent" && def ? " (terminate)" : ""}</h3>
      {!supported && <p className="error small">The engine doesn't run a {bo.$type.replace("bpmn:", "")}; publishing will refuse it.</p>}
      <Field label="Id" value={bo.id} readOnly mono />
      {!is(bo, "bpmn:TextAnnotation") && <Text label="Name" value={bo.name} onCommit={(v) => ed.set(el, { name: v })} />}

      {is(bo, "bpmn:Lane") && <LaneProps ed={ed} el={el} ctx={ctx} />}
      {is(bo, "bpmn:UserTask") && <UserTaskProps ed={ed} el={el} lane={lane} ctx={ctx} />}
      {is(bo, "bpmn:ServiceTask") && <ServiceTaskProps ed={ed} el={el} lane={lane} ctx={ctx} />}
      {is(bo, "bpmn:CallActivity") && (
        <Select label="Calls process" value={bo.calledElement} options={ctx.processes} blank="(choose a published process)"
          onChange={(v) => ed.set(el, { calledElement: v })} hint="It runs as a child process; this step waits until it ends." />
      )}
      {is(bo, "bpmn:ExclusiveGateway") && <GatewayProps ed={ed} el={el} />}
      {is(bo, "bpmn:ParallelGateway") && (
        <p className="muted small">Several flows out: every branch runs. Several flows in: waits for all of them.</p>
      )}
      {is(bo, "bpmn:SequenceFlow") && <FlowProps ed={ed} el={el} />}
      {is(bo, "bpmn:BoundaryEvent") && (
        <Check label="Interrupting" checked={bo.cancelActivity !== false} onChange={(v) => ed.set(el, { cancelActivity: v })}
          hint={bo.cancelActivity !== false ? "Cancels the task it is attached to and goes on from here." :
            "Starts a side branch (a reminder, say) and leaves the task running."} />
      )}
      {def && is(def, "bpmn:TimerEventDefinition") && <TimerProps ed={ed} el={el} def={def} />}
      {def && is(def, "bpmn:MessageEventDefinition") && <MessageProps ed={ed} el={el} def={def} />}
      {is(bo, "bpmn:StartEvent") && <p className="muted small">A process has exactly one start event; the start data is what the process is started with.</p>}
    </>
  );
}

function LaneProps({ ed, el, ctx }: { ed: Editing; el: any; ctx: ModelerContext }) {
  const bo = el.businessObject;
  const value = bo.agent ? `agent:${bo.agent}` : bo.role ? `role:${bo.role}` : "";
  return (
    <label className="prop">
      <span>Who works it</span>
      <select value={value} onChange={(e) => {
        const [kind, v] = e.target.value.split(":");
        ed.set(el, { "fuzer:role": kind === "role" ? v : undefined, "fuzer:agent": kind === "agent" ? v : undefined });
      }}>
        <option value="">(from the lane name)</option>
        <optgroup label="People">{ctx.roles.person.map((r) => <option key={r} value={`role:${r}`}>{human(r)}</option>)}</optgroup>
        <optgroup label="Agents">{ctx.roles.agent.map((r) => <option key={r} value={`agent:${r}`}>{r} agent</option>)}</optgroup>
      </select>
      <small>Tasks in the lane go to this role's queue or agent unless the task names its own.</small>
    </label>
  );
}

function UserTaskProps({ ed, el, lane, ctx }: { ed: Editing; el: any; lane: any; ctx: ModelerContext }) {
  const bo = el.businessObject;
  const laneRole = lane?.role;
  return (
    <>
      {lane?.agent && !bo.role && <p className="error small">This person's task is in the {lane.agent} agent's lane: give it a role or move it.</p>}
      <Select label="Role" value={bo.role} options={ctx.roles.person} blank={laneRole ? `${human(laneRole)} (from the lane)` : "(choose)"}
        onChange={(v) => ed.set(el, { "fuzer:role": v })} />
      <Text label="Assignee" value={bo.assignee} placeholder="anyone with the role"
        onCommit={(v) => ed.set(el, { "fuzer:assignee": v })} hint="A person's name, or leave it to the role's queue." />
      <Check label="Needs a person" checked={!!bo.requiresHuman} onChange={(v) => ed.set(el, { "fuzer:requiresHuman": v || undefined })}
        hint="Approvals, closing safety items, anything reaching outside Fuzer. No agent can claim, complete or be handed it." />
      <Text label="SLA" value={bo.sla} mono placeholder="PT4H" onCommit={(v) => ed.set(el, { "fuzer:sla": v })}
        hint="ISO 8601 duration (PT4H, P1D). Every person's task needs one, or a timer on its edge." />
      <Select label="Escalate to" value={bo.escalateTo} options={ctx.roles.person} blank="(nobody)"
        onChange={(v) => ed.set(el, { "fuzer:escalateTo": v })} hint="Gets the task once the SLA passes." />
      <label className="prop">
        <span>Form</span>
        <select value={bo.form ?? ""} onChange={(e) => ed.set(el, { "fuzer:form": e.target.value || undefined })}>
          <option value="">(no form: complete only)</option>
          {[...new Set([...ctx.forms, ...(bo.form ? [bo.form] : [])])].sort().map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
        <small>
          {bo.form && <><a href={href(`/work/forms/${bo.form}`)} target="_blank" rel="noreferrer">Edit {bo.form}</a> · </>}
          <a href={href("/work/forms/new", { key: bo.form ? undefined : `${ed.process?.id ?? "form"}-${bo.id}` })} target="_blank" rel="noreferrer">New form</a>
          {" "}(opens in a new tab; the field keys become process data for conditions)
        </small>
      </label>
    </>
  );
}

function ServiceTaskProps({ ed, el, lane, ctx }: { ed: Editing; el: any; lane: any; ctx: ModelerContext }) {
  const bo = el.businessObject;
  return (
    <>
      <Select label="Agent" value={bo.agent} options={ctx.roles.agent}
        blank={lane?.agent ? `${lane.agent} (from the lane)` : "(choose)"} onChange={(v) => ed.set(el, { "fuzer:agent": v })}
        hint="A worker claims the step, runs this agent and records its result." />
      <Select label="If the agent keeps failing" value={bo.fallbackRole} options={ctx.roles.person} blank="the process owner"
        onChange={(v) => ed.set(el, { "fuzer:fallbackRole": v })} hint="The role that gets the step after the retries." />
      {bo.requiresHuman && (
        <Check label="Needs a person" checked onChange={() => ed.set(el, { "fuzer:requiresHuman": undefined })}
          hint="An agent's task can't need a person: untick this or make it a person's task." />
      )}
    </>
  );
}

function GatewayProps({ ed, el }: { ed: Editing; el: any }) {
  const bo = el.businessObject;
  const outs: any[] = el.outgoing ?? [];
  if (outs.length < 2) return <p className="muted small">Draw two or more flows out, each with a condition (or one as the default).</p>;
  return (
    <div className="prop">
      <span>Branches</span>
      {outs.map((c) => (
        <div key={c.id} className="branch">
          <div className="row small">
            <strong>{c.businessObject.name || c.id}</strong>
            <label className="row"><input type="radio" checked={bo.default === c.businessObject}
              onChange={() => ed.set(el, { default: c.businessObject })} /> default</label>
          </div>
          {bo.default !== c.businessObject && (
            <Text value={c.businessObject.conditionExpression?.body} mono placeholder='triage == "real"'
              onCommit={(v) => ed.set(c, { conditionExpression: ed.expr(v) })} />
          )}
        </div>
      ))}
      {bo.default && <button className="small" onClick={() => ed.set(el, { default: undefined })}>No default</button>}
      <small>The first branch whose condition is true is taken; the default when none is. Conditions read the process data
        (form fields, agent results) and <code>thresholds["…"]</code>.</small>
    </div>
  );
}

function FlowProps({ ed, el }: { ed: Editing; el: any }) {
  const bo = el.businessObject;
  const src = el.source?.businessObject;
  if (!is(src, "bpmn:ExclusiveGateway")) return null;
  if (src.default === bo) return <p className="muted small">The default branch of {src.name || src.id}: taken when no other condition is true.</p>;
  return <Text label="Condition" value={bo.conditionExpression?.body} mono placeholder='approval == "approved"'
    onCommit={(v) => ed.set(el, { conditionExpression: ed.expr(v) })} />;
}

const TIMER_KINDS = [["timeDuration", "Wait for (duration)", "PT6H"], ["timeDate", "Until (date)", "2026-12-01T06:00:00Z"],
  ["timeCycle", "Every (cycle)", "R3/PT1H"]] as const;

function TimerProps({ ed, el, def }: { ed: Editing; el: any; def: any }) {
  const kind = TIMER_KINDS.find(([k]) => def[k])?.[0] ?? "timeDuration";
  const body = def[kind]?.body;
  const put = (k: string, v: string | undefined) =>
    ed.setOn(def, { timeDuration: undefined, timeDate: undefined, timeCycle: undefined, [k]: ed.expr(v) }, el);
  return (
    <>
      <Select label="Timer" value={kind} options={TIMER_KINDS.map(([k]) => k)} labels={Object.fromEntries(TIMER_KINDS.map(([k, l]) => [k, l]))}
        onChange={(v) => put(v ?? "timeDuration", body)} />
      <Text label="When" value={body} mono placeholder={TIMER_KINDS.find(([k]) => k === kind)![2]}
        onCommit={(v) => put(kind, v)} hint="ISO 8601, or an expression over the process data." />
    </>
  );
}

const MESSAGES = ["evidence", "notification_raised", "comment"];

function MessageProps({ ed, el, def }: { ed: Editing; el: any; def: any }) {
  return (
    <Text label="Message" value={def.messageRef?.name} mono placeholder="evidence" list="fz-messages"
      onCommit={(v) => ed.setOn(def, { messageRef: v ? ed.message(v) : undefined }, el)}
      hint="Reaches the open process with the same correlation key: evidence (agents' send_evidence), notification_raised, comment, or your own name." />
  );
}

// --- fields ----------------------------------------------------------------------------------------

function Field({ label, value, readOnly, mono, hint }: { label: string; value?: string; readOnly?: boolean; mono?: boolean; hint?: string }) {
  return (
    <label className="prop">
      <span>{label}</span>
      <input className={mono ? "mono" : ""} value={value ?? ""} readOnly={readOnly} />
      {hint && <small>{hint}</small>}
    </label>
  );
}

/** A text field that commits on blur or Enter, so each edit is one undo step. Empty clears it. */
function Text({ label, value, onCommit, placeholder, hint, mono, list }: {
  label?: string; value?: string; onCommit: (v: string | undefined) => void; placeholder?: string; hint?: string; mono?: boolean; list?: string;
}) {
  const [v, setV] = useState(value ?? "");
  useEffect(() => setV(value ?? ""), [value]);
  const commit = () => {
    if (v.trim() !== (value ?? "")) onCommit(v.trim() || undefined);
  };
  return (
    <label className="prop">
      {label && <span>{label}</span>}
      <input className={mono ? "mono" : ""} value={v} placeholder={placeholder} list={list} onChange={(e) => setV(e.target.value)}
        onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} />
      {list === "fz-messages" && <datalist id="fz-messages">{MESSAGES.map((m) => <option key={m} value={m} />)}</datalist>}
      {hint && <small>{hint}</small>}
    </label>
  );
}

function Select({ label, value, options, labels, blank, onChange, hint }: {
  label: string; value?: string; options: string[]; labels?: Record<string, string>; blank?: string;
  onChange: (v: string | undefined) => void; hint?: string;
}) {
  const all = value && !options.includes(value) ? [...options, value] : options;
  return (
    <label className="prop">
      <span>{label}</span>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
        {blank !== undefined && <option value="">{blank}</option>}
        {all.map((o) => <option key={o} value={o}>{labels?.[o] ?? human(o)}{options.includes(o) ? "" : " (unknown)"}</option>)}
      </select>
      {hint && <small>{hint}</small>}
    </label>
  );
}

function Check({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="prop check">
      <span><input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} /> {label}</span>
      {hint && <small>{hint}</small>}
    </label>
  );
}
