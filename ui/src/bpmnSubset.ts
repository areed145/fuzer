// What the modeler offers (#97): only the BPMN subset the Fuzer engine runs (src/fuzer/workflow/bpmn.py),
// and the `fuzer:` properties it reads, so they load and save as attributes rather than being dropped.
// The palette, the context pad's append buttons and the wrench (replace) menu are all cut down to the
// subset; the publish checks still refuse anything that slips through (a pasted file, say).

export const FUZER_NS = "https://fuzer.dev/schema/bpmn";

const attr = (name: string, type = "String") => ({ name, isAttr: true, type });

/** bpmn-moddle descriptor for the fuzer: namespace. */
export const fuzerModdle = {
  name: "Fuzer",
  prefix: "fuzer",
  uri: FUZER_NS,
  xml: { tagAlias: "lowerCase" },
  associations: [],
  types: [
    { name: "FuzerProcess", extends: ["bpmn:Process"],
      properties: [attr("workItemType"), attr("correlationKey"), attr("ownerRole")] },
    { name: "FuzerLane", extends: ["bpmn:Lane"], properties: [attr("role"), attr("agent")] },
    { name: "FuzerUserTask", extends: ["bpmn:UserTask"],
      properties: [attr("role"), attr("assignee"), attr("requiresHuman", "Boolean"), attr("sla"), attr("escalateTo"),
                   attr("form"), attr("dispatch"), attr("gate")] },
    { name: "FuzerServiceTask", extends: ["bpmn:ServiceTask"],
      properties: [attr("agent"), attr("fallbackRole"), attr("requiresHuman", "Boolean"),
                   attr("component"), attr("instructions"), attr("gate"), attr("gateRole")] },
    // A score threshold (#100) in the process's extensionElements, read in conditions as thresholds["name"].
    { name: "Threshold", superClass: ["Element"], meta: { allowedIn: ["bpmn:Process"] },
      properties: [attr("name"), attr("value")] },
  ],
};

// Wrench menu entries that stay; everything else (other task and event kinds, sub-processes, loop and
// compensation toggles) goes.
const REPLACE_OK = new Set([
  "user-task", "service-task", "call-activity", "exclusive-gateway", "parallel-gateway", "none-start", "none-end",
  "terminate-end", "timer-intermediate-catch", "message-intermediate-catch", "timer-boundary", "message-boundary",
  "non-interrupting-timer-boundary", "non-interrupting-message-boundary", "expanded-pool", "collapsed-pool",
  "sequence-flow", "default-flow",
].map((id) => `replace-with-${id}`));

const PALETTE_KEEP = ["hand-tool", "lasso-tool", "space-tool", "global-connect-tool", "tool-separator",
  "create.start-event", "create.end-event"];

type Shape = { type: string; eventDefinitionType?: string };

const SHAPES: Record<string, { group: string; className: string; title: string; shape: Shape }> = {
  "user-task": { group: "activity", className: "bpmn-icon-user-task", title: "Person's task (user task)",
    shape: { type: "bpmn:UserTask" } },
  "service-task": { group: "activity", className: "bpmn-icon-service-task", title: "Agent's task (service task)",
    shape: { type: "bpmn:ServiceTask" } },
  "call-activity": { group: "activity", className: "bpmn-icon-call-activity", title: "Call another process",
    shape: { type: "bpmn:CallActivity" } },
  "exclusive-gateway": { group: "gateway", className: "bpmn-icon-gateway-xor", title: "Decision (exclusive gateway)",
    shape: { type: "bpmn:ExclusiveGateway" } },
  "parallel-gateway": { group: "gateway", className: "bpmn-icon-gateway-parallel", title: "Split or join (parallel gateway)",
    shape: { type: "bpmn:ParallelGateway" } },
  "timer-event": { group: "event", className: "bpmn-icon-intermediate-event-catch-timer", title: "Wait (timer)",
    shape: { type: "bpmn:IntermediateCatchEvent", eventDefinitionType: "bpmn:TimerEventDefinition" } },
  "message-event": { group: "event", className: "bpmn-icon-intermediate-event-catch-message",
    title: "Wait for a message (evidence, a notification, a comment)",
    shape: { type: "bpmn:IntermediateCatchEvent", eventDefinitionType: "bpmn:MessageEventDefinition" } },
};

