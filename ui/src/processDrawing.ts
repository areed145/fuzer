// A BPMN file as the shapes Fuzer draws (BpmnView): each pool, lane, step, event and gateway where the file's diagram
// puts it, and each sequence flow's path. Pure, so it is read the same in the browser and in tests.

export interface Box { x: number; y: number; w: number; h: number }
export interface Shape extends Box {
  id: string;
  type: string;            // the BPMN element's local name: userTask, startEvent, exclusiveGateway, lane, ...
  name: string;
  label?: Box;             // where the file puts an event's or gateway's label, if it says
  event?: string;          // an event's definition: timer, message, signal, error, ...
}
export interface Edge { id: string; name: string; points: { x: number; y: number }[]; isDefault: boolean; label?: Box }
export interface Drawing { pools: Shape[]; lanes: Shape[]; nodes: Shape[]; edges: Edge[]; box: Box }

const num = (el: Element, attr: string) => Number(el.getAttribute(attr) ?? 0);
const boundsOf = (el: Element | undefined): Box | undefined => {
  const b = el && Array.from(el.children).find((c) => c.localName === "Bounds");
  return b ? { x: num(b, "x"), y: num(b, "y"), w: num(b, "width"), h: num(b, "height") } : undefined;
};
const labelOf = (el: Element) => boundsOf(Array.from(el.children).find((c) => c.localName === "BPMNLabel"));

/** The drawing in a BPMN 2.0 file with diagram interchange (as Fuzer's modeler writes). Throws on a file with no
 * diagram, so the page can say it couldn't draw it. */
export function parseProcess(xml: string, parser: { parseFromString(s: string, t: string): Document } = new DOMParser()): Drawing {
  const doc = parser.parseFromString(xml, "application/xml");
  if (doc.getElementsByTagName("parsererror").length) throw new Error("the file isn't valid XML");
  const model = new Map<string, Element>();
  for (const el of Array.from(doc.getElementsByTagName("*"))) {
    const id = el.getAttribute("id");
    if (id && !el.namespaceURI?.includes("/DI")) model.set(id, el);
  }
  const defaults = new Set<string>();
  model.forEach((el) => { const d = el.getAttribute("default"); if (d) defaults.add(d); });

  const pools: Shape[] = [], lanes: Shape[] = [], nodes: Shape[] = [], edges: Edge[] = [];
  for (const di of Array.from(doc.getElementsByTagNameNS("*", "BPMNShape"))) {
    const id = di.getAttribute("bpmnElement") ?? "";
    const el = model.get(id);
    const b = boundsOf(di);
    if (!el || !b) continue;
    const def = Array.from(el.children).find((c) => c.localName.endsWith("EventDefinition"));
    const shape: Shape = { id, type: el.localName, name: (el.getAttribute("name") ?? "").trim(), ...b, label: labelOf(di),
      event: def?.localName.replace(/EventDefinition$/, "") };
    if (el.localName === "participant") pools.push(shape);
    else if (el.localName === "lane") lanes.push(shape);
    else nodes.push(shape);
  }
  for (const di of Array.from(doc.getElementsByTagNameNS("*", "BPMNEdge"))) {
    const id = di.getAttribute("bpmnElement") ?? "";
    const el = model.get(id);
    const points = Array.from(di.children).filter((c) => c.localName === "waypoint").map((w) => ({ x: num(w, "x"), y: num(w, "y") }));
    if (!el || points.length < 2) continue;
    edges.push({ id, name: (el.getAttribute("name") ?? "").trim(), points, isDefault: defaults.has(id), label: labelOf(di) });
  }
  if (!pools.length && !lanes.length && !nodes.length) throw new Error("the file has no diagram to draw");

  const xs: number[] = [], ys: number[] = [];
  const add = (b: Box) => { xs.push(b.x, b.x + b.w); ys.push(b.y, b.y + b.h); };
  [...pools, ...lanes, ...nodes].forEach(add);
  nodes.forEach((n) => n.label && add(n.label));
  edges.forEach((e) => e.points.forEach((p) => { xs.push(p.x); ys.push(p.y); }));
  const x = Math.min(...xs), y = Math.min(...ys);
  return { pools, lanes, nodes, edges, box: { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y } };
}

/** A step's name in lines that fit `width` at about `charW` per character, at most `max` lines (the last one cut with
 * an ellipsis). */
export function wrap(text: string, width: number, charW = 6.6, max = 3): string[] {
  const fit = Math.max(4, Math.floor(width / charW));
  const lines: string[] = [];
  let line = "";
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const next = line ? `${line} ${word}` : word;
    if (next.length <= fit) { line = next; continue; }
    if (line) lines.push(line);
    line = word.length > fit ? `${word.slice(0, fit - 1)}…` : word;
  }
  if (line) lines.push(line);
  if (lines.length > max) {
    const kept = lines.slice(0, max);
    kept[max - 1] = `${kept[max - 1].slice(0, fit - 1).replace(/\s+\S*$/, "") || kept[max - 1].slice(0, fit - 1)}…`;
    return kept;
  }
  return lines;
}

/** A polyline with its corners rounded (radius `r`, less where a segment is short), as an SVG path. */
export function roundedPath(points: { x: number; y: number }[], r = 8): string {
  if (points.length < 3) return points.map((p, i) => `${i ? "L" : "M"}${p.x} ${p.y}`).join(" ");
  let d = `M${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [a, b, c] = [points[i - 1], points[i], points[i + 1]];
    const l1 = Math.hypot(b.x - a.x, b.y - a.y), l2 = Math.hypot(c.x - b.x, c.y - b.y);
    const k = Math.min(r, l1 / 2, l2 / 2);
    if (!k) { d += ` L${b.x} ${b.y}`; continue; }
    const p1 = { x: b.x - ((b.x - a.x) / l1) * k, y: b.y - ((b.y - a.y) / l1) * k };
    const p2 = { x: b.x + ((c.x - b.x) / l2) * k, y: b.y + ((c.y - b.y) / l2) * k };
    d += ` L${p1.x} ${p1.y} Q${b.x} ${b.y} ${p2.x} ${p2.y}`;
  }
  const last = points[points.length - 1];
  return `${d} L${last.x} ${last.y}`;
}