class SubsetPalette {
  static $inject = ["palette", "create", "elementFactory"];
  constructor(palette: any, private create: any, private elementFactory: any) {
    palette.registerProvider(500, this);
  }

  getPaletteEntries() {
    return (entries: Record<string, any>) => {
      const out: Record<string, any> = {};
      const keep = (id: string) => entries[id] && (out[id] = entries[id]);
      const add = (id: string) => {
        const s = SHAPES[id];
        const start = (event: Event) => this.create.start(event, this.elementFactory.createShape(s.shape));
        out[`create.${id}`] = { group: s.group, className: s.className, title: s.title, action: { dragstart: start, click: start } };
      };
      PALETTE_KEEP.forEach(keep);
      ["timer-event", "message-event", "exclusive-gateway", "parallel-gateway", "user-task", "service-task",
       "call-activity"].forEach(add);
      keep("create.participant-expanded");
      return out;
    };
  }
}

class SubsetContextPad {
  static $inject = ["contextPad", "create", "elementFactory", "autoPlace"];
  constructor(contextPad: any, private create: any, private elementFactory: any, private autoPlace: any) {
    contextPad.registerProvider(500, this);
  }

  getContextPadEntries(element: any) {
    return (entries: Record<string, any>) => {
      const append = (id: string) => {
        const s = SHAPES[id];
        const make = () => this.elementFactory.createShape(s.shape);
        return {
          group: "model", className: s.className, title: `Append: ${s.title}`,
          action: {
            click: (_e: Event, el: any) => this.autoPlace.append(el, make()),
            dragstart: (e: Event) => this.create.start(e, make(), { source: element }),
          },
        };
      };
      const out: Record<string, any> = {};
      for (const [id, entry] of Object.entries(entries)) {
        if (id === "append.append-task") {
          out["append.user-task"] = append("user-task");
          out["append.service-task"] = append("service-task");
        } else if (id === "append.intermediate-event") {
          out["append.timer-event"] = append("timer-event");
        } else if (!id.startsWith("append.") || ["append.end-event", "append.gateway", "append.text-annotation"].includes(id)) {
          out[id] = entry;
        }
      }
      return out;
    };
  }
}

class SubsetReplaceMenu {
  static $inject = ["popupMenu"];
  constructor(popupMenu: any) {
    popupMenu.registerProvider("bpmn-replace", 500, this);
  }

  getPopupMenuEntries() {
    return (entries: Record<string, any>) => Object.fromEntries(Object.entries(entries).filter(([id]) => REPLACE_OK.has(id)));
  }

  getPopupMenuHeaderEntries() {
    return () => ({});
  }
}

/** additionalModules entry for the bpmn-js Modeler. */
export const subsetModule = {
  __init__: ["fuzerPalette", "fuzerContextPad", "fuzerReplaceMenu"],
  fuzerPalette: ["type", SubsetPalette],
  fuzerContextPad: ["type", SubsetContextPad],
  fuzerReplaceMenu: ["type", SubsetReplaceMenu],
};

/** Select an element in a modeler and bring it into view (from the problems or changes list). */
export function focusElement(modeler: { get: (name: string) => unknown }, id: string) {
  const el = (modeler.get("elementRegistry") as any).get(id);
  if (!el) return;
  (modeler.get("selection") as any).select(el);
  if (!el.waypoints) (modeler.get("canvas") as any).scrollToElement(el);
}

/** A new process: a start event in an empty diagram. */
export function blankProcess(key: string, name: string): string {
  const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  return `<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI" xmlns:dc="http://www.omg.org/spec/DD/20100524/DC" xmlns:di="http://www.omg.org/spec/DD/20100524/DI" xmlns:fuzer="${FUZER_NS}" id="Definitions_${key}" targetNamespace="https://fuzer.dev/workflow">
  <bpmn:process id="${key}" name="${esc(name)}" isExecutable="true" fuzer:workItemType="${key}">
    <bpmn:startEvent id="start" name="Started" />
  </bpmn:process>
  <bpmndi:BPMNDiagram id="Diagram_${key}">
    <bpmndi:BPMNPlane id="Plane_${key}" bpmnElement="${key}">
      <bpmndi:BPMNShape id="start_di" bpmnElement="start"><dc:Bounds x="180" y="160" width="36" height="36" /></bpmndi:BPMNShape>
    </bpmndi:BPMNPlane>
  </bpmndi:BPMNDiagram>
</bpmn:definitions>
`;
}
